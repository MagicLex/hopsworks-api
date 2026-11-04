"""``hops fg embed``: stable chunks, the run order's decisions, and the command surface.

The run is driven against in-memory stand-ins for HopsFS and the two stores
that record the order of every call; the real-store acceptance (kills between
the steps, read-back on the vector database) runs on a cluster.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest import mock

import pandas as pd
import pytest
from click.testing import CliRunner
from hopsworks.cli import embed_run
from hopsworks.cli.main import cli


# --- chunking ----------------------------------------------------------------


def test_chunks_are_a_pure_function_of_the_text():
    text = "".join(chr(97 + i % 26) for i in range(5000))
    assert embed_run.chunks(text, 2000, 200) == embed_run.chunks(text, 2000, 200)
    parts = embed_run.chunks(text, 2000, 200)
    assert [len(p) for p in parts] == [2000, 2000, 1400]
    assert parts[1][:200] == parts[0][-200:]
    assert "".join([parts[0], *(p[200:] for p in parts[1:])]) == text


def test_appending_text_keeps_the_earlier_chunks_and_their_numbers():
    text = "x" * 1000 + "y" * 3000
    before = embed_run.chunks(text, 2000, 200)
    after = embed_run.chunks(text + "z" * 500, 2000, 200)
    assert after[: len(before) - 1] == before[:-1]


@pytest.mark.parametrize("value", [None, "", "   ", float("nan")])
def test_empty_text_has_no_chunks(value):
    assert embed_run.chunks(value) == []


def test_a_composite_key_is_one_stable_id():
    assert embed_run.doc_id([7]) == "7"
    assert embed_run.doc_id(["a", 1]) == '["a", "1"]'


# --- run order ---------------------------------------------------------------


class Files:
    """HopsFS as `DatasetApi` serves it: paths to bytes, a move that refuses an existing target."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = set()

    def exists(self, path):
        return path in self.files or path in self.dirs

    def mkdir(self, path):
        self.dirs.add(path)

    def upload(self, local, folder, overwrite=False):
        with open(local, "rb") as f:
            self.files[f"{folder}/{os.path.basename(local)}"] = f.read()

    def move(self, source, target):
        if self.exists(target):
            raise RuntimeError(f"{target} already exists")
        self.files[target] = self.files.pop(source)

    def remove(self, path):
        self.files.pop(path, None)
        self.dirs.discard(path)

    def read_content(self, path):
        return SimpleNamespace(content=self.files[path])

    def put(self, path, body):
        self.files[path] = json.dumps(body).encode()

    def json(self, path):
        return json.loads(self.files[path])


class Stores:
    """One group's offline and online rows; every call lands in `log`."""

    def __init__(self, log, fail_online_remove=False, online_lag=False):
        self.off: dict = {}
        self.on: set = set()
        self.log = log
        self.fail_online_remove = fail_online_remove
        self.online_lag = online_lag

    def offline(self):
        return dict(self.off)

    def online_present(self, keys):
        return {k for k in keys if k in self.on}

    def remove(self, keys, storage, timeout_s):
        self.log.append(("remove", storage, sorted(keys)))
        if storage == "online" and self.fail_online_remove:
            raise RuntimeError("killed between the legs")
        for k in keys:
            if storage == "offline":
                self.off.pop(k, None)
            elif not self.online_lag:
                self.on.discard(k)

    def upsert(self, rows):
        self.log.append(("upsert", sorted((r["doc_id"], r["chunk_ix"]) for r in rows)))
        for r in rows:
            key = (r["doc_id"], r["chunk_ix"])
            self.off[key] = (r["content_sha"], r["embedding"], r["embedded_at"])
            self.on.add(key)


