"""A confirm prompt with no one to answer it fails naming --yes; --yes skips it."""

from __future__ import annotations

from unittest import mock

import pytest
from click.testing import CliRunner
from hopsworks.cli import output
from hopsworks.cli.main import cli


@pytest.fixture
def job(mock_project):
    return mock_project.get_job_api.return_value.get_job.return_value


def test_hops_no_input_names_yes_even_on_a_terminal(job, tty):
    result = CliRunner().invoke(
        cli, ["job", "delete", "etl"], env={"HOPS_NO_INPUT": "1"}
    )
    assert result.exit_code == 1
    assert "Delete job 'etl' needs confirmation: pass --yes." in result.output
    assert "Aborted!" not in result.output
    job.delete.assert_not_called()


def test_no_terminal_names_yes_without_asking(job):
    result = CliRunner().invoke(cli, ["job", "delete", "etl"], input="y\n")
    assert result.exit_code == 1
    assert "Delete job 'etl' needs confirmation: pass --yes." in result.output
    assert "[y/N]" not in result.output
    job.delete.assert_not_called()


def test_json_output_still_needs_yes(job):
    result = CliRunner().invoke(cli, ["job", "delete", "etl", "--json"])
    assert result.exit_code == 1
    assert "needs confirmation: pass --yes." in result.output
    job.delete.assert_not_called()


def test_yes_deletes_without_asking(job):
    result = CliRunner().invoke(
        cli, ["job", "delete", "etl", "--yes"], env={"HOPS_NO_INPUT": "1"}
    )
    assert result.exit_code == 0, result.output
    job.delete.assert_called_once_with()


def test_an_answer_on_a_terminal_still_decides(job, tty):
    declined = CliRunner().invoke(cli, ["job", "delete", "etl"], input="n\n")
    assert declined.exit_code == 1
    assert "Aborted!" in declined.output
    job.delete.assert_not_called()

    accepted = CliRunner().invoke(cli, ["job", "delete", "etl"], input="y\n")
    assert accepted.exit_code == 0, accepted.output
    # The question goes to stderr, so a piped stdout holds only the result.
    assert "Delete job 'etl'? [y/N]" in accepted.stderr
    assert "Delete job 'etl'?" not in accepted.stdout
    job.delete.assert_called_once_with()


def test_ctrl_c_at_the_prompt_aborts(job, tty):
    with mock.patch("click.termui.visible_prompt_func", side_effect=KeyboardInterrupt):
        result = CliRunner().invoke(cli, ["job", "delete", "etl"])
    assert result.exit_code == 1
    assert "Aborted!" in result.output
    assert "needs confirmation" not in result.output
    job.delete.assert_not_called()


def test_a_question_with_more_text_names_only_its_action(mock_project):
    fs = mock_project.get_feature_store.return_value
    fs.get_feature_group.return_value.version = 1
    result = CliRunner().invoke(cli, ["fg", "delete", "sales", "--version", "1"])
    assert result.exit_code == 1
    assert (
        "Deleting feature group 'sales' v1 needs confirmation: pass --yes."
        in result.output
    )


@pytest.mark.parametrize(
    ("argv", "action"),
    [
        (["job", "schedule", "delete", "etl"], "Remove the schedule of job 'etl'"),
        (["git", "provider", "delete"], "Remove the GitHub token for github.com"),
    ],
)
def test_commands_that_gained_a_confirmation(mock_project, argv, action):
    with (
        mock.patch("hopsworks.cli.commands.git.find_provider", return_value=object()),
        mock.patch("hopsworks.cli.commands.git.git_provider_api") as providers,
    ):
        result = CliRunner().invoke(cli, argv)
        assert result.exit_code == 1
        assert f"{action} needs confirmation: pass --yes." in result.output
        job = mock_project.get_job_api.return_value.get_job.return_value
        providers.GitProviderApi.return_value._delete_provider.assert_not_called()
        job.unschedule.assert_not_called()
        assert CliRunner().invoke(cli, [*argv, "--yes"]).exit_code == 0


def test_a_question_takes_its_default_when_no_one_can_answer(monkeypatch):
    monkeypatch.setenv("HOPS_NO_INPUT", "1")
    assert output.ask("Build an app?", default=True) is True
    assert output.ask("Push now?", default=False) is False
