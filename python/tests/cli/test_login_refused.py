"""A refused API key says so; only a project the backend does not know is "Could not find project"."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import click
import pytest
import requests
from hopsworks import ProjectException, _prompt_project
from hopsworks.cli import auth, session
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
    forbidden = _error(
        403, b'{"errorMsg": "Your role does not allow this"}'
    )
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
    assert auth.login_failure(e.value) == (
        "the API key was refused (API key not found in the database)"
    )
