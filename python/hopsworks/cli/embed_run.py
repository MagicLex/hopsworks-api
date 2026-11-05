"""One run of ``hops fg embed``: chunk a text column, embed it, converge the embedding group.

The group is keyed by ``(doc_id, chunk_ix)``; ``doc_id`` is the source row's
primary key. A run converges the group onto the source in this order, so a
run killed at any point leaves a state the next run finishes:

Input limits come first. Before anything is written or deleted, the run reads
the pending-delete files, the source (streamed from one pinned commit, stopped
as soon as it passes the maximum chunks or bytes) and the vectors the group
holds (stopped at the maximum). Input over the maximum stops the run with
nothing written: there are no partial runs.

0. Settle: each version with a pending-delete file has its keys removed,
   offline then online, read back absent from both, and the file deleted.
1. The desired keys D are those of the full source read above. A failed read
   stops the run before anything is written or deleted.
2. X = the keys the group holds now minus D; X is written to the pending file.
3. Upsert the changed chunks of D and any unchanged one the online store
   lacks; an unchanged chunk keeps its vector and is not sent to the endpoint.
4. Remove X, offline then online.
5. Read back X absent from both stores and D present online, then delete the
   pending file.

One run per group at a time: an exclusive lease file, taken over once older
than the lease timeout.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable


CHUNK_CHARS = 2000
CHUNK_OVERLAP = 200
#: Seconds a read-back waits for the online store to show a write or a delete:
#: 30x the worst lag seen on the dev cluster (1.8 s, 2026-11-04, light load).
READBACK_S = 60
#: Seconds after which a lease is taken over: longer than any run, shorter than a schedule gap worth noticing.
LEASE_S = 6 * 3600
#: Keys per online read-back request.
READ_BATCH = 200
POLL_S = 5
#: The supported input of one group, unless the Hopsworks variables of
#: embed_limits() grant more: chunks (also the most source rows, pending deletes
#: and held vectors a run reads) and bytes of source text.
MAX_CHUNKS = 50_000
MAX_BYTES = 100_000_000
#: Source rows per Arrow batch of the source read.
SOURCE_BATCH = 1_000
#: Hopsworks variables an admin sets; each is a positive integer, absent for the default.
VAR_MAX_CHUNKS = "platform_jobs_embed_max_chunks"
VAR_MAX_BYTES = "platform_jobs_embed_max_source_bytes"
VAR_MIN_INTERVAL = "platform_jobs_embed_min_interval_minutes"

STATE_DIR = "Resources/hopsable-embed"
OWNER = "hopsable.embed.owner="
SEARCH = "hopsable.embed.search="
KEYS = ["doc_id", "chunk_ix"]

_DESCRIPTION = re.compile(
    r"\((?P<model>[^,()]+), (?P<dimension>\d+)d, cosine\) "
    + re.escape(OWNER)
    + r"(?P<owner>\S+)$"
)


class EmbedError(Exception):
    """The run stopped; the message says what was and was not changed."""


def variable_int(name: str, default: int) -> int:
    """A positive-integer Hopsworks variable; `default` when it is not set."""
    from hopsworks.cli import versions  # noqa: PLC0415
    from hopsworks_common.core.variable_api import VariableApi  # noqa: PLC0415

    try:
        raw = VariableApi()._get_variable(name)
    except Exception as exc:  # noqa: BLE001
        if versions.not_found(exc):
            return default
        raise EmbedError(
            f"Could not read the Hopsworks variable {name}: {exc}"
        ) from exc
    try:
        value = int(str(raw).strip())
    except ValueError:
        value = 0
    if value < 1:
        raise EmbedError(
            f"The Hopsworks variable {name} is '{raw}', not a positive integer."
        )
    return value


def embed_limits() -> tuple[int, int]:
    """(maximum chunks, maximum source bytes) of one group on this cluster."""
    return variable_int(VAR_MAX_CHUNKS, MAX_CHUNKS), variable_int(
        VAR_MAX_BYTES, MAX_BYTES
    )


def chunks(
    text: Any, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP
) -> list[str]:
    """Windows of `size` characters, each starting `size - overlap` after the last.

    A pure function of the text, so unchanged text keeps its chunk numbers.
    Empty or missing text has no chunks.
    """
    if not 0 <= overlap < size:
        raise ValueError("The overlap must be at least 0 and less than the chunk size.")
    if text is None or (isinstance(text, float) and text != text):
        return []
    text = str(text)
    if not text.strip():
        return []
    step = size - overlap
    out = []
    for start in range(0, len(text), step):
        out.append(text[start : start + size])
        if start + size >= len(text):
            break
    return out


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def doc_id(values: list[Any]) -> str:
    """The primary key as one string; a composite key joins its parts as a JSON list."""
    if len(values) == 1:
        return str(values[0])
    return json.dumps([str(v) for v in values])


def read_source(
    source: Any,
    primary_key: list[str],
    text: str,
    size: int,
    overlap: int,
    max_chunks: int,
    max_bytes: int,
) -> dict:
    """(doc_id, chunk_ix) -> chunk text, for every row of the source, read once.

    The query streams in Arrow batches and the read stops at the first row that
    takes the input past `max_chunks` (rows count too: each is a document) or
    `max_bytes` of UTF-8 text, so what is held is never more than the maximum.
    """
    out: dict = {}
    rows = 0
    total = 0
    columns = [*primary_key, text]
    with source.select(columns).read_batches(batch_size=SOURCE_BATCH) as batches:
        for batch in batches:
            for row in batch.to_pylist():
                rows += 1
                value = row[text]
                if value is not None:
                    total += len(str(value).encode("utf-8"))
                if rows > max_chunks or total > max_bytes:
                    raise _over(source, max_chunks, max_bytes)
                doc = doc_id([row[k] for k in primary_key])
                for ix, chunk in enumerate(chunks(value, size, overlap)):
                    out[(doc, ix)] = chunk
                if len(out) > max_chunks:
                    raise _over(source, max_chunks, max_bytes)
    return out


def _over(source: Any, max_chunks: int, max_bytes: int) -> EmbedError:
    return EmbedError(
        f"Feature group '{source.name}' is over the supported input of {max_chunks} chunks "
        f"(rows count too) or {max_bytes} bytes of text. Nothing was written or deleted."
    )


def _pin(source: Any) -> str | None:
    """The source's latest commit id, or None when it keeps no commits."""
    if str(getattr(source, "time_travel_format", None)).upper() not in (
        "HUDI",
        "DELTA",
        "ICEBERG",
    ):
        return None
    try:
        commits = source.commit_details(limit=1)
    except Exception as exc:  # noqa: BLE001 - an unreadable timeline is not an unchanged one
        raise EmbedError(
            f"Could not read the commits of feature group '{source.name}': {exc}. Nothing was written."
        ) from exc
    return str(max(commits)) if commits else ""


