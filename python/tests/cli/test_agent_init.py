"""``hops agent init`` writes a script that parses, with the spans and bounds the playbook names."""

from __future__ import annotations

import ast
from types import SimpleNamespace
from unittest import mock

import pandas as pd
import pytest
from click.testing import CliRunner
from hopsworks.cli.main import cli


def _fg(name, columns, primary_key, online=True, version=1):
    fg = mock.MagicMock()
    fg.name, fg.version = name, version
    fg.description = ""
    fg.features = [SimpleNamespace(name=n, type=t) for n, t in columns]
    fg.primary_key = primary_key
    fg.online_enabled = online
    fg.select_all.return_value.show.return_value = pd.DataFrame(
        {n: [1, 2, 3] for n, _ in columns}
    )
    return fg


@pytest.fixture
def contacts(mock_project):
    fg = _fg("contacts", [("id", "bigint"), ("email", "string")], ["id"])
    mock_project.get_feature_store.return_value.get_feature_group.return_value = fg
    return fg


@pytest.fixture(autouse=True)
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def _init(tmp_path, *args):
    result = CliRunner().invoke(cli, ["agent", "init", *args])
    files = list(tmp_path.glob("agents/*/agent.py"))
    return result, files[0].read_text() if files else None


def test_init_writes_a_script_that_parses(contacts, tmp_path):
    result, code = _init(tmp_path, "bot1", "--reads", "contacts")
    assert result.exit_code == 0, result.output
    ast.parse(code)
    assert 'framework="custom"' in code
    assert "tool_events=True" in code
    assert "aggregate_contacts" in code and "lookup_contacts" in code
    assert "LIMIT {MAX_ROWS}" in code and "MAX_ROWS = 50" in code
    assert '"openinference.span.kind": "TOOL"' in code
    assert code.count("llm.token_count.prompt") == 1
    assert "asyncio.to_thread" in code
    assert 'if __name__ == "__main__":' in code
    assert "claude-sonnet-5-5 input 2.00, output 10.00" in code
    assert "contacts_1" in code and "email (string)" in code
    assert "input_token_price_per_million_tokens" in result.output


def test_init_reads_the_key_from_the_secret_and_never_names_a_value(contacts, tmp_path):
    result, code = _init(
        tmp_path, "bot1", "--reads", "contacts", "--key-secret", "mykey"
    )
    assert result.exit_code == 0, result.output
    assert 'KEY_SECRET = "mykey"' in code
    assert "get_secrets_api().get(KEY_SECRET)" in code
    assert "print(" not in code


def test_init_refuses_to_overwrite_without_force(contacts):
    runner = CliRunner()
    base = ["agent", "init", "bot1", "--reads", "contacts"]
    assert runner.invoke(cli, base).exit_code == 0
    again = runner.invoke(cli, base)
    assert again.exit_code == 1
    assert "already exists. Pass --force" in again.output
    assert runner.invoke(cli, [*base, "--force"]).exit_code == 0


def test_init_without_an_online_store_has_no_lookup(mock_project, tmp_path):
    fg = _fg("events", [("id", "bigint")], ["id"], online=False)
    mock_project.get_feature_store.return_value.get_feature_group.return_value = fg
    result, code = _init(tmp_path, "bot1", "--reads", "events")
    assert result.exit_code == 0, result.output
    assert "aggregate_events" in code and "lookup_events" not in code


def test_init_an_unreadable_preview_still_writes_the_script(mock_project, tmp_path):
    fg = _fg("events", [("id", "bigint")], ["id"])
    fg.select_all.return_value.show.side_effect = RuntimeError("no commits")
    mock_project.get_feature_store.return_value.get_feature_group.return_value = fg
    result, code = _init(tmp_path, "bot1", "--reads", "events")
    assert result.exit_code == 0, result.output
    ast.parse(code)


def test_init_search_without_the_embedding_module_is_omitted(contacts, tmp_path):
    result, code = _init(
        tmp_path, "bot1", "--reads", "contacts", "--search", "contacts"
    )
    assert result.exit_code == 0, result.output
    assert "Search needs the embedding endpoint" in result.output
    assert "def search(" not in code


def test_init_search_with_the_embedding_module(mock_project, tmp_path):
    docs = _fg("docs", [("id", "bigint"), ("vec", "array<float>")], ["id"])
    docs.embedding_index.get_embeddings.return_value = [SimpleNamespace(name="vec")]
    mock_project.get_feature_store.return_value.get_feature_group.return_value = docs
    with mock.patch("importlib.util.find_spec", return_value=object()):
        result, code = _init(tmp_path, "bot1", "--reads", "docs", "--search", "docs")
    assert result.exit_code == 0, result.output
    ast.parse(code)
    assert "from hopsworks_agents.embed import embed_query" in code
    assert "min(int(k), 10)" in code
    assert 'SEARCH_SKIP = ["vec"]' in code


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["1bot", "--reads", "contacts"], "letter first"),
        (["bot1", "--reads", "contacts", "--model", "openai/gpt"], "anthropic/<model>"),
    ],
)
def test_init_rejects_bad_input(contacts, tmp_path, args, message):
    result, code = _init(tmp_path, *args)
    assert result.exit_code == 2
    assert message in result.output
    assert code is None
