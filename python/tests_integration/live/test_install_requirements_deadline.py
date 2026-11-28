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
r"""Live check: a requirements install awaited under a finite timeout returns once it is done.

The install's poll sends each request with a deadline. When the install ends, the backend removes the
library row and the poll's GET answers 404 with error code 300003, which the poll reads as done. That
needs a `RestAPIError` carrying the code, as `hops app deploy` relies on.

Not part of the unit suite (`pytest tests`). Run from the python directory:

    HOPSWORKS_LIVE_HOST=<host or url> HOPSWORKS_LIVE_KEY_FILE=<file holding the API key> \
        HOPSWORKS_LIVE_PROJECT=<project name> HOPSWORKS_LIVE_PROJECT_ID=<project id> \
        pytest tests_integration/live/test_install_requirements_deadline.py

The key's user must be allowed to create Python environments in the project. The test clones
python-app-pipeline into a new environment hopsable-test-<time>-app-env, uploads a one-line
requirements file to Resources/hopsable-test-<time>, installs it and reads the library back, then deletes both and polls until
the environment answers 404. A missing environment variable or an unreachable cluster fails the run:
there is no skip.
"""

import os
import tempfile
import time
import urllib.parse

import hopsworks
import pytest
from hopsworks_common import client
from hopsworks_common.client.exceptions import RestAPIError


BASE_ENVIRONMENT = "python-app-pipeline"
REQUIREMENTS = "humanize==4.9.0\n"
INSTALL_TIMEOUT_SECONDS = 900
REQUEST_TIMEOUT_SECONDS = 120
CLEANUP_BUDGET_SECONDS = 600


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
    selected = hopsworks.login(
        host=host,
        port=443,
        project=project_name,
        api_key_file=_required_env("HOPSWORKS_LIVE_KEY_FILE"),
        hostname_verification=False,
        engine="python",
    )
    try:
        assert selected.id == project_id, (
            f"project {project_name} is id {selected.id}, not {project_id}"
        )
        yield selected
    finally:
        hopsworks.logout()


def _environment_present(project, name):
    try:
        client._get_instance()._send_request(
            "GET",
            ["project", project.id, "python", "environments", name],
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except RestAPIError as err:
        if err.response.status_code == RestAPIError.STATUS_CODE_NOT_FOUND:
            return False
        raise
    return True


@pytest.fixture
def app_environment(project):
    app = f"hopsable-test-{int(time.time() * 1000)}"
    name = f"{app}-app-env"
    folder = f"Resources/{app}"
    env_api = project.get_environment_api()
    datasets = project.get_dataset_api()
    assert not _environment_present(project, name), f"environment {name} exists"
    assert not datasets.exists(folder), f"folder {folder} exists"
    try:
        environment = env_api.create_environment(
            name,
            base_environment_name=BASE_ENVIRONMENT,
            created_for_app=app,
            await_creation=True,
        )
        datasets.mkdir(folder)
        with tempfile.TemporaryDirectory() as tmp:
            local = os.path.join(tmp, "app-requirements.txt")
            with open(local, "w") as f:
                f.write(REQUIREMENTS)
            datasets.upload(local_path=local, upload_path=folder, overwrite=True)
        yield (
            env_api,
            environment,
            f"/Projects/{project.backing_name}/{folder}/app-requirements.txt",
        )
    finally:
        deadline = time.monotonic() + CLEANUP_BUDGET_SECONDS
        while _environment_present(project, name):
            try:
                env_api.get_environment(name).delete()
            except RestAPIError as err:
                if err.response.status_code != RestAPIError.STATUS_CODE_NOT_FOUND:
                    raise
            assert time.monotonic() < deadline, f"environment {name} still exists"
            time.sleep(10)
        if datasets.exists(folder):
            datasets.remove(folder)
        assert not datasets.exists(folder), f"folder {folder} still exists"


def test_requirements_install_under_a_timeout_returns_when_done(app_environment):
    env_api, environment, requirements = app_environment
    environment.install_requirements(
        requirements, await_installation=True, timeout=INSTALL_TIMEOUT_SECONDS
    )
    installed = env_api.get_environment(environment.name).library_versions or {}
    assert installed.get("humanize") == "4.9.0", installed
