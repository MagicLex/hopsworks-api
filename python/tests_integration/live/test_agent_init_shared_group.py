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
        HOPSWORKS_LIVE_SHARED_STORE=<name of a feature store shared with the project> \
        HOPSWORKS_LIVE_SHARED_GROUP=<feature group that exists only in that shared store> \
        HOPSWORKS_LIVE_SHADOWED_GROUP=<feature group name that exists in the project's own store and in the shared one, with different columns> \
        pytest tests_integration/live/test_agent_init_shared_group.py

The groups must exist already; the test only reads them and writes agent scripts into a temporary
directory. A missing environment variable, an unreachable cluster or a missing fixture fails the run:
there is no skip.
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


@pytest.fixture(scope="module")
def stores(live_env):
    """The project's own feature store and the shared one, read with the SDK."""
    project = hopsworks.login(
        host=live_env["HOPSWORKS_HOST"],
        port=443,
        project=live_env["HOPSWORKS_PROJECT"],
        api_key_value=live_env["HOPSWORKS_API_KEY"],
        hostname_verification=False,
        engine="python",
    )
    try:
        own = project.get_feature_store()
        shared = project.get_feature_store(_required_env("HOPSWORKS_LIVE_SHARED_STORE"))
        assert own.id != shared.id, "the shared store is the project's own store"
        yield own, shared
    finally:
        hopsworks.logout()


def _columns(store, group):
    found = store.get_feature_groups(group)
    assert found, f"feature group '{group}' is missing from store '{store.name}'"
    return {f.name for f in max(found, key=lambda g: g.version).features}


@pytest.fixture
def init(live_env, tmp_path, monkeypatch):
    for k, v in live_env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)

    def run(*groups):
        result = CliRunner().invoke(
            cli, ["agent", "init", "livebot", "--reads", ",".join(groups)]
        )
        return result, tmp_path / "agents" / "livebot" / "agent.py"

    return run


def test_a_group_found_only_in_a_shared_store_is_refused(init, stores):
    own, shared = stores
    group = _required_env("HOPSWORKS_LIVE_SHARED_GROUP")
    assert _columns(shared, group)
    assert not own.get_feature_groups(group), "the group is also in the own store"
    result, script = init(group)
    assert result.exit_code == 1, result.output
    assert f"'{group}'" in result.output and f"'{shared.name}'" in result.output
    assert not script.exists()


def test_a_mix_of_own_and_shared_groups_is_refused(init, stores):
    own, shared = stores
    shared_group = _required_env("HOPSWORKS_LIVE_SHARED_GROUP")
    own_group = _required_env("HOPSWORKS_LIVE_SHADOWED_GROUP")
    assert _columns(own, own_group)
    assert not own.get_feature_groups(shared_group)
    result, script = init(own_group, shared_group)
    assert result.exit_code == 1, result.output
    assert f"'{shared_group}'" in result.output and f"'{shared.name}'" in result.output
    assert not script.exists()


def test_a_shadowed_name_reads_the_own_group(init, stores):
    own, shared = stores
    group = _required_env("HOPSWORKS_LIVE_SHADOWED_GROUP")
    own_columns = _columns(own, group)
    shared_columns = _columns(shared, group)
    assert own_columns != shared_columns, (
        f"'{group}' has the same columns in both stores: {sorted(own_columns)}"
    )
    result, script = init(group)
    assert result.exit_code == 0, result.output
    code = script.read_text()
    assert all(f"{c} (" in code for c in own_columns)
    assert not any(f"{c} (" in code for c in shared_columns - own_columns)
