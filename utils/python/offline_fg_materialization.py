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
"""Offline materialization of a Delta feature group with delta-rs, in a python job pod.

The backend runs this instead of the Spark job (hsfs_utils.py offline_fg_materialization) for an append,
and for an upsert into a small table (OfflineMaterializationController.runsInPython). It takes the same
arguments and keeps the same files: the kafka_offsets directory, the pending range of an append and the
Delta application transaction an append is fenced with, so either job can follow the other.

The range is read in chunks of at most HOPSWORKS_MATERIALIZATION_CHUNK_BYTES of kept records. Each chunk
is committed, then the offsets after it are saved, which bounds the memory of a run.

A merge that would read more of the table than HOPSWORKS_MATERIALIZATION_MAX_MERGE_TABLE_BYTES is not
attempted: the run keeps the chunks before it and exits with HOPSWORKS_MATERIALIZATION_NEEDS_SPARK_EXIT_CODE,
and the backend runs the rest on Spark.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
import uuid
from io import BytesIO
from urllib.parse import urlparse

import hopsworks
import polars as pl
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.fs as pafs
from confluent_kafka import (
    OFFSET_BEGINNING,
    Consumer,
    KafkaError,
    KafkaException,
    TopicPartition,
)
from deltalake import DeltaTable
from deltalake.exceptions import TableNotFoundError
from fastavro import parse_schema, schemaless_reader
from hopsworks_common.core.type_systems import _convert_offline_type_to_pyarrow_type
from hsfs.core import delta_engine, kafka_engine
from materialization_offsets import (
    _build_offsets,
    _offsets_since_creation,
    _reconcile_offsets,
    write_options_of,
)


DEFAULT_JOB_LIMIT = 5000000
ROWS_PER_BATCH = 10000
POLL_SECONDS = 1.0
# A range whose partitions all stay silent this long has a broker problem, not a slow topic.
STALL_SECONDS = 300

hopsfs = pafs.HadoopFileSystem("default", user=os.environ["HADOOP_USER_NAME"])


def _path(uri: str) -> str:
    return urlparse(uri).path


def _exists(path: str) -> bool:
    return hopsfs.get_file_info(path).type != pafs.FileType.NotFound


def _remove_path(path: str) -> None:
    if _exists(path):
        hopsfs.delete_dir(path)


def read_job_conf(path: str) -> dict:
    with hopsfs.open_input_stream(_path(path)) as f:
        return json.loads(f.read().decode())


def _read_json_dir(path: str) -> str | None:
    """The one-line JSON of a directory written by Spark's df.coalesce(1).write.json, or None."""
    if not _exists(path):
        return None
    parts = [
        info
        for info in hopsfs.get_file_info(pafs.FileSelector(path))
        if info.base_name.startswith("part-") and info.base_name.endswith(".json")
    ]
    if not parts:
        return None
    with hopsfs.open_input_stream(max(parts, key=lambda info: info.mtime_ns).path) as f:
        lines = f.read().decode().strip().splitlines()
    return lines[0] if lines else None


def _write_json_dir(path: str, value: dict) -> None:
    """Write `value` the way Spark writes a one-row DataFrame as JSON, so Spark and the backend read it."""
    hopsfs.create_dir(path, recursive=True)
    previous = [
        info.path
        for info in hopsfs.get_file_info(pafs.FileSelector(path))
        if info.base_name.startswith("part-")
    ]
    # The new part lands before the old ones go: a run that dies in between leaves two to choose from,
    # and _read_json_dir takes the newest, where the other order would leave no offsets at all.
    with hopsfs.open_output_stream(f"{path}/part-00000-{uuid.uuid4()}-c000.json") as f:
        f.write((json.dumps(value, separators=(",", ":")) + "\n").encode())
    for part in previous:
        hopsfs.delete_file(part)
    with hopsfs.open_output_stream(f"{path}/_SUCCESS"):
        pass


def _pending_offsets(
    location: str, legacy_location: str, pending_location: str
) -> str | None:
    """The range an unfinished append claimed, as hsfs_utils._pending_offsets reads it."""
    if _exists(legacy_location):
        if _exists(pending_location) or not _exists(location + "/_delta_log"):
            _remove_path(legacy_location)
        else:
            hopsfs.move(legacy_location, pending_location)
    if not _exists(pending_location):
        return None
    return _read_json_dir(pending_location)


