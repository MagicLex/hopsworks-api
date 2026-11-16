"""`hops job`: the job's configuration, its logs, and waits that follow an execution."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest
from click.testing import CliRunner
from hopsworks.cli import session, waits
from hopsworks.cli.commands import job as job_cmd
from hopsworks.cli.main import cli
from hopsworks_common.execution import Execution

from tests.cli.conftest import backend, execution


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


STDOUT = execution("get_python_failed")["stdoutPath"]
STDERR = execution("get_python_failed")["stderrPath"]


# Each state as the backend sends it: the captured execution in that state, or for a state
# with no capture, the closest capture with the state and final status the backend writes.
_BODIES = {
    "FINISHED": lambda: execution("get_python_finished"),
    "FAILED": lambda: execution("get_python_failed"),
    "KILLED": lambda: backend("execution_killed"),
    # KubeJobsMonitor.updateFailedState: the state, with final status FAILED.
    "QUEUE_TIMEOUT": lambda: {
        **execution("get_python_failed"),
        "state": "QUEUE_TIMEOUT",
    },
}


def _body(state):
    """The execution body for `state`, or for a Spark `(state, finalStatus)` pair."""
    if isinstance(state, tuple):
        state, final_status = state
        return {
            **backend("execution_spark_finished"),
            "state": state,
            "finalStatus": final_status,
        }
    body = _BODIES.get(state, lambda: {**backend("execution_running"), "state": state})
    return body()


def _wait(
    mock_project,
    states,
    *args,
    monitor=None,
    job_type="PYTHON",
    command="run",
):
    """Run `hops job run etl --wait` while the backend reports `states`, one per poll.

    Only the HTTP boundary is fake: each GET of the execution answers the body the backend
    sends in the next state. Returns the result, the expansion each GET asked for, and the
    execution the run returned.
    """
    started = mock.MagicMock(id=7)
    job = mock_project.get_job_api.return_value.get_job.return_value
    job.name, job.job_type = "etl", job_type
    job.run.return_value = started
    job.get_executions.return_value = [started]
    polls = iter(states)
    expands = []
    last = [None]

    def send(method, path, query_params=None, headers=None):
        # The SDK's await_termination reads the execution too, with headers; only the
        # CLI's own polls are recorded.
        if headers is None:
            expands.append((query_params or {}).get("expand"))
        # The SDK's own wait polls on: the last state holds.
        last[0] = next(polls, last[0])
        body = {**_body(last[0]), "id": 7}
        body["kubeMonitor"] = (monitor or {"job": {}}) if expands[-1] else {}
        return body

    fake = mock.MagicMock(_project_id=119)
    fake._send_request.side_effect = send
    with mock.patch("hopsworks_common.client._get_instance", return_value=fake):
        verb = ["run", "etl", "--wait"] if command == "run" else [command, "etl"]
        result = CliRunner().invoke(cli, ["job", *verb, *args])
    return result, expands, started


def test_a_wait_prints_each_state_once_with_the_pod_reason(
    mock_project, hopsfs, clock, unschedulable
):
    result, expands, started = _wait(
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
    assert [e is not None for e in expands] == [True, True, True, True, False, False]


def test_a_wait_stuck_before_running_names_the_state_and_leaves_it(
    mock_project, hopsfs, clock, unschedulable
):
    result, _, started = _wait(
        mock_project, ["QUEUED"] * 30, "--start-timeout", "60", monitor=unschedulable
    )
    assert result.exit_code == 1
    assert (
        "etl: execution #7 still QUEUED after 60 s: FailedScheduling: 0/5 nodes are "
        "available: 3 Insufficient memory. The execution is left as is" in result.output
    )
    started.stop.assert_not_called()


def test_the_start_timeout_ends_once_the_execution_runs(mock_project, hopsfs, clock):
    result, _, _ = _wait(
        mock_project, ["QUEUED", *["RUNNING"] * 30, "FINISHED"], "--start-timeout", "10"
    )
    assert result.exit_code == 0, result.output
    assert "etl: execution #7: FINISHED (93 s)" in result.output


def test_a_spark_job_that_failed_to_start_fails_at_once(mock_project, hopsfs, clock):
    # YarnJob sets INITIALIZATION_FAILED alone; the final status stays UNDEFINED.
    spark = backend("execution_spark_finished")
    for path in (spark["stdoutPath"], spark["stderrPath"]):
        hopsfs.files[path] = ("", 1)
    result, expands, _ = _wait(
        mock_project,
        [("INITIALIZING", "UNDEFINED"), ("INITIALIZATION_FAILED", "UNDEFINED")],
        job_type="SPARK",
    )
    assert result.exit_code == 1
    assert len(expands) == 2
    assert clock.now == 3
    assert "etl: execution #7: INITIALIZATION_FAILED (3 s)" in result.output
    assert "etl: execution #7 INITIALIZATION_FAILED. Full logs" in result.output
    assert "final status is still" not in result.output


def test_a_queue_timeout_ends_the_wait_at_once_and_is_named(
    mock_project, hopsfs, clock
):
    result, expands, _ = _wait(mock_project, ["QUEUED", "QUEUE_TIMEOUT"])
    assert result.exit_code == 1
    assert len(expands) == 2
    assert "etl: execution #7: QUEUE_TIMEOUT (3 s)" in result.output
    assert "etl: execution #7 QUEUE_TIMEOUT. Full logs" in result.output
    assert "left as is" not in result.output


def test_a_failed_execution_without_stdout_reports_at_once(mock_project, hopsfs, clock):
    # The pod was killed before it wrote stdout.log.
    hopsfs.files[STDERR] = ("Traceback\nMemoryError\n", 1)
    result, _, started = _wait(mock_project, ["RUNNING", "FAILED"])
    assert result.exit_code == 1
    assert clock.now == 3
    assert "=== stderr, last 50 lines ===\nTraceback\nMemoryError" in result.output
    assert "(no stdout.log)" in result.output
    assert "etl: execution #7 FAILED. Full logs" in result.output


def test_a_failed_execution_without_logs_names_them_without_waiting(
    mock_project, hopsfs, clock
):
    result, _, _ = _wait(mock_project, ["RUNNING", "FAILED"])
    assert result.exit_code == 1
    assert clock.now == 3
    assert hopsfs.checks == {STDOUT: 1, STDERR: 1}
    assert "(no stdout.log)\n(no stderr.log)" in result.output


@pytest.mark.parametrize("job_type", ["PYTHON", "DOCKER"])
def test_a_job_that_writes_its_logs_first_reports_at_once(
    mock_project, hopsfs, clock, job_type
):
    result, expands, _ = _wait(mock_project, ["RUNNING", "FAILED"], job_type=job_type)
    assert result.exit_code == 1
    assert len(expands) == 2
    assert clock.now == 3


@pytest.mark.parametrize("job_type", ["SPARK", "PYSPARK", "RAY", "INGESTION"])
def test_other_jobs_keep_the_sdk_wait_for_their_logs_for_what_is_left(
    mock_project, hopsfs, clock, job_type
):
    # Spark and Ray aggregate their logs after the final status, multi-table ingestion after
    # the final state.
    hopsfs.files[STDOUT] = ("written after the final state\n", 2)
    hopsfs.files[STDERR] = ("Traceback\n", 1)
    left = []
    original = Execution.await_termination

    def await_termination(self, timeout=None):
        left.append(timeout)
        return original(self, timeout)

    with mock.patch.object(Execution, "await_termination", await_termination):
        result, _, _ = _wait(
            mock_project,
            ["RUNNING", "FAILED"],
            "--timeout",
            "100",
            job_type=job_type,
            command="wait",
        )
    assert result.exit_code == 1
    assert left == [97.0]
    assert clock.now > 3
    assert "written after the final state" in result.output


def test_a_spark_wait_ends_once_its_final_status_is_known(mock_project, hopsfs, clock):
    # YARN writes the state and the final status apart; either may land first.
    result, expands, _ = _wait(
        mock_project,
        [
            ("RUNNING", "UNDEFINED"),
            ("FINISHED", "UNDEFINED"),
            ("FINISHED", "SUCCEEDED"),
        ],
        job_type="SPARK",
    )
    assert result.exit_code == 0, result.output
    assert "etl: execution #7: FINISHED (3 s)" in result.output
    assert "execution #7 SUCCEEDED" in result.output

    # Spark sets the final status before it aggregates the logs: only the final state ends it.
    result, expands, _ = _wait(
        mock_project,
        [
            ("RUNNING", "UNDEFINED"),
            ("AGGREGATING_LOGS", "FAILED"),
            ("FAILED", "FAILED"),
        ],
        job_type="SPARK",
    )
    assert result.exit_code == 1
    assert len(expands) >= 3
    assert "etl: execution #7: AGGREGATING_LOGS (3 s)" in result.output
    assert "etl: execution #7 FAILED" in result.output


def test_a_spark_final_status_that_never_comes_is_named(mock_project, hopsfs, clock):
    result, _, _ = _wait(
        mock_project,
        [("RUNNING", "UNDEFINED"), *[("FINISHED", "UNDEFINED")] * 30],
        job_type="SPARK",
    )
    assert result.exit_code == 1
    assert (
        "etl: execution #7 FINISHED, but its final status is still UNDEFINED after 33 s."
        in result.output
    )


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
