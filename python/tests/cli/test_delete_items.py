"""Delete commands: consent, help and refusal messages."""

from __future__ import annotations

from unittest import mock

import pytest
import requests
from click.testing import CliRunner
from hopsworks.cli.main import cli


DELETES = [
    ["files", "delete", "Resources/x.csv"],
    ["superset", "dataset", "delete", "1"],
    ["superset", "chart", "delete", "1"],
    ["superset", "dashboard", "delete", "1"],
    ["deployment", "delete", "dep"],
    ["fg", "delete", "sales", "--version", "1"],
    ["fv", "delete", "sales_fv", "--version", "1"],
    ["td", "delete", "sales_fv", "--fv-version", "1"],
    ["model", "delete", "m", "--version", "1"],
    ["job", "delete", "etl"],
    ["job", "schedule", "delete", "etl"],
    ["app", "delete", "dash"],
    ["agent", "delete", "bot"],
    ["alert", "delete", "1"],
    ["datasource", "delete", "s3"],
    ["datasource", "credentials", "delete", "s3"],
    ["git", "provider", "delete"],
    ["factory", "delete", "f"],
    ["factory", "system", "remove", "7"],
    ["factory", "system", "delete", "7"],
    ["factory", "system", "delete-assets", "7", "--job", "sys-etl"],
]


@pytest.fixture
def lookups(tmp_path, monkeypatch):
    from hopsworks.cli import teardown
    from hopsworks_common.core import ml_system_api

    home = tmp_path / "Users" / "u"
    (home / "sys").mkdir(parents=True)
    (home / "sys" / "system.yaml").write_text("system: {name: Sys}\n")
    monkeypatch.setenv("HOPSFS_USER_HOME_DIR", str(home))
    system = {"id": 7, "name": "Sys", "pathToCode": "/Projects/demo/Users/u/sys"}
    monkeypatch.setattr(ml_system_api, "_list", lambda factory=None: [system])
    monkeypatch.setattr(ml_system_api, "_get", lambda system_id: system)
    monkeypatch.setattr(teardown, "require_data_owner", lambda project: None)
    with (
        mock.patch("hopsworks.cli.commands.git.find_provider", return_value=object()),
        mock.patch("hopsworks.cli.commands.git.git_provider_api") as providers,
        mock.patch("hopsworks.cli.commands.agent._is_agent", return_value=True),
    ):
        yield providers


@pytest.mark.parametrize("argv", DELETES, ids=lambda a: " ".join(a[:-1]))
@pytest.mark.parametrize("how", ["json", "no-input", "json-no-input"])
def test_every_delete_needs_yes_when_no_one_can_answer(
    mock_project, lookups, argv, how, tty
):
    # A terminal is attached: --json asks as any call does, HOPS_NO_INPUT fails naming --yes.
    args = ["--json", *argv] if how.startswith("json") else argv
    env = {"HOPS_NO_INPUT": "1"} if how.endswith("no-input") else {}
    result = CliRunner().invoke(cli, args, env=env, input="n\n")
    assert result.exit_code == 1, result.output
    assert "Deleted" not in result.output and "Removed" not in result.output
    if how == "json":
        assert "[y/N]" in result.stderr
        assert "Aborted!" in result.output
    else:
        assert "needs confirmation: pass --yes." in result.output
        assert "[y/N]" not in result.output


def test_fv_delete_force_help_names_what_it_overrides():
    result = CliRunner().invoke(cli, ["fv", "delete", "--help"])
    assert "models or deployments" in result.output
    assert "training data exists" not in result.output


def test_app_delete_help_does_not_offer_a_no_op_force():
    result = CliRunner().invoke(cli, ["app", "delete", "--help"])
    assert "--force" not in result.output
    assert "A running app is stopped first." in result.output


def test_job_delete_help_says_the_executions_go_too():
    result = CliRunner().invoke(cli, ["job", "delete", "--help"])
    assert "logs are kept" not in result.output
    assert "executions and their logs" in result.output


@pytest.mark.parametrize("kind", ["dataset", "chart", "dashboard"])
def test_superset_info_on_a_missing_id_is_one_line(mock_project, kind):
    response = mock.Mock(status_code=404)
    missing = requests.HTTPError(
        f'404 NOT FOUND for GET /api/v1/{kind}/9: {{"message":"Not found"}}',
        response=response,
    )
    api = mock_project.get_superset_api.return_value
    getattr(api, f"get_{kind}").side_effect = missing
    result = CliRunner().invoke(cli, ["superset", kind, "info", "9"])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert result.output.strip() == f"Error: Superset {kind} 9 not found."


def test_superset_info_lets_a_bug_surface(mock_project):
    # Only an HTTP failure is reported as one; anything else is a bug and keeps its traceback.
    api = mock_project.get_superset_api.return_value
    api.get_chart.side_effect = KeyError("result")
    result = CliRunner().invoke(cli, ["superset", "chart", "info", "9"])
    assert isinstance(result.exception, KeyError)


def test_a_view_whose_feature_group_is_gone_shows_and_deletes(
    mock_project, backend_fixtures
):
    from hsfs.feature_view import FeatureView

    view = dict(backend_fixtures["feature_view"]["get"]["response"])
    del view["query"]
    with (
        mock.patch("hsfs.engine._get_type"),
        mock.patch(
            "hsfs.core.feature_view_api.FeatureViewApi._delete_by_name_version"
        ) as delete,
    ):
        orphan = FeatureView.from_response_json(view)
        fs = mock_project.get_feature_store.return_value
        fs.get_feature_view.return_value = orphan

        shown = CliRunner().invoke(cli, ["fv", "info", "test_name", "--version", "1"])
        assert shown.exit_code == 0, shown.output
        assert "test_name" in shown.output

        argv = ["fv", "delete", "test_name", "--version", "1", "--yes", "--force"]
        deleted = CliRunner().invoke(cli, argv)
        assert deleted.exit_code == 0, deleted.output
        delete.assert_called_once_with("test_name", 1, True)
