"""Shared fixtures for CLI tests.

The central ``mock_project`` fixture patches ``hopsworks.cli.session`` so no
test ever touches ``hopsworks.login()`` or the real SDK. Individual tests
customise the returned ``Project`` mock to shape the command's output.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from hopsworks.cli import config, session
from hopsworks.cli.commands import factory


@pytest.fixture(autouse=True)
def factory_enabled():
    """Run `hops factory` commands as on a cluster with the Factory enabled; test_factory_enabled checks the check."""
    with mock.patch.object(factory, "_require_enabled"):
        yield


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Redirect HOME and env vars so config operations are hermetic."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / ".hops.toml")
    monkeypatch.setattr(config, "LEGACY_YAML_PATH", tmp_path / ".hops" / "config")
    for key in (
        "HOPSWORKS_HOST",
        "HOPSWORKS_API_KEY",
        "HOPSWORKS_PROJECT",
        "HOPSWORKS_PROJECT_ID",
        "HOPSWORKS_HOSTNAME_VERIFICATION",
        "REST_ENDPOINT",
        "PROJECT_NAME",
        "SECRETS_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


@pytest.fixture
def authed_config(tmp_home):
    """Persist a valid-looking config so ``session.require_auth`` passes."""
    config.save(
        config.HopsConfig(
            host="https://c.app.hopsworks.ai",
            api_key="AAA.BBB",
            project="demo",
        )
    )
    return config.load()


@pytest.fixture
def mock_project(authed_config):
    """Yield a fake ``Project`` wired into ``session.get_project``.

    Tests can reach into the returned mock to set return values on feature
    store, job api, model registry, and model serving before invoking the
    CLI with ``CliRunner``.
    """
    project = mock.MagicMock(name="Project")
    project.name = "demo"
    project.id = 119

    fs = mock.MagicMock(name="FeatureStore")
    fs.id = 67
    fs.name = "demo_featurestore"
    project.get_feature_store.return_value = fs

    # The CLI resolves an omitted version through the list of a name's versions, so
    # a test that shapes the single-version getter gets the same object, or error,
    # there. A listing without a name keeps the list's own return value.
    def versions_of(getter):
        def listed(name=None, *args, **kwargs):
            if name is None:
                return mock.DEFAULT
            return [item for item in [getter(name)] if item is not None]

        return listed

    fs.get_feature_groups.side_effect = versions_of(fs.get_feature_group)
    fs.get_feature_views.side_effect = versions_of(fs.get_feature_view)

    with (
        mock.patch.object(session, "get_project", return_value=project),
        mock.patch.object(session, "get_feature_store", return_value=fs),
        # A logged-in session has a client; these tests have none, and run as
        # if inside the cluster unless a test says otherwise.
        mock.patch("hopsworks_common.client._is_external", return_value=False),
    ):
        yield project


def environment_refused(code: int = 300029) -> Exception:
    """The 403 Hopsworks answers a change to an environment the caller did not create (code 300029)."""
    import requests
    from hopsworks_common.client.exceptions import RestAPIError

    response = requests.Response()
    response.status_code = 403
    response.reason = "Forbidden"
    response._content = json.dumps(
        {
            "errorCode": code,
            "errorMsg": "Only the creator of the environment or a Data owner can change it.",
            "usrMsg": "Only the creator of environment shop-app-env or a Data owner can change it.",
        }
    ).encode()
    return RestAPIError(
        "https://h/hopsworks-api/api/project/119/python/environments/shop-app-env/libraries/r.txt",
        response,
    )


def environment_missing(name: str) -> Exception:
    """The 404 Hopsworks answers for an environment that is gone."""
    import requests
    from hopsworks_common.client.exceptions import RestAPIError

    response = requests.Response()
    response.status_code = 404
    response.reason = "Not Found"
    response._content = json.dumps({"errorMsg": "Environment not found."}).encode()
    return RestAPIError(
        f"https://h/hopsworks-api/api/project/119/python/environments/{name}",
        response,
    )


class Environments:
    """The project's environments by name, as `get_environment_api()` serves them; a clone or create adds one."""

    def __init__(self, mock_project, *names):
        self.api = mock_project.get_environment_api.return_value
        self.envs = {}
        for name in names:
            self.add(name)
        self.api.get_environment.side_effect = self.envs.get
        self.api.create_environment.side_effect = lambda name, **kwargs: self.add(name)

    def add(self, name):
        env = mock.MagicMock(name=f"env {name}")
        env.name = name
        # A copy `<env>-app-env-<user>` was created by its user.
        env.creator = name.rsplit("-app-env-", 1)[1] if "-app-env-" in name else None
        env.commands = None
        # A project environment; a test of a built-in one sets it to True.
        env.preinstalled = False
        self.envs[name] = env
        return env
