#
#   Copyright 2026 Hopsworks AB
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#
"""The delta-rs materializer of the python job image, on local files instead of HopsFS."""

from __future__ import annotations

import datetime
import importlib.util
import io
import json
import os
import pathlib
import sys
from unittest import mock

import pyarrow as pa
import pyarrow.fs as pafs
import pytest


UTILS = pathlib.Path(__file__).resolve().parents[3] / "utils" / "python"
UTC = datetime.timezone.utc


@pytest.fixture(scope="module")
def job():
    pytest.importorskip("polars")
    pytest.importorskip("deltalake")
    pytest.importorskip("fastavro")
    pytest.importorskip("confluent_kafka")
    os.environ.setdefault("HADOOP_USER_NAME", "test_user")
    spec = importlib.util.spec_from_file_location(
        "offline_fg_materialization_under_test", UTILS / "offline_fg_materialization.py"
    )
    module = importlib.util.module_from_spec(spec)
    # Registered as imported modules are: its dataclasses look their module up by name.
    sys.modules[spec.name] = module
    sys.path.insert(0, str(UTILS))
    try:
        # The job opens a HopsFS client as it is imported; the local filesystem stands in for it.
        with mock.patch(
            "pyarrow.fs.HadoopFileSystem",
            lambda *args, **kwargs: pafs.LocalFileSystem(),
        ):
            spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(UTILS))
    return module


AVRO = {
    "type": "record",
    "name": "row",
    "fields": [
        {"name": "id", "type": ["null", "long"]},
        {
            "name": "ts",
            "type": ["null", {"type": "long", "logicalType": "timestamp-micros"}],
        },
        {"name": "p", "type": ["null", "string"]},
        {"name": "v", "type": ["null", "bytes"]},
    ],
}
ARRAY = ["null", {"type": "array", "items": ["null", "long"]}]
TYPES = {"id": "bigint", "ts": "timestamp", "p": "string", "v": "array<bigint>"}


class Feature:
    def __init__(self, name):
        self.name = name
        self.type = TYPES[name]


class FeatureGroup:
    primary_key = ["id"]
    event_time = "ts"
    partition_key = ["p"]

    def _get_encoded_avro_schema(self):
        return json.dumps(AVRO)

    def _get_feature_avro_schema(self, name):
        return json.dumps(ARRAY)

    def get_complex_features(self):
        return ["v"]

    def get_feature(self, name):
        return Feature(name)


def encode(row):
    from fastavro import parse_schema, schemaless_writer

    array = io.BytesIO()
    schemaless_writer(array, parse_schema(ARRAY), row["v"])
    value = io.BytesIO()
    schemaless_writer(value, parse_schema(AVRO), {**row, "v": array.getvalue()})
    return value.getvalue()


TS = datetime.datetime(2026, 1, 1, tzinfo=UTC)


class TestHeaders:
    def test_keeps_the_records_of_the_group(self, job):
        assert job._keeps([("featureGroupId", b"9"), ("subjectId", b"3")], "9", "3")

    def test_drops_records_for_onlinefs_alone_tombstones_and_other_groups(self, job):
        base = [("featureGroupId", b"9"), ("subjectId", b"3")]
        assert not job._keeps([*base, ("storage", b"online")], "9", "3")
        assert not job._keeps([*base, ("operation", b"delete")], "9", "3")
        assert not job._keeps([("featureGroupId", b"8"), ("subjectId", b"3")], "9", "3")
        assert not job._keeps([("subjectId", b"3")], "9", "3")

    def test_the_first_header_of_a_key_counts(self, job):
        headers = [
            ("featureGroupId", b"9"),
            ("featureGroupId", b"8"),
            ("subjectId", b"3"),
        ]
        assert job._keeps(headers, "9", "3")


class TestChunk:
    def test_keeps_the_last_row_of_a_key(self, job):
        chunk = job._Chunk(job._Decoder(FeatureGroup()))
        for row in [
            {"id": 1, "ts": TS, "p": "a", "v": [1]},
            {"id": 2, "ts": TS, "p": "a", "v": [2]},
            {"id": 1, "ts": TS, "p": "a", "v": [3]},
        ]:
            chunk.add(job._Decoder(FeatureGroup()).decode(encode(row)))
        table = chunk.table()
        assert table.to_pylist() == [
            {"id": 2, "ts": datetime.datetime(2026, 1, 1), "p": "a", "v": [2]},
            {"id": 1, "ts": datetime.datetime(2026, 1, 1), "p": "a", "v": [3]},
        ]
        assert table.schema.field("ts").type == pa.timestamp("us")

    def test_an_empty_chunk_has_the_schema(self, job):
        table = job._Chunk(job._Decoder(FeatureGroup())).table()
        assert table.num_rows == 0
        assert table.schema.names == ["id", "ts", "p", "v"]


