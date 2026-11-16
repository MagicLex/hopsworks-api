"""CLI tests for ``hops app`` — Python app lifecycle commands."""

from __future__ import annotations

import contextlib
import json
import os
from unittest import mock

import pytest
from click.testing import CliRunner
from hopsworks.cli.commands import app as app_commands
from hopsworks.cli.main import cli

from tests.cli.conftest import (
    PENDING_REASON,
    Clock,
    Environments,
    backend,
    environment_refused,
)


@pytest.fixture(autouse=True)
def _fast_waits(clock):
    """Every wait here runs on the fake clock."""


@pytest.fixture(autouse=True)
def _own_lock_dir(tmp_path, monkeypatch):
    """Each test's app environment locks in its own directory."""
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "locks"))
    (tmp_path / "locks").mkdir()


def _fake_app(**overrides):
    a = mock.MagicMock(name="App")
    a.job_id = overrides.get("job_id", 42)
    a.name = overrides.get("name", "my_app")
    a.app_kind = overrides.get("app_kind", "STREAMLIT")
    a.state = overrides.get("state", "CREATED")
    a.serving = overrides.get("serving", False)
    a.environment = overrides.get("environment", "python-app-pipeline")
    a.environment_name = overrides.get("environment_name", "python-app-pipeline")
    a.memory = overrides.get("memory", 2048)
    a.cores = overrides.get("cores", 1.0)
    a.app_path = overrides.get("app_path", "Resources/app.py")
    a.app_port = overrides.get("app_port")
    a.entrypoint_command = overrides.get("entrypoint_command")
    a.git_url = overrides.get("git_url")
    a.git_provider = overrides.get("git_provider")
    a.git_branch = overrides.get("git_branch")
    a.latest_commit = overrides.get("latest_commit")
    a.entrypoint_script = overrides.get("entrypoint_script")
    a.monitoring_config = overrides.get("monitoring_config")
    a.monitoringConfig = overrides.get("monitoringConfig")
    a.description = overrides.get("description")
    a.app_base_path = overrides.get("app_base_path")
    a.proxy_path_mode = overrides.get("proxy_path_mode")
    a.readiness_probe_path = overrides.get("readiness_probe_path")
    a.db_access = overrides.get("db_access", True)
    a.idle_scale_to_zero = overrides.get("idle_scale_to_zero", False)
    a.idle_cooldown_seconds = overrides.get("idle_cooldown_seconds")
    a.cold_start_timeout_seconds = overrides.get("cold_start_timeout_seconds")
    a.app_url = overrides.get("app_url")
    return a