def _header(headers, key: str) -> str | None:
    """The first header of that key as a string, as the Spark job's filter(headers, ...)[0] reads it."""
    for name, value in headers or []:
        if name == key:
            return None if value is None else value.decode()
    return None


def _keeps(headers, feature_group_id: str, subject_id: str) -> bool:
    """The header filters of the Spark job: this group's records, not meant for OnlineFS alone, no tombstones."""
    return (
        _header(headers, "featureGroupId") == feature_group_id
        and _header(headers, "subjectId") == subject_id
        and _header(headers, "storage") != "online"
        and _header(headers, "operation") != "delete"
    )


class _Range:
    """The records of the topic between the starting and the ending offsets, in consumption order."""

    def __init__(self, entity, topic: str, start: dict, end: dict):
        config = kafka_engine._get_kafka_config(
            entity.feature_store_id, {}, engine="confluent"
        )
        config.update(
            {
                "group.id": f"hopsworks_offline_fg_materialization_{entity.id}",
                "enable.auto.commit": False,
                "enable.partition.eof": True,
                # Records removed by retention meanwhile are skipped, as with the Spark job's failOnDataLoss=false.
                "auto.offset.reset": "earliest",
            }
        )
        self._consumer = Consumer(config)
        self._topic = topic
        self._end = {int(partition): int(offset) for partition, offset in end.items()}
        start = {int(partition): int(offset) for partition, offset in start.items()}
        self._active = {
            partition
            for partition, offset in self._end.items()
            if start.get(partition, OFFSET_BEGINNING) < offset
        }
        if self._active:
            self._consumer.assign(
                [
                    TopicPartition(
                        topic, partition, start.get(partition, OFFSET_BEGINNING)
                    )
                    for partition in self._active
                ]
            )

    @property
    def done(self) -> bool:
        return not self._active

    def _finish(self, partition: int) -> None:
        if partition in self._active:
            self._active.discard(partition)
            self._consumer.pause([TopicPartition(self._topic, partition)])

    def records(self):
        """(partition, offset, headers, value) of every record of the range."""
        idle_since = time.monotonic()
        while self._active:
            message = self._consumer.poll(POLL_SECONDS)
            if message is None:
                if time.monotonic() - idle_since > STALL_SECONDS:
                    raise TimeoutError(
                        f"No record from partitions {sorted(self._active)} of {self._topic} in {STALL_SECONDS}s"
                    )
                continue
            idle_since = time.monotonic()
            error = message.error()
            if error is not None:
                if error.code() == KafkaError._PARTITION_EOF:
                    self._finish(message.partition())
                    continue
                if error.fatal() or not error.retriable():
                    raise KafkaException(error)
                continue
            partition, offset = message.partition(), message.offset()
            if partition not in self._active:
                continue
            if offset >= self._end[partition]:
                self._finish(partition)
                continue
            if offset + 1 >= self._end[partition]:
                self._finish(partition)
            yield partition, offset, message.headers(), message.value()

    def close(self) -> None:
        self._consumer.close()


class _Decoder:
    """Kafka values to Arrow rows with the feature group's offline types, as the Spark job's _deserialize_from_avro."""

    def __init__(self, entity):
        schema = json.loads(entity._get_encoded_avro_schema())
        self._writer_schema = parse_schema(schema)
        self._complex = {
            name: parse_schema(json.loads(entity._get_feature_avro_schema(name)))
            for name in entity.get_complex_features()
        }
        names = [field["name"] for field in schema["fields"]]
        self.schema = pa.schema(
            [
                pa.field(
                    name,
                    _convert_offline_type_to_pyarrow_type(
                        entity.get_feature(name).type
                    ),
                )
                for name in names
            ]
        )
        self.key = None
        if entity.primary_key:
            self.key = list(entity.primary_key)
            if entity.event_time:
                self.key.append(entity.event_time)
            if entity.partition_key:
                self.key.extend(entity.partition_key)

    def decode(self, value: bytes) -> dict:
        row = schemaless_reader(BytesIO(value), self._writer_schema, None)
        for name, schema in self._complex.items():
            if row.get(name) is not None:
                row[name] = schemaless_reader(BytesIO(row[name]), schema, None)
        return row