def description(
    source: str, version: int, column: str, model: str, dimension: int, group: str
) -> str:
    return f"Embeddings of {source} v{version} {column} ({model}, {dimension}d, cosine) {OWNER}{group}"


def described(fg: Any) -> dict | None:
    """Model, dimension and owner from a group `hops fg embed` made; None for any other group."""
    found = _DESCRIPTION.search((getattr(fg, "description", "") or "").strip())
    if not found:
        return None
    return {
        "model": found.group("model"),
        "dimension": int(found.group("dimension")),
        "owner": found.group("owner"),
    }


@dataclass
class Plan:
    """What a run decided, for its report and for tests."""

    group: str = ""
    version: int | None = None
    settled: dict = field(default_factory=dict)
    desired: int = 0
    embedded: int = 0
    upserted: int = 0
    removed: int = 0
    created: bool = False


class State:
    """The lease and pending-delete files of one group on HopsFS.

    Every write is an upload under a run-unique name and a move onto the final
    name: a crash leaves either the old file or the new one, never half a file.
    """

    def __init__(self, dataset_api: Any, group: str, run_id: str):
        self.files = dataset_api
        self.group = group
        self.run_id = run_id
        self.lease = f"{STATE_DIR}/{group}.lease.json"

    def pending(self, version: int) -> str:
        return f"{STATE_DIR}/{self.group}_v{version}/pending-delete.json"

    def _read(self, path: str) -> dict | None:
        if not self.files.exists(path):
            return None
        answer = self.files.read_content(path)
        body = getattr(answer, "content", answer)
        return json.loads(body.decode("utf-8") if isinstance(body, bytes) else body)

    def _write(self, path: str, body: dict) -> None:
        from hopsworks.cli.commands.files import makedirs  # noqa: PLC0415

        folder, name = path.rsplit("/", 1)
        makedirs(self.files, folder)
        staged = f"{name}.{self.run_id}"
        with tempfile.TemporaryDirectory() as tmp:
            local = os.path.join(tmp, staged)
            with open(local, "w", encoding="utf-8") as f:
                json.dump(body, f)
            self.files.upload(local, folder, overwrite=True)
        try:
            self.files.move(f"{folder}/{staged}", path)
        finally:
            # A move that lost a race leaves the staged copy behind.
            if self.files.exists(f"{folder}/{staged}"):
                self.files.remove(f"{folder}/{staged}")

    def acquire(self, lease_s: int, now: float) -> None:
        held = self._read(self.lease)
        if held is not None:
            if now - held.get("started_at", 0) <= lease_s:
                raise EmbedError(
                    f"Another run of the embedding of '{self.group}' holds its lease "
                    f"(run {held.get('run_id')}, started {_iso(held.get('started_at'))}). "
                    f"Wait for it, or pass --lease-timeout to take over a lease older than that."
                )
            self.files.remove(self.lease)
        try:
            self._write(self.lease, {"run_id": self.run_id, "started_at": now})
        except Exception as exc:  # noqa: BLE001 - another run won the move
            raise EmbedError(
                f"Another run of the embedding of '{self.group}' took its lease first: {exc}"
            ) from exc
        self.verify()

    def verify(self) -> None:
        """Stop unless the lease still names this run; checked before every write."""
        held = self._read(self.lease)
        if not held or held.get("run_id") != self.run_id:
            raise EmbedError(
                f"The lease of '{self.group}' no longer names this run: another run took it over. Nothing more is written."
            )

    def release(self) -> None:
        held = self._read(self.lease)
        if held and held.get("run_id") == self.run_id:
            self.files.remove(self.lease)

    def read_pending(self, version: int) -> list[tuple[str, int]] | None:
        body = self._read(self.pending(version))
        if body is None:
            return None
        return [(str(d), int(i)) for d, i in body["keys"]]

    def write_pending(self, version: int, keys: list[tuple[str, int]]) -> None:
        if self.files.exists(self.pending(version)):
            raise EmbedError(
                f"A pending-delete file of '{self.group}' v{version} appeared during the run; nothing was deleted."
            )
        self._write(
            self.pending(version),
            {"run_id": self.run_id, "keys": [list(k) for k in keys]},
        )

    def remove_pending(self, version: int) -> None:
        self.files.remove(self.pending(version))


