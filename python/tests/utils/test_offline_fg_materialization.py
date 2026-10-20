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
            chunk.add(encode(row))
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
        chunk.add(encode({"id": 1, "ts": TS, "p": "a", "v": [1]}))
        table = chunk.table()
        uri = str(tmp_path / "table")
        assert job._merge_target_bytes(uri, None, FeatureGroup(), table) == 0
        write_deltalake(uri, table, partition_by=["p"])
        other = pa.Table.from_pylist(
            [{"id": 2, "ts": TS, "p": "b", "v": [1]}], schema=table.schema
        )
        assert job._merge_target_bytes(uri, None, FeatureGroup(), table) > 0
        assert job._merge_target_bytes(uri, None, FeatureGroup(), other) == 0
