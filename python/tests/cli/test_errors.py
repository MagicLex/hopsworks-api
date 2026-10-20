"""The root group reduces a REST failure to one line the user can act on.

A command that lets the SDK's ``RestAPIError`` escape used to end in a
traceback whose only useful part was the scope name buried in the body; a
command that wrapped it in a ``ClickException`` printed the same raw text.
These tests pin the rewrite for both paths and check that unrelated
exceptions still propagate.
"""

from __future__ import annotations

import json
from unittest import mock

import click
import requests
from click.testing import CliRunner
from hopsworks.cli.main import _explain_rest_error, cli
from hopsworks_common.client.exceptions import RestAPIError


NO_SCOPE = {
    "errorCode": 320004,
    "usrMsg": (
        "No valid scope found for this invocation. "
        "Valid scope for this invocation is: [PYTHON_LIBRARIES]"
    ),
    "errorMsg": "No valid scope found for this invocation",
}


def _rest_error(
    status: int, body: dict | str, reason: str = "Forbidden"
) -> RestAPIError:
    response = requests.Response()
    response.status_code = status
    response.reason = reason
    response._content = (json.dumps(body) if isinstance(body, dict) else body).encode()
    return RestAPIError(
        "https://c.app.hopsworks.ai/hopsworks-api/api/project/119/python/environments",
        response,
    )


def _run_command_raising(tmp_home, exc: BaseException, wrap: str | None = None):
    """Invoke a throwaway ``hops boom`` whose body raises ``exc``.

    With ``wrap`` set the command catches the error and re-raises it inside a
    ``ClickException`` with that prefix, the way most commands do.
    """

    @click.command("boom")
    def boom() -> None:
        if wrap is None:
            raise exc
        try:
            raise exc
        except Exception as caught:
            raise click.ClickException(f"{wrap}{caught}") from caught

    cli.commands["boom"] = boom
    try:
        return CliRunner().invoke(cli, ["boom"])
    finally:
        del cli.commands["boom"]


def test_missing_scope_on_a_real_command_names_scope_and_fix(mock_project):
    api = mock_project.get_environment_api.return_value
    api.get_environments.side_effect = _rest_error(403, NO_SCOPE)

    result = CliRunner().invoke(cli, ["env", "list"])

    assert result.exit_code == 1
    assert "the PYTHON_LIBRARIES scope, and yours does not have it" in result.output
    assert "hops setup --force" in result.output
    assert "Metadata operation error" not in result.output
    assert "Traceback" not in result.output


def test_wrapped_rest_error_keeps_the_prefix_and_drops_the_raw_text(tmp_home):
    result = _run_command_raising(
        tmp_home, _rest_error(403, NO_SCOPE), wrap="Could not list models: "
    )

    assert result.exit_code == 1
    assert result.output.startswith(
        "Error: Could not list models: This command needs an API key with the "
        "PYTHON_LIBRARIES scope"
    )
    assert "Metadata operation error" not in result.output


def test_several_accepted_scopes_are_listed():
    body = dict(
        NO_SCOPE, usrMsg="Valid scope for this invocation is: [FEATURESTORE, KAFKA]"
    )

    text = _explain_rest_error(_rest_error(403, body))

    assert "one of the scopes FEATURESTORE, KAFKA, and yours has none of them" in text


def test_other_rest_errors_keep_status_code_and_server_message(tmp_home):
    body = {"errorCode": 270227, "usrMsg": "Feature group commit not found."}

    result = _run_command_raising(tmp_home, _rest_error(404, body, reason="Not Found"))

    assert result.exit_code == 1
    assert (
        "Error: Hopsworks refused the request (HTTP 404, error 270227): "
        "Feature group commit not found." in result.output
    )
    assert "Traceback" not in result.output


