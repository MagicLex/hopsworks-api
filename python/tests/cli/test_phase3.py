"""CliRunner tests for Phase 3: REST escape-hatch + chart/dashboard/superset.

Every command is driven through the shared ``mock_project`` fixture. We also
stub out ``hopsworks_common.client._get_instance`` where commands reach through
it for raw REST calls (connector create/delete).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

import pytest
from click.testing import CliRunner
from hopsworks.cli.main import cli

from tests.cli.conftest import execution as execution_body


# --- deployment ------------------------------------------------------------


def _state(status, reason=None):
    return SimpleNamespace(
        status=status,
        condition=SimpleNamespace(reason=reason) if reason is not None else None,
    )


def _deployment(mock_project, *states):
    deployment = mock.MagicMock()
    deployment.get_state.side_effect = list(states)
    mock_project.get_model_serving.return_value.get_deployment.return_value = deployment
    return deployment


def test_deployment_start_polls_the_state_itself(mock_project, clock):
    deployment = _deployment(mock_project, _state("Stopped"), _state("Running"))
    result = CliRunner().invoke(cli, ["deployment", "start", "fraud"])
    assert result.exit_code == 0, result.output
    deployment.start.assert_called_with(await_running=0)
    deployment._serving_engine._await_route.assert_called_once_with(deployment, 600)


def test_deployment_start_prints_each_status_once(mock_project, clock):
    unschedulable = (
        "Failed to be scheduled: 0/5 nodes are available: 3 Insufficient memory"
    )
    _deployment(
        mock_project,
        _state("Stopped"),
        _state("Starting", "Deployment is starting"),
        _state("Starting", "Deployment is starting"),
        _state("Starting", unschedulable),
        _state("Starting", unschedulable),
        _state("Running", "Deployment is ready"),
    )
    result = CliRunner().invoke(cli, ["deployment", "start", "fraud"])
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if "Deployment fraud:" in line]
    assert lines == [
        "Deployment fraud: Starting: Deployment is starting (0 s)",
        f"Deployment fraud: Starting: {unschedulable} (10 s)",
        "Deployment fraud: Running: Deployment is ready (20 s)",
    ]


def test_deployment_start_names_the_stuck_state_on_timeout(mock_project, clock):
    unschedulable = (
        "Failed to be scheduled: 0/5 nodes are available: 3 Insufficient memory"
    )
    _deployment(
        mock_project, _state("Stopped"), *[_state("Starting", unschedulable)] * 20
    )
    result = CliRunner().invoke(
        cli, ["deployment", "start", "fraud", "--timeout", "30"]
    )
    assert result.exit_code == 1
    assert (
        f"Deployment fraud still Starting after 30 s: {unschedulable}. "
        "Read `hops deployment logs fraud`" in result.output
    )


def test_deployment_start_fails_at_once_on_failed(mock_project, clock):
    deployment = _deployment(
        mock_project,
        _state("Stopped"),
        _state("Failed", "Predictor container terminated: OOMKilled"),
    )
    result = CliRunner().invoke(cli, ["deployment", "start", "fraud"])
    assert result.exit_code == 1
    assert (
        "Deployment fraud failed: Predictor container terminated: OOMKilled"
        in result.output
    )
    assert deployment.get_state.call_count == 2
    assert clock.now == 0


@pytest.mark.parametrize(
    "reason",
    [None, "", "null", "Failed to run: predictor terminated unsuccessfully. null"],
)
def test_deployment_start_without_a_reason_names_the_condition_and_the_log_tail(
    mock_project, clock, reason
):
    failed = SimpleNamespace(
        status="Failed", condition=SimpleNamespace(type="STARTED", reason=reason)
    )
    deployment = _deployment(mock_project, _state("Stopped"), failed)
    deployment.read_logs.return_value = (
        "ValueError: Predict class constructor contains one or more "
        "unsupported parameters"
    )
    result = CliRunner().invoke(cli, ["deployment", "start", "fraud"])
    assert result.exit_code == 1
    assert "Deployment fraud failed (condition STARTED)" in result.output
    assert "unsupported parameters" in result.output
    assert "null" not in result.output
    deployment.read_logs.assert_called_once_with(component="predictor", tail=30)


def test_deployment_start_failed_keeps_the_reason_when_the_logs_fail(
    mock_project, clock
):
    deployment = _deployment(
        mock_project,
        _state("Stopped"),
        _state("Failed", "Predictor container terminated: OOMKilled"),
    )
    deployment.read_logs.side_effect = RuntimeError("logs unavailable")
    result = CliRunner().invoke(cli, ["deployment", "start", "fraud"])
    assert result.exit_code == 1
    assert (
        "Deployment fraud failed: Predictor container terminated: OOMKilled. "
        "Read `hops deployment logs fraud`." in result.output
    )
    assert "logs unavailable" not in result.output


def test_deployment_stop_polls_until_stopped(mock_project, clock):
    deployment = _deployment(
        mock_project, _state("Stopping"), _state("Stopped", "Deployment is stopped")
    )
    result = CliRunner().invoke(cli, ["deployment", "stop", "fraud", "--timeout", "30"])
    assert result.exit_code == 0, result.output
    deployment.stop.assert_called_with(await_stopped=0)
    assert "Deployment fraud: Stopped: Deployment is stopped (5 s)" in result.output


def test_deployment_predict_parses_json_data(mock_project):
    ms = mock.MagicMock()
    deployment = mock.MagicMock()
    deployment.predict.return_value = {"predictions": [0.9]}
    ms.get_deployment.return_value = deployment
    mock_project.get_model_serving.return_value = ms
    result = CliRunner().invoke(
        cli, ["deployment", "predict", "fraud", "--data", '{"instances": [[1,2]]}']
    )
    assert result.exit_code == 0, result.output
    deployment.predict.assert_called_with(data={"instances": [[1, 2]]}, validate=True)


def test_deployment_logs_defaults_to_kubernetes_source(mock_project):
    ms = mock.MagicMock()
    deployment = mock.MagicMock()
    deployment.read_logs.return_value = "line1\nline2"
    ms.get_deployment.return_value = deployment
    mock_project.get_model_serving.return_value = ms
    result = CliRunner().invoke(cli, ["deployment", "logs", "fraud", "--tail", "5"])
    assert result.exit_code == 0, result.output
    deployment.read_logs.assert_called_with(
        component="predictor", tail=5, source="kubernetes", since=None, until=None
    )
    assert "line1" in result.output


def test_deployment_logs_download_calls_download_logs(mock_project):
    ms = mock.MagicMock()
    deployment = mock.MagicMock()
    deployment.download_logs.return_value = ["/tmp/a.log"]
    ms.get_deployment.return_value = deployment
    mock_project.get_model_serving.return_value = ms
    result = CliRunner().invoke(cli, ["deployment", "logs", "fraud", "--download"])
    assert result.exit_code == 0, result.output
    deployment.download_logs.assert_called_once_with(path=None)
    deployment.read_logs.assert_not_called()
    assert "/tmp/a.log" in result.output


def test_deployment_logs_download_goes_to_the_given_dir(mock_project, tmp_path):
    ms = mock.MagicMock()
    deployment = mock.MagicMock()
    deployment.download_logs.return_value = []
    ms.get_deployment.return_value = deployment
    mock_project.get_model_serving.return_value = ms
    logs = tmp_path / "Logs" / "factory" / "churn"
    result = CliRunner().invoke(
        cli, ["deployment", "logs", "fraud", "--download", "--dir", str(logs)]
    )
    assert result.exit_code == 0, result.output
    deployment.download_logs.assert_called_once_with(path=str(logs))
    assert logs.is_dir()


def test_deployment_logs_download_conflicts_with_follow_and_source(mock_project):
    ms = mock.MagicMock()
    deployment = mock.MagicMock()
    ms.get_deployment.return_value = deployment
    mock_project.get_model_serving.return_value = ms
    for extra in (["--follow"], ["--source", "kubernetes"]):
        result = CliRunner().invoke(
            cli, ["deployment", "logs", "fraud", "--download", *extra]
        )
        assert result.exit_code != 0
        assert "--download cannot be combined" in result.output
    deployment.download_logs.assert_not_called()


def test_deployment_delete(mock_project):
    ms = mock.MagicMock()
    deployment = mock.MagicMock()
    ms.get_deployment.return_value = deployment
    mock_project.get_model_serving.return_value = ms
    result = CliRunner().invoke(cli, ["deployment", "delete", "fraud", "--yes"])
    assert result.exit_code == 0, result.output
    deployment.delete.assert_called_with(force=False)


def test_deployment_create_via_model_deploy(mock_project):
    mr = mock.MagicMock()
    model = mock.MagicMock()
    model.version = 1
    created = mock.MagicMock()
    created.name = "fraud"
    model.deploy.return_value = created
    mr.get_models.return_value = [model]
    mock_project.get_model_registry.return_value = mr
    result = CliRunner().invoke(
        cli, ["deployment", "create", "fraud", "--name", "fraud"]
    )
    assert result.exit_code == 0, result.output
    model.deploy.assert_called_once()


@pytest.mark.parametrize(
    ("args", "suggested"),
    [
        (["order_returns"], "orderreturns"),
        (["m", "--name", "order-returns"], "orderreturns"),
        (["m", "--name", "2024churn"], "m2024churn"),
        (["m", "--name", "1-churn"], "m1churn"),
    ],
)
def test_deployment_create_refuses_a_name_hopsworks_refuses(mock_project, args, suggested):
    result = CliRunner().invoke(cli, ["deployment", "create", *args])
    assert result.exit_code == 2
    assert "a letter first, then letters and digits ([a-zA-Z][a-zA-Z0-9]*)" in result.output
    assert f"--name {suggested}" in result.output
    mock_project.get_model_registry.assert_not_called()


@pytest.mark.parametrize(
    ("framework", "has_view", "args", "expected"),
    [
        ("SKLEARN", True, [], True),
        ("PYTHON", True, [], True),
        ("SKLEARN", False, [], None),
        ("TENSORFLOW", True, [], None),
        ("SKLEARN", True, ["--no-default-predictor"], False),
        ("TENSORFLOW", True, ["--default-predictor"], True),
    ],
)
def test_deployment_create_serves_a_model_with_a_view_by_the_default_predictor(
    mock_project, framework, has_view, args, expected
):
    mr = mock.MagicMock()
    model = mock.MagicMock()
    model.version = 1
    model.framework = framework
    model.get_feature_view.return_value = mock.MagicMock() if has_view else None
    model.deploy.return_value = mock.MagicMock()
    mr.get_models.return_value = [model]
    mock_project.get_model_registry.return_value = mr
    result = CliRunner().invoke(cli, ["deployment", "create", "fraud", *args])
    assert result.exit_code == 0, result.output
    assert model.deploy.call_args.kwargs["default_predictor"] is expected


def test_deployment_create_sets_the_cores_and_memory_it_is_given(mock_project):
    mr = mock.MagicMock()
    model = mock.MagicMock()
    model.version = 1
    model.deploy.return_value = mock.MagicMock()
    mr.get_models.return_value = [model]
    mock_project.get_model_registry.return_value = mr
    result = CliRunner().invoke(cli, ["deployment", "create", "fraud", "--cores", "2"])
    assert result.exit_code == 0, result.output
    resources = model.deploy.call_args.kwargs["resources"]
    assert resources.to_dict()["predictorResources"] == {
        "requests": {"cores": 1.0, "gpus": 0},
        "limits": {"cores": 2.0, "gpus": 0},
    }
    assert resources.num_instances == 1

    model.deploy.reset_mock()
    result = CliRunner().invoke(
        cli, ["deployment", "create", "fraud", "--memory", "2048"]
    )
    assert result.exit_code == 0, result.output
    resources = model.deploy.call_args.kwargs["resources"]
    assert resources.to_dict()["predictorResources"] == {
        "requests": {"memory": 2048, "gpus": 0},
        "limits": {"memory": 2048, "gpus": 0},
    }

    model.deploy.reset_mock()
    result = CliRunner().invoke(cli, ["deployment", "create", "fraud"])
    assert result.exit_code == 0, result.output
    assert model.deploy.call_args.kwargs["resources"] is None


def test_deployment_create_refuses_an_unsupported_predict_init_before_upload(
    mock_project, tmp_path
):
    script = tmp_path / "bad.py"
    script.write_text("class Predict:\n    def __init__(self, config):\n        pass\n")
    mr = mock.MagicMock()
    model = mock.MagicMock()
    model.version = 1
    mr.get_models.return_value = [model]
    mock_project.get_model_registry.return_value = mr
    dataset = mock.MagicMock()
    mock_project.get_dataset_api.return_value = dataset

    result = CliRunner().invoke(
        cli, ["deployment", "create", "fraud", "--script", str(script)]
    )

    assert result.exit_code != 0
    assert f"{script}:2" in result.output
    assert "config" in result.output
    dataset.upload.assert_not_called()
    dataset.mkdir.assert_not_called()
    model.deploy.assert_not_called()


def test_deployment_create_deploys_the_latest_version_whatever_the_order(mock_project):
    # The registry lists versions newest first, so the last one is version 1.
    versions = []
    for number in (3, 1, 2):
        model = mock.MagicMock()
        model.version = number
        model.deploy.return_value = mock.MagicMock()
        versions.append(model)
    mr = mock.MagicMock()
    mr.get_models.return_value = versions
    mock_project.get_model_registry.return_value = mr
    result = CliRunner().invoke(
        cli, ["deployment", "create", "fraud", "--name", "fraud"]
    )
    assert result.exit_code == 0, result.output
    versions[0].deploy.assert_called_once()
    versions[1].deploy.assert_not_called()
    versions[2].deploy.assert_not_called()


# --- job -------------------------------------------------------------------


def test_job_run_with_wait(mock_project):
    api = mock.MagicMock()
    job = mock.MagicMock(job_type="PYTHON")
    execution = mock.MagicMock()
    execution.id, execution.state = 5, "RUNNING"
    job.run.return_value = execution
    api.get_job.return_value = job
    mock_project.get_job_api.return_value = api
    with _polled("FINISHED", 5, "SUCCEEDED"):
        result = CliRunner().invoke(cli, ["job", "run", "etl", "--wait"])
    assert result.exit_code == 0, result.output
    # The CLI holds the execution it started and waits on that one, not the job's latest.
    job.run.assert_called_with(
        args=None, await_termination=False, start_time=None, end_time=None
    )


def _polled(state, ident, final_status):
    """The CLI's wait sees the execution in `state` straight away, as the backend returns it."""
    body = {
        **execution_body("get_python_failed"),
        "id": ident,
        "state": state,
        "finalStatus": final_status,
    }
    backend_client = mock.MagicMock(_project_id=119)
    backend_client._send_request.return_value = body
    return mock.patch(
        "hopsworks_common.client._get_instance", return_value=backend_client
    )


