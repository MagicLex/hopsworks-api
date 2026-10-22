"""`hops job create`: the job's configuration, including its environment."""

from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner
from hopsworks.cli import session
from hopsworks.cli.main import cli


def _project(created: list):
    api = SimpleNamespace(
        get_configuration=lambda job_type: {
            "type": job_type,
            "environmentName": "default-env",
        },
        create_job=lambda name, config: (
            created.append((name, config)) or SimpleNamespace(name=name)
        ),
    )
    return SimpleNamespace(get_job_api=lambda: api)


def test_create_runs_the_job_in_the_named_environment(monkeypatch):
    created: list = []
    monkeypatch.setattr(session, "get_project", lambda ctx: _project(created))
    args = [
        "job",
        "create",
        "ingest",
        "--type",
        "python",
        "--app-path",
        "Resources/a.py",
    ]
    done = CliRunner().invoke(
        cli, [*args, "--env", "helpdesk-example-env", "--args", "--docs d"]
    )
    assert done.exit_code == 0, done.output
    name, config = created[0]
    assert name == "ingest" and config["appPath"] == "Resources/a.py"
    assert config["environmentName"] == "helpdesk-example-env"
    assert config["defaultArgs"] == "--docs d"

    # Without --env the type's default stays.
    assert CliRunner().invoke(cli, args).exit_code == 0
    assert created[1][1]["environmentName"] == "default-env"


def test_logs_download_into_the_given_dir_not_the_working_directory(
    monkeypatch, tmp_path
):
    calls = []
    execution = SimpleNamespace(
        id=7,
        download_logs=lambda path=None: (
            calls.append(path) or (f"{path}/stdout.log", f"{path}/stderr.log")
        ),
    )
    job = SimpleNamespace(get_executions=lambda: [execution])
    monkeypatch.setattr(
        session,
        "get_project",
        lambda ctx: SimpleNamespace(
            get_job_api=lambda: SimpleNamespace(get_job=lambda name: job)
        ),
    )
    logs = tmp_path / "Logs" / "factory" / "churn"
    done = CliRunner().invoke(cli, ["job", "logs", "churn-train", "--dir", str(logs)])
    assert done.exit_code == 0, done.output
    assert calls == [str(logs)] and logs.is_dir()
    assert "working directory" not in done.output


def _missing(name):
    # What the backend answers for a job that does not exist: JOB_NOT_FOUND, a 404.
    from unittest import mock

    from hopsworks_common.client.exceptions import RestAPIError

    response = mock.MagicMock(status_code=404, reason="Not Found", content=b"{}")
    response.json.return_value = {"errorCode": 130009, "errorMsg": "Job not found."}
    raise RestAPIError(f"https://host/jobs/{name}", response)


def _deploy_project(created: list, existing: str | None):
    api = SimpleNamespace(
        get_configuration=lambda job_type: {"type": job_type},
        create_job=lambda name, config: (
            created.append((name, config)) or SimpleNamespace(name=name)
        ),
        get_job=lambda name: SimpleNamespace(name=name) if name == existing else _missing(name),
    )
    return SimpleNamespace(get_job_api=lambda: api)


def test_deploy_new_refuses_a_job_that_exists(monkeypatch):
    created: list = []
    monkeypatch.setattr(session, "get_project", lambda ctx: _deploy_project(created, "churn-train"))
    args = ["job", "deploy", "churn-train", "Resources/jobs/churn-train/train.py", "--new"]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code != 0
    assert "churn-train" in result.output and "already exists" in result.output
    assert created == []


def test_deploy_new_creates_a_job_that_does_not_exist(monkeypatch):
    created: list = []
    monkeypatch.setattr(session, "get_project", lambda ctx: _deploy_project(created, None))
    args = ["job", "deploy", "churn-train", "Resources/jobs/churn-train/train.py", "--new"]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert [name for name, _ in created] == ["churn-train"]


def test_deploy_without_new_updates_a_job_that_exists(monkeypatch):
    created: list = []
    monkeypatch.setattr(session, "get_project", lambda ctx: _deploy_project(created, "churn-train"))
    result = CliRunner().invoke(cli, ["job", "deploy", "churn-train", "Resources/jobs/churn-train/train.py"])
    assert result.exit_code == 0, result.output
    assert [name for name, _ in created] == ["churn-train"]
