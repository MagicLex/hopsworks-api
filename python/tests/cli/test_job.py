"""`hops job`: the job's configuration, its logs, and waits that follow an execution."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from click.testing import CliRunner
from hopsworks.cli import session, waits
from hopsworks.cli.commands import job as job_cmd
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


def test_kube_reason_is_the_newest_warning_event(unschedulable):
    assert (
        waits.kube_reason(unschedulable)
        == "FailedScheduling: 0/5 nodes are available: 3 Insufficient memory."
    )


def test_kube_reason_falls_back_to_an_unmet_pod_condition(unschedulable):
    pod = unschedulable["job"]["pods"][0]
    monitor = {"deployment": {"pods": [{**pod, "events": []}]}}
    assert (
        waits.kube_reason(monitor) == "0/5 nodes are available: 3 Insufficient memory."
    )


def test_kube_reason_keeps_a_message_that_names_its_reason():
    event = {"type": "Warning", "reason": "BackOff", "message": "BackOff pulling image"}
    assert waits.kube_reason({"deployment": {"events": [event]}}) == (
        "BackOff pulling image"
    )


def test_kube_reason_without_a_reason():
    assert waits.kube_reason(None) is None
    assert waits.kube_reason({}) is None
    assert waits.kube_reason({"deployment": {"pods": [{"name": "p"}]}}) is None


def _wait(mock_project, states, *args, success=True, monitor=None):
    """Run `hops job run etl --wait` while the backend reports `states`, one per poll.

    Returns the result and the `kube` flag of each poll.
    """
    execution = mock.MagicMock(id=7, state=states[-1], final_status=None)
    execution.success = success
    job = mock_project.get_job_api.return_value.get_job.return_value
    job.run.return_value = execution
    job.get_executions.return_value = [execution]
    polls = iter(states)
    kube = []

    def execution_json(name, ident, expand):
        kube.append(expand)
        state = next(polls)
        return {"state": state, **({"kubeMonitor": monitor} if expand else {})}

    with mock.patch.object(job_cmd, "_execution_json", side_effect=execution_json):
        result = CliRunner().invoke(cli, ["job", "run", "etl", "--wait", *args])
    return result, kube, execution


def test_a_wait_prints_each_state_once_with_the_pod_reason(
    mock_project, clock, unschedulable
):
    result, kube, execution = _wait(
        mock_project,
        ["INITIALIZING", "QUEUED", "QUEUED", "RUNNING", "RUNNING", "FINISHED"],
        monitor=unschedulable,
    )
    assert result.exit_code == 0, result.output
    reason = "FailedScheduling: 0/5 nodes are available: 3 Insufficient memory."
    lines = [line for line in result.output.splitlines() if "execution #7:" in line]
    assert lines == [
        f"etl: execution #7: INITIALIZING: {reason} (0 s)",
        f"etl: execution #7: QUEUED: {reason} (3 s)",
        "etl: execution #7: RUNNING (9 s)",
        "etl: execution #7: FINISHED (15 s)",
    ]
    # The pod's reasons are asked for only while the execution waits for its pod.
    assert kube == [True, True, True, True, False, False]
    execution.await_termination.assert_called_once_with(None)


def test_a_wait_stuck_before_running_names_the_state_and_leaves_it(
    mock_project, clock, unschedulable
):
    result, _, execution = _wait(
        mock_project, ["QUEUED"] * 30, "--start-timeout", "60", monitor=unschedulable
    )
    assert result.exit_code == 1
    assert (
        "etl: execution #7 still QUEUED after 60 s: FailedScheduling: 0/5 nodes are "
        "available: 3 Insufficient memory. The execution is left as is" in result.output
    )
    execution.stop.assert_not_called()
    execution.await_termination.assert_not_called()


def test_the_start_timeout_ends_once_the_execution_runs(mock_project, clock):
    result, _, _ = _wait(
        mock_project, ["QUEUED", *["RUNNING"] * 30, "FINISHED"], "--start-timeout", "10"
    )
    assert result.exit_code == 0, result.output
    assert "etl: execution #7: FINISHED (93 s)" in result.output


def test_a_failed_start_fails_at_once_with_the_last_reason(
    mock_project, clock, unschedulable
):
    result, kube, _ = _wait(
        mock_project,
        ["QUEUED", "INITIALIZATION_FAILED"],
        success=False,
        monitor=unschedulable,
    )
    assert result.exit_code == 1
    assert len(kube) == 2
    assert "etl: execution #7 INITIALIZATION_FAILED" in result.output
    assert "QUEUED: FailedScheduling" in result.output
    assert "etl: execution #7: INITIALIZATION_FAILED (3 s)" in result.output


def test_execution_json_falls_back_when_the_backend_refuses_the_kube_monitor():
    # The backend answers 404 to expand=kubeMonitor once the execution's pod is gone.
    fake = mock.MagicMock(_project_id=7)
    fake._send_request.side_effect = [Exception("404"), {"state": "FINISHED"}]
    with mock.patch("hopsworks_common.client._get_instance", return_value=fake):
        assert job_cmd._execution_json("train", 3, kube=True) == {"state": "FINISHED"}
    first, second = fake._send_request.call_args_list
    assert first.kwargs == {"query_params": {"expand": "kubeMonitor"}}
    assert second.args == ("GET", ["project", 7, "jobs", "train", "executions", 3])
    assert second.kwargs == {}
