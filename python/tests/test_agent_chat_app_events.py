"""An agent's chat app is made by the server, so the SDK writes its hops line itself."""

from __future__ import annotations

import json
import types

from hopsworks_agents.eval.sdk.agent import Agent


class _Http:
    def __init__(self):
        self.posts = []

    def post(self, path, json=None):
        self.posts.append(path)
        return {"name": "rondocs_chat"}


def _agent(monkeypatch):
    http = _Http()
    agent = Agent(types.SimpleNamespace(http=http), 7, serving={"name": "rondocs"})
    monkeypatch.setattr(Agent, "_app", lambda self, name: name)
    return agent, http


def test_creating_the_chat_app_writes_an_app_create_line_with_its_name(
    tmp_path, monkeypatch
):
    events_file = tmp_path / "events.jsonl"
    monkeypatch.setenv("HOPS_EVENTS_FILE", str(events_file))
    agent, http = _agent(monkeypatch)

    assert agent.create_chat_app() == "rondocs_chat"

    assert http.posts == ["/serving/7/chatapp"]
    (line,) = [json.loads(x) for x in events_file.read_text().splitlines()]
    assert (line["kind"], line["op"], line["phase"]) == ("app", "create", "done")
    assert line["key"]["name"] == "rondocs_chat"


def test_customizing_the_chat_app_writes_an_app_update_line(tmp_path, monkeypatch):
    events_file = tmp_path / "events.jsonl"
    monkeypatch.setenv("HOPS_EVENTS_FILE", str(events_file))
    agent, _ = _agent(monkeypatch)

    agent.customize_chat_app()

    (line,) = [json.loads(x) for x in events_file.read_text().splitlines()]
    assert (line["kind"], line["op"], line["key"]["name"]) == (
        "app",
        "update",
        "rondocs_chat",
    )
