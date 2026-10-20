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
"""The backend runs a small materialization in local mode through HOPSWORKS_SPARK_MASTER."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def builder(hsfs_utils, monkeypatch):
    builder = MagicMock()
    builder.enableHiveSupport.return_value = builder
    builder.master.return_value = builder
    monkeypatch.setattr(hsfs_utils.SparkSession, "builder", builder, raising=False)
    return builder


def test_the_master_from_the_environment_is_set_on_the_builder(
    hsfs_utils, builder, monkeypatch
):
    monkeypatch.setenv("HOPSWORKS_SPARK_MASTER", "local[1]")

    hsfs_utils.setup_spark()

    builder.master.assert_called_once_with("local[1]")
    builder.getOrCreate.assert_called_once_with()


@pytest.mark.parametrize("value", [None, ""])
def test_no_master_is_set_when_the_variable_is_unset_or_empty(
    hsfs_utils, builder, monkeypatch, value
):
    if value is None:
        monkeypatch.delenv("HOPSWORKS_SPARK_MASTER", raising=False)
    else:
        monkeypatch.setenv("HOPSWORKS_SPARK_MASTER", value)

    hsfs_utils.setup_spark()

    builder.master.assert_not_called()
    builder.getOrCreate.assert_called_once_with()
