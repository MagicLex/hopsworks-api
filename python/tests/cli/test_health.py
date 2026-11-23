"""`hops factory system status`: the facts of a system's health and the page that shows them."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from hopsworks.cli import health


SPEC = {
    "system": {"name": "Churn next month"},
    "requirements": {"system_type": "batch"},
    "features": {"pipelines": [{"job": {"name": "churn-example-features"}}]},
    "inference": {"realtime": {"deployment": "churnpredictor"}},
    "app": {"name": "churn-example-app"},
}


downloads: list = []


def _project(tmp_path):
    now = datetime.now(timezone.utc)
    log = tmp_path / "stderr.log"
    log.write_text("\n".join(f"line {i}" for i in range(100)) + "\nMemoryError\n")

    def execution(i, status, hours_ago):
        return SimpleNamespace(
            id=i,
            state="FINISHED" if status == "SUCCEEDED" else "FAILED",
            final_status=status,
            submission_time=(now - timedelta(hours=hours_ago)).isoformat(),
            duration=90_000,
            download_logs=lambda path=None: downloads.append(path) or (None, str(log)),
        )

    job = SimpleNamespace(
        job_type="PYTHON",
        job_schedule=SimpleNamespace(enabled=True),
        get_executions=lambda: [
            execution(1, "SUCCEEDED", 30),  # outside the window
            execution(2, "SUCCEEDED", 5),
            execution(3, "FAILED", 1),
        ],
    )
    deployment = SimpleNamespace(get_state=lambda: SimpleNamespace(status="Running"))
    return SimpleNamespace(
        name="churnfresh",
        get_job_api=lambda: SimpleNamespace(get_job=lambda name: job),
        get_model_serving=lambda: SimpleNamespace(
            get_deployment=lambda name: deployment
        ),
        get_app_api=lambda: SimpleNamespace(
            get_app=lambda name: SimpleNamespace(state="RUNNING", app_url="/app")
        ),
    )


def test_the_facts_hold_the_last_day_of_runs_and_the_pods(tmp_path, monkeypatch):
    pods = {
        "items": [
            {
                "metadata": {"name": "churnpredictor-predictor-00001-abc"},
                "spec": {
                    "containers": [
                        {"resources": {"limits": {"cpu": "2", "memory": "1Gi"}}}
                    ]
                },
                "status": {
                    "phase": "Running",
                    "containerStatuses": [
                        {
                            "name": "kserve-container",
                            "ready": True,
                            "restartCount": 2,
                            "lastState": {
                                "terminated": {"reason": "OOMKilled", "exitCode": 137}
                            },
                        }
                    ],
                },
            },
            {"metadata": {"name": "someone-else"}, "spec": {}, "status": {}},
        ]
    }

    def kubectl(*args):
        if args[:2] == ("get", "pods"):
            return json.dumps(pods)
        return "churnpredictor-predictor-00001-abc kserve-container 250m 900Mi\n"

    monkeypatch.setattr(health, "_kubectl", kubectl)
    downloads.clear()
    facts = health.collect(_project(tmp_path), SPEC, "churn-example", hours=24)
    # The report runs in the system's git work tree, so the failed run's logs
    # are read through a temporary directory that is gone afterwards.
    assert downloads and all(
        p and p != os.getcwd() and not os.path.exists(p) for p in downloads
    )
    [job] = facts["jobs"]
    assert [r["id"] for r in job["runs"]] == [3, 2]
    assert job["runs"][0]["log_tail"].endswith("MemoryError")
    assert len(job["runs"][0]["log_tail"].splitlines()) == health.LOG_TAIL_LINES
    deployment = next(s for s in facts["services"] if s["kind"] == "deployment")
    [pod] = deployment["pods"]
    assert pod["cpu_m"] == 250 and pod["memory_mib"] == 900
    assert pod["cpu_limit_m"] == 2000 and pod["memory_limit_mib"] == 1024
    assert pod["last_terminations"][0]["reason"] == "OOMKilled"
    assert facts["overall"] == "failing"
    assert facts["counts"] == {
        "jobs": 1,
        "runs": 2,
        "failed_runs": 1,
        "broken_jobs": 0,
        "services": 2,
        "unhealthy_services": 1,
        "pipelines": 0,
        "pipeline_problems": 0,
    }


def test_app_pods_are_found_by_the_backends_deployment_name(tmp_path, monkeypatch):
    # hopsworks-ee PythonAppServiceName.getDeploymentName: "pythonapp-" + the project
    # namespace + "--" + the app name lowercased, [^a-z0-9-] as "-", trailing dashes
    # dropped, cut at 63. Kubernetes cuts a pod's generated name base at 58 characters.
    long_app = "Sales_Dashboard_" + "x" * 60
    cases = [
        (
            {"name": "churn_fresh", "backing_name": "churn_fresh_2"},
            "churn-fresh-2",
            "Churn_Example_App!",
            "pythonapp-churn-fresh-2--churn-example-app-67cb65cbd9-q4m8z",
        ),
        (
            {"name": "Churn", "backing_name": "Churn_2", "project_namespace": None},
            None,
            "Churn_Example_App!",
            "pythonapp-churn-2--churn-example-app-67cb65cbd9-q4m8z",
        ),
        (
            {"name": "churn_fresh", "backing_name": "churn_fresh"},
            "churn-fresh",
            long_app,
            ("pythonapp-churn-fresh--sales-dashboard-" + "x" * 60)[:58] + "q4m8z",
        ),
    ]
    for names, namespace, app, pod in cases:
        listed = json.dumps(
            {
                "items": [
                    {
                        "metadata": {"name": pod},
                        "spec": {},
                        "status": {"phase": "Running", "containerStatuses": []},
                    }
                ]
            }
        )
        monkeypatch.setattr(
            health,
            "_kubectl",
            lambda *a, listed=listed: listed if a[0] == "get" else "",
        )
        project = _project(tmp_path)
        for key, value in names.items():
            setattr(project, key, value)
        if namespace:
            project.project_namespace = namespace
        facts = health.collect(project, {"app": {"name": app}}, "churn-example")
        [service] = facts["services"]
        assert [p["pod"] for p in service["pods"]] == [pod], (names, app)


def test_a_deleted_job_is_not_healthy(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "_kubectl", lambda *a: "")
    project = _project(tmp_path)
    project.get_job_api = lambda: SimpleNamespace(get_job=lambda name: None)
    facts = health.collect(project, SPEC, "churn-example")
    assert facts["jobs"][0]["missing"]
    assert facts["counts"]["broken_jobs"] == 1
    assert facts["overall"] != "healthy"


def test_the_page_carries_the_facts_and_cannot_be_broken_out_of(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "_kubectl", lambda *a: "")
    facts = health.collect(_project(tmp_path), SPEC, "churn-example")
    facts["jobs"][0]["runs"][0]["log_tail"] = "</script><script>alert(1)</script>"
    page = health.render(facts, "<p>One run failed.</p>")
    assert "<title>Churn next month status</title>" in page
    # The UI styles only a report that declares the token contract.
    assert '<meta name="hopsworks-design-tokens" content="1">' in page
    assert "</script><script>alert(1)" not in page
    assert '"failed_runs": 1' in page and "One run failed." in page


def test_the_summary_keeps_only_the_tags_it_asked_for(monkeypatch):
    monkeypatch.setattr(health.shutil, "which", lambda name: "/usr/bin/claude")
    reply = "```html\n<p>All <strong>good</strong>.</p><script>x()</script><img src=x onerror=y>\n```"
    monkeypatch.setattr(
        health.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=reply),
    )
    summary = health.summarize({"counts": {}})
    assert summary.startswith("<p>All <strong>good</strong>.</p>")
    assert "<script>" not in summary and "<img" not in summary


def test_an_app_pod_prefix_without_namespace_or_backing_name_still_answers():
    # A project read before its backing name is set carries backing_name None.
    project = SimpleNamespace(name="churn", backing_name=None, project_namespace=None)
    assert health._app_pod_prefix(project, "Dash").startswith("pythonapp-")
