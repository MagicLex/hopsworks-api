"""``hops project list`` shows every membership, not just the active project.

The previous implementation feature-detected a ``hopsworks.get_project_api``
function that does not exist and fell back to the logged-in project, so the
table always had exactly one row.
"""

from __future__ import annotations

from unittest import mock

from click.testing import CliRunner
from hopsworks.cli import auth, config, session
from hopsworks.cli.main import cli
from hopsworks_common.core import project_api


TEAMS = [
    {"project": {"id": 124, "name": "linda"}, "teamRole": "Data owner"},
    {"project": {"id": 119, "name": "demo"}, "teamRole": "Data owner"},
    {"project": {"id": 136, "name": "Shared"}, "teamRole": "Data scientist"},
]


def _run_list(teams):
    with (
        mock.patch.object(auth, "login", return_value=mock.MagicMock(name="Project")),
        mock.patch.object(
            project_api.ProjectApi, "_get_project_teams", return_value=teams
        ),
    ):
        return CliRunner().invoke(cli, ["project", "list"])


def test_project_list_shows_every_membership_sorted_with_active_marked(authed_config):
    result = _run_list(TEAMS)

    assert result.exit_code == 0, result.output
    lines = [
        line for line in result.output.splitlines() if line and not line.startswith("-")
    ]
    assert lines[0].split() == ["ID", "NAME", "ROLE", "ACTIVE"]
    assert [line.split()[1] for line in lines[1:]] == ["demo", "linda", "Shared"]
    assert lines[1].split() == ["119", "demo", "Data", "owner", "*"]
    assert lines[2].split() == ["124", "linda", "Data", "owner"]
    assert "Data scientist" in result.output


def test_project_list_json_keeps_name_clean_and_flags_active(authed_config):
    with (
        mock.patch.object(auth, "login", return_value=mock.MagicMock(name="Project")),
        mock.patch.object(
            project_api.ProjectApi, "_get_project_teams", return_value=TEAMS
        ),
    ):
        result = CliRunner().invoke(cli, ["--json", "project", "list"])

    assert result.exit_code == 0, result.output
    assert '"NAME": "demo"' in result.output
    assert '"NAME": "demo *"' not in result.output
    assert '"ID": 124' in result.output
    assert '"ROLE": "Data scientist"' in result.output
    assert result.output.count('"ACTIVE": "*"') == 1


def test_project_list_with_no_memberships_prints_only_the_header(authed_config):
    result = _run_list([])

    assert result.exit_code == 0, result.output
    assert "ID" in result.output and "demo" not in result.output


def _use_project(send_request):
    """Run `hops project use demo` with the SDK login and REST client mocked out."""
    project = mock.MagicMock()
    project.name = "demo"
    project.backing_name = "demo"
    project.id = 119
    fake_client = mock.Mock()
    fake_client._project_id = 119
    fake_client._send_request = send_request
    with (
        mock.patch.object(auth, "login", return_value=project),
        mock.patch("hopsworks_common.client._get_instance", return_value=fake_client),
    ):
        result = CliRunner().invoke(cli, ["project", "use", "demo"])
    return result, project, fake_client


def test_project_use_reads_the_feature_store_id_over_rest(authed_config):
    """Building a FeatureStore imports hsfs (~3s); the id comes from the response."""
    result, project, fake_client = _use_project(
        mock.Mock(return_value={"featurestoreId": 67})
    )

    assert result.exit_code == 0, result.output
    project.get_feature_store.assert_not_called()
    assert fake_client._send_request.call_args.args[0] == "GET"
    assert fake_client._send_request.call_args.args[1] == [
        "project",
        119,
        "featurestores",
        "demo_featurestore",
    ]
    assert config.load().feature_store_id == 67


def test_project_use_survives_a_project_without_a_feature_store(authed_config):
    result, _, _ = _use_project(mock.Mock(side_effect=RuntimeError("no feature store")))

    assert result.exit_code == 0, result.output
    assert config.load().feature_store_id is None
    assert config.load().project == "demo"


def test_get_project_logs_in_by_the_saved_project_id(tmp_home):
    import click
    from hopsworks.cli import session

    cfg = config.HopsConfig(host="h", api_key="k", project="demo", project_id=7)
    ctx = click.Context(cli)
    ctx.obj = {"config": cfg}
    with mock.patch.object(auth, "login", return_value=mock.MagicMock()) as login:
        session.get_project(ctx)

    assert login.call_args.kwargs["project_id"] == 7
    assert login.call_args.kwargs["project"] == "demo"


