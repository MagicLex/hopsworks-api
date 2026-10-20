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
"""Where an offline materialization run reads its topic from, shared by its two jobs.

hsfs_utils.py offline_fg_materialization runs on Spark and offline_fg_materialization.py on
delta-rs in a python job. Both ship beside this file, and this module must not import pyspark,
which the python job image does not have.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from hsfs.core import kafka_engine


# Records a run materializes at most, unless write_options job_limit says otherwise.
DEFAULT_JOB_LIMIT = 5000000


def write_options_of(job_conf) -> dict:
    return job_conf.get("write_options", {}) or {}


def _timestamp_ms(date_string: str) -> int:
    """Milliseconds since the epoch for a date the backend serialized.

    Parsed here rather than through `hsfs.util`, deliberately. This file ships in the job
    images beside whatever SDK the image happens to carry, so a
    parser reached through the SDK is a second thing that has to be current for the floor
    below to work at all — and when it is not, the floor does not fail loudly, it quietly
    falls back to reading the whole topic.

    `fromisoformat` also accepts the whole of the backend's `yyyy-MM-dd'T'HH:mm:ss.SSSXXX`
    format, including the `+02:00` rendering of that trailing `XXX`, which the SDK's
    pattern list does not.
    """
    parsed = datetime.fromisoformat(date_string.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        # The backend always sends an offset; anything that does not is read as UTC
        # rather than as the job container's local time.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _offsets_since_creation(entity, low_offsets: dict, write_options: dict) -> dict:
    """Offsets a first materialization run should start from, floored at the feature group's creation.

    A run gets told where to start by `-initialCheckPointString`, which only the Python
    client passes, or by the offsets file a previous run saved. With neither, the start
    falls back to the topic's low watermark, and the topic is by default
    `<project>_onlinefs`, shared by every online-enabled feature group in the project: the
    run reads the whole retained history of all of them just to drop nearly all of it in
    the featureGroupId filter.

    No record of a feature group can predate the feature group, so its creation time is a
    sound floor. Kafka stamps a record with the producing client's clock by default, so a
    client whose clock lags the backend's could stamp one just before that time;
    `initial_offset_margin_hours` (1 by default) is how far back the floor is moved to
    absorb the skew, and raising it costs only a longer read.

    The low watermark offsets are returned unchanged whenever the floor cannot be
    established, so a failed lookup reads too much rather than too little.
    """
    if not low_offsets or not entity.created:
        return low_offsets

    # Everything the floor is derived from is under the guard: an unparseable margin or
    # creation time is as much a reason to fall back as a broker that will not answer,
    # and none of the three may take the materialization job down with it.
    try:
        margin_hours = float(write_options.get("initial_offset_margin_hours", 1))
        timestamp = _timestamp_ms(entity.created) - int(margin_hours * 60 * 60 * 1000)
        offsets = _build_offsets(
            kafka_engine._kafka_get_offsets_for_times(
                topic_name=entity._online_topic_name,
                feature_store_id=entity.feature_store_id,
                offline_write_options={},
                timestamp=timestamp,
            )
        )
    except Exception as e:
        print(f"Failed to look offsets up by creation time: {e}")
        return low_offsets

    if not offsets:
        return low_offsets

    print(f"No saved offsets, starting from the feature group's creation: {offsets}")
    return offsets


def _build_offsets(initial_check_point_string: str):
    if not initial_check_point_string:
        return ""

    # Split the input string into the topic and partition-offset pairs
    topic, offsets = initial_check_point_string.split(",", 1)
    # Split the offsets and build a dictionary from them
    offsets_dict = {
        partition: int(offset)
        for partition, offset in (pair.split(":") for pair in offsets.split(","))
    }
    # Create the final dictionary structure
    result = {topic: offsets_dict}
    return result


def _reconcile_offsets(
    starting_offset_string: str | None,
    low_offsets: dict,
    topic_name: str,
) -> dict:
    """Reconcile saved offsets against current topic state.

    Handles cases where:
    - No saved offsets exist (returns low watermark offsets)
    - The topic has changed (returns low watermark offsets)
    - Partitions were added (uses low watermark for new partitions)
    - Saved offsets are behind the low watermark (uses low watermark)
    """
    if not starting_offset_string or not low_offsets:
        return low_offsets if low_offsets else {}

    try:
        saved_offsets = json.loads(starting_offset_string)
    except (ValueError, TypeError):
        # Malformed or unexpected JSON — fall back to low watermark offsets
        return low_offsets

    # Expect a mapping of {topic_name: {partition: offset}}
    if not isinstance(saved_offsets, dict):
        return low_offsets

    # topic changed — start from low watermark
    if topic_name not in saved_offsets:
        return low_offsets

    saved_partition_offsets = saved_offsets[topic_name]
    low_partition_offsets = low_offsets.get(topic_name)

    if not isinstance(saved_partition_offsets, dict) or not isinstance(
        low_partition_offsets, dict
    ):
        return low_offsets

    reconciled = {}
    for partition, low_offset in low_partition_offsets.items():
        # Normalize low watermark offset to int; if it cannot be parsed, keep original.
        try:
            low_offset_int = int(low_offset)
        except (TypeError, ValueError):
            reconciled[partition] = low_offset
            continue

        saved_raw_offset = saved_partition_offsets.get(partition)
        try:
            saved_offset_int = (
                int(saved_raw_offset) if saved_raw_offset is not None else None
            )
        except (TypeError, ValueError):
            saved_offset_int = None

        if saved_offset_int is not None and saved_offset_int >= low_offset_int:
            reconciled[partition] = saved_offset_int
        else:
            reconciled[partition] = low_offset_int
    return {topic_name: reconciled}