class TestOffsetsFiles:
    def test_the_latest_offsets_replace_the_previous(self, job, tmp_path):
        path = str(tmp_path / "kafka_offsets")
        job._write_json_dir(path, {"t": {"0": 5}})
        job._write_json_dir(path, {"t": {"0": 6, "1": 7}})
        assert json.loads(job._read_json_dir(path)) == {"t": {"0": 6, "1": 7}}
        assert sorted(name.split("-")[0] for name in os.listdir(path)) == [
            "_SUCCESS",
            "part",
        ]

    def test_no_offsets_yet(self, job, tmp_path):
        assert job._read_json_dir(str(tmp_path / "kafka_offsets")) is None


class TestDeltaTable:
    def test_committed_transaction_version(self, job, tmp_path):
        from deltalake import CommitProperties, Transaction, write_deltalake

        table = job._Chunk(job._Decoder(FeatureGroup())).table()
        uri = str(tmp_path / "table")
        assert job._committed_version(uri, None, "app") is None
        write_deltalake(
            uri,
            table,
            commit_properties=CommitProperties(
                app_transactions=[Transaction(app_id="app", version=13)]
            ),
        )
        assert job._committed_version(uri, None, "app") == 13

    def test_merge_reads_only_the_partitions_it_touches(self, job, tmp_path):
        from deltalake import write_deltalake

        chunk = job._Chunk(job._Decoder(FeatureGroup()))
        chunk.add(
            job._Decoder(FeatureGroup()).decode(
                encode({"id": 1, "ts": TS, "p": "a", "v": [1]})
            )
        )
        table = chunk.table()
        uri = str(tmp_path / "table")
        assert job._merge_target_bytes(uri, None, ["p"], table) == 0
        write_deltalake(uri, table, partition_by=["p"])
        other = pa.Table.from_pylist(
            [{"id": 2, "ts": TS, "p": "b", "v": [1]}], schema=table.schema
        )
        assert job._merge_target_bytes(uri, None, ["p"], table) > 0
        assert job._merge_target_bytes(uri, None, ["p"], other) == 0

    def test_merge_pruning_compares_boolean_and_timestamp_partitions_as_values(
        self, job, tmp_path
    ):
        from deltalake import write_deltalake

        # As in a table Spark created: its timestamps carry the UTC zone, the chunk's do not.
        stored = pa.table(
            {
                "id": [1, 2, 3],
                "flag": [True, False, True],
                "day": pa.array(
                    [
                        TS,
                        TS + datetime.timedelta(days=1),
                        TS + datetime.timedelta(days=1),
                    ],
                    pa.timestamp("us", tz="UTC"),
                ),
            }
        )
        uri = str(tmp_path / "table")
        write_deltalake(uri, stored, partition_by=["flag", "day"])
        chunk = pa.table(
            {
                "id": [4],
                "flag": [True],
                "day": pa.array([datetime.datetime(2026, 1, 2)], pa.timestamp("us")),
            }
        )
        everything = job._merge_target_bytes(uri, None, [], chunk)
        touched = job._merge_target_bytes(uri, None, ["flag", "day"], chunk)
        assert 0 < touched < everything
        nothing = pa.table(
            {
                "id": [5],
                "flag": [False],
                "day": pa.array([datetime.datetime(2026, 1, 1)], pa.timestamp("us")),
            }
        )
        assert job._merge_target_bytes(uri, None, ["flag", "day"], nothing) == 0


class TestRowBytes:
    def test_row_sizes_add_up_to_the_arrow_table(self, job):
        decoder = job._Decoder(FeatureGroup())
        rows = [
            decoder.decode(
                encode({"id": i, "ts": TS, "p": "x" * i, "v": list(range(i))})
            )
            for i in range(200)
        ]
        table = pa.Table.from_pylist(rows, schema=decoder.schema)
        sized = sum(decoder.row_bytes(row) for row in rows)
        # Arrow adds one offset per variable-width column and validity bitmaps on top of the row sizes.
        assert table.nbytes - 64 <= sized <= table.nbytes


class Log(FeatureGroup):
    """A feature group without a primary key: every record is a row."""

    primary_key = []


