"""`hops job`: the job's configuration, its logs, and waits that follow an execution."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest
from click.testing import CliRunner
from hopsworks.cli import session, waits
from hopsworks.cli.commands import job as job_cmd
from hopsworks.cli.main import cli
from hopsworks_common.client.exceptions import RestAPIError
from hopsworks_common.execution import Execution

from tests.cli.conftest import FIXTURES, PENDING_REASON, execution


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


def test_kube_reason_names_why_a_pod_is_not_scheduled(unschedulable):
    assert waits.kube_reason(unschedulable) == PENDING_REASON


def test_kube_reason_is_the_newest_warning_event(unschedulable):
    pod = unschedulable["job"]["pods"]["items"][0]
    older = {**pod["events"]["items"][0], "age": "3m", "message": "0/6 nodes: older"}
    pod["events"]["items"].insert(0, older)
    assert waits.kube_reason(unschedulable) == PENDING_REASON


def test_kube_reason_falls_back_to_the_condition_of_a_pod_not_ready(unschedulable):
    unschedulable["job"]["pods"]["items"][0]["events"] = {"items": []}
    assert waits.kube_reason(unschedulable) == PENDING_REASON.removeprefix(
        "FailedScheduling: "
    )


def test_kube_reason_keeps_a_message_that_names_its_reason(unschedulable):
    event = {"type": "Warning", "reason": "BackOff", "message": "BackOff pulling image"}
    unschedulable["job"]["pods"]["items"][0]["events"] = {"items": [event]}
    assert waits.kube_reason(unschedulable) == "BackOff pulling image"


def test_kube_reason_without_a_reason():
    assert waits.kube_reason(None) is None
    assert waits.kube_reason({}) is None
    # A Python job's execution once its pod is gone, and a Python App's execution.
    assert waits.kube_reason(execution("get_python_failed")["kubeMonitor"]) is None
    assert waits.kube_reason(execution("get_app_running")["kubeMonitor"]) is None


# The backend's answer to an expansion it does not know, as captured.
NOT_FOUND = {
    "errorCode": 120004,
    "usrMsg": "HTTP 404 Not Found",
    "errorMsg": "Web application exception occurred",
}


def _rest_error(status: int, body: dict) -> RestAPIError:
    response = mock.Mock(status_code=status, reason="", content=b"")
    response.json.return_value = body
    return RestAPIError("https://h/hopsworks-api/api/project", response)


STDOUT = execution("get_python_failed")["stdoutPath"]
STDERR = execution("get_python_failed")["stderrPath"]


# Each state as the backend sends it: the captured execution in that state, or for a state
# with no capture, the closest capture with the state and final status the backend writes.
_BODIES = {
    "FINISHED": lambda: execution("get_python_finished"),
    "FAILED": lambda: execution("get_python_failed"),
    "KILLED": lambda: execution("get_app_killed"),
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
            **execution("get_spark_finished"),
            "state": state,
            "finalStatus": final_status,
        }
    body = _BODIES.get(
        state, lambda: {**execution("get_python_running"), "state": state}
    )
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
            if expands[-1] not in (None, "kube_monitor"):
                raise _rest_error(404, NOT_FOUND)
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
    lines = [line for line in result.output.splitlines() if "execution #7:" in line]
    assert lines == [
        f"etl: execution #7: INITIALIZING: {PENDING_REASON} (0 s)",
        f"etl: execution #7: QUEUED: {PENDING_REASON} (3 s)",
        "etl: execution #7: RUNNING (9 s)",
        "etl: execution #7: FINISHED (15 s)",
    ]
    # The pod's reasons are asked for only while the execution waits for its pod.
    assert expands == [*["kube_monitor"] * 4, None, None]


def test_a_wait_stuck_before_running_names_the_state_and_leaves_it(
    mock_project, hopsfs, clock, unschedulable
):
    result, _, started = _wait(
        mock_project, ["QUEUED"] * 30, "--start-timeout", "60", monitor=unschedulable
    )
    assert result.exit_code == 1
    assert (
        f"etl: execution #7 still QUEUED after 60 s: {PENDING_REASON.rstrip('.')}. "
        "The execution is left as is" in result.output
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
    spark = execution("get_spark_finished")
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


def test_a_python_job_that_never_ran_keeps_the_last_reason(
    mock_project, hopsfs, clock, unschedulable
):
    result, _, _ = _wait(
        mock_project, ["QUEUED", "QUEUE_TIMEOUT"], monitor=unschedulable
    )
    assert result.exit_code == 1
    assert f"QUEUED: {PENDING_REASON}" in result.output
    assert (
        f"etl: execution #7 QUEUE_TIMEOUT: {PENDING_REASON.rstrip('.')}. Full logs"
        in result.output
    )


def test_a_failed_execution_without_stdout_reports_at_once(mock_project, hopsfs, clock):
    # The pod was killed before it wrote stdout.log.
    hopsfs.files[STDERR] = ("Traceback\nMemoryError\n", 1)
    result, _, started = _wait(mock_project, ["RUNNING", "FAILED"])
    assert result.exit_code == 1
    assert clock.now == 3
    assert "=== stderr, last 50 lines ===\nTraceback\nMemoryError" in result.output
    assert "(no stdout.log)" in result.output
    assert "etl: execution #7 FAILED. Full logs" in result.output


# Python and Docker write their logs before the final state, Spark and Ray set it after them.
@pytest.mark.parametrize("job_type", ["PYTHON", "DOCKER", "SPARK", "PYSPARK", "RAY"])
def test_a_job_whose_logs_are_known_at_its_final_state_reports_at_once(
    mock_project, hopsfs, clock, job_type
):
    result, expands, _ = _wait(mock_project, ["RUNNING", "FAILED"], job_type=job_type)
    assert result.exit_code == 1
    assert len(expands) == 2
    assert clock.now == 3
    assert hopsfs.checks == {STDOUT: 1, STDERR: 1}
    assert "(no stdout.log)\n(no stderr.log)" in result.output


def test_other_jobs_keep_the_sdk_wait_for_their_logs_for_what_is_left(
    mock_project, hopsfs, clock
):
    # Multi-table ingestion merges its logs after the final state.
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
            job_type="INGESTION",
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
    start = clock.now
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
    # The SDK's wait ends with the final state, though the backend wrote no log.
    assert clock.now - start == 6
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


def test_a_failure_after_the_pod_ran_is_not_blamed_on_its_scheduling(
    mock_project, hopsfs, clock, unschedulable
):
    hopsfs.files[STDERR] = ("Traceback\nZeroDivisionError: division by zero\n", 1)
    result, _, _ = _wait(
        mock_project, ["QUEUED", "RUNNING", "FAILED"], monitor=unschedulable
    )
    assert result.exit_code == 1
    assert f"QUEUED: {PENDING_REASON}" in result.output
    assert "etl: execution #7 FAILED. Full logs" in result.output
    error = result.output[result.output.index("Error:") :]
    assert "FailedScheduling" not in error


def test_a_failed_execution_names_the_oom_kill_the_backend_recorded(
    mock_project, hopsfs, clock
):
    # The backend's summary heads stderr.log, above more container log lines than the tail shows.
    summary = (FIXTURES / "stderr_oomkilled.txt").read_text()
    rows = "".join(f"row {n}\n" for n in range(2 * job_cmd.LOG_TAIL))
    hopsfs.files[STDERR] = (summary + rows, 1)
    result, _, _ = _wait(mock_project, ["RUNNING", "FAILED"])
    assert result.exit_code == 1
    assert "etl: execution #7 FAILED: OOMKilled (exit code 137). Full logs" in (
        result.output
    )


def test_a_failed_execution_names_a_container_that_never_started(
    mock_project, hopsfs, clock
):
    # KubeJobsMonitor's line after a container-waiting timeout.
    line = 'Job failed with: ErrImagePull - pull access denied for "etl:1"\n'
    hopsfs.files[STDERR] = (line, 1)
    result, _, _ = _wait(mock_project, ["RUNNING", "FAILED"])
    assert (
        'etl: execution #7 FAILED: ErrImagePull: pull access denied for "etl:1". Full'
        in result.output
    )


def test_a_failed_execution_without_the_backend_summary_names_only_its_state(
    mock_project, hopsfs, clock
):
    hopsfs.files[STDERR] = ("Traceback\nValueError: bad row\n", 1)
    result, _, _ = _wait(mock_project, ["RUNNING", "FAILED"])
    assert "etl: execution #7 FAILED. Full logs" in result.output


def test_a_failed_execution_gives_the_egress_line_as_its_reason(
    mock_project, hopsfs, clock
):
    # The dlt job prints the line last, after the vendor's traceback.
    line = (
        "Egress from project demo to port 5432/TCP is closed. Ask for it: https://h/x."
    )
    hopsfs.files[STDERR] = (
        f"Traceback\npsycopg2.OperationalError: Connection refused\nhopsworks egress: {line}\n",
        1,
    )
    result, _, _ = _wait(mock_project, ["RUNNING", "FAILED"])
    assert result.exit_code == 1
    assert f"etl: execution #7 FAILED: {line[:-1]}. Full logs" in result.output


def test_a_failed_execution_ignores_text_that_only_holds_the_egress_prefix(
    mock_project, hopsfs, clock
):
    hopsfs.files[STDERR] = ("log: hopsworks egress: not at the start\n", 1)
    result, _, _ = _wait(mock_project, ["RUNNING", "FAILED"])
    assert "etl: execution #7 FAILED. Full logs" in result.output


def test_execution_json_falls_back_when_the_backend_has_no_kube_monitor():
    fake = mock.MagicMock(_project_id=7)
    fake._send_request.side_effect = [
        _rest_error(404, NOT_FOUND),
        {"state": "FINISHED"},
    ]
    with mock.patch("hopsworks_common.client._get_instance", return_value=fake):
        assert job_cmd._execution_json("train", 3, kube=True) == {"state": "FINISHED"}
    first, second = fake._send_request.call_args_list
    assert first.kwargs == {"query_params": {"expand": "kube_monitor"}}
    assert second.args == ("GET", ["project", 7, "jobs", "train", "executions", 3])
    assert second.kwargs == {}


def test_execution_json_lets_any_other_error_through():
    fake = mock.MagicMock(_project_id=7)
    fake._send_request.side_effect = [_rest_error(500, {"errorMsg": "boom"})]
    with (
        mock.patch("hopsworks_common.client._get_instance", return_value=fake),
        pytest.raises(RestAPIError),
    ):
        job_cmd._execution_json("train", 3, kube=True)


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
        get_job=lambda name: (
            SimpleNamespace(name=name) if name == existing else _missing(name)
        ),
    )
    return SimpleNamespace(get_job_api=lambda: api)


def test_deploy_new_refuses_a_job_that_exists(monkeypatch):
    created: list = []
    monkeypatch.setattr(
        session, "get_project", lambda ctx: _deploy_project(created, "churn-train")
    )
    args = [
        "job",
        "deploy",
        "churn-train",
        "Resources/jobs/churn-train/train.py",
        "--new",
    ]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code != 0
    assert "churn-train" in result.output and "already exists" in result.output
    assert created == []


def test_deploy_new_creates_a_job_that_does_not_exist(monkeypatch):
    created: list = []
    monkeypatch.setattr(
        session, "get_project", lambda ctx: _deploy_project(created, None)
    )
    args = [
        "job",
        "deploy",
        "churn-train",
        "Resources/jobs/churn-train/train.py",
        "--new",
    ]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert [name for name, _ in created] == ["churn-train"]


def test_deploy_without_new_updates_a_job_that_exists(monkeypatch):
    created: list = []
    monkeypatch.setattr(
        session, "get_project", lambda ctx: _deploy_project(created, "churn-train")
    )
    result = CliRunner().invoke(
        cli, ["job", "deploy", "churn-train", "Resources/jobs/churn-train/train.py"]
    )
    assert result.exit_code == 0, result.output
    assert [name for name, _ in created] == ["churn-train"]


def _resources_project(created: list, stored=None, lookup_error=None):
    def get_job(name):
        if lookup_error is not None:
            raise lookup_error
        return stored

    api = SimpleNamespace(
        get_configuration=lambda job_type: {
            "type": "pythonJobConfiguration",
            "resourceConfig": {"cores": 0.5, "memory": 2048, "gpus": 0},
        },
        get_job=get_job,
        create_job=lambda name, config: (
            created.append((name, config)) or SimpleNamespace(name=name)
        ),
    )
    return SimpleNamespace(get_job_api=lambda: api)


def _not_found():
    error = Exception("job not found")
    error.response = SimpleNamespace(status_code=404)
    return error


def test_create_sets_the_resources_given_and_keeps_the_other_defaults(monkeypatch):
    created: list = []
    project = _resources_project(created)
    monkeypatch.setattr(session, "get_project", lambda ctx: project)
    done = CliRunner().invoke(
        cli,
        [
            "job",
            "create",
            "train",
            "--type",
            "python",
            "--app-path",
            "Resources/a.py",
            "--resources",
            '{"resourceConfig": {"cores": 2, "memory": 8192}}',
        ],
    )
    assert done.exit_code == 0, done.output
    assert created[0][1]["resourceConfig"] == {"cores": 2, "memory": 8192, "gpus": 0}


def test_create_refuses_resources_that_are_not_a_json_object(monkeypatch):
    created: list = []
    project = _resources_project(created)
    monkeypatch.setattr(session, "get_project", lambda ctx: project)
    args = ["job", "create", "t", "--type", "python", "--app-path", "Resources/a.py"]
    for bad, says in (
        ("{cores: 2}", "--resources is not valid JSON"),
        ("[1, 2]", "--resources must be a JSON object"),
    ):
        done = CliRunner().invoke(cli, [*args, "--resources", bad])
        assert done.exit_code == 2, done.output
        assert says in done.output
    assert created == []


def test_redeploy_keeps_the_stored_configuration(monkeypatch):
    created: list = []
    stored = SimpleNamespace(
        job_type="PYTHON",
        config={
            "type": "pythonJobConfiguration",
            "appPath": "Resources/old.py",
            "resourceConfig": {"cores": 4, "memory": 16384, "gpus": 0},
            "defaultArgs": "--day 1",
        },
    )
    project = _resources_project(created, stored=stored)
    monkeypatch.setattr(session, "get_project", lambda ctx: project)
    done = CliRunner().invoke(cli, ["job", "deploy", "train", "Resources/new.py"])
    assert done.exit_code == 0, done.output
    config = created[0][1]
    assert config["appPath"] == "Resources/new.py"
    assert config["resourceConfig"] == {"cores": 4, "memory": 16384, "gpus": 0}
    assert config["defaultArgs"] == "--day 1"


def test_redeploy_changes_only_the_resources_given(monkeypatch):
    created: list = []
    stored = SimpleNamespace(
        job_type="PYTHON",
        config={"resourceConfig": {"cores": 4, "memory": 16384, "gpus": 0}},
    )
    project = _resources_project(created, stored=stored)
    monkeypatch.setattr(session, "get_project", lambda ctx: project)
    done = CliRunner().invoke(
        cli,
        [
            "job",
            "deploy",
            "train",
            "Resources/a.py",
            "--resources",
            '{"resourceConfig": {"memory": 32768}}',
        ],
    )
    assert done.exit_code == 0, done.output
    assert created[0][1]["resourceConfig"] == {"cores": 4, "memory": 32768, "gpus": 0}


def test_deploy_of_a_new_job_or_another_type_starts_from_the_default(monkeypatch):
    for stored, error in (
        (None, _not_found()),
        (SimpleNamespace(job_type="PYSPARK", config={"appPath": "x"}), None),
    ):
        created: list = []
        project = _resources_project(created, stored=stored, lookup_error=error)
        monkeypatch.setattr(session, "get_project", lambda ctx, p=project: p)
        done = CliRunner().invoke(cli, ["job", "deploy", "train", "Resources/a.py"])
        assert done.exit_code == 0, done.output
        assert created[0][1]["resourceConfig"] == {
            "cores": 0.5,
            "memory": 2048,
            "gpus": 0,
        }
        assert created[0][1]["type"] == "pythonJobConfiguration"


def test_deploy_stops_when_the_job_cannot_be_read(monkeypatch):
    created: list = []
    error = Exception("server error")
    error.response = SimpleNamespace(status_code=500)
    project = _resources_project(created, lookup_error=error)
    monkeypatch.setattr(session, "get_project", lambda ctx: project)
    done = CliRunner().invoke(cli, ["job", "deploy", "train", "Resources/a.py"])
    assert done.exit_code != 0
    assert "Could not read job 'train'" in done.output
    assert created == []
