"""CliRunner tests for ``hops secrets list``: names only, never a value."""

from __future__ import annotations

import json
from unittest import mock

from click.testing import CliRunner
from hopsworks.cli.main import cli


def _secret(name: str, value: str = "sk-live-do-not-print") -> mock.MagicMock:
    s = mock.MagicMock()
    s.name = name
    s.value = value
    s.visibility = "PRIVATE"
    s.scope = None
    s.created = "2026-11-01 10:00:00"
    return s


def test_secrets_list_shows_names_and_never_values(mock_project):
    with mock.patch("hopsworks.get_secrets_api") as api:
        api.return_value.get_secrets.return_value = [_secret("anthropic_api_key")]
        result = CliRunner().invoke(cli, ["secrets", "list"])
    assert result.exit_code == 0, result.output
    assert "anthropic_api_key" in result.output
    assert "PRIVATE" in result.output
    assert "sk-live-do-not-print" not in result.output


def test_secrets_list_json_has_no_value_field(mock_project):
    with mock.patch("hopsworks.get_secrets_api") as api:
        api.return_value.get_secrets.return_value = [_secret("anthropic_api_key")]
        result = CliRunner().invoke(cli, ["--json", "secrets", "list"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert rows == [
        {
            "name": "anthropic_api_key",
            "visibility": "PRIVATE",
            "scope": None,
            "added_on": "2026-11-01 10:00:00",
        }
    ]
    assert "sk-live-do-not-print" not in result.output


def test_secrets_list_failure_names_the_cause(mock_project):
    with mock.patch("hopsworks.get_secrets_api") as api:
        api.return_value.get_secrets.side_effect = RuntimeError("boom")
        result = CliRunner().invoke(cli, ["secrets", "list"])
    assert result.exit_code == 1
    assert "Could not list secrets: boom" in result.output