TOPIC = "t"


def log_records(partitions=3, per_partition=6):
    return {
        partition: [
            (
                offset,
                [("featureGroupId", b"9"), ("subjectId", b"3")],
                encode(
                    {"id": partition * 100 + offset, "ts": TS, "p": "a", "v": [offset]}
                ),
            )
            for offset in range(per_partition)
        ]
        for partition in range(partitions)
    }


class ListRange:
    """A topic range read from records in memory, in the partition order a test picks."""

    def __init__(self, records, order, start, end):
        self._queue = [
            (partition, offset, headers, value)
            for partition, offset, headers, value in self._interleave(records, order)
            if int(start.get(str(partition), 0))
            <= offset
            < int(end.get(str(partition), 0))
        ]
        self._returned = None

    @staticmethod
    def _interleave(records, order):
        cursors = dict.fromkeys(records, 0)
        while any(cursors[p] < len(records[p]) for p in records):
            for partition in order:
                if cursors[partition] < len(records[partition]):
                    offset, headers, value = records[partition][cursors[partition]]
                    cursors[partition] += 1
                    yield partition, offset, headers, value

    @property
    def done(self):
        return not self._queue and self._returned is None

    def unread(self, record):
        self._returned = record

    def records(self):
        while self._queue or self._returned is not None:
            if self._returned is not None:
                record, self._returned = self._returned, None
                yield record
            else:
                yield self._queue.pop(0)

    def close(self):
        pass


class Killed(Exception):
    pass


class TestAppendReplay:
    def _target(self, job, tmp_path, kill_after=None):
        from deltalake import CommitProperties, Transaction, write_deltalake

        location = str(tmp_path / "fg_1")
        commits = []

        def insert(table, options):
            fence = options["commit_properties"]
            write_deltalake(
                location,
                table,
                mode="append",
                commit_properties=CommitProperties(
                    app_transactions=[
                        Transaction(app_id=fence["app_id"], version=fence["version"])
                    ]
                ),
            )
            commits.append(fence["version"])
            # Dies between the commit and the offsets save.
            if kill_after is not None and len(commits) == kill_after:
                raise Killed

        target = job._Target(
            location=location,
            table_uri=location,
            storage_options=None,
            pending_location=location + "_kafka_offsets_pending_1",
            app_id="hopsworks_feature_log_materialization_1",
            partition_key=[],
            insert=insert,
        )
        return target, commits

    def _run(self, job, target, records, order, chunk_bytes):
        saved = job._read_json_dir(target.offset_location)
        pending = job._read_json_dir(target.pending_location)
        offsets = json.loads(saved) if saved else {TOPIC: {str(p): 0 for p in records}}
        ending = {TOPIC: {str(p): len(rows) for p, rows in records.items()}}
        return job._materialize(
            TOPIC,
            offsets,
            ending,
            json.loads(pending) if pending else None,
            lambda start, end: ListRange(records, order, start, end),
            job._Decoder(Log()),
            lambda headers: job._keeps(headers, "9", "3"),
            target,
            {"operation": "insert"},
            chunk_bytes,
            0,
        )

    @staticmethod
    def _ids(location):
        from deltalake import DeltaTable

        return sorted(DeltaTable(location).to_pyarrow_table().column("id").to_pylist())

    def test_a_rerun_in_another_partition_order_repeats_the_committed_chunk(
        self, job, tmp_path
    ):
        records = log_records()
        row_bytes = job._Decoder(Log()).row_bytes(
            job._Decoder(Log()).decode(records[0][0][2])
        )
        chunk_bytes = row_bytes * 4

        clean, _ = self._target(job, tmp_path / "clean")
        assert self._run(job, clean, records, [0, 1, 2], chunk_bytes)
        expected = self._ids(clean.location)
        assert len(expected) == 18

        target, commits = self._target(job, tmp_path / "killed", kill_after=2)
        with pytest.raises(Killed):
            self._run(job, target, records, [0, 1, 2], chunk_bytes)
        assert len(commits) == 2

        rerun, rerun_commits = self._target(job, tmp_path / "killed")
        assert self._run(job, rerun, records, [2, 1, 0], chunk_bytes)
        assert self._ids(rerun.location) == expected
        # The repeated chunk was found committed, not appended again.
        assert commits[1] not in rerun_commits
        assert not os.path.exists(rerun.pending_location)
        assert json.loads(job._read_json_dir(rerun.offset_location)) == {
            TOPIC: {"0": 6, "1": 6, "2": 6}
        }
