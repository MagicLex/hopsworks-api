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
r"""Live checks of the Delta checkpoint interval against a real Hopsworks cluster.

Not part of the unit suite (`pytest tests`). Run from the python directory:

    HOPSWORKS_LIVE_HOST=<host or url> HOPSWORKS_LIVE_KEY_FILE=<file holding the API key> \
        pytest tests_integration/live -m backend_without_interval

and, once the backend serves the variable:

    ... pytest tests_integration/live -m backend_serves_interval

A missing environment variable or an unreachable cluster fails the run. The case that does not
match the backend fails too: there is no skip. The module creates its own project and deletes it
at the end, polling until the project answers 404.
"""

import json
import os
import time
import urllib.parse
import uuid

import hopsworks
import pandas as pd
import pytest
from hopsworks_common import client
from hopsworks_common.client.exceptions import RestAPIError
from hopsworks_common.core.variable_api import VariableApi
from hsfs.feature import Feature


VARIABLE = "featurestore_delta_checkpoint_interval"
PROPERTY = "delta.checkpointInterval"
DELETE_TIMEOUT_SECONDS = 600


def _required_env(name):
    value = os.environ.get(name)
    if not value:
        pytest.fail(
            f"{name} is not set: the live lane needs it, see the module docstring."
        )
    return value


@pytest.fixture(scope="module")
def project():
    host_value = _required_env("HOPSWORKS_LIVE_HOST").strip()
    host = urllib.parse.urlparse(host_value).hostname or host_value
    hopsworks.login(
        host=host,
        port=443,
        api_key_file=_required_env("HOPSWORKS_LIVE_KEY_FILE"),
        hostname_verification=False,
        engine="python",
    )
    name = f"hable147{uuid.uuid4().hex[:8]}"
    created = hopsworks.create_project(name)
    assert created is not None and created.name == name
    try:
        yield created
    finally:
        _delete_project(created.id)
        hopsworks.logout()


def _delete_project(project_id):
    _client = client._get_instance()
    _client._send_request("POST", ["project", str(project_id), "delete"])
    deadline = time.monotonic() + DELETE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            _client._send_request("GET", ["project", str(project_id)])
        except RestAPIError as err:
            if err.response.status_code == RestAPIError.STATUS_CODE_NOT_FOUND:
                return
            raise
        time.sleep(5)
    pytest.fail(f"project {project_id} still answers after {DELETE_TIMEOUT_SECONDS}s")


def _written_feature_group(project, name):
    fs = project.get_feature_store()
    fg = fs.create_feature_group(
        name=name,
        version=1,
        primary_key=["id"],
        time_travel_format="DELTA",
        features=[Feature("id", "bigint"), Feature("v", "string")],
    )
    fg.save()
    for ids in ([1, 2], [2, 3]):
        fg.insert(
            pd.DataFrame({"id": ids, "v": ["x"] * len(ids)}),
            write_options={"wait_for_job": False},
        )
    return fg


def _table_configuration(project, fg):
    """The configuration of the table's last metaData action, read from its _delta_log in HopsFS."""
    dataset_api = project.get_dataset_api()
    log_dir = urllib.parse.urlparse(fg.location).path + "/_delta_log"
    commits = sorted(p for p in dataset_api.list(log_dir) if p.endswith(".json"))
    assert commits, f"no commit in {log_dir}"
    configuration = None
    for path in commits:
        body = dataset_api.read_content(path, "FEATURESTORE").content.decode()
        for line in body.splitlines():
            action = json.loads(line) if line.strip() else {}
            if "metaData" in action:
                configuration = action["metaData"].get("configuration", {})
    assert configuration is not None, f"no metaData action in {log_dir}"
    return configuration


@pytest.mark.backend_without_interval
def test_backend_without_the_variable_leaves_the_table_without_an_interval(project):
    with pytest.raises(RestAPIError) as absent:
        VariableApi()._get_variable(VARIABLE)
    assert absent.value.response.status_code == RestAPIError.STATUS_CODE_NOT_FOUND
    assert VariableApi()._get_featurestore_delta_checkpoint_interval() is None

    fg = _written_feature_group(project, "live_without_interval")

    assert PROPERTY not in _table_configuration(project, fg)


@pytest.mark.backend_serves_interval
def test_backend_with_the_variable_gives_a_new_table_its_interval(project):
    served = VariableApi()._get_variable(VARIABLE)
    interval = int(served)
    assert interval >= 1
    assert VariableApi()._get_featurestore_delta_checkpoint_interval() == interval

    fg = _written_feature_group(project, "live_with_interval")

    assert _table_configuration(project, fg)[PROPERTY] == str(interval)