class World:
    """A source group, an embedding group's versions, HopsFS and the endpoint."""

    MODEL = "bge_small_en_v1_5"

    def __init__(self, rows):
        self.log: list = []
        self.files = Files()
        self.stores: dict[int, Stores] = {}
        self.groups: list = []
        self.embedded: list[str] = []
        self.source = mock.MagicMock()
        self.source.name, self.source.version = "notes", 1
        self.source.primary_key = ["id"]
        self.set_source(rows)
        self.fs = mock.MagicMock()
        self.fs.get_feature_groups.side_effect = lambda name: list(self.groups)
        self.fs.create_feature_group.side_effect = self._create

    def set_source(self, rows):
        frame = pd.DataFrame(rows, columns=["id", "body"])
        self.source.select.return_value.read.return_value = frame

    def _create(self, **kw):
        fg = SimpleNamespace(
            name=kw["name"], version=kw["version"], description=kw["description"]
        )
        fg.save = lambda: None
        self.groups.append(fg)
        self.log.append(("create", kw["version"]))
        return fg

    def stores_for(self, fg):
        return self.stores.setdefault(fg.version, Stores(self.log))

    def embed(self, texts):
        self.embedded.extend(texts)
        return [[float(len(t)), 1.0, 0.0] for t in texts]

    def run(self, run_id="r1", **kw):
        state = embed_run.State(self.files, "notes_body_emb", run_id)
        args = {
            "fs": self.fs, "source": self.source, "text": "body", "group": "notes_body_emb",
            "model": self.MODEL, "embed": self.embed, "state": state,
            "stores_for": self.stores_for, "size": 10, "overlap": 2, "readback_s": 30,
            "now": lambda: 1000.0, "clock": lambda: 0.0, "sleep": lambda s: None,
        }  # fmt: skip
        args.update(kw)
        return embed_run.run(**args)

    def pending(self, version=1):
        return f"{embed_run.STATE_DIR}/notes_body_emb_v{version}/pending-delete.json"

    def lease(self):
        return f"{embed_run.STATE_DIR}/notes_body_emb.lease.json"


def test_a_first_run_creates_the_group_and_writes_every_chunk():
    w = World([[1, "short"], [2, "a longer body text"]])
    plan = w.run()
    assert plan.created and plan.version == 1
    assert sorted(w.stores[1].off) == [("1", 0), ("2", 0), ("2", 1)]
    assert w.stores[1].on == set(w.stores[1].off)
    assert "(bge_small_en_v1_5, 3d, cosine) hopsable.embed.owner=notes_body_emb" in (
        w.groups[0].description
    )
    assert not w.files.exists(w.lease()) and not w.files.exists(w.pending())


def test_a_rerun_with_no_change_calls_the_endpoint_only_for_the_dimension():
    w = World([[1, "short"], [2, "a longer body text"]])
    w.run()
    w.embedded.clear()
    plan = w.run("r2")
    assert plan.embedded == 0 and plan.removed == 0 and not plan.created
    assert w.embedded == ["short"]  # the dimension probe
    assert plan.upserted == 0


def test_an_unchanged_chunk_missing_online_is_written_again():
    w = World([[1, "short"], [2, "a longer body text"]])
    w.run()
    w.stores[1].on.discard(("1", 0))
    plan = w.run("r2")
    assert plan.embedded == 0 and plan.upserted == 1
    assert w.stores[1].on == set(w.stores[1].off)


def test_shortened_and_deleted_rows_are_removed_after_the_upsert_from_both_stores():
    w = World([[1, "a longer body text"], [2, "gone soon"]])
    w.run()
    w.set_source([[1, "a longer"]])
    w.log.clear()
    plan = w.run("r2")
    assert plan.removed == 2
    kinds = [e[0] if e[0] != "remove" else f"remove-{e[1]}" for e in w.log]
    assert kinds == ["upsert", "remove-offline", "remove-online"]
    assert sorted(w.stores[1].off) == [("1", 0)] and w.stores[1].on == {("1", 0)}
    assert not w.files.exists(w.pending())


def test_the_pending_file_holds_the_deletes_before_any_write():
    w = World([[1, "a longer body text"]])
    w.run()
    w.set_source([[1, "a longer"]])
    seen = []
    upsert = w.stores[1].upsert

    def watch(rows):
        seen.append(w.files.json(w.pending())["keys"])
        upsert(rows)

    w.stores[1].upsert = watch
    w.run("r2")
    assert seen == [[["1", 1]]]


def test_a_failed_source_read_writes_and_deletes_nothing():
    w = World([[1, "a longer body text"]])
    w.run()
    w.source.select.return_value.read.side_effect = RuntimeError("Trino down")
    w.log.clear()
    with pytest.raises(embed_run.EmbedError, match="Nothing was written or deleted"):
        w.run("r2")
    assert w.log == []
    assert len(w.stores[1].off) == 2
    assert not w.files.exists(w.pending()) and not w.files.exists(w.lease())