def test_auth_login_sends_the_id_instead_of_the_name(monkeypatch):
    import sys

    fake_hopsworks = mock.MagicMock()
    monkeypatch.setitem(sys.modules, "hopsworks", fake_hopsworks)

    auth.login(host="h", api_key_value="k", project="demo", project_id=7)

    kwargs = fake_hopsworks.login.call_args.kwargs
    assert kwargs["project_id"] == 7
    assert "project" not in kwargs


def test_project_use_takes_a_numeric_argument_as_an_id(authed_config):
    project = mock.MagicMock()
    project.name = "demo"
    project.id = 9
    project.backing_name = "demo_b"
    with (
        mock.patch.object(auth, "login", return_value=project) as login,
        mock.patch("hopsworks_common.client._get_instance") as get_client,
    ):
        get_client.return_value._send_request.return_value = {"featurestoreId": 3}
        result = CliRunner().invoke(cli, ["project", "use", "9"])

    assert result.exit_code == 0, result.output
    assert login.call_args.kwargs["project_id"] == 9
    assert login.call_args.kwargs["project"] is None
    assert config.load().project_id == 9


def test_project_list_marks_the_active_project_by_id(authed_config):
    teams = [
        {"project": {"id": 7, "name": "demo"}, "teamRole": "Data owner"},
        {"project": {"id": 9, "name": "demo"}, "teamRole": "Data owner"},
    ]
    cfg = config.load()
    cfg.project_id = 9
    config.save(cfg)

    result = _run_list(teams)

    rows = [line.split() for line in result.output.splitlines() if "demo" in line]
    assert [row[0] for row in rows if row[-1] == "*"] == ["9"]


def test_files_share_sends_a_numeric_target_as_an_id_without_a_lookup(mock_project):
    with mock.patch.object(project_api.ProjectApi, "_get_project_by_id") as lookup:
        result = CliRunner().invoke(
            cli, ["files", "share", "Resources/d", "--target", "22"]
        )

    assert result.exit_code == 0, result.output
    lookup.assert_not_called()
    dataset_api = mock_project.get_dataset_api.return_value
    assert dataset_api.share.call_args.kwargs["target_project"] == 22


def test_files_unshare_sends_a_numeric_target_as_an_id(mock_project):
    result = CliRunner().invoke(
        cli, ["files", "unshare", "Resources/d", "--target", "22"]
    )

    assert result.exit_code == 0, result.output
    dataset_api = mock_project.get_dataset_api.return_value
    assert dataset_api.unshare.call_args.kwargs["target_project"] == 22


def test_hops_login_verifies_with_the_saved_project_id(tmp_home):
    config.save(
        config.HopsConfig(host="https://h", api_key="k", project="demo", project_id=22)
    )
    project = mock.MagicMock()
    project.name = "demo"
    project.id = 22
    with mock.patch.object(auth, "verify", return_value=project) as verify:
        result = CliRunner().invoke(cli, ["login"])

    assert result.exit_code == 0, result.output
    assert verify.call_args.kwargs["project_id"] == 22


def test_a_saved_id_of_a_deleted_project_names_the_id_and_the_fix(tmp_home):
    import click
    from hopsworks.cli import session
    from hopsworks_common.client.exceptions import ProjectException

    cfg = config.HopsConfig(host="h", api_key="k", project="demo", project_id=22)
    ctx = click.Context(cli)
    ctx.obj = {"config": cfg}
    gone = ProjectException("Could not find project with id 22")
    with mock.patch.object(auth, "login", side_effect=gone):
        try:
            session.get_project(ctx)
        except click.ClickException as exc:
            assert "22" in exc.message and "hops project use" in exc.message
        else:
            raise AssertionError("expected a ClickException")


QUOTA = {
    "method": "KUEUE",
    "source": "other",
    "cpu": {"limit": 4.15, "used": 1.5, "available": 2.65},
    "memory": {"limit": 17152 * 1024**2, "used": 2 * 1024**3, "available": 0},
    "warnings": [],
}


def _run_info(send_request):
    from hopsworks_common.core import project_api, rest

    with (
        mock.patch.object(auth, "login", return_value=mock.MagicMock(name="Project")),
        mock.patch.object(
            project_api.ProjectApi, "_get_project_teams", return_value=[]
        ),
        mock.patch.object(rest, "_send_request", side_effect=send_request),
        mock.patch.object(rest, "_project_path", side_effect=lambda *p: list(p)),
    ):
        return CliRunner().invoke(cli, ["project", "info"])