@pytest.mark.parametrize("command", [["run", "etl", "--wait"], ["wait", "etl"]])
def test_a_failed_wait_prints_the_log_tails_and_exits_1(mock_project, hopsfs, command):
    failed = execution_body("get_python_failed")
    hopsfs.files[failed["stdoutPath"]] = ("".join(f"line {i}\n" for i in range(80)), 1)
    hopsfs.files[failed["stderrPath"]] = ("ValueError: bad column\n", 1)
    execution = mock.MagicMock(id=7, state="FAILED", final_status="FAILED")
    job = _job_api(mock_project, execution)
    job.get_executions.return_value = [execution]
    with _polled("FAILED", 7, "FAILED"):
        result = CliRunner().invoke(cli, ["job", *command])
    assert result.exit_code == 1
    assert "ValueError: bad column" in result.output
    assert "line 79" in result.output and "line 29" not in result.output
    assert "etl: execution #7 FAILED" in result.output
    assert "hops job logs etl --execution 7 --stdout" in result.output


def _job_api(mock_project, execution):
    api = mock.MagicMock()
    api.get_job.return_value.run.return_value = execution
    api.get_job.return_value.job_type = "PYTHON"
    mock_project.get_job_api.return_value = api
    return api.get_job.return_value


def test_a_waited_run_says_it_finished_and_how(mock_project):
    execution = mock.MagicMock(id=5, state="FINISHED", final_status="SUCCEEDED")
    _job_api(mock_project, execution)
    with _polled("FINISHED", 5, "SUCCEEDED"):
        result = CliRunner().invoke(
            cli,
            [
                "job",
                "backfill",
                "etl",
                "--start-time",
                "2026-10-01",
                "--end-time",
                "2026-10-02",
            ],
        )
    assert result.exit_code == 0, result.output
    assert (
        "etl finished for 2026-10-01T00:00:00 to 2026-10-02T00:00:00: "
        "execution #5 SUCCEEDED" in result.output
    )


