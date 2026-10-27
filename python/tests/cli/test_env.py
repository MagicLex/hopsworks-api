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

from tests.cli.conftest import (
    Environments,
    environment_missing,
    environment_refused,
)


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


def _clone_fails_in_its_build(envs, deletable):
    """The clone request goes through and its build fails: the copy's CREATE command is FAILED."""
    from hopsworks_common.command import Command

    def clone(name, **kwargs):
        half = envs.add(name)
        half.commands = [Command(status="FAILED", op="CREATE")]
        if deletable:
            half.delete.side_effect = lambda: envs.envs.pop(name)
        else:
            half.delete.side_effect = RuntimeError("503")
        return half

    envs.api.create_environment.side_effect = clone
    envs.api.await_creation.side_effect = RuntimeError("build failed")


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
        "fraud-app-env-scott",
        source_environment_name="fraud",
        created_for_app=None,
        await_creation=False,
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
    assert (
        "Installed into your copy 'shop-app-env-scott', not 'shop-app-env'"
        in result.output
    )
    assert "only when it runs on 'shop-app-env-scott'" in result.output


def test_env_install_any_other_refusal_is_reported_without_a_copy(mock_project):
    envs = Environments(mock_project, "fraud")
    envs.envs["fraud"].install_requirements.side_effect = environment_refused(300019)

    result = _install_as_scott("env", "install", "fraud", "-f", "Users/scott/r.txt")

    assert result.exit_code == 1
    assert "Install failed" in result.output
    envs.api.create_environment.assert_not_called()


def test_env_clone_of_a_project_environment_sends_it_as_the_source(mock_project):
    envs = Environments(mock_project, "fraud")
    envs.envs["fraud"].preinstalled = False

    result = CliRunner().invoke(cli, ["env", "clone", "fraud-2", "--from", "fraud"])

    assert result.exit_code == 0, result.output
    envs.api.create_environment.assert_called_once_with(
        "fraud-2",
        description=None,
        await_creation=True,
        source_environment_name="fraud",
    )


def test_env_clone_of_a_built_in_creates_from_its_base_image(mock_project):
    envs = Environments(mock_project, "python-feature-pipeline")
    envs.envs["python-feature-pipeline"].preinstalled = True

    result = CliRunner().invoke(cli, ["env", "clone", "mine"])

    assert result.exit_code == 0, result.output
    envs.api.create_environment.assert_called_once_with(
        "mine",
        description=None,
        await_creation=True,
        base_environment_name="python-feature-pipeline",
    )


def test_env_clone_of_an_unknown_environment_says_so(mock_project):
    envs = Environments(mock_project)

    result = CliRunner().invoke(cli, ["env", "clone", "x", "--from", "nope"])

    assert result.exit_code == 1
    assert "No environment named 'nope'" in result.output
    envs.api.create_environment.assert_not_called()


def test_env_install_deletes_a_clone_that_failed_and_clones_again(mock_project):
    envs = Environments(mock_project, "fraud")
    envs.envs["fraud"].install_requirements.side_effect = environment_refused()

    _clone_fails_in_its_build(envs, deletable=True)
    result = _install_as_scott("env", "install", "fraud", "-f", "Users/scott/r.txt")
    assert result.exit_code == 1
    assert (
        "Could not clone environment 'fraud' to 'fraud-app-env-scott'" in result.output
    )
    assert "fraud-app-env-scott" not in envs.envs

    envs.api.create_environment.side_effect = lambda name, **kwargs: envs.add(name)
    envs.api.await_creation.side_effect = None
    result = _install_as_scott("env", "install", "fraud", "-f", "Users/scott/r.txt")
    assert result.exit_code == 0, result.output
    envs.envs["fraud-app-env-scott"].install_requirements.assert_called_once()