class Stores:
    """The offline and online legs of one embedding group, through the SDK."""

    def __init__(self, fg: Any):
        self.fg = fg

    def offline(self, limit: int | None = None) -> dict:
        """(doc_id, chunk_ix) -> (content_sha, embedding, embedded_at) of every offline row.

        Streamed; with `limit`, the read stops once the group holds more rows than that.
        """
        import numpy as np  # noqa: PLC0415

        if not self.fg.commit_details(limit=1):
            return {}
        out = {}
        columns = [*KEYS, "content_sha", "embedding", "embedded_at"]
        with self.fg.select(columns).read_batches() as batches:
            for batch in batches:
                for row in batch.to_pylist():
                    out[(str(row["doc_id"]), int(row["chunk_ix"]))] = (
                        row["content_sha"],
                        np.asarray(row["embedding"], dtype=np.float32),
                        row["embedded_at"],
                    )
                if limit is not None and len(out) > limit:
                    raise EmbedError(
                        f"The group '{self.fg.name}' holds more than {limit} vectors, "
                        "over the supported input. Nothing was written or deleted."
                    )
        return out

    def online_present(self, keys: list[tuple[str, int]]) -> set:
        from hsfs.core.vector_db_client import VectorDbClient  # noqa: PLC0415

        client = VectorDbClient(self.fg.select_all())
        found = set()
        for start in range(0, len(keys), READ_BATCH):
            batch = keys[start : start + READ_BATCH]
            results = client._read_many(
                self.fg.id,
                self.fg.features,
                [{"doc_id": d, "chunk_ix": i} for d, i in batch],
            )
            for key, rows in zip(batch, results, strict=True):
                # A match query can match more than the exact key; compare the values.
                if any(
                    (str(r.get("doc_id")), int(r.get("chunk_ix"))) == key for r in rows
                ):
                    found.add(key)
        return found

    def remove(self, keys: list[tuple[str, int]], storage: str, timeout_s: int) -> None:
        import pandas as pd  # noqa: PLC0415

        frame = pd.DataFrame(keys, columns=KEYS)
        self.fg.remove_rows(
            frame,
            write_options={
                "wait_for_online_ingestion": True,
                "online_ingestion_options": {"timeout": timeout_s},
            },
            storage=storage,
        )

    def upsert(self, rows: list[dict]) -> None:
        self.fg.insert(upsert_frame(rows), wait=True)