def test_project_info_shows_the_compute_quota_and_its_use(authed_config):
    result = _run_info(lambda method, path: QUOTA)
    assert result.exit_code == 0, result.output
    assert "KUEUE (other)" in result.output
    assert "1.5 of 4.15 cores used" in result.output
    assert "2 of 16.75 GiB used" in result.output
    assert "GPU" not in result.output


def test_project_info_shows_what_a_kueue_queue_can_borrow(authed_config):
    borrowing = {
        "method": "KUEUE",
        "source": "other",
        "cpu": {"limit": 0.0, "used": 8.5, "available": 0.0, "canBorrow": 91.5},
        "memory": {
            "limit": 0.0,
            "used": 2 * 1024**3,
            "available": 0.0,
            "canBorrow": 10 * 1024**3,
        },
        "warnings": [],
    }
    result = _run_info(lambda method, path: borrowing)
    assert result.exit_code == 0, result.output
    assert "8.5 of 0 cores used, 91.5 more can be borrowed" in result.output
    assert "2 of 0 GiB used, 10 more can be borrowed" in result.output


def test_project_info_says_unavailable_when_the_quota_cannot_be_read(authed_config):
    def fail(method, path):
        raise RuntimeError("503")

    result = _run_info(fail)
    assert result.exit_code == 0, result.output
    assert "unavailable" in result.output
    assert "Project" in result.output


def test_project_info_shows_the_egress_policy(authed_config):
    from tests.cli.test_egress import ENFORCED

    result = _run_info(lambda method, path: ENFORCED if path[-1] == "egress" else QUOTA)
    assert result.exit_code == 0, result.output
    assert (
        "enforced: 443/TCP 80/TCP 8000-8010/TCP (global); 6543/TCP (project)"
        in result.output
    )


def test_project_info_says_egress_is_unavailable_when_it_cannot_be_read(authed_config):
    def send(method, path):
        if path[-1] == "egress":
            raise RuntimeError("503")
        return QUOTA

    result = _run_info(send)
    assert result.exit_code == 0, result.output
    assert "Egress" in result.output and "unavailable" in result.output
    assert "KUEUE (other)" in result.output


def _member(email, role, first="", last=""):
    m = mock.MagicMock()
    m.email, m.role, m.first_name, m.last_name, m.username = (
        email,
        role,
        first,
        last,
        None,
    )
    return m


def _members_project():
    project = mock.MagicMock()
    project.name = "demo"
    project.owner = "Owner@example.com"
    project.get_members.return_value = [
        _member("zed@example.com", "Data scientist", "Zed"),
        _member("owner@example.com", "Data owner", "Ann", "Owner"),
    ]
    return project


def _members(args, project, input=None, owner="Owner@example.com"):
    fake_client = mock.Mock()
    fake_client._send_request.return_value = {"owner": owner}
    with (
        mock.patch.object(session, "get_project", return_value=project),
        mock.patch("hopsworks_common.client._get_instance", return_value=fake_client),
    ):
        return CliRunner().invoke(cli, ["project", "members", *args], input=input)


def test_members_list_sorts_by_email_and_marks_the_owner(authed_config):
    result = _members(["list"], _members_project())

    assert result.exit_code == 0, result.output
    rows = [line.split() for line in result.output.splitlines()[2:] if line]
    assert rows[0] == ["owner@example.com", "Ann", "Owner", "Data", "owner", "*"]
    assert rows[1] == ["zed@example.com", "Zed", "Data", "scientist"]


def test_members_list_marks_the_owner_read_now_not_the_cached_one(authed_config):
    project = _members_project()
    project.owner = "owner@example.com"

    result = _members(["list"], project, owner="zed@example.com")

    assert result.exit_code == 0, result.output
    rows = [line.split() for line in result.output.splitlines()[2:] if line]
    assert rows[0][-1] != "*"
    assert rows[1][-1] == "*"


def test_members_add_passes_email_and_role(authed_config):
    project = _members_project()
    project.add_member.return_value = _member("new@example.com", "Data scientist")

    result = _members(["add", "new@example.com", "--role", "data scientist"], project)

    assert result.exit_code == 0, result.output
    project.add_member.assert_called_once_with("new@example.com", "data scientist")
    assert "Added new@example.com as Data scientist" in result.output


def test_members_add_refused_shows_hopsworks_reason(authed_config):
    project = _members_project()
    project.add_member.side_effect = RuntimeError("Your role does not allow it")

    result = _members(["add", "new@example.com", "--role", "Data owner"], project)

    assert result.exit_code == 1
    assert (
        "Could not add member 'new@example.com': Your role does not allow it"
        in result.output
    )


