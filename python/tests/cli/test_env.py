"""CLI tests for ``hops env install`` — the upload-or-passthrough contract.

The backend resolves whatever string it receives against the project root, so
the command must upload a local file before installing and must hand any other
string through untouched.
Everything here pins that resolution logic; the install call itself is the
SDK's problem.
"""

from __future__ import annotations

import json
from unittest import mock

from click.testing import CliRunner
from hopsworks.cli.main import cli

from tests.cli.conftest import Environments, environment_refused


def _env_api(mock_project, env_name="my_env"):
    api = mock_project.get_environment_api.return_value
    env = api.get_environment.return_value
    env.name = env_name
    return api, env


def test_env_install_uploads_local_file(mock_project, tmp_path):
    _, env = _env_api(mock_project)
    dataset = mock_project.get_dataset_api.return_value
    dataset.upload.return_value = "Resources/environments/my_env/requirements.txt"
    reqs = tmp_path / "requirements.txt"
    reqs.write_text("ibis-framework\n")

    result = CliRunner().invoke(cli, ["env", "install", "my_env", "-f", str(reqs)])

    assert result.exit_code == 0, result.output
    dataset.upload.assert_called_once_with(
        local_path=str(reqs),
        upload_path="Resources/environments/my_env",
        overwrite=True,
    )
    env.install_requirements.assert_called_once_with(
        "Resources/environments/my_env/requirements.txt", await_installation=True
    )


def test_env_install_passes_project_path_through(mock_project):
    _, env = _env_api(mock_project)
    dataset = mock_project.get_dataset_api.return_value

    result = CliRunner().invoke(
        cli, ["env", "install", "my_env", "-f", "Users/me/requirements.txt"]
    )

    assert result.exit_code == 0, result.output
    dataset.upload.assert_not_called()
    env.install_requirements.assert_called_once_with(
        "Users/me/requirements.txt", await_installation=True
    )


def test_env_install_honors_upload_dir_and_no_overwrite(mock_project, tmp_path):
    _env_api(mock_project)
    dataset = mock_project.get_dataset_api.return_value
    dataset.upload.return_value = None  # command falls back to dest/basename
    reqs = tmp_path / "requirements.txt"
    reqs.write_text("pandas\n")

    result = CliRunner().invoke(
        cli,
        [
            "env",
            "install",
            "my_env",
            "-f",
            str(reqs),
            "--upload-dir",
            "Users/me/reqs",
            "--no-overwrite",
        ],
    )

    assert result.exit_code == 0, result.output
    dataset.upload.assert_called_once_with(
        local_path=str(reqs), upload_path="Users/me/reqs", overwrite=False
    )


def test_env_install_reports_upload_failure(mock_project, tmp_path):
    _, env = _env_api(mock_project)
    dataset = mock_project.get_dataset_api.return_value
    dataset.upload.side_effect = RuntimeError("disk full")
    reqs = tmp_path / "requirements.txt"
    reqs.write_text("pandas\n")

    result = CliRunner().invoke(cli, ["env", "install", "my_env", "-f", str(reqs)])

    assert result.exit_code != 0
    assert "Could not upload requirements" in result.output
    env.install_requirements.assert_not_called()


def test_env_install_unknown_environment(mock_project):
    api = mock_project.get_environment_api.return_value
    api.get_environment.return_value = None

    result = CliRunner().invoke(
        cli, ["env", "install", "nope", "-f", "Users/me/requirements.txt"]
    )

    assert result.exit_code != 0
    assert "No environment named 'nope'" in result.output


def _install_as_scott(*args):
    users_api = mock.MagicMock()
    users_api._get_current_user.return_value.username = "scott"
    with mock.patch("hopsworks.get_users_api", return_value=users_api, create=True):
        return CliRunner().invoke(cli, [*args])


def test_env_install_into_a_shared_environment_clones_your_own_copy(mock_project):
    envs = Environments(mock_project, "fraud")
    envs.envs["fraud"].install_requirements.side_effect = environment_refused()

    result = _install_as_scott(
        "--json", "env", "install", "fraud", "-f", "Users/scott/r.txt"
    )

    assert result.exit_code == 0, result.output
    envs.api.create_environment.assert_called_once_with(
        "fraud-app-env-scott", source_environment_name="fraud", await_creation=True
    )
    envs.envs["fraud-app-env-scott"].install_requirements.assert_called_once_with(
        "Users/scott/r.txt", await_installation=True
    )
    assert json.loads(result.stdout[result.stdout.index("{") :]) == {
        "environment": "fraud-app-env-scott",
        "installed": "Users/scott/r.txt",
    }


def test_env_install_reuses_your_copy_of_an_app_environment(mock_project):
    envs = Environments(mock_project, "shop-app-env", "shop-app-env-scott")
    envs.envs["shop-app-env"].install_requirements.side_effect = environment_refused()

    result = _install_as_scott(
        "env", "install", "shop-app-env", "-f", "Users/scott/r.txt"
    )

    assert result.exit_code == 0, result.output
    envs.api.create_environment.assert_not_called()
    envs.envs["shop-app-env-scott"].install_requirements.assert_called_once_with(
        "Users/scott/r.txt", await_installation=True
    )
    assert "into environment 'shop-app-env-scott'" in result.output


def test_env_install_any_other_refusal_is_reported_without_a_copy(mock_project):
    envs = Environments(mock_project, "fraud")
    envs.envs["fraud"].install_requirements.side_effect = environment_refused(300019)

    result = _install_as_scott("env", "install", "fraud", "-f", "Users/scott/r.txt")

    assert result.exit_code == 1
    assert "Install failed" in result.output
    envs.api.create_environment.assert_not_called()