def test_an_unwaited_run_says_it_only_started(mock_project):
    execution = mock.MagicMock(id=6, state="INITIALIZING", final_status=None)
    _job_api(mock_project, execution)
    result = CliRunner().invoke(cli, ["job", "run", "etl", "--no-wait"])
    assert result.exit_code == 0, result.output
    assert "etl started: execution #6, INITIALIZING" in result.output


def test_job_schedule(mock_project):
    api = mock.MagicMock()
    job = mock.MagicMock()
    job.schedule.return_value = mock.MagicMock()
    api.get_job.return_value = job
    mock_project.get_job_api.return_value = api
    result = CliRunner().invoke(cli, ["job", "schedule", "set", "etl", "0 0 * * * ?"])
    assert result.exit_code == 0, result.output
    # Use call_args + kwargs check rather than a full assert_called_with so the
    # test isn't tied to every backfill/catchup option the schedule API grows.
    kwargs = job.schedule.call_args.kwargs
    assert kwargs["cron_expression"] == "0 0 * * * ?"
    assert kwargs["start_time"] is None
    assert kwargs["end_time"] is None


def test_job_unschedule(mock_project):
    api = mock.MagicMock()
    job = mock.MagicMock()
    api.get_job.return_value = job
    mock_project.get_job_api.return_value = api
    result = CliRunner().invoke(cli, ["job", "schedule", "delete", "etl", "--yes"])
    assert result.exit_code == 0, result.output
    job.unschedule.assert_called_once()


