"""A refused API key says so; only a project the backend does not know is "Could not find project"."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import click
import pytest
import requests
from click.testing import CliRunner
from hopsworks import ProjectException, _prompt_project
from hopsworks.cli import auth, config, session
from hopsworks.cli.main import cli
from hopsworks_common.client import base, exceptions, session_cache


def _error(status, body):
    response = requests.Response()
    response.status_code = status
    response.reason = "Unauthorized"
    response._content = body
    return exceptions.RestAPIError("https://h/hopsworks-api/api/project", response)


def _refused():
    return _error(
        401, b'{"errorCode": 320013, "errorMsg": "API key not found in the database"}'
    )


def _not_found():
    return _error(404, b'{"errorCode": 150004, "errorMsg": "Project not found"}')


def _connection(error):
    api = mock.Mock()
    api._get_project.side_effect = error
    return SimpleNamespace(_project_api=api)


def _get_project(monkeypatch, error):
    monkeypatch.setattr(
        session,
        "require_auth",
        lambda ctx: SimpleNamespace(
            host="h",
            api_key="k",
            project="blah",
            internal=False,
            hostname_verification=False,
            api_key_source=None,
        ),
    )
    monkeypatch.setattr(auth, "login", mock.Mock(side_effect=error))
    return session.get_project(click.Context(click.Command("x")))


def test_the_sdk_raises_a_refused_key_as_it_is():
    for status in (401, 403):
        error = _error(status, b'{"errorMsg": "nope"}')
        with pytest.raises(exceptions.RestAPIError):
            _prompt_project(_connection(error), "blah", False)


def test_the_sdk_still_reports_a_missing_project():
    with pytest.raises(ProjectException, match="Could not find project blah"):
        _prompt_project(_connection(_not_found()), "blah", False)


def test_the_cli_says_the_key_was_refused(monkeypatch):
    with pytest.raises(click.ClickException) as e:
        _get_project(monkeypatch, _refused())
    assert e.value.message == (
        "Could not log in: the API key was refused (API key not found in the database)"
    )


def test_the_cli_says_access_was_refused_on_a_403(monkeypatch):
    forbidden = _error(403, b'{"errorMsg": "Your role does not allow this"}')
    with pytest.raises(click.ClickException) as e:
        _get_project(monkeypatch, forbidden)
    assert e.value.message == (
        "Could not log in: access refused (Your role does not allow this)"
    )


def test_the_cli_still_says_a_project_is_missing(monkeypatch):
    with pytest.raises(click.ClickException) as e:
        _get_project(monkeypatch, ProjectException("Could not find project blah"))
    assert e.value.message == "Could not log in: Could not find project blah"


def test_a_refused_key_with_a_cached_session_says_the_same(tmp_path, monkeypatch):
    monkeypatch.setenv(session_cache.ENV_VAR, "1")
    monkeypatch.setenv(session_cache.DIR_ENV_VAR, str(tmp_path))
    session_cache.bind("h", 443, "k", "blah")
    session_cache.put("username", "meb")
    session_cache.bind("h", 443, "k", "blah")
    session_cache.get("username")

    refused = requests.Response()
    refused.status_code = 401
    refused.reason = "Unauthorized"
    refused._content = b'{"errorMsg": "API key not found in the database"}'
    client = base.Client.__new__(base.Client)
    client._connected = True
    client._base_url = "https://h:443"
    client._auth = None
    client._verify = False
    client._session = mock.Mock()
    client._session.prepare_request.side_effect = lambda request: request
    client._session.send.return_value = refused
    client._refresh_session = mock.Mock(
        side_effect=lambda p: (session_cache.invalidate(), p)[1]
    )

    try:
        with pytest.raises(exceptions.RestAPIError) as e:
            client._send_request("GET", ["project", "getProjectInfo", "blah"])
    finally:
        session_cache.bind("h", 443, "k", None)

    client._refresh_session.assert_called_once()
    with pytest.raises(click.ClickException) as refusal:
        auth.login_failed(e.value)
    assert refusal.value.message == (
        "Could not log in: the API key was refused (API key not found in the database)"
    )


def _login_refused_through_the_cli(monkeypatch, args=(), stdin=None, error=None):
    """Run a throwaway command whose login raises ``error``, with ``args`` as root options."""

    @click.command("boom")
    @click.pass_context
    def boom(ctx):
        session.get_project(ctx)

    monkeypatch.setattr(auth, "login", mock.Mock(side_effect=error or _refused()))
    cli.commands["boom"] = boom
    try:
        return CliRunner().invoke(cli, [*args, "boom"], input=stdin)
    finally:
        del cli.commands["boom"]


REFUSED = "Error: Could not log in: the API key from {} was refused (API key not found in the database). {}.\n"


def test_a_refused_key_from_the_config_file_names_file_and_profile(
    tmp_home, monkeypatch
):
    config.save(config.HopsConfig(host="https://h", api_key="A.B"))

    result = _login_refused_through_the_cli(monkeypatch)

    assert result.output == REFUSED.format(
        f"{config.CONFIG_PATH} (profile default)",
        "Check it, or run `hops setup --force` to create a new one",
    )


def test_a_refused_key_from_the_env_var_names_it_once(tmp_home, monkeypatch):
    config.save(config.HopsConfig(host="https://h", api_key="A.B"))
    monkeypatch.setenv("HOPSWORKS_API_KEY", "C.D")

    result = _login_refused_through_the_cli(monkeypatch)

    assert result.output == REFUSED.format(
        "HOPSWORKS_API_KEY",
        f"Check it, or unset it to use the key in {config.CONFIG_PATH}",
    )


def test_a_refused_key_from_the_flag_names_it(tmp_home, monkeypatch):
    monkeypatch.setenv("HOPSWORKS_HOST", "https://h")
    monkeypatch.setenv("HOPSWORKS_API_KEY", "C.D")

    result = _login_refused_through_the_cli(monkeypatch, args=("--api-key", "E.F"))

    assert result.output == REFUSED.format("--api-key", "Check it")


def test_a_refused_key_from_stdin_names_the_stdin_flag(tmp_home, monkeypatch):
    monkeypatch.setenv("HOPSWORKS_HOST", "https://h")

    result = _login_refused_through_the_cli(
        monkeypatch, args=("--api-key-stdin",), stdin="E.F\n"
    )

    assert result.output == REFUSED.format("--api-key-stdin", "Check it")


def test_hops_login_names_the_key_source_once(tmp_home, monkeypatch):
    monkeypatch.setenv("HOPSWORKS_API_KEY", "C.D")
    monkeypatch.setattr(auth, "verify", mock.Mock(side_effect=_refused()))

    result = CliRunner().invoke(cli, ["login", "--host", "https://h"])

    assert result.output == REFUSED.format(
        "HOPSWORKS_API_KEY",
        f"Check it, or unset it to use the key in {config.CONFIG_PATH}",
    )


def test_hops_login_uses_the_root_api_key_flags(tmp_home, monkeypatch):
    config.save(config.HopsConfig(host="https://h", api_key="FILE.KEY"))
    verify = mock.Mock(side_effect=_refused())
    monkeypatch.setattr(auth, "verify", verify)

    flag = CliRunner().invoke(cli, ["--api-key", "FLAG.KEY", "login"])
    stdin = CliRunner().invoke(cli, ["--api-key-stdin", "login"], input="STDIN.KEY\n")

    keys = [c.kwargs["api_key_value"] for c in verify.call_args_list]
    assert keys == ["FLAG.KEY", "STDIN.KEY"]
    assert flag.output == REFUSED.format("--api-key", "Check it")
    assert stdin.output == REFUSED.format("--api-key-stdin", "Check it")


def test_the_login_flag_overrides_the_root_one(tmp_home, monkeypatch):
    config.save(config.HopsConfig(host="https://h", api_key="FILE.KEY"))
    verify = mock.Mock(side_effect=_refused())
    monkeypatch.setattr(auth, "verify", verify)

    CliRunner().invoke(cli, ["--api-key", "ROOT.KEY", "login", "--api-key", "OWN.KEY"])

    assert verify.call_args.kwargs["api_key_value"] == "OWN.KEY"


def test_a_login_refused_for_scope_keeps_the_scope_hint(tmp_home, monkeypatch):
    monkeypatch.setenv("HOPSWORKS_HOST", "https://h")
    monkeypatch.setenv("HOPSWORKS_API_KEY", "C.D")
    no_scope = _error(
        403,
        b'{"errorCode": 320004, "errorMsg": "No valid scope found", '
        b'"usrMsg": "Valid scope for this invocation is: [PROJECT]"}',
    )

    result = _login_refused_through_the_cli(monkeypatch, error=no_scope)

    assert "Could not log in: access refused" in result.output
    assert "the PROJECT scope, and yours does not have it" in result.output