def test_env_install_refuses_a_copy_name_over_the_hopsworks_limit(mock_project):
    name = "f" * 120
    envs = Environments(mock_project, name)
    envs.envs[name].install_requirements.side_effect = environment_refused()

    result = _install_as_scott("env", "install", name, "-f", "Users/scott/r.txt")

    assert result.exit_code == 1
    assert "over the 128 Hopsworks allows" in result.output
    envs.api.create_environment.assert_not_called()


def test_env_install_clones_again_over_a_copy_whose_build_failed(mock_project):
    from hopsworks_common.command import Command

    envs = Environments(mock_project, "fraud", "fraud-app-env-scott")
    envs.envs["fraud"].install_requirements.side_effect = environment_refused()
    broken = envs.envs["fraud-app-env-scott"]
    broken.commands = [Command(status="FAILED", op="CREATE")]

    result = _install_as_scott("env", "install", "fraud", "-f", "Users/scott/r.txt")

    assert result.exit_code == 0, result.output
    broken.delete.assert_called_once()
    broken.install_requirements.assert_not_called()
    envs.api.create_environment.assert_called_once_with(
        "fraud-app-env-scott",
        source_environment_name="fraud",
        created_for_app=None,
        await_creation=False,
    )
    envs.envs["fraud-app-env-scott"].install_requirements.assert_called_once()


def test_env_install_says_how_to_delete_a_broken_copy_it_cannot_delete(mock_project):
    from hopsworks_common.command import Command

    envs = Environments(mock_project, "fraud", "fraud-app-env-scott")
    envs.envs["fraud"].install_requirements.side_effect = environment_refused()
    broken = envs.envs["fraud-app-env-scott"]
    broken.commands = [Command(status="FAILED", op="CREATE")]
    broken.delete.side_effect = RuntimeError("503")

    result = _install_as_scott("env", "install", "fraud", "-f", "Users/scott/r.txt")

    assert result.exit_code == 1
    assert "hops env delete fraud-app-env-scott --yes" in result.output
    envs.api.create_environment.assert_not_called()


def test_a_failed_clone_it_cannot_delete_is_named_with_how_to_delete_it(mock_project):
    envs = Environments(mock_project, "fraud")
    envs.envs["fraud"].install_requirements.side_effect = environment_refused()

    _clone_fails_in_its_build(envs, deletable=False)
    result = _install_as_scott("env", "install", "fraud", "-f", "Users/scott/r.txt")

    assert result.exit_code == 1
    assert (
        "Could not delete the unfinished copy fraud-app-env-scott: 503" in result.output
    )
    assert "hops env delete fraud-app-env-scott --yes" in result.output


def test_env_delete_deletes_after_confirmation(mock_project):
    envs = Environments(mock_project, "fraud-app-env-scott")

    result = CliRunner().invoke(cli, ["env", "delete", "fraud-app-env-scott", "--yes"])

    assert result.exit_code == 0, result.output
    envs.envs["fraud-app-env-scott"].delete.assert_called_once()


def test_env_delete_of_an_unknown_environment_says_so(mock_project):
    Environments(mock_project)

    result = CliRunner().invoke(cli, ["env", "delete", "nope", "--yes"])

    assert result.exit_code == 1
    assert "No environment named 'nope'" in result.output


def test_env_install_keeps_a_finished_copy_after_a_failed_install(mock_project):
    from hopsworks_common.command import Command

    envs = Environments(mock_project, "fraud", "fraud-app-env-scott")
    envs.envs["fraud"].install_requirements.side_effect = environment_refused()
    copy = envs.envs["fraud-app-env-scott"]
    copy.commands = [
        Command(status="FAILED", op="INSTALL"),
        Command(status="SUCCESS", op="CREATE"),
    ]

    result = _install_as_scott("env", "install", "fraud", "-f", "Users/scott/r.txt")

    assert result.exit_code == 0, result.output
    copy.delete.assert_not_called()
    envs.api.create_environment.assert_not_called()
    copy.install_requirements.assert_called_once()