def test_a_kill_between_the_delete_legs_is_settled_first_by_the_next_run():
    w = World([[1, "a longer body text"], [2, "gone soon"]])
    w.run()
    w.set_source([[1, "a longer body text"]])
    w.stores[1].fail_online_remove = True
    with pytest.raises(RuntimeError):
        w.run("r2")
    assert w.files.json(w.pending())["keys"] == [["2", 0]]
    assert ("2", 0) not in w.stores[1].off and ("2", 0) in w.stores[1].on

    # The row comes back before the next run: it is settled away, then written again.
    w.set_source([[1, "a longer body text"], [2, "gone soon"]])
    w.stores[1].fail_online_remove = False
    w.log.clear()
    plan = w.run("r3")
    assert plan.settled == {1: 1}
    assert w.log[:2] == [
        ("remove", "offline", [("2", 0)]),
        ("remove", "online", [("2", 0)]),
    ]
    assert w.log[2][0] == "upsert" and ("2", 0) in w.log[2][1]
    assert ("2", 0) in w.stores[1].off and ("2", 0) in w.stores[1].on
    assert not w.files.exists(w.pending())


def test_a_run_killed_after_its_settle_leaves_nothing_to_lose_track_of():
    w = World([[1, "a longer body text"], [2, "gone soon"]])
    w.run()
    w.set_source([[1, "a longer body text"]])
    w.stores[1].fail_online_remove = True
    with pytest.raises(RuntimeError):
        w.run("A")
    # K returns; B settles K away and dies before its upsert.
    w.set_source([[1, "a longer body text"], [2, "gone soon"]])
    w.stores[1].fail_online_remove = False
    with (
        mock.patch.object(w, "embed", side_effect=RuntimeError("killed")),
        pytest.raises(RuntimeError),
    ):
        w.run("B")
    assert not w.files.exists(w.pending())
    assert ("2", 0) not in w.stores[1].off and ("2", 0) not in w.stores[1].on
    # K leaves the source again; C converges with no residual vector.
    w.set_source([[1, "a longer body text"]])
    w.run("C")
    assert ("2", 0) not in w.stores[1].on and ("2", 0) not in w.stores[1].off
    assert not w.files.exists(w.pending())


def test_a_settle_that_does_not_converge_keeps_the_pending_file():
    w = World([[1, "a longer body text"]])
    w.run()
    w.files.put(w.pending(), {"run_id": "old", "keys": [["1", 0]]})
    w.stores[1].online_lag = True
    ticks = iter(range(0, 1000, 10))
    w.log.clear()
    with pytest.raises(embed_run.EmbedError, match="pending-delete file is kept"):
        w.run("r2", clock=lambda: float(next(ticks)))
    assert w.files.exists(w.pending())
    assert [e[0] for e in w.log] == ["remove", "remove"]


def test_a_held_lease_refuses_the_run_and_changes_nothing():
    w = World([[1, "short"]])
    w.files.put(w.lease(), {"run_id": "other", "started_at": 900.0})
    with pytest.raises(embed_run.EmbedError, match="holds its lease"):
        w.run()
    assert w.log == [] and w.files.json(w.lease())["run_id"] == "other"


def test_a_lease_older_than_the_timeout_is_taken_over():
    w = World([[1, "short"]])
    w.files.put(w.lease(), {"run_id": "dead", "started_at": 0.0})
    plan = w.run(lease_s=500)
    assert plan.upserted == 1 and not w.files.exists(w.lease())


def test_another_model_makes_a_new_version():
    w = World([[1, "short"]])
    w.run()
    plan = w.run("r2", model="other_model")
    assert plan.created and plan.version == 2


def test_a_group_not_made_by_embed_is_refused():
    w = World([[1, "short"]])
    w.groups.append(
        SimpleNamespace(name="notes_body_emb", version=1, description="mine")
    )
    with pytest.raises(embed_run.EmbedError, match="not made by hops fg embed"):
        w.run()
    assert w.log == []


def test_the_upsert_frame_matches_the_group_schema():
    from datetime import datetime, timezone

    from hsfs.engine import python

    row = {
        "doc_id": "1",
        "chunk_ix": 0,
        "text": "a",
        "embedding": [0.1, 0.2],
        "content_sha": "x",
        "embedded_at": datetime.now(timezone.utc),
    }
    features = python.Engine()._parse_schema_feature_group(
        embed_run.upsert_frame([row]), "HUDI"
    )
    assert {f.name: f.type for f in features} == {
        "doc_id": "string",
        "chunk_ix": "bigint",
        "text": "string",
        "embedding": "array<float>",
        "content_sha": "string",
        "embedded_at": "timestamp",
    }


# --- command surface ---------------------------------------------------------