def test_job_history(mock_project):
    api = mock.MagicMock()
    job = mock.MagicMock()
    e1 = mock.MagicMock()
    e1.id, e1.state, e1.final_status = 1, "FINISHED", "SUCCEEDED"
    e1.submission_time = "2026-04-01"
    e1.duration = 41_400
    e2 = mock.MagicMock()
    e2.id, e2.state, e2.final_status = 2, "RUNNING", "-"
    e2.submission_time = "2026-04-02"
    e2.duration = None
    job.get_executions.return_value = [e1, e2]
    api.get_job.return_value = job
    mock_project.get_job_api.return_value = api
    result = CliRunner().invoke(cli, ["job", "history", "etl"])
    assert result.exit_code == 0, result.output
    # Most recent first
    lines = [ln for ln in result.output.splitlines() if ln.startswith(("1", "2"))]
    assert lines and lines[0].startswith("2")
    # The finished run's duration in seconds; a running one has none yet.
    assert lines[1].split()[-1] == "41"
    assert lines[0].split()[-1] == "-"


def test_job_logs(mock_project):
    api = mock.MagicMock()
    job = mock.MagicMock()
    execution = mock.MagicMock()
    execution.download_logs.return_value = ("/tmp/out.log", "/tmp/err.log")
    job.get_executions.return_value = [execution]
    api.get_job.return_value = job
    mock_project.get_job_api.return_value = api
    result = CliRunner().invoke(cli, ["job", "logs", "etl"])
    assert result.exit_code == 0, result.output
    execution.download_logs.assert_called_once()