def test_unauthorized_points_at_the_cached_key(tmp_home):
    body = {"errorCode": 200003, "usrMsg": "Invalid API key."}

    result = _run_command_raising(
        tmp_home, _rest_error(401, body, reason="Unauthorized")
    )

    assert "HTTP 401, error 200003" in result.output
    assert "hops setup --force" in result.output


def test_missing_tag_schema_says_nothing_was_tagged():
    body = {"errorCode": 370000, "usrMsg": "schema: analytics_table is not defined"}

    text = _explain_rest_error(_rest_error(404, body, reason="Not Found"))

    assert "(HTTP 404, error 370000): schema: analytics_table is not defined. " in text
    assert "only a Hopsworks admin can define one; nothing was tagged" in text


def test_non_json_error_body_falls_back_to_the_http_reason():
    text = _explain_rest_error(
        _rest_error(502, "<html>bad gateway</html>", reason="Bad Gateway")
    )

    assert text == "Hopsworks refused the request (HTTP 502): Bad Gateway"


def test_unrelated_exceptions_are_not_rewritten(tmp_home):
    assert _explain_rest_error(ValueError("boom")) is None
    assert _explain_rest_error(None) is None

    result = _run_command_raising(tmp_home, ValueError("boom"))

    assert isinstance(result.exception, ValueError)


ROLE_FORBIDDEN = {
    "errorCode": 150068,
    "errorMsg": "Your project role does not allow to perform this action.",
}


def _owned_by_jane(mock_project):
    mock_project.owner = "jane@example.com"
    mock_project.project_team = [
        {
            "user": {"email": "dan@example.com", "fname": "Dan", "lname": "Lee"},
            "team_role": "Data scientist",
        },
        {
            "user": {"email": "jane@example.com", "fname": "Jane", "lname": "Doe"},
            "team_role": "Data owner",
        },
    ]


def test_role_refusal_names_the_project_owner_and_is_not_retried(mock_project):
    _owned_by_jane(mock_project)
    deployment = mock.MagicMock()
    deployment.stop.side_effect = _rest_error(403, ROLE_FORBIDDEN)
    mock_project.get_model_serving.return_value.get_deployment.return_value = deployment

    result = CliRunner().invoke(cli, ["deployment", "stop", "fraud"])

    assert result.exit_code == 1
    assert result.output.endswith(
        "Only the project's Data owner can do this: ask Jane Doe\n"
    )
    assert len(result.output.splitlines()) == 1
    deployment.stop.assert_called_once()
    deployment.get_state.assert_not_called()


def test_role_refusal_inside_a_wait_stops_it_at_once(mock_project, clock):
    _owned_by_jane(mock_project)
    deployment = mock.MagicMock()
    deployment.get_state.side_effect = _rest_error(403, ROLE_FORBIDDEN)
    mock_project.get_model_serving.return_value.get_deployment.return_value = deployment

    result = CliRunner().invoke(cli, ["deployment", "stop", "fraud"])

    assert result.exit_code == 1
    assert "Only the project's Data owner can do this: ask Jane Doe" in result.output
    assert deployment.get_state.call_count == 1
    assert clock.now == 0


def test_role_refusal_names_the_owner_email_when_no_name_is_known(mock_project):
    mock_project.owner = "jane@example.com"
    mock_project.project_team = None

    result = _run_command_raising(None, _rest_error(403, ROLE_FORBIDDEN))

    assert result.output == (
        "Error: Only the project's Data owner can do this: ask jane@example.com\n"
    )


def test_role_refusal_without_a_readable_project_asks_the_data_owner(tmp_home):
    result = _run_command_raising(tmp_home, _rest_error(403, ROLE_FORBIDDEN))

    assert result.exit_code == 1
    assert result.output == (
        "Error: Only the project's Data owner can do this: "
        "ask the project's Data owner\n"
    )
    assert _explain_rest_error(_rest_error(403, ROLE_FORBIDDEN)) == (
        "Only the project's Data owner can do this: ask the project's Data owner"
    )