def test_embed_help_names_the_command_without_the_hidden_verb():
    result = CliRunner().invoke(cli, ["fg", "embed", "-h"])
    assert result.exit_code == 0
    assert result.output.startswith("Usage: hops fg embed [OPTIONS] SOURCE")
    assert "HOPSABLE_EMBEDDING_PROJECT" in result.output
    delete = CliRunner().invoke(cli, ["fg", "embed", "delete", "-h"])
    assert delete.output.startswith("Usage: hops fg embed delete [OPTIONS] GROUP")


def test_embed_without_an_endpoint_names_the_settings(mock_project, monkeypatch):
    for name in ("PROJECT", "DEPLOYMENT", "MODEL"):
        monkeypatch.delenv(f"HOPSABLE_EMBEDDING_{name}", raising=False)
    result = CliRunner().invoke(cli, ["fg", "embed", "notes", "--text", "body"])
    assert result.exit_code == 2
    assert "HOPSABLE_EMBEDDING_PROJECT" in result.output


def _owned_fg(version, owner="notes_body_emb"):
    fg = mock.MagicMock()
    fg.name, fg.version = "notes_body_emb", version
    fg.description = (
        f"Embeddings of notes v1 body (m, 3d, cosine) hopsable.embed.owner={owner}"
    )
    return fg


@pytest.fixture
def embedded(mock_project):
    fs = mock_project.get_feature_store.return_value
    groups = [_owned_fg(1), _owned_fg(2)]
    fs.get_feature_groups.side_effect = lambda name: groups
    job = mock.MagicMock()
    job.name = "notes_body_emb_embed"
    job.description = (
        "Embeds notes v1 body into notes_body_emb. hopsable.embed.owner=notes_body_emb"
    )
    mock_project.get_job_api.return_value.get_job.return_value = job
    mock_project.get_model_serving.return_value.get_deployments.return_value = []
    files = Files()
    mock_project.get_dataset_api.return_value = files
    return SimpleNamespace(groups=groups, job=job, files=files, project=mock_project)


def _agent(name, description="", script=None):
    d = mock.MagicMock()
    d.name, d.has_model, d.description = name, False, description
    d.predictor.script_file = script
    return d


def test_delete_is_refused_while_an_agent_names_the_group(embedded):
    serving = embedded.project.get_model_serving.return_value
    serving.get_deployments.return_value = [
        _agent("bot1", "Searches notes_body_emb. hopsable.embed.search=notes_body_emb"),
        _agent("bot2", "hopsable.embed.search=notes_body_emb_old"),
    ]
    result = CliRunner().invoke(
        cli, ["fg", "embed", "delete", "notes_body_emb", "--yes"]
    )
    assert result.exit_code == 1
    assert "Agents bot1 search 'notes_body_emb'" in result.output
    embedded.job.delete.assert_not_called()
    assert not any(fg.delete.called for fg in embedded.groups)


def test_delete_is_refused_while_an_agent_script_quotes_the_group(embedded):
    embedded.files.files["Resources/agents/bot3/agent.py"] = (
        b'FG_SEARCH = fs.get_feature_group("notes_body_emb", version=1)'
    )
    serving = embedded.project.get_model_serving.return_value
    serving.get_deployments.return_value = [
        _agent("bot3", script="Resources/agents/bot3/agent.py")
    ]
    result = CliRunner().invoke(
        cli, ["fg", "embed", "delete", "notes_body_emb", "--yes"]
    )
    assert result.exit_code == 1 and "bot3" in result.output


