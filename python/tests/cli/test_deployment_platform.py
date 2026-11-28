"""`hops deployment platform`: what each verb sends and prints."""

from __future__ import annotations

import json
from unittest import mock

from click.testing import CliRunner
from hopsworks.cli.main import cli


def _run(args, answer):
    with mock.patch(
        "hopsworks_common.core.rest._send_request", side_effect=answer
    ) as sent:
        result = CliRunner().invoke(cli, args)
    return result, sent


def test_list_reads_the_platform_deployments(mock_project):
    listed = [{"id": 99, "name": "embedder", "projectName": "hopsable_embeddings", "projectId": 21623,
               "namespace": "5okprgv34044", "modelName": "bge", "modelVersion": 1, "rate": 10, "burst": 20}]  # fmt: skip
    result, sent = _run(
        ["--json", "deployment", "platform", "list"], lambda *a, **k: listed
    )
    assert result.exit_code == 0, result.output
    assert sent.call_args.args == ("GET", ["serving", "platform"])
    assert json.loads(result.output) == listed


def test_set_on_sends_the_rate_and_burst(mock_project):
    result, sent = _run(
        [
            "deployment",
            "platform",
            "set",
            "99",
            "--on",
            "--rate",
            "10",
            "--burst",
            "20",
        ],
        lambda *a, **k: {"id": 99},
    )
    assert result.exit_code == 0, result.output
    assert sent.call_args.args == ("PUT", ["admin", "serving", 99, "platform"])
    assert sent.call_args.kwargs["json_body"] == {
        "enabled": True,
        "callsPerSecond": 10,
        "burst": 20,
    }
    assert "platform inference on, 10/s per caller, burst 20" in result.output


def test_set_on_without_a_rate_sends_nothing(mock_project):
    result, sent = _run(
        ["deployment", "platform", "set", "99", "--on"], lambda *a, **k: {}
    )
    assert result.exit_code != 0
    assert "--on takes --rate and --burst" in result.output
    sent.assert_not_called()


def test_set_off_sends_no_rate(mock_project):
    result, sent = _run(
        ["deployment", "platform", "set", "99", "--off"], lambda *a, **k: {"id": 99}
    )
    assert result.exit_code == 0, result.output
    assert sent.call_args.kwargs["json_body"] == {
        "enabled": False,
        "callsPerSecond": None,
        "burst": None,
    }


def test_a_refused_set_shows_the_server_answer(mock_project):
    def refuse(*a, **k):
        raise RuntimeError("403 not an administrator")

    result, _ = _run(["deployment", "platform", "set", "99", "--off"], refuse)
    assert result.exit_code != 0
    assert "403 not an administrator" in result.output
