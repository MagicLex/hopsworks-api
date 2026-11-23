"""The embedding client: which deployment each call uses, and how it waits on a busy one.

The transport is a real ``hopsworks_common`` client over a real ``requests``
session whose ``send`` is the one patched point, so every test counts the HTTP
attempts the call made.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

import pytest
import requests
from hopsworks_agents import embed
from hopsworks_common.client import base


MODEL = "bge_small_en_v1_5"


def _response(status, body=None, headers=None):
    r = requests.Response()
    r.status_code = status
    r.reason = "x"
    r.raw = mock.MagicMock()
    r._content = json.dumps(body if body is not None else {}).encode()
    r.headers.update(headers or {})
    r.headers["Content-Type"] = "application/json"
    return r


def _ok(count=1, model=MODEL):
    data = [{"index": i, "embedding": [float(i)]} for i in range(count)]
    return _response(200, {"predictions": [{"model": model, "data": data}]})


class Clock:
    """A monotonic clock that only moves when a test says so, or when the call sleeps."""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def wire(monkeypatch):
    """The sent requests (URL, body), the scripted answers, and the clock."""
    monkeypatch.setattr(embed, "_routes", {})
    monkeypatch.setattr(embed.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setenv("HOPSABLE_EMBEDDING_PROJECT", "serving")
    monkeypatch.setenv("HOPSABLE_EMBEDDING_MODEL", MODEL)
    for name in (
        "DEPLOYMENT",
        "BUILD_DEPLOYMENT",
        "QUERY_DEADLINE_S",
        "BUILD_DEADLINE_S",
    ):
        monkeypatch.delenv(f"HOPSABLE_EMBEDDING_{name}", raising=False)
    clock = Clock()
    monkeypatch.setattr(embed.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(embed.time, "sleep", clock.sleep)
    monkeypatch.setattr(base.time, "monotonic", clock.monotonic)

    client = object.__new__(base.Client)
    client._base_url = "https://ingress"
    client._auth = None
    client._verify = False
    client._session = requests.Session()
    client._retry_window = 600.0
    client._connected = True
    sent: list[tuple[str, dict]] = []
    script: list = []
    wire = SimpleNamespace(
        sent=sent, script=script, clock=clock, client=client, attempt_s=0.0
    )

    def send(prepped, **kwargs):
        sent.append((prepped.url, json.loads(prepped.body)))
        timeout = kwargs["timeout"]
        read_timeout = (
            timeout[1] if isinstance(timeout, tuple) else timeout.read_timeout
        )
        if wire.attempt_s > read_timeout:
            clock.now += read_timeout
            raise requests.exceptions.ReadTimeout("read timed out")
        clock.now += wire.attempt_s
        answer = script.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer(prepped) if callable(answer) else answer

    client._session.send = send
    wire.platform = []
    wire.lookups = []
    route = mock.MagicMock()

    def lookup(method, path, **kwargs):
        wire.lookups.append(path)
        if path == ["serving", "platform"]:
            if isinstance(wire.platform, Exception):
                raise wire.platform
            return wire.platform
        return {"projectId": 1, "projectName": "serving", "backingName": "back"}

    route._send_request.side_effect = lookup
    with (
        mock.patch("hopsworks_common.client._get_instance", return_value=route),
        mock.patch("hopsworks_common.client.istio._get_instance", return_value=client),
    ):
        yield wire


def test_a_query_goes_to_the_query_deployment_in_batches_of_eight(wire):
    wire.script.append(_ok())
    assert embed.embed_query("q") == [0.0]
    assert wire.sent[0][0].endswith("/embedder:predict")
    assert embed.QUERY_BATCH == 8


def test_a_build_goes_to_the_build_deployment_in_batches_of_64(wire):
    wire.script += [_ok(64), _ok(64), _ok(2)]
    vectors = embed.embed_texts([str(i) for i in range(130)])
    assert len(vectors) == 130
    assert [len(body["instances"][0]["input"]) for _, body in wire.sent] == [64, 64, 2]
    assert all(url.endswith("/embedderbuild:predict") for url, _ in wire.sent)


def test_the_deployments_follow_their_environment(wire, monkeypatch):
    monkeypatch.setenv("HOPSABLE_EMBEDDING_DEPLOYMENT", "q2")
    monkeypatch.setenv("HOPSABLE_EMBEDDING_BUILD_DEPLOYMENT", "b2")
    wire.script += [_ok(), _ok()]
    embed.embed_query("q")
    embed.embed_texts(["t"])
    assert [u.rsplit("/", 1)[1] for u, _ in wire.sent] == ["q2:predict", "b2:predict"]


def test_another_model_in_the_answer_raises(wire):
    wire.script.append(_ok(model="other"))
    with pytest.raises(ValueError, match="reports model 'other'"):
        embed.embed_query("q")


def test_a_429_is_retried_after_retry_after_and_then_succeeds(wire):
    wire.script += [_response(429, headers={"Retry-After": "3"}), _ok()]
    assert embed.embed_query("q") == [0.0]
    assert len(wire.sent) == 2
    assert wire.clock.slept == [3.0]


def test_backoff_grows_when_retry_after_is_shorter(wire):
    wire.script += [_response(429, headers={"Retry-After": "0"})] * 3 + [_ok()]
    embed.embed_query("q")
    assert wire.clock.slept == [0.5, 1.0, 2.0]


def test_the_deadline_counts_the_time_an_attempt_takes(wire):
    wire.attempt_s = 12.0
    wire.script += [_response(429, headers={"Retry-After": "1"})] * 5
    with pytest.raises(
        embed.EmbedError, match=r"'embedder'.*within the deadline of 30 s"
    ):
        embed.embed_query("q")
    # 12 s + 1 s wait + 12 s + 1 s wait leave 4 s: the third attempt is cut at the deadline.
    assert len(wire.sent) == 3
    assert wire.clock.now - 1000.0 == 30.0


def test_an_error_answer_at_the_deadline_is_an_embed_error(wire):
    # The transport turns a non-2xx answer at or past the deadline into a plain Timeout.
    wire.attempt_s = 30.0
    wire.script += [_response(429, headers={"Retry-After": "1"})]
    with pytest.raises(
        embed.EmbedError, match=r"'embedder'.*within the deadline of 30 s"
    ):
        embed.embed_query("q")
    assert len(wire.sent) == 1


def test_a_build_call_has_its_own_longer_deadline(wire, monkeypatch):
    monkeypatch.setenv("HOPSABLE_EMBEDDING_BUILD_DEADLINE_S", "5")
    wire.attempt_s = 3.0
    wire.script += [_response(429, headers={"Retry-After": "1"})] * 5
    with pytest.raises(
        embed.EmbedError, match=r"'embedderbuild'.*within the deadline of 5 s"
    ):
        embed.embed_texts(["t"])
    assert len(wire.sent) == 2
    assert wire.clock.now - 1000.0 == 5.0
    assert embed.BUILD_DEADLINE_S == 600.0 and embed.QUERY_DEADLINE_S == 30.0


def test_a_busy_deployment_past_the_deadline_names_the_last_status(wire):
    wire.script += [_response(429, headers={"Retry-After": "20"})] * 3
    with pytest.raises(embed.EmbedError, match=r"'embedder'.*30 s.*last HTTP 429"):
        embed.embed_query("q")
    assert len(wire.sent) == 2


def test_a_500_is_not_retried(wire):
    wire.script.append(_response(500, {"error": "bad"}))
    with pytest.raises(embed.EmbedError, match=r"'embedder'.*HTTP 500"):
        embed.embed_query("q")
    assert len(wire.sent) == 1 and wire.clock.slept == []


@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_gateway_error_is_not_retried_by_the_transport_or_by_us(wire, status):
    wire.script.append(_response(status))
    with pytest.raises(embed.EmbedError):
        embed.embed_query("q")
    assert len(wire.sent) == 1


def test_a_connection_that_never_sent_is_retried(wire):
    import urllib3

    refused = requests.exceptions.ConnectionError(
        mock.Mock(reason=urllib3.exceptions.NewConnectionError(None, "refused"))
    )
    wire.script += [refused, _ok()]
    assert embed.embed_query("q") == [0.0]
    assert len(wire.sent) == 2


def test_a_connection_dropped_after_sending_is_not_retried(wire):
    wire.script.append(requests.exceptions.ConnectionError("reset by peer"))
    with pytest.raises(embed.EmbedError, match="after the request went out"):
        embed.embed_query("q")
    assert len(wire.sent) == 1


def test_one_call_of_ours_is_one_attempt_at_the_session(wire):
    adapter = wire.client._session.get_adapter("https://ingress/")
    assert adapter.max_retries.total == 0
    wire.script.append(_ok())
    embed.embed_query("q")
    assert len(wire.sent) == 1


def _platform(name="embedder", project="serving", namespace="platns", pid=7, sid=99):
    return {
        "id": sid, "name": name, "projectId": pid, "projectName": project,
        "namespace": namespace, "modelName": MODEL, "modelVersion": 1,
    }  # fmt: skip


def test_a_platform_deployment_is_called_by_its_namespace_and_never_looked_up_by_name(
    wire,
):
    wire.platform = [_platform()]
    wire.script.append(_ok())
    assert embed.embed_query("q") == [0.0]
    assert wire.sent[0][0].endswith("/v1/platns/embedder/v1/models/embedder:predict")
    assert wire.lookups == [["serving", "platform"]]


def test_outside_the_cluster_a_platform_deployment_goes_to_its_platform_route(wire):
    wire.platform = [_platform()]
    calls = []

    def post(method, path, **kwargs):
        calls.append(path)
        return {
            "predictions": [
                {"model": MODEL, "data": [{"index": 0, "embedding": [1.0]}]}
            ]
        }

    rest = mock.MagicMock()
    rest._send_request.side_effect = lambda method, path, **kw: (
        wire.platform if path == ["serving", "platform"] else post(method, path, **kw)
    )
    with (
        mock.patch("hopsworks_common.client._get_instance", return_value=rest),
        mock.patch("hopsworks_common.client.istio._get_instance", return_value=None),
    ):
        assert embed.embed_query("q") == [1.0]
    assert calls == [["serving", "platform", "99:predict"]]


def test_the_project_matches_a_platform_deployment_by_name_namespace_or_id(
    wire, monkeypatch
):
    for configured in ("serving", "platns", "7"):
        embed._routes.clear()
        wire.lookups.clear()
        monkeypatch.setenv("HOPSABLE_EMBEDDING_PROJECT", configured)
        wire.platform = [_platform()]
        wire.script.append(_ok())
        embed.embed_query("q")
        assert wire.lookups == [["serving", "platform"]], configured


def test_a_platform_deployment_of_another_project_falls_back_to_the_member_route(wire):
    wire.platform = [_platform(project="other", namespace="otherns", pid=8)]
    wire.script.append(_ok())
    embed.embed_query("q")
    assert wire.sent[0][0].endswith("/v1/back/embedder/v1/models/embedder:predict")
    assert wire.lookups == [
        ["serving", "platform"],
        ["project", "getProjectInfo", "serving"],
    ]


def test_two_matching_platform_deployments_are_refused_naming_the_ids(wire):
    wire.platform = [_platform(pid=7), _platform(namespace="n2", pid=8, sid=100)]
    with pytest.raises(embed.EmbedError, match="7, 8"):
        embed.embed_query("q")
    assert wire.sent == []


def test_a_backend_without_platform_deployments_uses_the_member_route(wire):
    from hopsworks_common.client.exceptions import RestAPIError

    wire.platform = RestAPIError(
        "https://ingress/serving/platform", _response(404, {"errorCode": 0})
    )
    wire.script.append(_ok())
    embed.embed_query("q")
    assert wire.sent[0][0].endswith("/v1/back/embedder/v1/models/embedder:predict")


def test_another_failure_of_the_platform_list_is_not_hidden(wire):
    from hopsworks_common.client.exceptions import RestAPIError

    wire.platform = RestAPIError(
        "https://ingress/serving/platform", _response(500, {"errorCode": 1})
    )
    with pytest.raises(embed.EmbedError, match="HTTP 500"):
        embed.embed_query("q")
    assert wire.sent == []
    assert wire.lookups == [["serving", "platform"]]
