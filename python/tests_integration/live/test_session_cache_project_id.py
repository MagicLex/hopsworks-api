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
r"""Live check: a session cached for a project id never acts on another project.

Not part of the unit suite (`pytest tests`). Run from the python directory:

    HOPSWORKS_LIVE_HOST=<host or url> HOPSWORKS_LIVE_KEY_FILE=<file holding the API key> \
        HOPSWORKS_LIVE_PROJECT_ID=<an existing project id of the key's user> \
        pytest tests_integration/live/test_session_cache_project_id.py

The key's user must be able to create two projects. The test creates project A, logs in to it by id in
one process (the session cache keeps it), deletes A, creates B with A's name and a folder in it, then in
a fresh process logs in to A by id from the cache and removes that folder path: the call must fail and
B's folder must stay. It also refreshes a cached session of HOPSWORKS_LIVE_PROJECT_ID and checks the
project id is unchanged. Every project it made is deleted at the end, waited for until it answers 404.
A missing environment variable or an unreachable cluster fails the run: there is no skip.
"""

import json
import os
import subprocess
import sys
import time
import urllib.parse

import pytest
import requests
import urllib3


DELETE_BUDGET_SECONDS = 600
FOLDER = "Resources/hcc_victim"


def _required_env(name):
    value = os.environ.get(name)
    if not value:
        pytest.fail(
            f"{name} is not set: the live lane needs it, see the module docstring."
        )
    return value


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    host_value = _required_env("HOPSWORKS_LIVE_HOST").strip()
    host = urllib.parse.urlparse(host_value).hostname or host_value
    key_file = _required_env("HOPSWORKS_LIVE_KEY_FILE")
    with open(key_file) as f:
        key = f.read().strip()
    urllib3.disable_warnings()
    return {
        "host": host,
        "key": key,
        "key_file": key_file,
        "existing": int(_required_env("HOPSWORKS_LIVE_PROJECT_ID")),
        "cache": str(tmp_path_factory.mktemp("session")),
    }


def _rest(live, method, path, **kwargs):
    return requests.request(
        method,
        f"https://{live['host']}/hopsworks-api/api{path}",
        headers={"Authorization": f"ApiKey {live['key']}"},
        verify=False,
        timeout=120,
        **kwargs,
    )


def _in_fresh_process(live, code, project_id):
    """Runs code after hopsworks.login(project_id=...) in a new interpreter with the session cache on."""
    env = {
        **os.environ,
        "HOPS_SESSION_CACHE": "1",
        "HOPS_SESSION_DIR": live["cache"],
        "HOPSWORKS_HOSTNAME_VERIFICATION": "false",
    }
    script = (
        "import json, hopsworks\n"
        f"project = hopsworks.login(host={live['host']!r}, api_key_file={live['key_file']!r}, "
        f"project_id={project_id}, hostname_verification=False, engine=\"python\")\n" + code
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _create(live, name):
    r = _rest(
        live,
        "POST",
        "/project",
        params={"projectName": name},
        json={"projectName": name},
    )
    assert r.status_code in (200, 201), f"create {name}: {r.status_code} {r.text[:300]}"
    r = _rest(live, "GET", f"/project/getProjectInfo/{name}")
    assert r.ok, f"read {name}: {r.status_code} {r.text[:300]}"
    return r.json()["projectId"]


def _delete_and_wait(live, project_id):
    r = _rest(live, "POST", f"/project/{project_id}/delete")
    assert r.status_code in (200, 202, 204, 404), (
        f"delete {project_id}: {r.status_code} {r.text[:300]}"
    )
    deadline = time.monotonic() + DELETE_BUDGET_SECONDS
    while time.monotonic() < deadline:
        if _rest(live, "GET", f"/project/{project_id}").status_code == 404:
            return
        time.sleep(5)
    pytest.fail(f"project {project_id} still answers after {DELETE_BUDGET_SECONDS} s")


def test_a_cached_project_id_never_acts_on_a_new_project_with_its_name(live):
    name = f"hcc{int(time.time())}"
    made = []
    try:
        a = _create(live, name)
        made.append(a)
        first = _in_fresh_process(live, "print(project.id)", a)
        assert first.returncode == 0, first.stderr[-2000:]
        _delete_and_wait(live, a)
        made.remove(a)

        b = _create(live, name)
        made.append(b)
        assert b != a
        r = _rest(
            live,
            "POST",
            f"/project/{b}/dataset/{FOLDER}",
            params={"action": "create", "type": "DATASET", "generate_readme": "false"},
        )
        assert r.ok, f"mkdir in B: {r.status_code} {r.text[:300]}"

        later = _in_fresh_process(
            live, f"project.get_dataset_api().remove({FOLDER!r})\nprint('removed')", a
        )
        assert later.returncode != 0, later.stdout[-2000:]
        assert "removed" not in later.stdout
        assert _rest(live, "GET", f"/project/{b}/dataset/{FOLDER}").ok, (
            "B's folder is gone: a call for project A reached project B"
        )
    finally:
        for project_id in made:
            _delete_and_wait(live, project_id)


def test_a_refreshed_session_keeps_its_project_id(live):
    existing = live["existing"]
    first = _in_fresh_process(live, "print(project.id)", existing)
    assert first.returncode == 0, first.stderr[-2000:]
    later = _in_fresh_process(
        live,
        "from hopsworks_common import client\n"
        f"path = client._get_instance()._refresh_session(['project', '{existing}', 'dataset', 'Resources'])\n"
        "print(json.dumps({'path': path, 'id': client._get_instance()._project_id, "
        "'listed': project.get_dataset_api().exists('Resources')}))",
        existing,
    )
    assert later.returncode == 0, later.stderr[-2000:]
    out = json.loads(later.stdout.strip().splitlines()[-1])
    assert out == {
        "path": ["project", str(existing), "dataset", "Resources"],
        "id": str(existing),
        "listed": True,
    }
