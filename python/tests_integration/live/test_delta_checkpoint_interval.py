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
    HOPSWORKS_LIVE_PROJECT=<project name> HOPSWORKS_LIVE_PROJECT_ID=<project id> \
        pytest tests_integration/live -m backend_without_interval

and, once the backend serves the variable:

    ... pytest tests_integration/live -m backend_serves_interval

HOPSWORKS_LIVE_PROJECT and HOPSWORKS_LIVE_PROJECT_ID name an existing project the key's user
belongs to; login selects it by name, so it never prompts, and the id must match. Each test creates
one Delta feature group named hopsable_test_<time> (feature group names allow no hyphen), deletes
exactly that one at the end and polls until it answers 404. No project is created or deleted.

A missing environment variable or an unreachable cluster fails the run. The case that does not
match the backend fails too: there is no skip.
"""

import json
import os
import time
import urllib.parse

import hopsworks
import pandas as pd
import pytest
import requests
from hopsworks_common import client
from hopsworks_common.client.exceptions import RestAPIError
from hopsworks_common.core.variable_api import VariableApi
from hsfs.feature import Feature


VARIABLE = "featurestore_delta_checkpoint_interval"
PROPERTY = "delta.checkpointInterval"
REQUEST_TIMEOUT_SECONDS = 120
CLEANUP_BUDGET_SECONDS = 300


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
    project_name = _required_env("HOPSWORKS_LIVE_PROJECT")
    project_id = int(_required_env("HOPSWORKS_LIVE_PROJECT_ID"))
    send = requests.Session.send

    def bounded_send(session, request, **kwargs):
        # The SDK sends without a timeout; give every request of the module, login included, a finite one.
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = REQUEST_TIMEOUT_SECONDS
        return send(session, request, **kwargs)

    requests.Session.send = bounded_send
    try:
        selected = hopsworks.login(
            host=host,
            port=443,
            project=project_name,
            api_key_file=_required_env("HOPSWORKS_LIVE_KEY_FILE"),
            hostname_verification=False,
            engine="python",
        )
        assert selected.id == project_id, (
            f"project {project_name} is id {selected.id}, not {project_id}"
        )
        yield selected
    finally:
        try:
            hopsworks.logout()
        finally:
            requests.Session.send = send


@pytest.fixture
def feature_group_name(project):
    name = f"hopsable_test_{int(time.time() * 1000)}"
    try:
        yield name
    finally:
        _delete_feature_group(project, name)


def _delete_feature_group(project, name):
    """Delete exactly feature group `name` version 1, then poll until it answers 404, within one budget."""
    deadline = time.monotonic() + CLEANUP_BUDGET_SECONDS
    _client = client._get_instance()
    fs_id = project.get_feature_store().id
    path = ["project", project.id, "featurestores", fs_id, "featuregroups"]

    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            pytest.fail(
                f"feature group {name} cleanup exceeded {CLEANUP_BUDGET_SECONDS}s"
            )
        return min(REQUEST_TIMEOUT_SECONDS, left)

    def lookup():
        try:
            return _client._send_request(
                "GET", path + [name], {"version": 1}, timeout=remaining()
            )
        except RestAPIError as err:
            if err.response.status_code == RestAPIError.STATUS_CODE_NOT_FOUND:
                return None
            raise

    found = lookup()
    if not found:
        return
    fg_id = (found[0] if isinstance(found, list) else found)["id"]
    _client._send_request("DELETE", path + [fg_id], timeout=remaining())
    while lookup():
        time.sleep(min(2, remaining()))


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
def test_backend_without_the_variable_leaves_the_table_without_an_interval(
    project, feature_group_name
):
    with pytest.raises(RestAPIError) as absent:
        VariableApi()._get_variable(VARIABLE)
    assert absent.value.response.status_code == RestAPIError.STATUS_CODE_NOT_FOUND
    assert VariableApi()._get_featurestore_delta_checkpoint_interval() is None

    fg = _written_feature_group(project, feature_group_name)

    assert PROPERTY not in _table_configuration(project, fg)


@pytest.mark.backend_serves_interval
def test_backend_with_the_variable_gives_a_new_table_its_interval(
    project, feature_group_name
):
    served = VariableApi()._get_variable(VARIABLE)
    interval = int(served)
    assert interval >= 1
    assert VariableApi()._get_featurestore_delta_checkpoint_interval() == interval

    fg = _written_feature_group(project, feature_group_name)

    assert _table_configuration(project, fg)[PROPERTY] == str(interval)