def test_env_install_waits_for_the_copy_another_run_clones(mock_project):
    # Two runs of one user: the other run's clone request won, Hopsworks refuses this one.
    envs = Environments(mock_project, "fraud")
    envs.envs["fraud"].install_requirements.side_effect = environment_refused()

    def made_by_another_run(name, **kwargs):
        envs.add(name)
        raise RuntimeError("ANACONDA_ENVIRONMENT_ALREADY_INITIALIZED")

    envs.api.create_environment.side_effect = made_by_another_run
    result = _install_as_scott("env", "install", "fraud", "-f", "Users/scott/r.txt")

    assert result.exit_code == 0, result.output
    copy = envs.envs["fraud-app-env-scott"]
    copy.delete.assert_not_called()
    envs.api.await_creation.assert_called_once_with("fraud-app-env-scott")
    copy.install_requirements.assert_called_once()


def test_env_install_fails_when_the_clone_request_fails_and_no_copy_exists(
    mock_project,
):
    envs = Environments(mock_project, "fraud")
    envs.envs["fraud"].install_requirements.side_effect = environment_refused()
    envs.api.create_environment.side_effect = RuntimeError("503")

    result = _install_as_scott("env", "install", "fraud", "-f", "Users/scott/r.txt")

    assert result.exit_code == 1
    assert "Could not clone environment 'fraud' to 'fraud-app-env-scott': 503" in (
        result.output
    )
    envs.api.await_creation.assert_not_called()


def test_env_install_waits_for_a_copy_cloned_again_while_its_own_built(
    mock_project,
):
    # Another run deleted this run's clone mid-build and cloned it again.
    envs = Environments(mock_project, "fraud")
    envs.envs["fraud"].install_requirements.side_effect = environment_refused()
    first = []

    def await_creation(name):
        if not first:
            first.append(envs.envs[name])
            envs.add(name)
            raise environment_missing(name)

    envs.api.await_creation.side_effect = await_creation
    result = _install_as_scott("env", "install", "fraud", "-f", "Users/scott/r.txt")

    assert result.exit_code == 0, result.output
    assert envs.api.await_creation.call_count == 2
    first[0].install_requirements.assert_not_called()
    envs.envs["fraud-app-env-scott"].install_requirements.assert_called_once()


def test_env_install_never_deletes_a_copy_another_run_cloned_again(mock_project):
    # Both runs saw the failed copy; the other run deleted it and cloned again first.
    from hopsworks_common.command import Command

    envs = Environments(mock_project, "fraud", "fraud-app-env-scott")
    envs.envs["fraud"].install_requirements.side_effect = environment_refused()
    broken = envs.envs["fraud-app-env-scott"]
    broken.commands = [Command(status="FAILED", op="CREATE")]
    seen = []

    def get_environment(name):
        env = envs.envs.get(name)
        if env is broken:
            if seen:
                env = envs.add(name)
            seen.append(name)
        return env

    envs.api.get_environment.side_effect = get_environment
    result = _install_as_scott("env", "install", "fraud", "-f", "Users/scott/r.txt")

    assert result.exit_code == 0, result.output
    broken.delete.assert_not_called()
    envs.api.create_environment.assert_not_called()
    envs.api.await_creation.assert_called_once_with("fraud-app-env-scott")
    envs.envs["fraud-app-env-scott"].install_requirements.assert_called_once()


def test_env_install_keeps_a_copy_still_building_after_a_timeout(mock_project):
    envs = Environments(mock_project, "fraud")
    envs.envs["fraud"].install_requirements.side_effect = environment_refused()
    envs.api.await_creation.side_effect = RuntimeError(
        "Timed out after 1800s waiting for environment"
    )

    result = _install_as_scott("env", "install", "fraud", "-f", "Users/scott/r.txt")

    assert result.exit_code == 1
    assert "The copy is kept: it may still be building" in result.output
    envs.envs["fraud-app-env-scott"].delete.assert_not_called()
    envs.envs["fraud-app-env-scott"].install_requirements.assert_not_called()