def test_delete_dry_run_lists_and_deletes_nothing(embedded):
    result = CliRunner().invoke(
        cli, ["fg", "embed", "delete", "notes_body_emb", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert (
        "versions [1, 2]" in result.output and "notes_body_emb_embed" in result.output
    )
    embedded.job.delete.assert_not_called()


def test_delete_removes_only_what_embed_made(embedded):
    embedded.groups.append(_owned_fg(3, owner="someone_else"))
    embedded.files.dirs.add(f"{embed_run.STATE_DIR}/notes_body_emb_v1")
    embedded.files.files[f"{embed_run.STATE_DIR}/notes_body_emb_embed_job.py"] = b""
    embedded.files.files[f"{embed_run.STATE_DIR}/other_body_emb_embed_job.py"] = b""
    result = CliRunner().invoke(
        cli, ["fg", "embed", "delete", "notes_body_emb", "--yes"]
    )
    assert result.exit_code == 0, result.output
    embedded.job.unschedule.assert_called_once()
    embedded.job.delete.assert_called_once()
    assert [fg.delete.called for fg in embedded.groups] == [True, True, False]
    assert not embedded.files.dirs
    assert list(embedded.files.files) == [
        f"{embed_run.STATE_DIR}/other_body_emb_embed_job.py"
    ]


def test_delete_is_refused_while_a_run_holds_the_lease(embedded):
    import time

    embedded.files.put(
        f"{embed_run.STATE_DIR}/notes_body_emb.lease.json",
        {"run_id": "r9", "started_at": time.time()},
    )
    result = CliRunner().invoke(
        cli, ["fg", "embed", "delete", "notes_body_emb", "--yes"]
    )
    assert result.exit_code == 1 and "holds its lease" in result.output
    embedded.job.delete.assert_not_called()


def test_create_with_an_embedding_builds_the_index(mock_project):
    fs = mock_project.get_feature_store.return_value
    result = CliRunner().invoke(
        cli,
        ["fg", "create", "docs", "--primary-key", "id", "--features", "id:bigint",
         "--embedding", "vec:3"],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    index = fs.create_feature_group.call_args.kwargs["embedding_index"]
    (vec,) = list(index.get_embeddings())
    assert (vec.name, vec.dimension, vec.similarity_function_type) == (
        "vec",
        3,
        "cosine",
    )


def test_agent_init_search_hint_records_the_group(mock_project, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    docs = mock.MagicMock()
    docs.name, docs.version, docs.description = "notes_body_emb", 1, ""
    docs.features = [SimpleNamespace(name="doc_id", type="string")]
    docs.primary_key, docs.online_enabled = ["doc_id"], True
    docs.embedding_index.get_embeddings.return_value = [
        SimpleNamespace(name="embedding")
    ]
    docs.select_all.return_value.show.return_value = pd.DataFrame({"doc_id": ["a"]})
    mock_project.get_feature_store.return_value.get_feature_group.return_value = docs
    result = CliRunner().invoke(
        cli,
        [
            "agent",
            "init",
            "bot1",
            "--reads",
            "notes_body_emb",
            "--search",
            "notes_body_emb",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "hopsable.embed.search=notes_body_emb" in result.output


# --- the embedding client ----------------------------------------------------


def test_the_client_pairs_each_vector_with_its_text():
    from hopsworks_agents import embed

    answer = {"predictions": [{"data": [
        {"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]},
    ]}]}  # fmt: skip
    assert embed._vectors(answer, 2) == [[1.0], [2.0]]
    with pytest.raises(ValueError, match="1 vectors for 2 texts"):
        embed._vectors({"predictions": [{"data": [{"embedding": [1.0]}]}]}, 2)


def test_the_client_needs_the_three_settings(monkeypatch):
    from hopsworks_agents import embed

    monkeypatch.setenv("HOPSABLE_EMBEDDING_PROJECT", "hopsable_embeddings")
    monkeypatch.delenv("HOPSABLE_EMBEDDING_DEPLOYMENT", raising=False)
    monkeypatch.delenv("HOPSABLE_EMBEDDING_MODEL", raising=False)
    with pytest.raises(
        ValueError, match="HOPSABLE_EMBEDDING_DEPLOYMENT, HOPSABLE_EMBEDDING_MODEL"
    ):
        embed.endpoint()
    assert embed.endpoint(deployment="embedder", model="m") == (
        "hopsable_embeddings",
        "embedder",
        "m",
    )


def test_the_client_posts_to_the_serving_project_by_its_backing_name(monkeypatch):
    from hopsworks_agents import embed

    monkeypatch.setattr(embed, "_routes", {})
    rest, istio = mock.MagicMock(), mock.MagicMock()
    rest._send_request.return_value = {
        "projectId": 21623, "projectName": "hopsable_embeddings", "backingName": "5okprgv34044",
    }  # fmt: skip
    istio._send_request.return_value = {
        "predictions": [{"data": [{"embedding": [0.5]}]}]
    }
    with (
        mock.patch("hopsworks_common.client._get_instance", return_value=rest),
        mock.patch("hopsworks_common.client.istio._get_instance", return_value=istio),
    ):
        vectors = embed.embed_texts(
            ["hi"], project="hopsable_embeddings", deployment="embedder", model="bge"
        )
    assert vectors == [[0.5]]
    call = istio._send_request.call_args
    assert call.args[1] == [
        "v1",
        "5okprgv34044",
        "embedder",
        "v1",
        "models",
        "embedder:predict",
    ]
    assert json.loads(call.kwargs["data"]) == {
        "instances": [{"model": "bge", "input": ["hi"]}]
    }
    assert call.kwargs["with_base_path_params"] is False