def test_app_list_renders_rows(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.get_apps.return_value = [
        _fake_app(name="a1", state="RUNNING", serving=True),
        _fake_app(job_id=43, name="a2", state="KILLED"),
    ]
    result = CliRunner().invoke(cli, ["app", "list"])
    assert result.exit_code == 0, result.output
    assert "a1" in result.output
    assert "a2" in result.output
    assert "RUNNING" in result.output


def test_app_list_shows_the_job_id_the_backend_sends(mock_project):
    from hopsworks_common.app import App

    apps = mock_project.get_app_api.return_value
    apps.get_apps.return_value = App.from_response_json_list(
        {"items": [{"jobId": 7321, "name": "dash", "state": "RUNNING"}]}
    )
    result = CliRunner().invoke(cli, ["app", "list"])
    assert result.exit_code == 0, result.output
    assert "7321" in result.output
    assert "?" not in result.output


def test_app_logs_prints_streams(mock_project):
    apps = mock_project.get_app_api.return_value
    a = _fake_app(name="dash")
    a.get_logs.return_value = {"stdout": "hello out", "stderr": "boom err"}
    apps.get_app.return_value = a
    result = CliRunner().invoke(cli, ["app", "logs", "dash"])
    assert result.exit_code == 0, result.output
    assert "hello out" in result.output
    assert "boom err" in result.output


def test_app_logs_stderr_only(mock_project):
    apps = mock_project.get_app_api.return_value
    a = _fake_app(name="dash")
    a.get_logs.return_value = {"stdout": "hello out", "stderr": "boom err"}
    apps.get_app.return_value = a
    result = CliRunner().invoke(cli, ["app", "logs", "dash", "--stream", "stderr"])
    assert result.exit_code == 0, result.output
    assert "boom err" in result.output
    assert "hello out" not in result.output


def test_app_logs_running_points_to_ui(mock_project):
    # A running app has no execution log file yet; the CLI must point at the UI
    # live logs instead of hitting the endpoint that 400s (#11).
    apps = mock_project.get_app_api.return_value
    a = _fake_app(name="dash", state="RUNNING", serving=True)
    a.get_url.return_value = "https://hopsworks.ai.local/p/119/apps"
    apps.get_app.return_value = a
    result = CliRunner().invoke(cli, ["app", "logs", "dash"])
    assert result.exit_code == 0, result.output
    assert "still running" in result.output
    assert "https://hopsworks.ai.local/p/119/apps" in result.output
    a.get_logs.assert_not_called()


def test_app_logs_handles_still_running_error(mock_project):
    # Race: state looked final but the backend still rejected with 130010.
    # Fall back to the same live-logs guidance, not a raw error.
    apps = mock_project.get_app_api.return_value
    a = _fake_app(name="dash", state="CREATED", serving=False)
    a.get_url.return_value = "https://hopsworks.ai.local/p/119/apps"
    a.get_logs.side_effect = RuntimeError(
        "errorCode 130010 Job still running. Execution state is invalid."
    )
    apps.get_app.return_value = a
    result = CliRunner().invoke(cli, ["app", "logs", "dash"])
    assert result.exit_code == 0, result.output
    assert "still running" in result.output


def test_app_create_drops_unsupported_kwargs(mock_project):
    """Create stays resilient when the deployed SDK predates app_kind etc."""
    apps = mock_project.get_app_api.return_value
    created = _fake_app(name="legacy")

    def old_create_app(
        name,
        app_path=None,
        environment="python-app-pipeline",
        env_vars=None,
    ):
        return created

    apps.create_app = old_create_app
    result = CliRunner().invoke(
        cli,
        [
            "app",
            "create",
            "legacy",
            "--app-kind",
            "STREAMLIT",
            "--path",
            "Resources/app.py",
            "--app-base-path",
            "/myapp",
            "--readiness-probe-path",
            "/health",
        ],
    )
    # would TypeError on app_kind without the signature filter
    assert result.exit_code == 0, result.output


def test_app_info_shows_url(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.get_app.return_value = _fake_app(
        name="dash", state="RUNNING", serving=True, app_url="https://host/dash"
    )
    result = CliRunner().invoke(cli, ["app", "info", "dash"])
    assert result.exit_code == 0, result.output
    assert "https://host/dash" in result.output
    assert "RUNNING" in result.output


def test_app_info_shows_custom_metadata(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.get_app.return_value = _fake_app(
        name="dash",
        state="RUNNING",
        serving=True,
        app_kind="CUSTOM",
        app_port=8080,
        entrypoint_command='python -m uvicorn dash:app --port "$APP_PORT"',
        description="FastAPI demo",
        app_base_path="/myapp",
        readiness_probe_path="/health",
    )
    result = CliRunner().invoke(cli, ["app", "info", "dash"])
    assert result.exit_code == 0, result.output
    assert "CUSTOM" in result.output
    assert "8080" in result.output
    assert "FastAPI demo" in result.output
    assert "/myapp" in result.output
    assert "/health" in result.output
    assert 'python -m uvicorn dash:app --port "$APP_PORT"' in result.output


def test_app_info_shows_the_routing_mode(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.get_app.return_value = _fake_app(name="dash", proxy_path_mode="PREFIX")
    result = CliRunner().invoke(cli, ["app", "info", "dash"])
    assert result.exit_code == 0, result.output
    assert "compatibility prefix" in result.output
    apps.get_app.return_value = _fake_app(name="dash", proxy_path_mode="ROOT")
    result = CliRunner().invoke(cli, ["app", "info", "dash"])
    assert "root (the app serves at its base path)" in result.output


def test_app_info_shows_monitoring_routes(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.get_app.return_value = _fake_app(
        name="dash",
        state="RUNNING",
        serving=True,
        monitoringConfig={
            "enabled": True,
            "routes": [
                {"path": "/api", "matchType": "prefix"},
                {"path": "/predict", "matchType": "exact"},
            ],
        },
    )
    result = CliRunner().invoke(cli, ["app", "info", "dash"])
    assert result.exit_code == 0, result.output
    assert "Monitoring routes" in result.output
    assert "/api (prefix)" in result.output
    assert "/predict (exact)" in result.output


def test_app_info_shows_git_metadata(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.get_app.return_value = _fake_app(
        name="dash",
        state="RUNNING",
        serving=True,
        app_kind="STREAMLIT",
        git_url="https://github.com/gibchikafa/appshopsworkstests.git",
        git_provider="GitHub",
        git_branch="main",
        latest_commit="0123456789abcdef0123456789abcdef01234567",
        entrypoint_script="streamlitapp.py",
    )
    result = CliRunner().invoke(cli, ["app", "info", "dash"])
    assert result.exit_code == 0, result.output
    assert "Git repository" in result.output
    assert "https://github.com/gibchikafa/appshopsworkstests.git" in result.output
    assert "GitHub" in result.output
    assert "main" in result.output
    assert "Latest commit" in result.output
    assert "0123456789abcdef0123456789abcdef01234567" in result.output
    assert "streamlitapp.py" in result.output


def test_app_info_json_includes_custom_metadata(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.get_app.return_value = _fake_app(
        name="dash",
        state="RUNNING",
        serving=True,
        app_kind="CUSTOM",
        app_port=8080,
        entrypoint_command='python -m uvicorn dash:app --port "$APP_PORT"',
        description="FastAPI demo",
        app_base_path="/myapp",
        readiness_probe_path="/health",
        proxy_path_mode="ROOT",
    )
    result = CliRunner().invoke(cli, ["--json", "app", "info", "dash"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["app_kind"] == "CUSTOM"
    assert payload["app_port"] == 8080
    assert (
        payload["entrypoint_command"] == 'python -m uvicorn dash:app --port "$APP_PORT"'
    )
    assert payload["description"] == "FastAPI demo"
    assert payload["app_base_path"] == "/myapp"
    assert payload["readiness_probe_path"] == "/health"
    assert payload["proxy_path_mode"] == "ROOT"
    assert payload["source"] == "Project file"


def test_app_info_json_includes_git_metadata(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.get_app.return_value = _fake_app(
        name="dash",
        state="RUNNING",
        serving=True,
        app_kind="STREAMLIT",
        git_url="https://github.com/gibchikafa/appshopsworkstests.git",
        git_provider="GitHub",
        git_branch="main",
        latest_commit="0123456789abcdef0123456789abcdef01234567",
        entrypoint_script="streamlitapp.py",
    )
    result = CliRunner().invoke(cli, ["--json", "app", "info", "dash"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["source"] == "Git repository"
    assert payload["git_url"] == "https://github.com/gibchikafa/appshopsworkstests.git"
    assert payload["git_provider"] == "GitHub"
    assert payload["git_branch"] == "main"
    assert payload["latest_commit"] == "0123456789abcdef0123456789abcdef01234567"
    assert payload["entrypoint_script"] == "streamlitapp.py"


def test_app_info_json_includes_monitoring_config(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.get_app.return_value = _fake_app(
        name="dash",
        state="RUNNING",
        serving=True,
        monitoring_config={
            "enabled": True,
            "routes": [
                {"path": "/api", "matchType": "prefix"},
                {"path": "/predict", "matchType": "exact"},
            ],
        },
    )
    result = CliRunner().invoke(cli, ["--json", "app", "info", "dash"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["monitoring"] == "enabled"
    assert payload["monitoring_config"]["enabled"] is True
    assert payload["monitoring_config"]["routes"] == [
        {"path": "/api", "matchType": "prefix"},
        {"path": "/predict", "matchType": "exact"},
    ]


def test_app_url_exits_non_zero_when_not_serving(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.get_app.return_value = _fake_app(state="INITIALIZING", serving=False)
    result = CliRunner().invoke(cli, ["app", "url", "dash"])
    assert result.exit_code != 0
    assert "no URL yet" in result.output


def test_app_url_prints_plain_url(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.get_app.return_value = _fake_app(
        state="RUNNING", serving=True, app_url="https://host/dash"
    )
    result = CliRunner().invoke(cli, ["app", "url", "dash"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "https://host/dash"


def test_app_create_forwards_args(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.create_app.return_value = _fake_app(name="dash")
    result = CliRunner().invoke(
        cli,
        [
            "app",
            "create",
            "dash",
            "--app-kind",
            "STREAMLIT",
            "--path",
            "Resources/dash.py",
            "--memory",
            "4096",
            "--cores",
            "2",
            "--env",
            "custom-env",
            "--app-base-path",
            "/myapp",
            "--readiness-probe-path",
            "/health",
        ],
    )
    assert result.exit_code == 0, result.output
    apps.create_app.assert_called_once_with(
        name="dash",
        app_path="Resources/dash.py",
        app_kind="STREAMLIT",
        environment="custom-env",
        memory=4096,
        cores=2.0,
        app_base_path="/myapp",
        readiness_probe_path="/health",
    )


def test_app_create_leaves_out_unset_memory_and_cores(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.create_app.return_value = _fake_app(name="dash")
    result = CliRunner().invoke(
        cli,
        [
            "app",
            "create",
            "dash",
            "--app-kind",
            "STREAMLIT",
            "--path",
            "Resources/dash.py",
        ],
    )
    assert result.exit_code == 0, result.output
    kwargs = apps.create_app.call_args.kwargs
    assert "memory" not in kwargs
    assert "cores" not in kwargs


def test_app_create_no_db_access(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.create_app.return_value = _fake_app(name="dash")
    result = CliRunner().invoke(
        cli,
        [
            "app",
            "create",
            "dash",
            "--app-kind",
            "STREAMLIT",
            "--path",
            "Resources/dash.py",
            "--no-db-access",
        ],
    )
    assert result.exit_code == 0, result.output
    assert apps.create_app.call_args.kwargs["db_access"] is False


def test_app_create_idle_scale_to_zero_forwards_the_timeouts(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.create_app.return_value = _fake_app(name="dash")
    result = CliRunner().invoke(
        cli,
        [
            "app",
            "create",
            "dash",
            "--app-kind",
            "STREAMLIT",
            "--path",
            "Resources/app.py",
            "--idle-scale-to-zero",
            "--idle-cooldown-seconds",
            "120",
            "--cold-start-timeout-seconds",
            "45",
        ],
    )
    assert result.exit_code == 0, result.output
    kwargs = apps.create_app.call_args.kwargs
    assert kwargs["idle_scale_to_zero"] is True
    assert kwargs["idle_cooldown_seconds"] == 120
    assert kwargs["cold_start_timeout_seconds"] == 45


def test_app_create_idle_timeouts_reject_the_option_off(mock_project):
    apps = mock_project.get_app_api.return_value
    result = CliRunner().invoke(
        cli,
        [
            "app",
            "create",
            "dash",
            "--app-kind",
            "STREAMLIT",
            "--path",
            "Resources/app.py",
            "--no-idle-scale-to-zero",
            "--idle-cooldown-seconds",
            "120",
        ],
    )
    assert result.exit_code != 0
    assert "--no-idle-scale-to-zero" in result.output
    apps.create_app.assert_not_called()


def test_app_create_idle_timeouts_alone_are_forwarded(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.create_app.return_value = _fake_app(name="dash")
    result = CliRunner().invoke(
        cli,
        [
            "app",
            "create",
            "dash",
            "--app-kind",
            "STREAMLIT",
            "--path",
            "Resources/app.py",
            "--idle-cooldown-seconds",
            "120",
        ],
    )
    assert result.exit_code == 0, result.output
    kwargs = apps.create_app.call_args.kwargs
    assert "idle_scale_to_zero" not in kwargs
    assert kwargs["idle_cooldown_seconds"] == 120


def test_app_create_leaves_idle_scale_to_zero_to_the_cluster_default(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.create_app.return_value = _fake_app(name="dash")
    result = CliRunner().invoke(
        cli,
        [
            "app",
            "create",
            "dash",
            "--app-kind",
            "STREAMLIT",
            "--path",
            "Resources/app.py",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "idle_scale_to_zero" not in apps.create_app.call_args.kwargs


def test_app_create_no_idle_scale_to_zero_opts_out(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.create_app.return_value = _fake_app(name="dash")
    result = CliRunner().invoke(
        cli,
        [
            "app",
            "create",
            "dash",
            "--app-kind",
            "STREAMLIT",
            "--path",
            "Resources/app.py",
            "--no-idle-scale-to-zero",
        ],
    )
    assert result.exit_code == 0, result.output
    assert apps.create_app.call_args.kwargs["idle_scale_to_zero"] is False


def test_app_info_shows_idle_scale_to_zero(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.get_app.return_value = _fake_app(
        name="dash",
        idle_scale_to_zero=True,
        idle_cooldown_seconds=120,
        cold_start_timeout_seconds=45,
    )
    result = CliRunner().invoke(cli, ["app", "info", "dash"])
    assert result.exit_code == 0, result.output
    assert "Scale to zero when idle" in result.output
    assert "after 120 s idle" in result.output
    assert "wakes within 45 s" in result.output


def test_app_create_defaults_to_db_access(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.create_app.return_value = _fake_app(name="dash")
    result = CliRunner().invoke(
        cli,
        [
            "app",
            "create",
            "dash",
            "--app-kind",
            "STREAMLIT",
            "--path",
            "Resources/dash.py",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "db_access" not in apps.create_app.call_args.kwargs


def test_app_create_custom_forwards_args(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.create_app.return_value = _fake_app(name="dash")
    result = CliRunner().invoke(
        cli,
        [
            "app",
            "create",
            "dash",
            "--app-kind",
            "CUSTOM",
            "--entrypoint-command",
            'python -m uvicorn dash:app --host 0.0.0.0 --port "$APP_PORT"',
            "--app-port",
            "8080",
            "--description",
            "FastAPI demo",
            "--memory",
            "4096",
            "--cores",
            "2",
            "--env",
            "custom-env",
            "--app-base-path",
            "/myapp",
            "--readiness-probe-path",
            "/health",
        ],
    )
    assert result.exit_code == 0, result.output
    apps.create_app.assert_called_once_with(
        name="dash",
        app_kind="CUSTOM",
        entrypoint_command='python -m uvicorn dash:app --host 0.0.0.0 --port "$APP_PORT"',
        app_port=8080,
        description="FastAPI demo",
        environment="custom-env",
        memory=4096,
        cores=2.0,
        app_base_path="/myapp",
        readiness_probe_path="/health",
    )


def test_app_create_flask_git_forwards_entrypoint_script(mock_project):
    """A Flask git app is launched from its file: an entrypoint script and a port, no command."""
    apps = mock_project.get_app_api.return_value
    apps.create_app.return_value = _fake_app(name="web", app_kind="FLASK")
    result = CliRunner().invoke(
        cli,
        [
            "app",
            "create",
            "web",
            "--app-kind",
            "FLASK",
            "--git-url",
            "https://github.com/gibchikafa/appshopsworkstests.git",
            "--git-provider",
            "GitHub",
            "--entrypoint-script",
            "flaskapp.py",
            "--app-port",
            "5000",
        ],
    )
    assert result.exit_code == 0, result.output
    apps.create_app.assert_called_once_with(
        name="web",
        app_kind="FLASK",
        environment="python-app-pipeline",
        app_port=5000,
        git_url="https://github.com/gibchikafa/appshopsworkstests.git",
        git_provider="GitHub",
        entrypoint_script="flaskapp.py",
    )


def test_app_create_nodejs_rejects_an_entrypoint_command(mock_project):
    result = CliRunner().invoke(
        cli,
        [
            "app",
            "create",
            "api",
            "--app-kind",
            "NODEJS",
            "--path",
            "Resources/server.js",
            "--entrypoint-command",
            "node server.js",
        ],
    )
    assert result.exit_code != 0
    assert "Node.js apps do not accept --entrypoint-command" in result.output
    mock_project.get_app_api.return_value.create_app.assert_not_called()


def test_app_create_streamlit_git_forwards_args(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.create_app.return_value = _fake_app(name="dash")
    result = CliRunner().invoke(
        cli,
        [
            "app",
            "create",
            "dash",
            "--app-kind",
            "STREAMLIT",
            "--git-url",
            "https://github.com/gibchikafa/appshopsworkstests.git",
            "--git-provider",
            "github",
            "--git-branch",
            "main",
            "--entrypoint-script",
            "streamlitapp.py",
            "--env",
            "custom-env",
        ],
    )
    assert result.exit_code == 0, result.output
    apps.create_app.assert_called_once_with(
        name="dash",
        app_kind="STREAMLIT",
        environment="custom-env",
        git_url="https://github.com/gibchikafa/appshopsworkstests.git",
        git_provider="GitHub",
        git_branch="main",
        entrypoint_script="streamlitapp.py",
    )


def test_app_create_custom_git_forwards_args(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.create_app.return_value = _fake_app(name="dash")
    result = CliRunner().invoke(
        cli,
        [
            "app",
            "create",
            "dash",
            "--app-kind",
            "CUSTOM",
            "--git-url",
            "https://github.com/gibchikafa/appshopsworkstests.git",
            "--git-provider",
            "GitHub",
            "--git-branch",
            "main",
            "--entrypoint-command",
            'python -m uvicorn dash:app --host 0.0.0.0 --port "$APP_PORT"',
            "--app-port",
            "8080",
            "--app-base-path",
            "/myapp",
            "--readiness-probe-path",
            "/health",
        ],
    )
    assert result.exit_code == 0, result.output
    apps.create_app.assert_called_once_with(
        name="dash",
        app_kind="CUSTOM",
        environment="python-app-pipeline",
        entrypoint_command='python -m uvicorn dash:app --host 0.0.0.0 --port "$APP_PORT"',
        app_port=8080,
        git_url="https://github.com/gibchikafa/appshopsworkstests.git",
        git_provider="GitHub",
        git_branch="main",
        app_base_path="/myapp",
        readiness_probe_path="/health",
    )


def test_app_create_streamlit_requires_path(mock_project):
    result = CliRunner().invoke(
        cli, ["app", "create", "dash", "--app-kind", "STREAMLIT"]
    )
    assert result.exit_code != 0
    assert "Streamlit apps require --path" in result.output


def test_app_create_custom_requires_entrypoint(mock_project):
    result = CliRunner().invoke(cli, ["app", "create", "dash", "--app-kind", "CUSTOM"])
    assert result.exit_code != 0
    assert "Custom apps require --entrypoint-command" in result.output


def test_app_create_streamlit_git_requires_script(mock_project):
    result = CliRunner().invoke(
        cli,
        [
            "app",
            "create",
            "dash",
            "--app-kind",
            "STREAMLIT",
            "--git-url",
            "https://github.com/gibchikafa/appshopsworkstests.git",
            "--git-provider",
            "GitHub",
        ],
    )
    assert result.exit_code != 0
    assert "require --entrypoint-script" in result.output


def test_app_create_with_start_triggers_run(mock_project):
    apps = mock_project.get_app_api.return_value
    a = _fake_app(name="dash", state="RUNNING", serving=True, app_url="https://h/x")
    apps.create_app.return_value = a
    with _polls(_serving("https://h/x")):
        result = CliRunner().invoke(
            cli,
            [
                "app",
                "create",
                "dash",
                "--app-kind",
                "STREAMLIT",
                "--path",
                "Resources/dash.py",
                "--start",
            ],
        )
    assert result.exit_code == 0, result.output
    a.run.assert_called_once_with(await_serving=False)
    assert "https://h/x" in result.output


def _serving(url="https://h/x", execution=9):
    return {
        "state": "RUNNING",
        "serving": True,
        "appUrl": url,
        "executionId": execution,
    }


def _polls(*apps):
    """The app's JSON as each poll of the CLI's wait sees it."""
    return mock.patch("hopsworks.cli.commands.app._app_json", side_effect=[*apps])


def test_app_start_waits_for_its_new_execution(mock_project, clock):
    apps = mock_project.get_app_api.return_value
    a = _fake_app(state="KILLED")
    a.execution_id = 8
    apps.get_app.return_value = a
    with _polls({"state": "KILLED", "executionId": 8}, _serving()) as polled:
        result = CliRunner().invoke(cli, ["app", "start", "dash"])
    assert result.exit_code == 0, result.output
    a.run.assert_called_once_with(await_serving=False)
    assert polled.call_count == 2
    assert "state=RUNNING, serving=yes" in result.output
    assert result.stdout.strip().splitlines()[-1] == "https://h/x"


def test_app_start_prints_the_url_on_the_clients_host(mock_project, clock):
    apps = mock_project.get_app_api.return_value
    a = _fake_app(state="KILLED")
    a.execution_id = 8
    apps.get_app.return_value = a
    hopsworks = mock.MagicMock(_base_url="https://h/")
    with (
        mock.patch("hopsworks_common.client._get_instance", return_value=hopsworks),
        _polls(_serving("/hopsworks-api/pythonapp/demo/dash/")),
    ):
        result = CliRunner().invoke(cli, ["app", "start", "dash"])
    assert result.exit_code == 0, result.output
    assert (
        result.stdout.strip().splitlines()[-1]
        == "https://h/hopsworks-api/pythonapp/demo/dash/"
    )


def test_app_start_prints_each_state_with_the_pod_reason(mock_project, clock):
    apps = mock_project.get_app_api.return_value
    apps.get_app.return_value = _fake_app()
    pending = {**backend("app_pending"), "executionId": 9}
    with _polls(pending, pending, {"state": "RUNNING", "executionId": 9}, _serving()):
        result = CliRunner().invoke(cli, ["app", "start", "dash"])
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if "App my_app:" in line]
    assert lines == [
        f"App my_app: INITIALIZING: {PENDING_REASON} (0 s)",
        f"App my_app: RUNNING ({2 * app_commands._POLL_SECONDS:.0f} s)",
    ]


def test_app_start_fails_at_once_naming_the_state_and_reason(mock_project, clock):
    apps = mock_project.get_app_api.return_value
    apps.get_app.return_value = _fake_app()
    pending = {**backend("app_pending"), "executionId": 9}
    failed = {"state": "INITIALIZATION_FAILED", "executionId": 9}
    with _polls(pending, failed):
        result = CliRunner().invoke(cli, ["app", "start", "dash"])
    assert result.exit_code == 1
    assert (
        "Could not start app 'my_app': it stopped in state INITIALIZATION_FAILED: "
        f"{PENDING_REASON.rstrip('.')}. Read `hops app logs my_app`." in result.output
    )
    assert clock.now == app_commands._POLL_SECONDS


def test_a_queued_app_names_the_scheduler_message_once_per_change(mock_project, clock):
    apps = mock_project.get_app_api.return_value
    apps.get_app.return_value = _fake_app()
    queued = {**backend("app_queued"), "executionId": 9}
    with _polls(queued, queued, {"state": "RUNNING", "executionId": 9}, _serving()):
        result = CliRunner().invoke(cli, ["app", "start", "dash"])
    assert result.exit_code == 0, result.output
    message = PENDING_REASON.removeprefix("FailedScheduling: ")
    lines = [line for line in result.output.splitlines() if "App my_app:" in line]
    assert lines == [
        f"App my_app: QUEUED: {message} (0 s)",
        f"App my_app: RUNNING ({2 * app_commands._POLL_SECONDS:.0f} s)",
    ]


def test_app_start_no_wait_passes_false(mock_project):
    apps = mock_project.get_app_api.return_value
    a = _fake_app()
    apps.get_app.return_value = a
    result = CliRunner().invoke(cli, ["app", "start", "dash", "--no-wait"])
    assert result.exit_code == 0, result.output
    a.run.assert_called_once_with(await_serving=False)


def test_app_redeploy_waits_for_the_new_execution(mock_project, clock):
    apps = mock_project.get_app_api.return_value
    a = _fake_app(state="RUNNING", serving=True, app_url="https://h/x")
    a.execution_id = 8
    apps.get_app.return_value = a
    with _polls(_serving(execution=8), _serving(execution=9)) as polled:
        result = CliRunner().invoke(cli, ["app", "redeploy", "dash"])
    assert result.exit_code == 0, result.output
    a.redeploy.assert_called_once_with(await_serving=False)
    # The old execution still serving does not count.
    assert polled.call_count == 2


def test_app_redeploy_no_wait_passes_false(mock_project):
    apps = mock_project.get_app_api.return_value
    a = _fake_app()
    apps.get_app.return_value = a
    result = CliRunner().invoke(cli, ["app", "redeploy", "dash", "--no-wait"])
    assert result.exit_code == 0, result.output
    a.redeploy.assert_called_once_with(await_serving=False)


def test_app_stop_calls_sdk(mock_project):
    apps = mock_project.get_app_api.return_value
    a = _fake_app()
    apps.get_app.return_value = a
    result = CliRunner().invoke(cli, ["app", "stop", "dash"])
    assert result.exit_code == 0, result.output
    a.stop.assert_called_once()


def test_app_delete_requires_confirmation(mock_project):
    apps = mock_project.get_app_api.return_value
    a = _fake_app()
    apps.get_app.return_value = a
    result = CliRunner().invoke(cli, ["app", "delete", "dash"], input="n\n")
    assert result.exit_code != 0
    a.delete.assert_not_called()


def test_app_delete_yes_skips_prompt(mock_project):
    apps = mock_project.get_app_api.return_value
    a = _fake_app()
    apps.get_app.return_value = a
    result = CliRunner().invoke(cli, ["app", "delete", "dash", "--yes"])
    assert result.exit_code == 0, result.output
    a.delete.assert_called_once()
    a.stop.assert_not_called()


def test_app_delete_force_is_accepted_and_does_nothing_more(mock_project):
    # The backend delete stops a running app; --force only keeps old scripts parsing.
    apps = mock_project.get_app_api.return_value
    a = _fake_app()
    apps.get_app.return_value = a
    result = CliRunner().invoke(cli, ["app", "delete", "dash", "--yes", "--force"])
    assert result.exit_code == 0, result.output
    a.stop.assert_not_called()
    a.delete.assert_called_once()


def test_app_info_surfaces_not_found(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.get_app.side_effect = RuntimeError("404")
    result = CliRunner().invoke(cli, ["app", "info", "ghost"])
    assert result.exit_code != 0
    assert "could not find app 'ghost'" in result.output.lower()


# --- deploy ------------------------------------------------------------------

_SOURCE = "/Projects/demo/Users/scott/apps/shop/app.py"
_URL = "https://h/hopsworks-api/pythonapp/demo/shop/"
# What the backend returns as appUrl: a path, which the CLI puts on the client's host.
_PATH = "/hopsworks-api/pythonapp/demo/shop/"


def _app_dir(tmp_path, requirements=None):
    root = tmp_path / "shop"
    (root / "static").mkdir(parents=True)
    (root / "app.py").write_text("app")
    (root / "static" / "index.html").write_text("<p>hi</p>")
    (root / ".env").write_text("SECRET=1")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"\0")
    if requirements is not None:
        (root / "app-requirements.txt").write_text(requirements)
    return root


def _deploy(
    mock_project,
    root,
    *args,
    existing=None,
    polls=(),
    installed=None,
    json_mode=False,
    hopsworks=None,
    user="scott",
    hopsfs=None,
    command="deploy",
):
    """Run `hops app deploy shop <root>` against fakes.

    `existing` is the app before the deploy, `polls` what each later `get_app`
    returns, `installed` the record of the last finished install (environment,
    then requirements), and `hopsworks` the client (on host https://h). What the
    deploy records is in `dataset_api.records`; the files it uploaded are in
    `dataset_api.hopsfs`, which a second deploy takes as `hopsfs`.
    """
    if hopsworks is None:
        hopsworks = mock.MagicMock(_verify=False)
    hopsworks._base_url = "https://h"
    apps = mock_project.get_app_api.return_value
    apps.get_app.return_value = existing
    dataset_api = mock_project.get_dataset_api.return_value
    node = (root / "package.json").exists() and not (root / "app.py").exists()
    record = f"Users/scott/apps/shop/{'.app-package.installed' if node else '.app-requirements.installed'}"
    files = dict(hopsfs or {})
    if installed is not None:
        files[record] = installed
    present = {"Users/scott"}
    dataset_api.exists.side_effect = lambda path: path in present or path in files
    dataset_api.mkdir.side_effect = present.add
    dataset_api.read_content.side_effect = lambda path: (
        mock.MagicMock(content=files[path].encode()) if path in files else None
    )
    dataset_api.records = []
    dataset_api.hopsfs = files

    def upload(local_path, upload_path, overwrite):
        import os

        with open(local_path, "rb") as f:
            text = f.read().decode("utf-8", "replace")
        files[f"{upload_path}/{os.path.basename(local_path)}"] = text
        if local_path.endswith(".installed"):
            dataset_api.records.append(text)

    dataset_api.upload.side_effect = upload
    username = user
    user = mock.MagicMock()
    user.username = username
    users_api = mock.MagicMock()
    users_api._get_current_user.return_value = user
    argv = [
        *(["--json"] if json_mode else []),
        "app",
        command,
        "shop",
        str(root),
        *args,
    ]
    with (
        mock.patch("hopsworks.get_users_api", return_value=users_api, create=True),
        mock.patch("hopsworks_common.client._get_instance", return_value=hopsworks),
        _polls(*[a if isinstance(a, dict) else _json(a) for a in polls]) as polled,
    ):
        result = CliRunner().invoke(cli, argv)
    apps.polled = polled
    return result, apps, dataset_api


def _json(a):
    return {
        "state": a.state,
        "serving": a.serving,
        "executionId": a.execution_id,
        "appUrl": _PATH,
    }


def _app(execution_id, state="RUNNING", serving=True, env="python-agent-pipeline"):
    a = _fake_app(
        name="shop",
        state=state,
        serving=serving,
        app_url=_URL,
        app_path=f"hdfs://{_SOURCE}",
    )
    a.execution_id = execution_id
    a.environment_name = env
    return a


def test_deploy_creates_the_app_and_prints_the_url_last(mock_project, tmp_path):
    root = _app_dir(tmp_path)
    created = _fake_app(name="shop")
    mock_project.get_app_api.return_value.create_app.return_value = created
    result, apps, dataset_api = _deploy(
        mock_project, root, polls=[_app(5, "INITIALIZING", serving=False), _app(5)]
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines()[-1] == _URL
    kwargs = apps.create_app.call_args.kwargs
    assert kwargs == {
        "name": "shop",
        "app_kind": "CUSTOM",
        "app_path": _SOURCE,
        "entrypoint_command": "python app.py",
        "app_port": 8080,
        "readiness_probe_path": "/health",
        "environment": "python-agent-pipeline",
    }
    created.run.assert_called_once_with(await_serving=False)
    uploads = [
        (c.kwargs["local_path"], c.kwargs["upload_path"], c.kwargs["overwrite"])
        for c in dataset_api.upload.call_args_list
    ]
    assert uploads == [
        (str(root / "app.py"), "Users/scott/apps/shop", True),
        (str(root / "static" / "index.html"), "Users/scott/apps/shop/static", True),
    ]
    assert [c.args[0] for c in dataset_api.mkdir.call_args_list] == [
        "Users/scott/apps",
        "Users/scott/apps/shop",
        "Users/scott/apps/shop/static",
    ]


def test_deploy_sets_the_description_of_a_new_app(mock_project, tmp_path):
    root = _app_dir(tmp_path)
    mock_project.get_app_api.return_value.create_app.return_value = _fake_app(
        name="shop"
    )
    result, apps, _ = _deploy(
        mock_project, root, "--description", "Revenue by country", polls=[_app(5)]
    )
    assert result.exit_code == 0, result.output
    assert apps.create_app.call_args.kwargs["description"] == "Revenue by country"


def test_deploy_keeps_the_description_of_an_existing_app(mock_project, tmp_path):
    root = _app_dir(tmp_path)
    running = _app(3)
    running.description = "Old"
    result, _, _ = _deploy(
        mock_project, root, "--description", "New", existing=running, polls=[_app(4)]
    )
    assert result.exit_code == 0, result.output
    assert "keeps its description" in result.output


def test_deploy_waits_for_the_new_execution_of_a_running_app(mock_project, tmp_path):
    root = _app_dir(tmp_path)
    running = _app(3)
    result, apps, _ = _deploy(
        mock_project, root, existing=running, polls=[_app(3), _app(4)]
    )
    assert result.exit_code == 0, result.output
    running.redeploy.assert_called_once_with(await_serving=False)
    apps.create_app.assert_not_called()
    assert apps.polled.call_count == 2


def test_deploy_starts_a_stopped_app(mock_project, tmp_path):
    root = _app_dir(tmp_path)
    stopped = _app(3, "KILLED", serving=False)
    result, _, _ = _deploy(mock_project, root, existing=stopped, polls=[_app(4)])
    assert result.exit_code == 0, result.output
    stopped.run.assert_called_once_with(await_serving=False)
    stopped.redeploy.assert_not_called()


def test_deploy_fails_when_the_new_execution_fails(mock_project, tmp_path):
    root = _app_dir(tmp_path)
    result, _, _ = _deploy(
        mock_project, root, existing=_app(3), polls=[_app(4, "FAILED", serving=False)]
    )
    assert result.exit_code == 1
    assert "Could not deploy app 'shop': it stopped in state FAILED." in result.output
    assert "hops app logs shop" in result.output


def test_deploy_refuses_an_app_in_another_environment(mock_project, tmp_path):
    root = _app_dir(tmp_path)
    result, _, dataset_api = _deploy(
        mock_project, root, existing=_app(3, env="python-app-pipeline")
    )
    assert result.exit_code == 1
    assert "--env python-app-pipeline" in result.output
    dataset_api.upload.assert_not_called()


def test_deploy_needs_app_py(mock_project, tmp_path):
    (tmp_path / "empty").mkdir()
    result = CliRunner().invoke(cli, ["app", "deploy", "shop", str(tmp_path / "empty")])
    assert result.exit_code == 2
    assert "has no app.py" in result.output


_PACKAGE = '{"name": "shop", "main": "server.mjs", "dependencies": {"express": "5.3.0"}, "devDependencies": {"vite": "7.0.0"}}'
_LOCKFILE = '{"lockfileVersion": 3}'


def _node_dir(tmp_path, package=_PACKAGE, lockfile=_LOCKFILE):
    root = tmp_path / "shop"
    (root / "public").mkdir(parents=True)
    (root / "package.json").write_text(package)
    if lockfile is not None:
        (root / "package-lock.json").write_text(lockfile)
    (root / "server.mjs").write_text("server")
    (root / "public" / "index.html").write_text("<p>hi</p>")
    (root / "node_modules" / "express").mkdir(parents=True)
    (root / "node_modules" / "express" / "index.js").write_text("express")
    return root


def _resolved(*packages):
    """What resolve_npm_package_json answers for `packages`, (name, version) pairs."""
    return {
        "packages": [
            {
                "name": n,
                "version": v,
                "status": "RESOLVED",
                "dependencyType": "DEPENDENCY",
            }
            for n, v in packages
        ]
    }


def _node_envs(mock_project, *names, have=None):
    """Environments whose package.json resolves to express 5.3.0, each holding the npm libraries `have`."""
    envs = Environments(mock_project)
    add = envs.add

    def add_node(name):
        env = add(name)
        env.resolve_npm_package_json.return_value = _resolved(("express", "5.3.0"))
        env.library_versions = dict(have or {})
        return env

    envs.add = add_node
    envs.api.create_environment.side_effect = lambda name, **kwargs: add_node(name)
    for name in names:
        add_node(name)
    return envs


def _assert_installs(env, packages):
    env.resolve_npm_package_json.assert_called_with(
        content=_PACKAGE, lockfile_content=_LOCKFILE, lockfile_name="package-lock.json"
    )
    env.install_npm.assert_called_once_with(packages, await_installation=True)


def _node_app(execution_id, env="shop-app-env"):
    a = _app(execution_id, env=env)
    a.app_kind = "NODEJS"
    a.app_path = "hdfs:///Projects/demo/Users/scott/apps/shop/server.mjs"
    return a


def test_deploy_ships_a_node_app_with_its_dependencies(mock_project, tmp_path):
    root = _node_dir(tmp_path)
    envs = _node_envs(mock_project)
    mock_project.get_app_api.return_value.create_app.return_value = _fake_app()
    result, apps, dataset_api = _deploy(mock_project, root, polls=[_node_app(1)])
    assert result.exit_code == 0, result.output
    assert apps.create_app.call_args.kwargs == {
        "name": "shop",
        "app_kind": "NODEJS",
        "app_path": "/Projects/demo/Users/scott/apps/shop/server.mjs",
        "app_port": 8080,
        "readiness_probe_path": "/health",
        "environment": "shop-app-env",
    }
    envs.api.create_environment.assert_called_once_with(
        "shop-app-env",
        base_environment_name="python-agent-pipeline",
        created_for_app="shop",
        await_creation=True,
    )
    _assert_installs(
        envs.envs["shop-app-env"], [{"name": "express", "version": "5.3.0"}]
    )
    # node_modules stays local: the environment holds the dependencies.
    assert sorted(
        os.path.relpath(c.kwargs["local_path"], root)
        for c in dataset_api.upload.call_args_list
        if not c.kwargs["local_path"].endswith(".installed")
    ) == [
        "package-lock.json",
        "package.json",
        os.path.join("public", "index.html"),
        "server.mjs",
    ]
    assert len(dataset_api.records) == 1
    assert dataset_api.records[0].startswith("shop-app-env\n")


def test_deploy_skips_the_install_of_an_unchanged_node_app(mock_project, tmp_path):
    root = _node_dir(tmp_path)
    envs = _node_envs(mock_project, "shop-app-env")
    first, _, dataset_api = _deploy(
        mock_project, root, existing=_node_app(3), polls=[_node_app(4)]
    )
    assert first.exit_code == 0, first.output
    result, _, _ = _deploy(
        mock_project,
        root,
        existing=_node_app(4),
        polls=[_node_app(5)],
        installed=dataset_api.records[0],
    )
    assert result.exit_code == 0, result.output
    assert "Environment shop-app-env is up to date." in result.output
    envs.envs["shop-app-env"].install_npm.assert_called_once()


def test_deploy_installs_only_the_node_dependencies_the_environment_lacks(
    mock_project, tmp_path
):
    root = _node_dir(tmp_path)
    envs = _node_envs(
        mock_project, "shop-app-env", have={"express": "5.2.0", "ms": "2.1.3"}
    )
    env = envs.envs["shop-app-env"]
    env.resolve_npm_package_json.return_value = _resolved(
        ("express", "5.3.0"), ("ms", "2.1.3")
    )
    result, _, _ = _deploy(
        mock_project, root, existing=_node_app(3), polls=[_node_app(4)]
    )
    assert result.exit_code == 0, result.output
    # Hopsworks refuses a second version of a library: the old one goes first.
    env.uninstall.assert_called_once_with("express", package_source="NPM")
    _assert_installs(env, [{"name": "express", "version": "5.3.0"}])


def test_deploy_puts_back_the_old_node_versions_when_the_install_fails(
    mock_project, tmp_path
):
    root = _node_dir(tmp_path)
    envs = _node_envs(mock_project, "shop-app-env", have={"express": "5.2.0"})
    env = envs.envs["shop-app-env"]
    env.install_npm.side_effect = [RuntimeError("registry down"), None]
    running = _node_app(3)
    result, _, _ = _deploy(mock_project, root, existing=running)
    assert result.exit_code == 1
    assert "registry down" in result.output
    assert "Put back express@5.2.0 in shop-app-env" in result.output
    env.uninstall.assert_called_once_with("express", package_source="NPM")
    assert env.install_npm.call_args_list == [
        mock.call([{"name": "express", "version": "5.3.0"}], await_installation=True),
        mock.call([{"name": "express", "version": "5.2.0"}], await_installation=True),
    ]
    running.redeploy.assert_not_called()


def test_deploy_says_when_the_old_node_versions_could_not_be_put_back(
    mock_project, tmp_path
):
    root = _node_dir(tmp_path)
    envs = _node_envs(mock_project, "shop-app-env", have={"express": "5.2.0"})
    envs.envs["shop-app-env"].install_npm.side_effect = RuntimeError("registry down")
    result, _, _ = _deploy(mock_project, root, existing=_node_app(3))
    assert result.exit_code == 1
    assert "Could not put back express@5.2.0 either" in result.output
    assert "the app's next start lacks them" in result.output


def test_deploy_of_a_node_app_on_a_shared_environment_says_how_to_move_to_the_copy(
    mock_project, tmp_path
):
    root = _node_dir(tmp_path)
    envs = _node_envs(mock_project, "shop-app-env", "shop-app-env-scott")
    envs.envs["shop-app-env"].install_npm.side_effect = environment_refused()
    running = _node_app(3)
    result, _, _ = _deploy(mock_project, root, existing=running)
    assert result.exit_code == 1
    assert (
        "does not get package.json dependencies; they are in your copy 'shop-app-env-scott'"
        in result.output
    )
    envs.envs["shop-app-env-scott"].install_npm.assert_called_once()
    running.redeploy.assert_not_called()


def test_deploy_reports_a_node_dependency_it_cannot_resolve(mock_project, tmp_path):
    root = _node_dir(tmp_path)
    envs = _node_envs(mock_project, "shop-app-env")
    env = envs.envs["shop-app-env"]
    env.resolve_npm_package_json.return_value = {
        "packages": [
            {
                "name": "nope",
                "requested": "^9",
                "status": "UNRESOLVED",
                "reason": "no such package",
            }
        ]
    }
    result, _, _ = _deploy(mock_project, root, existing=_node_app(3))
    assert result.exit_code == 1
    assert "Could not resolve nope@^9: no such package." in result.output
    env.install_npm.assert_not_called()


def test_deploy_runs_a_node_app_without_dependencies_on_the_base_environment(
    mock_project, tmp_path
):
    root = _node_dir(tmp_path, package='{"main": "server.mjs"}', lockfile=None)
    mock_project.get_app_api.return_value.create_app.return_value = _fake_app()
    env_api = mock_project.get_environment_api.return_value
    result, apps, _ = _deploy(
        mock_project, root, polls=[_node_app(1, env="python-agent-pipeline")]
    )
    assert result.exit_code == 0, result.output
    assert apps.create_app.call_args.kwargs["environment"] == "python-agent-pipeline"
    env_api.create_environment.assert_not_called()


def test_deploy_needs_the_lockfile_of_node_dependencies(mock_project, tmp_path):
    root = _node_dir(tmp_path, lockfile=None)
    result = CliRunner().invoke(cli, ["app", "deploy", "shop", str(root)])
    assert result.exit_code == 2
    assert "no package-lock.json: run npm install in" in result.output


def test_deploy_needs_a_package_main_that_names_the_server(mock_project, tmp_path):
    root = _node_dir(tmp_path, package='{"main": "README.md"}')
    result = CliRunner().invoke(cli, ["app", "deploy", "shop", str(root)])
    assert result.exit_code == 2
    assert (
        "has no app.py, nor a package.json whose main names the server" in result.output
    )


def test_deploy_refuses_a_node_app_under_the_name_of_a_python_app(
    mock_project, tmp_path
):
    root = _node_dir(tmp_path)
    result, _, dataset_api = _deploy(
        mock_project, root, existing=_app(3, env="shop-app-env")
    )
    assert result.exit_code == 1
    assert f"App 'shop' is a Streamlit app running hdfs://{_SOURCE}" in result.output
    assert "hops app delete shop --yes --force` (ask the user)" in result.output
    dataset_api.upload.assert_not_called()


def test_deploy_clones_and_installs_the_requirements(mock_project, tmp_path):
    root = _app_dir(tmp_path, requirements="polars==1.0\n")
    mock_project.get_app_api.return_value.create_app.return_value = _fake_app()
    env_api = mock_project.get_environment_api.return_value
    env_api.get_environment.return_value = None
    clone = env_api.create_environment.return_value
    result, apps, _ = _deploy(mock_project, root, polls=[_app(1, env="shop-app-env")])
    assert result.exit_code == 0, result.output
    env_api.create_environment.assert_called_once_with(
        "shop-app-env",
        base_environment_name="python-agent-pipeline",
        created_for_app="shop",
        await_creation=True,
    )
    clone.install_requirements.assert_called_once_with(
        "/Projects/demo/Users/scott/apps/shop/app-requirements.txt",
        await_installation=True,
    )
    assert apps.create_app.call_args.kwargs["environment"] == "shop-app-env"


def test_deploy_skips_the_install_when_the_requirements_did_not_change(
    mock_project, tmp_path
):
    root = _app_dir(tmp_path, requirements="polars==1.0\n")
    envs = Environments(mock_project, "shop-app-env")
    result, _, _ = _deploy(
        mock_project,
        root,
        existing=_app(3, env="shop-app-env"),
        polls=[_app(4, env="shop-app-env")],
        installed="shop-app-env\npolars==1.0\n",
    )
    assert result.exit_code == 0, result.output
    envs.envs["shop-app-env"].install_requirements.assert_not_called()
    envs.api.create_environment.assert_not_called()


def test_deploy_installs_again_when_the_environment_lost_a_recorded_library(
    mock_project, tmp_path
):
    root = _app_dir(tmp_path, requirements="polars==1.0\n# plots\nScikit_Learn>=1.5\n")
    envs = Environments(mock_project, "shop-app-env")
    envs.envs["shop-app-env"].library_names = ["polars", "pandas"]
    result, _, _ = _deploy(
        mock_project,
        root,
        existing=_app(3, env="shop-app-env"),
        polls=[_app(4, env="shop-app-env")],
        installed="shop-app-env\npolars==1.0\n# plots\nScikit_Learn>=1.5\n",
    )
    assert result.exit_code == 0, result.output
    assert "lacks scikit-learn" in result.output
    envs.envs["shop-app-env"].install_requirements.assert_called_once()


def test_deploy_reinstalls_changed_requirements(mock_project, tmp_path):
    root = _app_dir(tmp_path, requirements="polars==1.1\n")
    envs = Environments(mock_project, "shop-app-env")
    result, _, _ = _deploy(
        mock_project,
        root,
        existing=_app(3, env="shop-app-env"),
        polls=[_app(4, env="shop-app-env")],
        installed="shop-app-env\npolars==1.0\n",
    )
    assert result.exit_code == 0, result.output
    envs.envs["shop-app-env"].install_requirements.assert_called_once()


def test_deploy_into_a_shared_app_environment_clones_your_own_copy(
    mock_project, tmp_path
):
    root = _app_dir(tmp_path, requirements="polars==1.0\n")
    mock_project.get_app_api.return_value.create_app.return_value = _fake_app()
    envs = Environments(mock_project, "shop-app-env")
    envs.envs["shop-app-env"].install_requirements.side_effect = environment_refused()
    result, apps, _ = _deploy(
        mock_project, root, polls=[_app(1, env="shop-app-env-scott")]
    )
    assert result.exit_code == 0, result.output
    envs.api.create_environment.assert_called_once_with(
        "shop-app-env-scott",
        source_environment_name="shop-app-env",
        created_for_app="shop",
        await_creation=False,
    )
    envs.envs["shop-app-env-scott"].install_requirements.assert_called_once_with(
        "/Projects/demo/Users/scott/apps/shop/app-requirements.txt",
        await_installation=True,
    )
    assert apps.create_app.call_args.kwargs["environment"] == "shop-app-env-scott"


def test_deploy_reuses_your_copy(mock_project, tmp_path):
    root = _app_dir(tmp_path, requirements="polars==1.1\n")
    envs = Environments(mock_project, "shop-app-env", "shop-app-env-scott")
    running = _app(3, env="shop-app-env-scott")
    result, apps, dataset_api = _deploy(
        mock_project,
        root,
        existing=running,
        polls=[_app(4, env="shop-app-env-scott")],
        installed="shop-app-env-scott\npolars==1.0\n",
    )
    assert result.exit_code == 0, result.output
    envs.api.create_environment.assert_not_called()
    envs.envs["shop-app-env"].install_requirements.assert_not_called()
    envs.envs["shop-app-env-scott"].install_requirements.assert_called_once()
    running.redeploy.assert_called_once_with(await_serving=False)
    assert dataset_api.records == ["shop-app-env-scott\npolars==1.1\n"]


def test_deploy_an_app_on_a_shared_environment_says_how_to_move_to_the_copy(
    mock_project, tmp_path
):
    root = _app_dir(tmp_path, requirements="polars==1.1\n")
    envs = Environments(mock_project, "shop-app-env")
    envs.envs["shop-app-env"].install_requirements.side_effect = environment_refused()
    running = _app(3, env="shop-app-env")
    result, _, _ = _deploy(
        mock_project, root, existing=running, installed="shop-app-env\npolars==1.0\n"
    )
    assert result.exit_code == 1
    assert "they are in your copy 'shop-app-env-scott'" in result.output
    assert "ask before doing either" in result.output
    assert "--env" not in result.output
    envs.envs["shop-app-env-scott"].install_requirements.assert_called_once()
    running.redeploy.assert_not_called()


def test_deploy_records_the_install_and_the_next_deploy_skips_it(
    mock_project, tmp_path
):
    root = _app_dir(tmp_path, requirements="polars==1.0\n")
    mock_project.get_app_api.return_value.create_app.return_value = _fake_app()
    envs = Environments(mock_project, "shop-app-env")
    envs.envs["shop-app-env"].install_requirements.side_effect = environment_refused()
    result, _, dataset_api = _deploy(
        mock_project, root, polls=[_app(1, env="shop-app-env-scott")]
    )
    assert result.exit_code == 0, result.output
    assert dataset_api.records == ["shop-app-env-scott\npolars==1.0\n"]

    result, _, _ = _deploy(
        mock_project,
        root,
        existing=_app(1, env="shop-app-env-scott"),
        polls=[_app(2, env="shop-app-env-scott")],
        installed=dataset_api.records[0],
    )
    assert result.exit_code == 0, result.output
    envs.envs["shop-app-env-scott"].install_requirements.assert_called_once()


def test_a_failed_install_into_the_copy_is_installed_again_on_redeploy(
    mock_project, tmp_path
):
    root = _app_dir(tmp_path, requirements="polars==1.0\n")
    mock_project.get_app_api.return_value.create_app.return_value = _fake_app()
    envs = Environments(mock_project, "shop-app-env", "shop-app-env-scott")
    envs.envs["shop-app-env"].install_requirements.side_effect = environment_refused()
    copy = envs.envs["shop-app-env-scott"]
    copy.install_requirements.side_effect = [RuntimeError("pip failed"), None]
    result, apps, dataset_api = _deploy(mock_project, root)
    assert result.exit_code == 1
    assert dataset_api.records == []
    apps.create_app.assert_not_called()

    result, apps, dataset_api = _deploy(
        mock_project,
        root,
        polls=[_app(1, env="shop-app-env-scott")],
        hopsfs=dataset_api.hopsfs,
    )
    assert result.exit_code == 0, result.output
    assert copy.install_requirements.call_count == 2
    assert apps.create_app.call_args.kwargs["environment"] == "shop-app-env-scott"


def test_a_failed_clone_is_deleted_and_cloned_again_on_redeploy(mock_project, tmp_path):
    root = _app_dir(tmp_path, requirements="polars==1.0\n")
    mock_project.get_app_api.return_value.create_app.return_value = _fake_app()
    envs = Environments(mock_project, "shop-app-env")
    envs.envs["shop-app-env"].install_requirements.side_effect = environment_refused()

    from hopsworks_common.command import Command

    def clone(name, **kwargs):
        half = envs.add(name)
        half.commands = [Command(status="FAILED", op="CREATE")]
        half.delete.side_effect = lambda: envs.envs.pop(name)
        return half

    envs.api.create_environment.side_effect = clone
    envs.api.await_creation.side_effect = RuntimeError("build failed")
    result, _, dataset_api = _deploy(mock_project, root)
    assert result.exit_code == 1
    assert "Could not clone environment 'shop-app-env' to 'shop-app-env-scott'" in (
        result.output
    )
    assert "shop-app-env-scott" not in envs.envs
    assert dataset_api.records == []

    envs.api.create_environment.side_effect = lambda name, **kwargs: envs.add(name)
    envs.api.await_creation.side_effect = None
    result, _, _ = _deploy(
        mock_project,
        root,
        polls=[_app(1, env="shop-app-env-scott")],
        hopsfs=dataset_api.hopsfs,
    )
    assert result.exit_code == 0, result.output
    envs.envs["shop-app-env-scott"].install_requirements.assert_called_once()


def test_deploy_refuses_a_copy_name_over_the_hopsworks_limit(mock_project, tmp_path):
    root = _app_dir(tmp_path, requirements="polars==1.0\n")
    envs = Environments(mock_project, "shop-app-env")
    envs.envs["shop-app-env"].install_requirements.side_effect = environment_refused()
    result, _, _ = _deploy(mock_project, root, user="s" * 120)
    assert result.exit_code == 1
    assert "over the 128 Hopsworks allows" in result.output
    envs.api.create_environment.assert_not_called()


def test_deploy_any_other_install_refusal_is_reported_without_a_copy(
    mock_project, tmp_path
):
    root = _app_dir(tmp_path, requirements="polars==1.0\n")
    envs = Environments(mock_project, "shop-app-env")
    envs.envs["shop-app-env"].install_requirements.side_effect = environment_refused(
        300019
    )
    result, apps, _ = _deploy(mock_project, root)
    assert result.exit_code == 1
    assert "Could not install into environment 'shop-app-env'" in result.output
    envs.api.create_environment.assert_not_called()
    apps.create_app.assert_not_called()


def test_deploy_json_lists_every_file_with_its_sha256(mock_project, tmp_path):
    import hashlib

    root = _app_dir(tmp_path)
    mock_project.get_app_api.return_value.create_app.return_value = _fake_app()
    result, _, _ = _deploy(mock_project, root, polls=[_app(1)], json_mode=True)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["url"] == _URL
    assert payload["environment"] == "python-agent-pipeline"
    assert payload["source"] == "Users/scott/apps/shop"
    assert payload["files"] == [
        {"path": "app.py", "sha256": hashlib.sha256(b"app").hexdigest()},
        {
            "path": "static/index.html",
            "sha256": hashlib.sha256(b"<p>hi</p>").hexdigest(),
        },
    ]


def test_deploy_checks_a_route_through_the_proxy(mock_project, tmp_path):
    root = _app_dir(tmp_path)
    mock_project.id = 119
    mock_project.get_app_api.return_value.create_app.return_value = _fake_app()
    proxy_client = mock.MagicMock(_verify=False)
    proxy_client._send_request.return_value = {"token": "jwt"}
    answer = mock.MagicMock(ok=False, status_code=500, text="Internal Server Error")
    with mock.patch("requests.get", return_value=answer) as get:
        result, _, _ = _deploy(
            mock_project,
            root,
            "--check",
            "api/top",
            polls=[_app(1)],
            hopsworks=proxy_client,
        )
    assert result.exit_code == 1
    assert "api/top answered 500" in result.output
    assert proxy_client._send_request.call_args.args == (
        "POST",
        ["project", 119, "apps", "shop", "proxy-token"],
    )
    assert get.call_args.args == (_URL + "api/top",)
    assert get.call_args.kwargs["cookies"] == {"proxy_session": "jwt"}


def _check(mock_project, tmp_path, answers, path="api/top"):
    root = _app_dir(tmp_path)
    mock_project.get_app_api.return_value.create_app.return_value = _fake_app()
    proxy_client = mock.MagicMock(_verify=False)
    proxy_client._send_request.return_value = {"token": "jwt"}
    with (
        mock.patch("requests.get", side_effect=answers) as get,
        mock.patch("time.sleep"),
    ):
        result, _, _ = _deploy(
            mock_project,
            root,
            "--check",
            path,
            polls=[_app(1)],
            hopsworks=proxy_client,
        )
    return result, get


def _answer(status, content_type="application/json", text="{}"):
    return mock.MagicMock(
        ok=200 <= status < 300,
        status_code=status,
        text=text,
        headers={"content-type": content_type},
    )


def test_deploy_check_waits_for_a_starting_deployment_behind_the_app(
    mock_project, tmp_path
):
    result, get = _check(
        mock_project,
        tmp_path,
        [_answer(502, "text/plain", "no healthy upstream"), _answer(200)],
    )
    assert result.exit_code == 0, result.output
    assert get.call_count == 2


def test_deploy_check_fails_with_the_last_answer_after_the_boot_window(
    mock_project, tmp_path
):
    with mock.patch("hopsworks.cli.commands.app._CHECK_BOOT_SECONDS", 0):
        result, get = _check(
            mock_project, tmp_path, [_answer(503, "text/plain", "no healthy upstream")]
        )
    assert result.exit_code == 1
    assert "api/top answered 503: no healthy upstream" in result.output
    assert get.call_count == 1


def test_deploy_check_fails_on_an_html_page_for_an_api_route(mock_project, tmp_path):
    result, _ = _check(
        mock_project, tmp_path, [_answer(200, "text/html; charset=utf-8", "<html>")]
    )
    assert result.exit_code == 1
    assert "answered an HTML page" in result.output


_POLLS_PAST_30_S = int(30 / app_commands._POLL_SECONDS) + 2


def test_deploy_names_the_scheduler_message_of_a_queued_app_on_timeout(
    mock_project, tmp_path
):
    root = _app_dir(tmp_path)
    queued = {**backend("app_queued"), "executionId": 4}
    result, _, _ = _deploy(
        mock_project, root, "--timeout", "30", existing=_app(3), polls=[queued] * _POLLS_PAST_30_S
    )
    assert result.exit_code == 1
    message = PENDING_REASON.removeprefix("FailedScheduling: ").rstrip(".")
    assert f"App shop still QUEUED after 30 s: {message}. Read" in result.output


def test_deploy_names_the_stuck_state_and_reason_on_timeout(mock_project, tmp_path):
    root = _app_dir(tmp_path)
    pending = {**backend("app_pending"), "executionId": 4}
    result, _, _ = _deploy(
        mock_project, root, "--timeout", "30", existing=_app(3), polls=[pending] * _POLLS_PAST_30_S
    )
    assert result.exit_code == 1
    assert (
        f"App shop still INITIALIZING after 30 s: {PENDING_REASON.rstrip('.')}. "
        "Read `hops app logs shop`, or raise --timeout." in result.output
    )


def test_app_create_defaults_to_a_custom_app(mock_project):
    apps = mock_project.get_app_api.return_value
    apps.create_app.return_value = _fake_app(name="dash")
    result = CliRunner().invoke(
        cli, ["app", "create", "dash", "--entrypoint-command", "python app.py"]
    )
    assert result.exit_code == 0, result.output
    assert apps.create_app.call_args.kwargs["app_kind"] == "CUSTOM"


# --- prepare-env -------------------------------------------------------------


def _prepare(mock_project, root, **kwargs):
    return _deploy(mock_project, root, command="prepare-env", **kwargs)


@contextlib.contextmanager
def _env_held(release_after=None):
    """Another run holds shop's environment lock; the CLI's clock releases it after `release_after` s."""
    import fcntl
    import os
    import tempfile

    path = os.path.join(tempfile.gettempdir(), "hops-app-env-demo-shop.lock")
    with open(path, "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        clock = Clock()
        sleep = clock.sleep

        def tick(seconds):
            sleep(seconds)
            if release_after is not None and clock.now >= release_after:
                fcntl.flock(held, fcntl.LOCK_UN)

        clock.sleep = tick
        with mock.patch.object(app_commands, "time", clock):
            yield


def test_prepare_env_without_requirements_clones_nothing(mock_project, tmp_path):
    root = _app_dir(tmp_path)
    envs = Environments(mock_project)
    result, _, dataset_api = _prepare(mock_project, root)
    assert result.exit_code == 0, result.output
    assert "has no app-requirements.txt: nothing to prepare" in result.output
    assert "ended after" in result.output
    envs.api.create_environment.assert_not_called()
    dataset_api.upload.assert_not_called()


def test_prepare_env_clones_and_installs_and_the_deploy_skips_the_install(
    mock_project, tmp_path
):
    root = _app_dir(tmp_path, requirements="polars==1.0\n")
    mock_project.get_app_api.return_value.create_app.return_value = _fake_app()
    envs = Environments(mock_project)
    result, apps, dataset_api = _prepare(mock_project, root)
    assert result.exit_code == 0, result.output
    envs.api.create_environment.assert_called_once_with(
        "shop-app-env",
        base_environment_name="python-agent-pipeline",
        created_for_app="shop",
        await_creation=True,
    )
    envs.envs["shop-app-env"].install_requirements.assert_called_once_with(
        "/Projects/demo/Users/scott/apps/shop/app-requirements.txt",
        await_installation=True,
    )
    assert dataset_api.records == ["shop-app-env\npolars==1.0\n"]
    apps.create_app.assert_not_called()

    result, apps, _ = _deploy(
        mock_project,
        root,
        polls=[_app(1, env="shop-app-env")],
        hopsfs=dataset_api.hopsfs,
    )
    assert result.exit_code == 0, result.output
    envs.api.create_environment.assert_called_once()
    envs.envs["shop-app-env"].install_requirements.assert_called_once()
    assert apps.create_app.call_args.kwargs["environment"] == "shop-app-env"


def test_prepare_env_installs_a_node_app_and_the_deploy_skips_the_install(
    mock_project, tmp_path
):
    root = _node_dir(tmp_path)
    mock_project.get_app_api.return_value.create_app.return_value = _fake_app()
    envs = _node_envs(mock_project)
    result, apps, dataset_api = _prepare(mock_project, root)
    assert result.exit_code == 0, result.output
    _assert_installs(
        envs.envs["shop-app-env"], [{"name": "express", "version": "5.3.0"}]
    )
    assert len(dataset_api.records) == 1
    apps.create_app.assert_not_called()

    result, apps, _ = _deploy(
        mock_project, root, polls=[_node_app(1)], hopsfs=dataset_api.hopsfs
    )
    assert result.exit_code == 0, result.output
    envs.envs["shop-app-env"].install_npm.assert_called_once()
    assert apps.create_app.call_args.kwargs["app_kind"] == "NODEJS"


def test_prepare_env_installs_into_your_copy_of_a_shared_environment(
    mock_project, tmp_path
):
    root = _app_dir(tmp_path, requirements="polars==1.0\n")
    mock_project.get_app_api.return_value.create_app.return_value = _fake_app()
    envs = Environments(mock_project, "shop-app-env")
    envs.envs["shop-app-env"].install_requirements.side_effect = environment_refused()
    result, _, dataset_api = _prepare(mock_project, root)
    assert result.exit_code == 0, result.output
    envs.api.create_environment.assert_called_once_with(
        "shop-app-env-scott",
        source_environment_name="shop-app-env",
        created_for_app="shop",
        await_creation=False,
    )
    copy = envs.envs["shop-app-env-scott"]
    copy.install_requirements.assert_called_once()
    assert dataset_api.records == ["shop-app-env-scott\npolars==1.0\n"]

    result, apps, _ = _deploy(
        mock_project,
        root,
        polls=[_app(1, env="shop-app-env-scott")],
        hopsfs=dataset_api.hopsfs,
    )
    assert result.exit_code == 0, result.output
    copy.install_requirements.assert_called_once()
    assert envs.envs["shop-app-env"].install_requirements.call_count == 1
    assert apps.create_app.call_args.kwargs["environment"] == "shop-app-env-scott"


def test_prepare_env_failure_only_logs(mock_project, tmp_path):
    root = _app_dir(tmp_path, requirements="polars==1.0\n")
    env_api = mock_project.get_environment_api.return_value
    env_api.get_environment.side_effect = RuntimeError("boom")
    result, _, dataset_api = _prepare(mock_project, root)
    assert result.exit_code == 0, result.output
    assert "Could not prepare the environment of app shop" in result.output
    assert "boom" in result.output
    assert "ended after" in result.output
    assert dataset_api.records == []


def test_a_second_prepare_env_steps_aside(mock_project, tmp_path):
    root = _app_dir(tmp_path, requirements="polars==1.0\n")
    envs = Environments(mock_project)
    with _env_held():
        result, _, dataset_api = _prepare(mock_project, root)
    assert result.exit_code == 0, result.output
    assert "Another prepare-env or deploy of shop holds its environment" in (
        result.output
    )
    envs.api.create_environment.assert_not_called()
    dataset_api.upload.assert_not_called()


def test_deploy_waits_for_a_running_prepare_env(mock_project, tmp_path):
    root = _app_dir(tmp_path, requirements="polars==1.0\n")
    mock_project.get_app_api.return_value.create_app.return_value = _fake_app()
    envs = Environments(mock_project)
    with _env_held(release_after=30):
        result, apps, _ = _deploy(
            mock_project, root, polls=[_app(1, env="shop-app-env")]
        )
    assert result.exit_code == 0, result.output
    assert "Waited 30 s for the environment of app shop" in result.output
    envs.envs["shop-app-env"].install_requirements.assert_called_once()
    assert apps.create_app.call_args.kwargs["environment"] == "shop-app-env"


def test_deploy_fails_after_600_s_beside_a_running_prepare_env(mock_project, tmp_path):
    root = _app_dir(tmp_path, requirements="polars==1.0\n")
    envs = Environments(mock_project)
    with _env_held():
        result, apps, dataset_api = _deploy(mock_project, root)
    assert result.exit_code == 1
    assert "still holds its environment after 600 s: run this deploy again" in (
        result.output
    )
    dataset_api.upload.assert_not_called()
    envs.api.create_environment.assert_not_called()
    apps.create_app.assert_not_called()


def _delete_shop(mock_project):
    a = _fake_app(name="shop", environment_name="shop-app-env-scott")
    mock_project.get_app_api.return_value.get_app.return_value = a
    return CliRunner().invoke(cli, ["app", "delete", "shop", "--yes"]), a


def test_app_delete_waits_for_a_running_prepare_env(mock_project):
    with _env_held(release_after=30):
        result, a = _delete_shop(mock_project)
    assert result.exit_code == 0, result.output
    assert "Waited 30 s for the environment of app shop" in result.output
    a.delete.assert_called_once()


def test_app_delete_fails_after_600_s_beside_a_running_prepare_env(mock_project):
    with _env_held():
        result, a = _delete_shop(mock_project)
    assert result.exit_code == 1
    assert "still holds its environment after 600 s: run this delete again" in (
        result.output
    )
    a.delete.assert_not_called()


@pytest.mark.parametrize(
    ("status", "uses_mount"),
    [
        ({"state": "mounted", "mountpoint": "/Projects/blah"}, True),
        ({"state": "mounted", "mountpoint": "/Projects/other"}, False),
        ({"state": "starting", "mountpoint": "/Projects/blah"}, False),
        ("not json", False),
        (None, False),
    ],
)
def test_deploy_writes_through_the_mount_only_when_it_serves_the_project(
    tmp_path, monkeypatch, status, uses_mount
):
    path = tmp_path / "status.json"
    if isinstance(status, dict):
        path.write_text(json.dumps(status))
    elif status is not None:
        path.write_text(status)
    monkeypatch.setattr(app_commands, "_MOUNT_STATUS", str(path))
    project = type("Project", (), {"backing_name": "blah"})()

    assert app_commands._project_mount(project) == (
        "/Projects/blah" if uses_mount else None
    )