class _Chunk:
    """The kept rows of a chunk, deduplicated as the Spark job does: one row per key, the one of highest offset.

    Records of one key share a Kafka partition, which is read in offset order, so the last row of a key wins.
    """

    def __init__(self, decoder: _Decoder):
        self._decoder = decoder
        self._batches = []
        self._rows = []
        self._live = bytearray()
        self._index = {}

    def add(self, value: bytes) -> None:
        row = self._decoder.decode(value)
        if self._decoder.key is not None:
            key = tuple(row[column] for column in self._decoder.key)
            previous = self._index.get(key)
            if previous is not None:
                self._live[previous] = 0
            self._index[key] = len(self._live)
        self._live.append(1)
        self._rows.append(row)
        if len(self._rows) >= ROWS_PER_BATCH:
            self._flush()

    def _flush(self) -> None:
        if self._rows:
            self._batches.append(
                pa.Table.from_pylist(self._rows, schema=self._decoder.schema)
            )
            self._rows = []

    def table(self) -> pa.Table:
        self._flush()
        table = (
            pa.concat_tables(self._batches)
            if self._batches
            else self._decoder.schema.empty_table()
        )
        if self._decoder.key is not None and 0 in self._live:
            table = table.filter(pa.array(list(map(bool, self._live))))
        return table


def _committed_version(table_uri: str, storage_options, app_id: str) -> int | None:
    try:
        return DeltaTable(
            table_uri, storage_options=storage_options
        ).transaction_version(app_id)
    except TableNotFoundError:
        return None


def _merge_target_bytes(
    table_uri: str, storage_options, entity, table: pa.Table
) -> int:
    """Bytes of the files a merge of `table` reads, from the table's add actions pruned as the merge prunes."""
    try:
        delta_table = DeltaTable(table_uri, storage_options=storage_options)
    except TableNotFoundError:
        return 0
    actions = pa.record_batch(delta_table.get_add_actions(flatten=True))
    keep = pa.array([True] * actions.num_rows)
    for column in entity.partition_key or []:
        if f"partition.{column}" not in actions.schema.names:
            continue
        values = [str(value) for value in pc.unique(table.column(column)).to_pylist()]
        files = pa.array(
            [str(value) for value in actions.column(f"partition.{column}").to_pylist()]
        )
        keep = pc.and_(keep, pc.is_in(files, value_set=pa.array(values)))
    return pc.sum(actions.column("size_bytes").filter(keep)).as_py() or 0


