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
r"""Live check: `hops agent init` reads the project's own feature store only.

Not part of the unit suite (`pytest tests`). Run from the python directory:

    HOPSWORKS_LIVE_HOST=<host or url> HOPSWORKS_LIVE_KEY_FILE=<file holding the API key> \
        HOPSWORKS_LIVE_PROJECT=<project name> HOPSWORKS_LIVE_PROJECT_ID=<project id> \
        HOPSWORKS_LIVE_SHARED_GROUP=<feature group that exists only in a feature store shared with the project> \
        HOPSWORKS_LIVE_SHADOWED_GROUP=<feature group name that exists in the project's own store and in a shared one> \
        pytest tests_integration/live/test_agent_init_shared_group.py

The two groups must exist already; the test only reads them and writes agent scripts into a temporary
directory. A missing environment variable or an unreachable cluster fails the run: there is no skip.
"""

import os
import urllib.parse

import hopsworks
import pytest
from click.testing import CliRunner
from hopsworks.cli.main import cli


def _required_env(name):
    value = os.environ.get(name)
    if not value:
        pytest.fail(
            f"{name} is not set: the live lane needs it, see the module docstring."
        )
    return value


@pytest.fixture(scope="module")
def live_env():
    host_value = _required_env("HOPSWORKS_LIVE_HOST").strip()
    with open(_required_env("HOPSWORKS_LIVE_KEY_FILE")) as f:
        key = f.read().strip()
    return {
        "HOPSWORKS_HOST": urllib.parse.urlparse(host_value).hostname or host_value,
        "HOPSWORKS_API_KEY": key,
        "HOPSWORKS_PROJECT": _required_env("HOPSWORKS_LIVE_PROJECT"),
        "HOPSWORKS_PROJECT_ID": _required_env("HOPSWORKS_LIVE_PROJECT_ID"),
    }


@pytest.fixture
def init(live_env, tmp_path, monkeypatch):
    for k, v in live_env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)

    def run(group):
        result = CliRunner().invoke(cli, ["agent", "init", "livebot", "--reads", group])
        script = tmp_path / "agents" / "livebot" / "agent.py"
        return result, script

    return run


def test_a_group_found_only_in_a_shared_store_is_refused(init):
    group = _required_env("HOPSWORKS_LIVE_SHARED_GROUP")
    result, script = init(group)
    assert result.exit_code != 0, result.output
    assert f"'{group}'" in result.output and "shared feature store" in result.output
    assert not script.exists()


def test_a_shadowed_name_reads_the_own_group(init, live_env):
    group = _required_env("HOPSWORKS_LIVE_SHADOWED_GROUP")
    result, script = init(group)
    assert result.exit_code == 0, result.output
    project = hopsworks.login(
        host=live_env["HOPSWORKS_HOST"],
        port=443,
        project=live_env["HOPSWORKS_PROJECT"],
        api_key_value=live_env["HOPSWORKS_API_KEY"],
        hostname_verification=False,
        engine="python",
    )
    try:
        own = project.get_feature_store().get_feature_groups(group)
        columns = {f.name for f in max(own, key=lambda g: g.version).features}
    finally:
        hopsworks.logout()
    code = script.read_text()
    assert all(f"{c} (" in code for c in columns)