def test_job_logs_stdout(mock_project):
    import os
    import tempfile

    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "out.log")
    err = os.path.join(tmp, "err.log")
    with open(out, "w") as fd:
        fd.write("metrics: {'r2': 0.5}\n")
    with open(err, "w") as fd:
        fd.write("warn: something\n")
    api = mock.MagicMock()
    job = mock.MagicMock()
    execution = mock.MagicMock()
    execution.download_logs.return_value = (out, err)
    job.get_executions.return_value = [execution]
    api.get_job.return_value = job
    mock_project.get_job_api.return_value = api
    result = CliRunner().invoke(cli, ["job", "logs", "etl", "--stdout"])
    assert result.exit_code == 0, result.output
    # content streamed to the terminal, not just a path printed
    assert "metrics:" in result.output
    assert "warn: something" in result.output


# --- files (formerly `dataset`) -------------------------------------------


def test_files_mkdir(mock_project):
    api = mock.MagicMock()
    api.exists.side_effect = lambda path: path == "/Projects/demo"
    mock_project.get_dataset_api.return_value = api
    result = CliRunner().invoke(cli, ["files", "mkdir", "/Projects/demo/newdir"])
    assert result.exit_code == 0, result.output
    api.mkdir.assert_called_once_with("/Projects/demo/newdir")