def offline_fg_materialization(
    job_conf: dict, initial_check_point_string: str | None
) -> bool:
    """Materialize the range; False when a merge is left to Spark."""
    chunk_bytes = int(os.environ["HOPSWORKS_MATERIALIZATION_CHUNK_BYTES"])
    max_merge_table_bytes = int(
        os.environ["HOPSWORKS_MATERIALIZATION_MAX_MERGE_TABLE_BYTES"]
    )

    fs = hopsworks.login().get_feature_store(job_conf.pop("feature_store"))
    entity = fs.get_feature_group(name=job_conf["name"], version=job_conf["version"])
    write_options = write_options_of(job_conf)
    appends = write_options.get("operation") == "insert"
    topic = entity._online_topic_name

    delta = delta_engine.DeltaEngine(
        entity.feature_store_id, entity.feature_store_name, entity, None, None
    )
    table_uri = delta._get_delta_rs_location()
    storage_options = delta._get_delta_rs_storage_options() or None

    location = _path(entity.location)
    offset_location = location + "/kafka_offsets"
    pending_offset_location = (
        f"{location.rstrip('/')}_kafka_offsets_pending_{entity.id}"
    )
    pending_offsets = _pending_offsets(
        location, offset_location + "_pending", pending_offset_location
    )
    try:
        if initial_check_point_string:
            starting_offset_string = json.dumps(
                _build_offsets(initial_check_point_string)
            )
        else:
            starting_offset_string = _read_json_dir(offset_location)
    except Exception as e:
        print(f"Failed to use existing offsets: {e}")
        starting_offset_string = None

    low_offsets = _build_offsets(
        kafka_engine._kafka_get_offsets(
            topic_name=topic,
            feature_store_id=entity.feature_store_id,
            offline_write_options={},
            high=False,
        )
    )
    if not starting_offset_string:
        low_offsets = _offsets_since_creation(entity, low_offsets, write_options)
    offsets = _reconcile_offsets(starting_offset_string, low_offsets, topic)
    print(f"startingOffsets: {json.dumps(offsets)}")

    ending_offsets = _build_offsets(
        kafka_engine._kafka_get_offsets(
            topic_name=topic,
            feature_store_id=entity.feature_store_id,
            offline_write_options={},
            high=True,
        )
    )
    if pending_offsets and appends:
        ending_offsets = json.loads(pending_offsets)
        print(
            f"repeating the range of an unfinished run, endingOffsets: {json.dumps(ending_offsets)}"
        )
    print(f"endingOffsets: {json.dumps(ending_offsets)}")
    if appends:
        # Claimed before the read, as the Spark job does: a run started meanwhile reads a wider range and
        # would derive other transaction versions.
        _write_json_dir(pending_offset_location, ending_offsets)

    limit = int(write_options.get("job_limit", DEFAULT_JOB_LIMIT))
    app_id = f"hopsworks_feature_log_materialization_{entity.id}"
    decoder = _Decoder(entity)
    feature_group_id, subject_id = str(entity._id), str(entity.subject["id"])
    topic_range = _Range(
        entity, topic, offsets.get(topic, {}), ending_offsets.get(topic, {})
    )
    records = topic_range.records()
    kept_total = 0
    try:
        while True:
            chunk = _Chunk(decoder)
            consumed, kept = {}, {}
            kept_bytes = 0
            limited = False
            for partition, offset, headers, value in records:
                consumed[partition] = offset
                if value is None or not _keeps(headers, feature_group_id, subject_id):
                    continue
                chunk.add(value)
                kept[partition] = offset
                kept_bytes += len(value)
                kept_total += 1
                if kept_total >= limit:
                    limited = True
                    break
                if kept_bytes >= chunk_bytes:
                    break

            # Past every record read, other groups' included; at the job limit only past the kept records,
            # the Spark job's rule for a limited read.
            chunk_offsets = dict(offsets.get(topic, {}))
            for partition, offset in (kept if limited else consumed).items():
                chunk_offsets[str(partition)] = offset + 1
            table = chunk.table()
            del chunk

            if table.num_rows == 0 and _exists(location + "/_delta_log"):
                print(
                    "No records for the offline table in this range, skipping the insert"
                )
            elif appends:
                version = sum(int(v) for v in chunk_offsets.values())
                committed = _committed_version(table_uri, storage_options, app_id)
                # delta-rs records the application transaction but, unlike Spark, does not skip a write that
                # carries a version it already holds, so the run checks it.
                if committed is not None and committed >= version:
                    print(
                        f"Version {version} of {app_id} is already committed, skipping the insert"
                    )
                else:
                    fence = {
                        "commit_properties": {"app_id": app_id, "version": version}
                    }
                    _insert(entity, table, {**write_options, **fence})
            else:
                target_bytes = _merge_target_bytes(
                    table_uri, storage_options, entity, table
                )
                if target_bytes > max_merge_table_bytes:
                    print(
                        f"The merge would read {target_bytes} bytes of the table, more than {max_merge_table_bytes};"
                        " leaving the rest to Spark"
                    )
                    return False
                # No transaction fence on a merge: on the primary key, event time and partition key it is
                # idempotent, so a rerun after a commit whose offsets were never saved rewrites the same rows.
                _insert(entity, table, write_options)

            del table
            offsets = {topic: chunk_offsets}
            _write_json_dir(offset_location, offsets)
            if limited or topic_range.done:
                break
    finally:
        topic_range.close()
    if appends:
        _remove_path(pending_offset_location)
    return True


def _insert(entity, table: pa.Table, write_options: dict) -> None:
    # Do not apply transformation functions: the data written to Kafka already has them applied.
    entity.stream = False
    entity.insert(
        pl.from_arrow(table),
        storage="offline",
        operation=write_options.get("operation", "upsert"),
        transform=False,
        write_options=write_options,
        validation_options={"schema_validation": False},
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Offline feature group materialization with delta-rs"
    )
    parser.add_argument(
        "-op", type=str, choices=["offline_fg_materialization"], help="Operation type"
    )
    parser.add_argument(
        "-path", type=str, help="Location on HopsFS of the JSON configuration"
    )
    parser.add_argument("-start_time", type=str, help="Job start time")
    parser.add_argument(
        "-initialCheckPointString",
        type=str,
        help="Kafka offset to start consuming from",
    )
    args = parser.parse_args()

    try:
        done = offline_fg_materialization(
            read_job_conf(args.path), args.initialCheckPointString
        )
    except Exception as e:
        print(f"Error : {e}", traceback.format_exc())
        sys.exit(1)
    sys.exit(
        0
        if done
        else int(os.environ["HOPSWORKS_MATERIALIZATION_NEEDS_SPARK_EXIT_CODE"])
    )
