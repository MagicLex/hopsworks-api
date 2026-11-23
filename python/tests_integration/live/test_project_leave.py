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
r"""Live check: `hops project leave` as a member, with the session cache on, as in a sandbox.

Not part of the unit suite (`pytest tests`). Run from the python directory:

    HOPSWORKS_LIVE_HOST=<host or url> HOPSWORKS_LIVE_KEY_FILE=<owner's API key file> \
        HOPSWORKS_LIVE_MEMBER_KEY_FILE=<another user's API key file> \
        pytest tests_integration/live/test_project_leave.py

The owner's user must be able to create a project. The test creates one, adds the member as a Data
scientist, and runs `hops` in fresh processes with the project chosen by the environment and the
session cache on: the member reads `hops project info`, leaves with `--yes`, and a later
`hops project info` must fail rather than answer from the cached session. The owner's leave must be
refused. The project is deleted at the end, waited for until it answers 404.
A missing environment variable or an unreachable cluster fails the run: there is no skip.
"""

import os
import subprocess
import sys
import time
import urllib.parse

import pytest
import requests
import urllib3


DELETE_BUDGET_SECONDS = 600


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
    urllib3.disable_warnings()
    return {
        "host": host,
        "owner": _required_env("HOPSWORKS_LIVE_KEY_FILE"),
        "member": _required_env("HOPSWORKS_LIVE_MEMBER_KEY_FILE"),
        "home": tmp_path_factory.mktemp("home"),
    }


def _key(key_file):
    with open(key_file) as f:
        return f.read().strip()


def _rest(live, key_file, method, path, **kwargs):
    # The key is read here and never kept: pytest prints fixture values on a failure.
    return requests.request(
        method,
        f"https://{live['host']}/hopsworks-api/api{path}",
        headers={"Authorization": f"ApiKey {_key(key_file)}"},
        verify=False,
        timeout=120,
        **kwargs,
    )


def _hops(live, key_file, project, project_id, *args):
    """Runs `hops` in a new process the way a sandbox does: project from the environment, cache on."""
    env = {
        **os.environ,
        "HOME": str(live["home"]),
        "HOPSWORKS_HOST": live["host"],
        "HOPSWORKS_API_KEY": _key(key_file),
        "HOPSWORKS_PROJECT": project,
        "HOPSWORKS_PROJECT_ID": str(project_id),
        "HOPSWORKS_HOSTNAME_VERIFICATION": "false",
        "HOPS_SESSION_CACHE": "1",
        "HOPS_SESSION_DIR": str(live["home"] / "session"),
        "HOPS_NO_INPUT": "1",
    }
    return subprocess.run(
        [sys.executable, "-c", "from hopsworks.cli.main import main; main()", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _delete_and_wait(live, project_id):
    r = _rest(live, live["owner"], "POST", f"/project/{project_id}/delete")
    assert r.status_code in (200, 202, 204, 404), (
        f"delete {project_id}: {r.status_code} {r.text[:300]}"
    )
    deadline = time.monotonic() + DELETE_BUDGET_SECONDS
    while time.monotonic() < deadline:
        if (
            _rest(live, live["owner"], "GET", f"/project/{project_id}").status_code
            == 404
        ):
            return
        time.sleep(5)
    pytest.fail(f"project {project_id} still answers after {DELETE_BUDGET_SECONDS} s")


def test_a_member_leaves_and_the_cached_session_no_longer_answers(live):
    name = f"hpl{int(time.time())}"
    member_email = _rest(live, live["member"], "GET", "/users/profile").json()["email"]
    project_id = None
    try:
        r = _rest(
            live,
            live["owner"],
            "POST",
            "/project",
            json={
                "projectName": name,
                "description": "",
                "services": ["JOBS", "FEATURESTORE"],
                "projectTeam": [],
            },
        )
        assert r.status_code in (200, 201), f"create: {r.status_code} {r.text[:300]}"
        r = _rest(live, live["owner"], "GET", f"/project/getProjectInfo/{name}")
        assert r.ok, f"read: {r.status_code} {r.text[:300]}"
        project_id = r.json()["projectId"]
        r = _rest(
            live,
            live["owner"],
            "POST",
            f"/project/{project_id}/projectMembers",
            json={
                "projectTeam": [
                    {
                        "projectTeamPK": {
                            "projectId": project_id,
                            "teamMember": member_email,
                        },
                        "teamRole": "Data scientist",
                    }
                ]
            },
        )
        assert r.ok, f"add member: {r.status_code} {r.text[:300]}"

        info = _hops(live, live["member"], name, project_id, "project", "info")
        assert info.returncode == 0, info.stderr[-2000:]
        assert "Data scientist" in info.stdout

        left = _hops(
            live, live["member"], name, project_id, "project", "leave", "--yes"
        )
        assert left.returncode == 0, left.stderr[-2000:]
        assert f"Left {name}" in left.stdout + left.stderr
        members = _rest(
            live, live["owner"], "GET", f"/project/{project_id}/projectMembers"
        )
        assert members.ok
        emails = [m.get("user", {}).get("email") for m in members.json()]
        assert member_email not in emails

        after = _hops(live, live["member"], name, project_id, "project", "info")
        assert after.returncode != 0, (
            "hops project info answered after the leave: " + after.stdout[-2000:]
        )
        assert "Data scientist" not in after.stdout

        owner = _hops(
            live, live["owner"], name, project_id, "project", "leave", "--yes"
        )
        assert owner.returncode != 0, owner.stdout[-2000:]
        assert "Could not leave" in owner.stderr
        assert _rest(live, live["owner"], "GET", f"/project/{project_id}").ok
    finally:
        if project_id is not None:
            _delete_and_wait(live, project_id)