def upsert_frame(rows: list[dict]) -> Any:
    """The rows as a frame whose embeddings infer as array<float>, the group's type.

    A list of Python floats infers as array<double>, which the insert refuses.
    """
    import numpy as np  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415

    frame = pd.DataFrame(rows)
    frame["embedding"] = [np.asarray(v, dtype=np.float32) for v in frame["embedding"]]
    return frame


def _iso(epoch: Any) -> str:
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (TypeError, ValueError):
        return "?"


def wait_converged(
    stores: Stores,
    absent: list[tuple[str, int]],
    present: list[tuple[str, int]],
    timeout_s: int,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Poll until `absent` is in neither store and `present` is online, or raise past `timeout_s`."""
    deadline = clock() + timeout_s
    offline_clear = not absent
    while True:
        if not offline_clear:
            offline_clear = not (set(absent) & set(stores.offline()))
        online = stores.online_present(sorted(set(absent) | set(present)))
        left_absent = [k for k in absent if k in online]
        left_present = [k for k in present if k not in online]
        if offline_clear and not left_absent and not left_present:
            return
        if clock() >= deadline:
            raise EmbedError(
                f"After {timeout_s} s the stores have not converged: "
                f"{'some deleted keys are still offline; ' if not offline_clear else ''}"
                f"{len(left_absent)} deleted keys still online, {len(left_present)} written keys not online yet. "
                "The pending-delete file is kept; the next run settles it."
            )
        sleep(POLL_S)


def run(
    *,
    fs: Any,
    source: Any,
    text: str,
    group: str,
    model: str,
    embed: Callable[[list[str]], list[list[float]]],
    state: State,
    stores_for: Callable[[Any], Stores] = Stores,
    size: int = CHUNK_CHARS,
    overlap: int = CHUNK_OVERLAP,
    readback_s: int = READBACK_S,
    lease_s: int = LEASE_S,
    max_chunks: int = MAX_CHUNKS,
    max_bytes: int = MAX_BYTES,
    now: Callable[[], float] = time.time,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = lambda _: None,
) -> Plan:
    plan = Plan(group=group)
    state.acquire(lease_s, now())
    try:
        versions = _versions(fs, group)

        # Limits: every read below stops past the maximum, and none of it writes.
        pending = {}
        for fg in versions:
            keys = state.read_pending(fg.version)
            if keys is not None:
                pending[fg.version] = keys
        settling = sum(len(k) for k in pending.values())
        if settling > max_chunks:
            raise EmbedError(
                f"The pending deletes of '{group}' hold {settling} keys, over the supported input of "
                f"{max_chunks}. Nothing was written or deleted."
            )

        primary_key = list(getattr(source, "primary_key", []) or [])
        if not primary_key:
            raise EmbedError(
                f"Feature group '{source.name}' has no primary key, so its rows have no stable id. Nothing was written."
            )
        pin = _pin(source)
        try:
            want = read_source(
                source, primary_key, text, size, overlap, max_chunks, max_bytes
            )
        except EmbedError:
            raise
        except Exception as exc:  # noqa: BLE001 - any failed read is not an empty source
            raise EmbedError(
                f"Could not read feature group '{source.name}': {exc}. Nothing was written or deleted."
            ) from exc
        if _pin(source) != pin:
            raise EmbedError(
                f"Feature group '{source.name}' was committed to while it was read. "
                "Nothing was written or deleted; run again."
            )
        plan.desired = len(want)

        first = next(iter(want.values()), "dimension")
        probe = embed([first])
        dimension = len(probe[0])
        model_text = description(
            source.name, source.version, text, model, dimension, group
        )
        reused = _reusable(versions, group, model, dimension)
        held = {}
        if reused is not None:
            held = stores_for(reused).offline(max_chunks)

        # 0. Settle every version's pending deletes before anything else.
        for fg in versions:
            keys = pending.get(fg.version)
            if keys is None:
                continue
            log(f"Settling {len(keys)} pending deletes of {group} v{fg.version}")
            state.verify()
            stores = stores_for(fg)
            stores.remove(keys, "offline", readback_s)
            stores.remove(keys, "online", readback_s)
            wait_converged(stores, keys, [], readback_s, clock, sleep)
            state.remove_pending(fg.version)
            plan.settled[fg.version] = len(keys)
            if fg is reused:
                for key in keys:
                    held.pop(key, None)

        # 1. The desired keys D are the source read above.
        fg, created = _group_for(fs, versions, reused, group, dimension, model_text)
        plan.version, plan.created = fg.version, created
        stores = stores_for(fg)

        # 2. Keys to delete, recorded before any write.
        delete = sorted(set(held) - set(want))
        if delete:
            state.verify()
            state.write_pending(fg.version, delete)

        # 3. Upsert D; unchanged chunks keep their vectors.
        stamp = datetime.now(timezone.utc)
        changed = [
            k for k, chunk in want.items() if held.get(k, (None,))[0] != sha(chunk)
        ]
        vectors = dict(
            zip(
                changed,
                _embed_all(embed, [want[k] for k in changed], first, probe),
                strict=True,
            )
        )
        # Unchanged chunks are rewritten only when the online store lacks them
        # (a run that died after the offline write), so a refresh's writes stay
        # proportional to what changed.
        missing = set(want) - set(changed)
        if missing:
            missing -= stores.online_present(sorted(missing))
        rows = []
        for key, chunk in want.items():
            if key not in vectors and key not in missing:
                continue
            vector, at = (
                (vectors[key], stamp)
                if key in vectors
                else (held[key][1], held[key][2])
            )
            rows.append(
                {
                    "doc_id": key[0],
                    "chunk_ix": key[1],
                    "text": chunk,
                    "embedding": vector,
                    "content_sha": sha(chunk),
                    "embedded_at": at,
                }
            )
        if rows:
            state.verify()
            stores.upsert(rows)
        plan.embedded, plan.upserted = len(changed), len(rows)

        # 4. Delete X, offline then online.
        if delete:
            state.verify()
            stores.remove(delete, "offline", readback_s)
            stores.remove(delete, "online", readback_s)
        plan.removed = len(delete)

        # 5. Converged in both stores, then the pending file goes.
        wait_converged(stores, delete, sorted(want), readback_s, clock, sleep)
        if delete:
            state.remove_pending(fg.version)
        return plan
    finally:
        try:
            state.release()
        except Exception as exc:  # noqa: BLE001 - the run's own outcome is what to report
            log(
                f"Could not release the lease of {group}: {exc}. It is taken over after --lease-timeout."
            )


def _embed_all(
    embed, texts: list[str], first: str, probe: list[list[float]]
) -> list[list[float]]:
    if texts and texts[0] == first:
        return probe + (embed(texts[1:]) if len(texts) > 1 else [])
    return embed(texts) if texts else []


def _versions(fs: Any, group: str) -> list[Any]:
    from hopsworks.cli import versions  # noqa: PLC0415

    try:
        found = fs.get_feature_groups(group) or []
    except Exception as exc:  # noqa: BLE001
        if versions.not_found(exc):
            return []
        raise EmbedError(
            f"Could not read feature group '{group}': {exc}. Nothing was written."
        ) from exc
    return sorted(found, key=lambda fg: fg.version)


def _reusable(versions, group, model, dimension):
    """The latest version when it holds this model and dimension, else None (a new version)."""
    latest = versions[-1] if versions else None
    if latest is None:
        return None
    info = described(latest)
    if info is None or info["owner"] != group:
        raise EmbedError(
            f"Feature group '{group}' exists and was not made by hops fg embed. Pass --to with another name."
        )
    return latest if (info["model"], info["dimension"]) == (model, dimension) else None


def _group_for(fs, versions, reused, group, dimension, text):
    """`reused` as it is, else a new version."""
    if reused is not None:
        return reused, False
    latest = versions[-1] if versions else None
    from hsfs.embedding import EmbeddingIndex  # noqa: PLC0415
    from hsfs.feature import Feature  # noqa: PLC0415

    index = EmbeddingIndex()
    index.add_embedding("embedding", dimension, similarity_function_type="cosine")
    fg = fs.create_feature_group(
        name=group,
        version=(latest.version + 1) if latest else 1,
        description=text,
        online_enabled=True,
        primary_key=KEYS,
        features=[
            Feature("doc_id", "string"),
            Feature("chunk_ix", "bigint"),
            Feature("text", "string"),
            Feature("embedding", "array<float>"),
            Feature("content_sha", "string"),
            Feature("embedded_at", "timestamp"),
        ],
        embedding_index=index,
        statistics_config=False,
    )
    fg.save()
    return fg, True