def test_files_mkdir_creates_missing_parents_top_down(mock_project):
    api = mock.MagicMock()
    api.exists.side_effect = lambda path: path == "Resources"
    mock_project.get_dataset_api.return_value = api
    result = CliRunner().invoke(cli, ["files", "mkdir", "Resources/app/static"])
    assert result.exit_code == 0, result.output
    assert api.mkdir.call_args_list == [
        mock.call("Resources/app"),
        mock.call("Resources/app/static"),
    ]


def test_files_mkdir_on_an_existing_directory_succeeds(mock_project):
    api = mock.MagicMock()
    api.exists.return_value = True
    mock_project.get_dataset_api.return_value = api
    result = CliRunner().invoke(cli, ["files", "mkdir", "Resources/app"])
    assert result.exit_code == 0, result.output
    assert "already exists" in result.output
    api.mkdir.assert_not_called()


def test_files_upload_creates_the_remote_parents(mock_project, tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<p>hi</p>")
    api = mock.MagicMock()
    api.exists.side_effect = lambda path: path == "Resources"
    api.upload.return_value = "Resources/apps/site"
    mock_project.get_dataset_api.return_value = api
    result = CliRunner().invoke(
        cli, ["files", "upload", str(site), "Resources/apps/site"]
    )
    assert result.exit_code == 0, result.output
    api.mkdir.assert_called_once_with("Resources/apps")
    assert api.upload.call_args.kwargs["upload_path"] == "Resources/apps"


def test_files_upload_strips_matching_basename(mock_project, tmp_path):
    # When the destination basename equals the source filename, `files upload`
    # strips it: the SDK's upload() takes a *parent* path and re-appends the
    # local basename, so passing the full path through would double it
    # (e.g. .../key.json/key.json).
    api = mock.MagicMock()
    api.upload.return_value = "Resources/key.json"
    mock_project.get_dataset_api.return_value = api
    src = tmp_path / "key.json"
    src.write_text("{}")
    result = CliRunner().invoke(
        cli, ["files", "upload", str(src), "Resources/key.json"]
    )
    assert result.exit_code == 0, result.output
    api.upload.assert_called_with(
        local_path=str(src), upload_path="Resources", overwrite=False
    )


def test_files_upload_to_directory(mock_project, tmp_path):
    api = mock.MagicMock()
    api.upload.return_value = "Resources/folder/key.json"
    mock_project.get_dataset_api.return_value = api
    src = tmp_path / "key.json"
    src.write_text("{}")
    result = CliRunner().invoke(cli, ["files", "upload", str(src), "Resources/folder"])
    assert result.exit_code == 0, result.output
    api.upload.assert_called_with(
        local_path=str(src), upload_path="Resources/folder", overwrite=False
    )


def test_files_remove(mock_project):
    api = mock.MagicMock()
    mock_project.get_dataset_api.return_value = api
    result = CliRunner().invoke(
        cli, ["files", "delete", "/Projects/demo/stale", "--yes"]
    )
    assert result.exit_code == 0, result.output
    api.remove.assert_called_with("/Projects/demo/stale")


# --- datasource (formerly `connector`) writes -----------------------------


def test_datasource_create_jdbc_posts_body(mock_project):
    fs = mock_project.get_feature_store.return_value
    fs.id = 67
    fake_client = mock.MagicMock()
    fake_client._project_id = 119
    with mock.patch("hopsworks_common.client._get_instance", return_value=fake_client):
        result = CliRunner().invoke(
            cli,
            [
                "datasource",
                "create",
                "jdbc",
                "mydb",
                "--url",
                "jdbc:postgresql://host/db",
                "--user",
                "u",
                "--password",
                "p",
            ],
        )
    assert result.exit_code == 0, result.output
    call = fake_client._send_request.call_args
    assert call.args[0] == "POST"
    body = json.loads(call.kwargs["data"])
    # The Jackson polymorphic discriminator: without it the backend 500s with a
    # class-cast error (it cannot tell which connector subtype to build).
    assert body["type"] == "featurestoreJdbcConnectorDTO"
    assert body["storageConnectorType"] == "JDBC"
    assert body["connectionString"] == "jdbc:postgresql://host/db"
    assert {"name": "user", "value": "u"} in body["arguments"]


def _create_connector_body(args: list[str]) -> dict:
    """Invoke a ``datasource create`` command and return the POSTed JSON body."""
    fake_client = mock.MagicMock()
    fake_client._project_id = 119
    with mock.patch("hopsworks_common.client._get_instance", return_value=fake_client):
        result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    return json.loads(fake_client._send_request.call_args.kwargs["data"])


def test_datasource_create_carries_type_discriminator(mock_project):
    mock_project.get_feature_store.return_value.id = 67
    s3 = _create_connector_body(["datasource", "create", "s3", "lake", "--bucket", "b"])
    assert s3["type"] == "featurestoreS3ConnectorDTO"
    sf = _create_connector_body(
        [
            "datasource",
            "create",
            "snowflake",
            "wh",
            "--url",
            "https://x",
            "--user",
            "u",
            "--password",
            "p",
            "--database",
            "d",
            "--schema",
            "s",
            "--warehouse",
            "w",
        ]
    )
    assert sf["type"] == "featurestoreSnowflakeConnectorDTO"
    bq = _create_connector_body(
        [
            "datasource",
            "create",
            "bigquery",
            "bq",
            "--parent-project",
            "bill",
            "--project-id",
            "proj",
            "--key-path",
            "/Projects/p/k.json",
        ]
    )
    assert bq["type"] == "featurestoreBigqueryConnectorDTO"
    assert bq["parentProject"] == "bill"


def test_datasource_delete_calls_rest(mock_project):
    fs = mock_project.get_feature_store.return_value
    fs.id = 67
    fake_client = mock.MagicMock()
    fake_client._project_id = 119
    with mock.patch("hopsworks_common.client._get_instance", return_value=fake_client):
        result = CliRunner().invoke(cli, ["datasource", "delete", "mydb", "--yes"])
    assert result.exit_code == 0, result.output
    call = fake_client._send_request.call_args
    assert call.args[0] == "DELETE"
    assert "mydb" in call.args[1]


def test_datasource_databases_delegates_to_sdk(mock_project):
    fs = mock_project.get_feature_store.return_value
    ds = mock.MagicMock()
    ds.get_databases.return_value = ["db1", "db2"]
    fs.get_data_source.return_value = ds
    result = CliRunner().invoke(cli, ["datasource", "databases", "mydb"])
    assert result.exit_code == 0, result.output
    assert "db1" in result.output


# `chart` and `dashboard` top-level commands were removed — Superset is now
# the single home for that workflow (see test_superset_dashboard_* below).


# --- superset --------------------------------------------------------------


def test_superset_dashboard_list(mock_project):
    api = mock.MagicMock()
    api.list_dashboards.return_value = {
        "result": [
            {"id": 1, "dashboard_title": "Ops", "published": True, "slug": "ops"}
        ],
        "count": 1,
    }
    mock_project.get_superset_api.return_value = api
    result = CliRunner().invoke(cli, ["superset", "dashboard", "list"])
    assert result.exit_code == 0, result.output
    assert "Ops" in result.output


def test_superset_dashboards_list_uses_the_sdk(mock_project):
    api = mock.MagicMock()
    api.list_dashboards.return_value = {
        "result": [{"id": 4, "dashboard_title": "Deals", "published": True}]
    }
    mock_project.get_superset_api.return_value = api
    with mock.patch("hopsworks_common.client._is_external", return_value=True):
        result = CliRunner().invoke(cli, ["superset", "dashboard", "list"])
    assert result.exit_code == 0, result.output
    assert "Deals" in result.output


def test_superset_dashboard_create(mock_project):
    api = mock.MagicMock()
    api.create_dashboard.return_value = {"id": 3, "dashboard_title": "My Dash"}
    mock_project.get_superset_api.return_value = api
    result = CliRunner().invoke(
        cli, ["superset", "dashboard", "create", "My Dash", "--published"]
    )
    assert result.exit_code == 0, result.output
    api.create_dashboard.assert_called_with(
        dashboard_title="My Dash", published=True, slug=None
    )


def test_superset_dashboard_create_json_prints_the_id(mock_project):
    api = mock.MagicMock()
    api.create_dashboard.return_value = {"id": 3, "result": {"dashboard_title": "D"}}
    mock_project.get_superset_api.return_value = api
    result = CliRunner().invoke(cli, ["--json", "superset", "dashboard", "create", "D"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["id"] == 3


def test_superset_dataset_list(mock_project):
    api = mock.MagicMock()
    api.list_datasets.return_value = {
        "result": [{"id": 1, "table_name": "txn", "schema": "public", "database": {}}],
    }
    mock_project.get_superset_api.return_value = api
    result = CliRunner().invoke(cli, ["superset", "dataset", "list"])
    assert result.exit_code == 0, result.output
    assert "txn" in result.output


def test_superset_chart_create(mock_project):
    api = mock.MagicMock()
    api.create_chart.return_value = {"id": 7, "slice_name": "Bar"}
    mock_project.get_superset_api.return_value = api
    result = CliRunner().invoke(
        cli,
        [
            "superset",
            "chart",
            "create",
            "--name",
            "Bar",
            "--viz-type",
            "bar",
            "--datasource-id",
            "1",
            "--params",
            "{}",
        ],
    )
    assert result.exit_code == 0, result.output
    api.create_chart.assert_called_once()


def test_superset_chart_delete(mock_project):
    api = mock.MagicMock()
    mock_project.get_superset_api.return_value = api
    result = CliRunner().invoke(cli, ["superset", "chart", "delete", "7", "--yes"])
    assert result.exit_code == 0, result.output
    api.delete_chart.assert_called_with(7)
