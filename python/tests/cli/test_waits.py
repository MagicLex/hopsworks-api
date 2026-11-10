"""Waits read the reasons in the KubeMonitorDTO bodies the backend sends; only HTTP is fake."""

from __future__ import annotations

from unittest import mock

from hopsworks.cli import notebook_api, waits

from tests.cli.conftest import PENDING_REASON, backend


PENDING = "Server is in pending state - waiting to be scheduled"


def test_an_app_names_why_its_pod_is_not_scheduled():
    assert waits.kube_reason(backend("app_pending")["kubeMonitor"]) == PENDING_REASON
    assert waits.kube_reason(backend("app_running")["kubeMonitor"]) is None


def test_an_app_names_its_failing_readiness_probe():
    app = backend("app_running")
    probe = {
        "type": "Warning",
        "reason": "Unhealthy",
        "age": "5s",
        "from": "kubelet",
        "message": "Readiness probe failed: HTTP probe failed with statuscode: 503",
    }
    pod = {
        "name": "pythonapp-blah--hopsable-test-echo-67cb65cbd9-q4m8z",
        "status": "Running",
        "ready": False,
        "events": {"items": [probe]},
    }
    app["kubeMonitor"]["deployment"]["pods"] = {"items": [pod], "count": 1}
    assert waits.kube_reason(app["kubeMonitor"]) == (
        "Unhealthy: Readiness probe failed: HTTP probe failed with statuscode: 503"
    )


def test_the_jupyter_wait_names_the_pending_reason_and_hides_the_token(clock):
    running = {
        "hostName": "jupyter-blah--hopsabl2.blah.svc.cluster.local",
        "port": 8888,
        "token": "t",
        "ready": True,
        "status": "Server is running",
    }
    starting = {"status": "Server is starting", "ready": False}
    answers = {
        "running": iter([{"status": PENDING, "ready": False}, starting, running]),
        # Pending, then up with the Unhealthy event of its first readiness probe.
        "monitor": iter([backend("jupyter_pending"), backend("jupyter_monitor")]),
    }

    def send(method, path, **kwargs):
        return next(answers[path[-1]])

    fake = mock.Mock(_base_url="https://h", _verify=True)
    fake._send_request.side_effect = send
    with (
        mock.patch.object(notebook_api.client, "_get_instance", return_value=fake),
        mock.patch.object(notebook_api.Jupyter, "wait_ready"),
        mock.patch.object(waits.click, "echo") as echo,
    ):
        notebook_api.connect(2167, timeout=300)
    lines = [call.args[0] for call in echo.call_args_list]
    assert lines[0] == f"Jupyter: {PENDING}: {PENDING_REASON} (0 s)"
    # The readiness probe's reason shows, without the token its URL carries.
    assert lines[1].startswith(
        "Jupyter: Server is starting: Unhealthy: Readiness probe failed: Get "
    )
    assert "token=<hidden>" in lines[1]
    assert "connect: connection refused (2 s)" in lines[1]
    assert not any("token=" in line and "<hidden>" not in line for line in lines)