def test_members_set_updates_the_role(authed_config):
    project = _members_project()

    result = _members(["set", "zed@example.com", "--role", "Data owner"], project)

    assert result.exit_code == 0, result.output
    project.get_members_api.return_value.update_role.assert_called_once_with(
        "zed@example.com", "Data owner"
    )


def _job(name, creator_email, enabled=True, fails=False):
    job = mock.MagicMock()
    job.name = name
    job.creator = {"email": creator_email, "username": name}
    job.job_schedule = mock.MagicMock(enabled=enabled) if enabled is not None else None
    if fails:
        job.pause_schedule.side_effect = RuntimeError("refused")
    return job


def test_members_remove_answered_no_changes_nothing(authed_config, tty):
    project = _members_project()

    result = _members(["remove", "zed@example.com"], project, input="n\n")

    assert result.exit_code == 1
    project.remove_member.assert_not_called()


def test_members_remove_keeps_the_home_dir_then_pauses_only_their_enabled_schedules(
    authed_config,
):
    project = _members_project()
    theirs, disabled, unscheduled, others = (
        _job("theirs", "Zed@Example.com"),
        _job("disabled", "zed@example.com", enabled=False),
        _job("unscheduled", "zed@example.com", enabled=None),
        _job("others", "owner@example.com"),
    )
    project.get_job_api.return_value.get_jobs.return_value = [
        theirs,
        disabled,
        unscheduled,
        others,
    ]
    calls = mock.Mock()
    calls.attach_mock(project.remove_member, "remove")
    calls.attach_mock(project.get_job_api.return_value.get_jobs, "jobs")

    result = _members(["remove", "zed@example.com", "--yes"], project)

    assert result.exit_code == 0, result.output
    assert [c[0] for c in calls.mock_calls] == ["remove", "jobs"]
    project.remove_member.assert_called_once_with(
        "zed@example.com", delete_home_dir=False
    )
    theirs.pause_schedule.assert_called_once_with()
    for job in (disabled, unscheduled, others):
        job.pause_schedule.assert_not_called()


def test_members_remove_keep_schedules_reads_no_jobs(authed_config):
    project = _members_project()

    result = _members(
        ["remove", "zed@example.com", "--yes", "--keep-schedules"], project
    )

    assert result.exit_code == 0, result.output
    project.remove_member.assert_called_once()
    project.get_job_api.assert_not_called()


def test_members_remove_names_a_failed_pause_and_exits_1(authed_config):
    project = _members_project()
    project.get_job_api.return_value.get_jobs.return_value = [
        _job("stuck", "zed@example.com", fails=True),
        _job("fine", "zed@example.com"),
    ]

    result = _members(["remove", "zed@example.com", "--yes"], project)

    assert result.exit_code == 1
    project.remove_member.assert_called_once()
    assert "Could not pause the schedule of stuck" in result.output


def test_members_remove_refused_reads_no_jobs(authed_config):
    project = _members_project()
    project.remove_member.side_effect = RuntimeError("not a member")

    result = _members(["remove", "x@example.com", "--yes"], project)

    assert result.exit_code == 1
    assert "Could not remove member 'x@example.com': not a member" in result.output
    project.get_job_api.assert_not_called()


def test_members_transfer_confirms_then_transfers(authed_config, tty):
    project = _members_project()

    declined = _members(["transfer", "zed@example.com"], project, input="n\n")
    assert declined.exit_code == 1
    project.transfer_ownership.assert_not_called()

    result = _members(["transfer", "zed@example.com", "--yes"], project)
    assert result.exit_code == 0, result.output
    project.transfer_ownership.assert_called_once_with("zed@example.com")
    assert "zed@example.com owns demo" in result.output


def test_members_transfer_refused_shows_hopsworks_reason(authed_config):
    project = _members_project()
    project.transfer_ownership.side_effect = RuntimeError("not a data owner")

    result = _members(["transfer", "zed@example.com", "--yes"], project)

    assert result.exit_code == 1
    assert (
        "Could not transfer project 'demo' to 'zed@example.com': not a data owner"
        in result.output
    )


def test_members_remove_and_transfer_without_a_terminal_name_yes(authed_config):
    project = _members_project()

    for args in (["remove", "zed@example.com"], ["transfer", "zed@example.com"]):
        result = _members(args, project)
        assert result.exit_code == 1
        assert "pass --yes" in result.output
    project.remove_member.assert_not_called()
    project.transfer_ownership.assert_not_called()
