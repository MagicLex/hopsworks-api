#
#   Copyright 2025 Hopsworks AB
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
"""Spark side of the Delta checkpoint interval checks, run by test_delta_engine_spark.py in its own process."""

import json
import os
import pathlib
import sys

import pytest
from hsfs.core.delta_engine import DeltaEngine


try:
    import delta  # noqa: F401
    import pyspark  # noqa: F401
except ImportError as err:
    raise RuntimeError(
        "The Spark lane needs pyspark and delta-spark installed (see the dev extra in pyproject.toml)."
    ) from err


def _last_checkpoint(location):
    path = pathlib.Path(location) / "_delta_log" / "_last_checkpoint"
    return json.loads(path.read_text()) if path.exists() else None


def _use_client(monkeypatch, checkpoint_interval):
    """Install a client with no connection that holds the interval the variable read would cache."""
    from hopsworks_common import client
    from hopsworks_common.client import base
    from hopsworks_common.core import variable_api

    local = object.__new__(base.Client)
    variable_api._DELTA_CHECKPOINT_INTERVALS[local] = checkpoint_interval
    monkeypatch.setattr(client, "_client", local)


@pytest.fixture(scope="module")
def local_spark():
    """A real local Spark session with Delta.

    DELTA_SPARK_JARS, a comma separated list of jars, replaces the Maven resolution of the
    delta-spark package for machines without network access.
    Spark keeps one JVM per process and its extensions are fixed when it starts, so a session
    an earlier module started without Delta is stopped; later modules start their own.
    """
    from delta import configure_spark_with_delta_pip
    from pyspark.sql import SparkSession

    earlier = SparkSession.getActiveSession()
    if earlier is not None:
        earlier.stop()
    builder = (
        SparkSession.builder.master("local[1]")
        .appName("hopsworks-delta-engine-tests")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
        .config("spark.pyspark.python", sys.executable)
        .config("spark.pyspark.driver.python", sys.executable)
        .config("spark.ui.enabled", "false")
    )
    jars = os.environ.get("DELTA_SPARK_JARS")
    if jars:
        spark = builder.config("spark.jars", jars).getOrCreate()
    else:
        spark = configure_spark_with_delta_pip(builder).getOrCreate()
    yield spark
    spark.stop()


def _spark_engine(
    monkeypatch, spark, location, checkpoint_interval=10, clustered_by=None
):
    from hopsworks_common import connection
    from hsfs import feature, feature_group

    _use_client(monkeypatch, checkpoint_interval)
    monkeypatch.setattr(connection, "_hsfs_engine_type", "spark")
    fg = feature_group.FeatureGroup(
        name="fg",
        version=1,
        featurestore_id=1,
        location=location,
        primary_key=["id"],
        partition_key=[],
        clustered_by=clustered_by,
        time_travel_format="DELTA",
        features=[feature.Feature("id", "bigint"), feature.Feature("v", "string")],
    )
    return DeltaEngine(1, "fs", fg, spark, spark.sparkContext)


def _spark_properties(spark, location):
    return (
        spark.sql(f"DESCRIBE DETAIL delta.`{location}`")
        .select("properties")
        .collect()[0][0]
    )


def _spark_rows(spark, i):
    return spark.createDataFrame([(i, "x")], "id long, v string")


def test_spark_created_table_carries_the_checkpoint_interval(
    monkeypatch, tmp_path, local_spark
):
    location = str(tmp_path / "fg")
    engine = _spark_engine(monkeypatch, local_spark, location, checkpoint_interval=7)

    engine._write_delta_dataset(_spark_rows(local_spark, 1), {})

    assert _spark_properties(local_spark, location)["delta.checkpointInterval"] == "7"


def test_spark_empty_table_carries_the_checkpoint_interval(
    monkeypatch, tmp_path, local_spark
):
    location = str(tmp_path / "fg")
    engine = _spark_engine(monkeypatch, local_spark, location, checkpoint_interval=7)

    engine._save_empty_table()

    assert _spark_properties(local_spark, location)["delta.checkpointInterval"] == "7"


def test_spark_a_checkpoint_follows_every_interval_of_writes(
    monkeypatch, tmp_path, local_spark
):
    interval = 3
    location = str(tmp_path / "fg")
    engine = _spark_engine(
        monkeypatch, local_spark, location, checkpoint_interval=interval
    )

    for i in range(interval + 1):
        engine._write_delta_dataset(_spark_rows(local_spark, i), {})

    assert _last_checkpoint(location)["version"] == interval


@pytest.mark.parametrize("operation", ["upsert", "insert"])
def test_spark_existing_table_without_the_property_gets_it_on_its_first_write(
    monkeypatch, tmp_path, local_spark, operation
):
    location = str(tmp_path / "fg")
    engine = _spark_engine(monkeypatch, local_spark, location, checkpoint_interval=4)
    _spark_rows(local_spark, 1).write.format("delta").mode("append").save(location)
    assert "delta.checkpointInterval" not in _spark_properties(local_spark, location)

    commit = engine._write_delta_dataset(
        _spark_rows(local_spark, 2), {}, operation=operation
    )

    assert _spark_properties(local_spark, location)["delta.checkpointInterval"] == "4"
    assert int(commit.rows_inserted) == 1
    assert local_spark.read.format("delta").load(location).count() == 2

    engine._write_delta_dataset(_spark_rows(local_spark, 3), {}, operation=operation)
    assert _spark_properties(local_spark, location)["delta.checkpointInterval"] == "4"


def test_spark_existing_table_with_an_explicit_interval_keeps_it(
    monkeypatch, tmp_path, local_spark
):
    location = str(tmp_path / "fg")
    engine = _spark_engine(monkeypatch, local_spark, location, checkpoint_interval=4)
    _spark_rows(local_spark, 1).write.format("delta").option(
        "delta.checkpointInterval", "50"
    ).mode("append").save(location)

    engine._write_delta_dataset(_spark_rows(local_spark, 2), {})

    assert _spark_properties(local_spark, location)["delta.checkpointInterval"] == "50"


def test_spark_clustered_create_carries_the_checkpoint_interval(
    monkeypatch, tmp_path, local_spark
):
    location = str(tmp_path / "fg")
    engine = _spark_engine(
        monkeypatch,
        local_spark,
        location,
        checkpoint_interval=6,
        clustered_by=["id"],
    )

    engine._write_delta_dataset(_spark_rows(local_spark, 1), {})

    assert _spark_properties(local_spark, location)["delta.checkpointInterval"] == "6"
