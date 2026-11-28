"""``hops project``: list available projects, switch the active one, show its cost, leave one, and manage its members."""

from __future__ import annotations

from typing import Any

import click
from hopsworks.cli import auth, config, egress, output, session


@click.group("project")
def project_group() -> None:
    """List and switch Hopsworks projects."""


@project_group.command("list")
@click.pass_context
def project_list(ctx: click.Context) -> None:
    """Show every project the current user is a member of.

    \f
    Args:
        ctx: Click context carrying the resolved `HopsConfig`.
    """
    cfg = _require_auth(ctx)
    session.get_project(ctx)
    from hopsworks_common.core import project_api  # noqa: PLC0415

    # One request: the team list already carries each project's id and name,
    # where the SDK's _get_projects() would fetch every project again by name.
    teams = project_api.ProjectApi()._get_project_teams() or []
    rows = []
    for team in sorted(
        teams, key=lambda t: t.get("project", {}).get("name", "").lower()
    ):
        project = team.get("project", {})
        active = (
            "*"
            if (
                project.get("id") == cfg.project_id
                if cfg.project_id
                else project.get("name") == cfg.project
            )
            else ""
        )
        rows.append(
            [
                project.get("id", "?"),
                project.get("name", "?"),
                team.get("teamRole", ""),
                active,
            ]
        )
    output.print_table(["ID", "NAME", "ROLE", "ACTIVE"], rows)


@project_group.command("use")
@click.argument("name")
@click.pass_context
def project_use(ctx: click.Context, name: str) -> None:
    """Switch the default project.

    Switch the default project to `name` and cache its identifiers.
    A project name starts with a letter, so a numeric `name` is a project id.
    Use it when several projects share a name.

    \f
    Args:
        ctx: Click context carrying the resolved `HopsConfig`.
        name: Project name or id to activate.
    """
    cfg = _require_auth(ctx)
    by_id = name.isdigit()
    try:
        project = auth.login(
            host=cfg.host or "",
            api_key_value=cfg.api_key,
            project=None if by_id else name,
            project_id=int(name) if by_id else None,
            internal=cfg.internal,
            hostname_verification=cfg.hostname_verification,
        )
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(
            f"Could not switch to project '{name}': {exc}"
        ) from exc

    cfg.project = getattr(project, "name", name)
    cfg.project_id = getattr(project, "id", None)
    cfg.feature_store_id = _feature_store_id(project)
    config.save(cfg)
    output.success("✓ Active project set to %s", cfg.project)


def _feature_store_id(project: Any) -> int | None:
    """The project's feature store id, without building a feature store object.

    ``project.get_feature_store()`` imports hsfs, and with it pandas, pyarrow and
    the engine: close to three seconds on a laptop, spent on a number the REST
    response already carries and that only ``hops project info`` ever prints.
    ``None`` when the project has no feature store.
    """
    from hopsworks_common import (  # noqa: PLC0415 - keeps `hops --help` light
        client,
        util,
    )

    _client = client._get_instance()
    store = util._append_feature_store_suffix(
        getattr(project, "backing_name", "") or ""
    )
    try:
        raw = _client._send_request(
            "GET", ["project", _client._project_id, "featurestores", store]
        )
    except Exception:  # noqa: BLE001 - a project need not have a feature store
        return None
    return raw.get("featurestoreId")


@project_group.command("info")
@click.pass_context
def project_info(ctx: click.Context) -> None:
    """Print details about the currently active project.

    \f
    Args:
        ctx: Click context carrying the resolved `HopsConfig`.
    """
    cfg = _require_auth(ctx)
    if not cfg.project and not cfg.project_id and not cfg.internal:
        # The SDK would prompt for a project and wait on stdin.
        raise click.ClickException("No active project. Run `hops project use <name>`.")
    project = session.get_project(ctx)
    team = _team(getattr(project, "id", cfg.project_id))
    rows = [
        ["Host", cfg.host or "?"],
        ["Project", getattr(project, "name", cfg.project or "?")],
        ["Project ID", str(getattr(project, "id", cfg.project_id or "?"))],
        ["Owner", _owner_name(team) or "?"],
        ["Role", (team or {}).get("teamRole") or "?"],
        ["Feature store ID", str(cfg.feature_store_id or "?")],
        ["Mode", cfg.mode()],
    ]
    rows.extend(_compute_quota_rows())
    rows.append(egress.row())
    output.print_table(["FIELD", "VALUE"], rows)


def _compute_quota_rows() -> list[list[str]]:
    """Rows for the compute the project may use: used and limit per resource.

    A quota Hopsworks could not read is shown as unavailable, never as a value.
    """
    from hopsworks_common.core import rest  # noqa: PLC0415

    try:
        quota = rest._send_request("GET", rest._project_path("kube", "quota"))
    except Exception:  # noqa: BLE001 - the rest of the info still prints
        return [["Compute quota", "unavailable"]]
    method = quota.get("method") or "NONE"
    if method == "NONE":
        return [["Compute quota", "none"]]
    source = quota.get("source")
    rows = [["Compute quota", f"{method} ({source})" if source else method]]
    for label, key, scale, unit in (
        ("CPU", "cpu", 1, " cores"),
        ("Memory", "memory", 1024**3, " GiB"),
        ("GPU", "gpu", 1, ""),
    ):
        limit = quota.get(key)
        if limit and limit.get("limit") is not None:
            used = (limit.get("used") or 0) / scale
            text = f"{used:g} of {limit['limit'] / scale:g}{unit} used"
            if limit.get("canBorrow"):
                text += f", {limit['canBorrow'] / scale:g} more can be borrowed"
            rows.append([label, text])
    for warning in quota.get("warnings") or []:
        rows.append(["Warning", str(warning)])
    return rows


@project_group.command("leave")
@click.option("--yes", is_flag=True, help="Leave without asking to confirm.")
@click.pass_context
def project_leave(ctx: click.Context, yes: bool) -> None:
    """Leave the active project, as a member who does not own it.

    The owner cannot leave: Hopsworks refuses it. What you made in the project
    stays, and your home directory there goes to a data owner.

    \f
    Args:
        ctx: Click context carrying the resolved ``HopsConfig``.
        yes: Skip the confirmation prompt.
    """
    cfg = _require_auth(ctx)
    if not cfg.project and not cfg.project_id and not cfg.internal:
        # The SDK would prompt for a project and wait on stdin.
        raise click.ClickException("No active project. Run `hops project use <name>`.")
    project = session.get_project(ctx)
    name = getattr(project, "name", cfg.project or "?")
    from hopsworks_common import client  # noqa: PLC0415

    email = (
        client._get_instance()._send_request("GET", ["users", "profile"]).get("email")
    )
    if not email:
        raise click.ClickException("Could not read your user profile.")
    if not yes:
        output.confirm(f"Leave {name}?")
    try:
        project.remove_member(email)
    except Exception as exc:  # noqa: BLE001 - SDK raises a mix of types
        raise click.ClickException(f"Could not leave {name}: {exc}") from exc
    from hopsworks_common.client import session_cache  # noqa: PLC0415

    # The session keeps the project for an hour; a later command must not answer from it.
    session_cache.invalidate()
    config.forget_project(cfg.host, name)
    output.success("Left %s", name)


def _team(project_id: Any) -> dict | None:
    """The user's team row for the project: their role and the project's owner."""
    from hopsworks_common.core import project_api  # noqa: PLC0415

    for team in project_api.ProjectApi()._get_project_teams() or []:
        if team.get("project", {}).get("id") == project_id:
            return team
    return None


def _owner_name(team: dict | None) -> str | None:
    owner = (team or {}).get("project", {}).get("owner") or {}
    name = " ".join(p for p in (owner.get("fname"), owner.get("lname")) if p)
    return name or owner.get("username")


@project_group.command("costs")
@click.option(
    "--window",
    type=click.Choice(("hour", "day", "week", "month", "year"), case_sensitive=False),
    default="week",
    show_default=True,
    help="Time window ending now.",
)
@click.option(
    "--project",
    "project_name",
    default=None,
    help="Project to show; the active project when not given.",
)
@click.pass_context
def project_costs(ctx: click.Context, window: str, project_name: str | None) -> None:
    """Show the Kubernetes cost of a project over a recent window.

    Only the project's data owners can see it. Costs come from OpenCost; week,
    month and year also read the daily history Hopsworks has recorded so far.
    When some buckets of the window have no data, a coverage line says how
    many do; missing buckets are unknown cost, not zero.

    \f
    Args:
        ctx: Click context.
        window: One of hour, day, week, month, year.
        project_name: Project to show, or None for the active project.
    """
    import hopsworks
    from hopsworks.cli.commands.admin import print_costs

    session.get_project(ctx)
    try:
        costs = hopsworks.get_costs_api().get_project_costs(
            window, project=project_name
        )
    except Exception as exc:  # noqa: BLE001 - SDK raises a mix of types
        raise click.ClickException(f"Could not get project costs: {exc}") from exc
    print_costs(costs, "namespace", window, show_idle=False)


@project_group.group("members")
def members_group() -> None:
    """List and change the members of the active project."""


@members_group.command("list")
@click.pass_context
def members_list(ctx: click.Context) -> None:
    """List the members of the active project: role and owner.

    \f
    Args:
        ctx: Click context carrying the resolved ``HopsConfig``.
    """
    project = session.get_project(ctx)
    try:
        members = project.get_members()
    except Exception as exc:  # noqa: BLE001 - SDK raises a mix of types
        raise click.ClickException(f"Could not list members: {exc}") from exc
    owner = _owner_email(project)
    rows = []
    for m in sorted(members, key=lambda m: (m.email or "").casefold()):
        name = " ".join(p for p in (m.first_name, m.last_name) if p)
        rows.append(
            [
                m.email or "?",
                name or m.username or "?",
                m.role or "?",
                "*" if owner and (m.email or "").casefold() == owner else "",
            ]
        )
    output.print_table(["EMAIL", "NAME", "ROLE", "OWNER"], rows)


def _owner_email(project: Any) -> str:
    """The project's owner read now: a cached project keeps the owner it was read with."""
    from hopsworks_common import client  # noqa: PLC0415

    try:
        raw = client._get_instance()._send_request("GET", ["project", project.id])
    except Exception:  # noqa: BLE001 - the list still shows without the marker
        return ""
    return (raw.get("owner") or "").casefold()


@members_group.command("add")
@click.argument("email")
@click.option(
    "--role",
    required=True,
    help="Data owner, Data scientist, Observer, Feature store restricted, or a custom project role.",
)
@click.pass_context
def members_add(ctx: click.Context, email: str, role: str) -> None:
    """Add a Hopsworks user to the active project, by email.

    Only a Data owner of the project can add members.

    \f
    Args:
        ctx: Click context carrying the resolved ``HopsConfig``.
        email: Email address of the Hopsworks user to add.
        role: The project role to grant.
    """
    project = session.get_project(ctx)
    try:
        member = project.add_member(email, role)
    except Exception as exc:  # noqa: BLE001 - SDK raises a mix of types
        raise click.ClickException(f"Could not add member '{email}': {exc}") from exc
    output.success("Added %s as %s", email, getattr(member, "role", None) or role)


@members_group.command("set")
@click.argument("email")
@click.option("--role", required=True, help="The member's new project role.")
@click.pass_context
def members_set(ctx: click.Context, email: str, role: str) -> None:
    """Change a member's role in the active project.

    Only a Data owner of the project can change roles.

    \f
    Args:
        ctx: Click context carrying the resolved ``HopsConfig``.
        email: Email address of the member.
        role: The project role to give them.
    """
    project = session.get_project(ctx)
    try:
        project.get_members_api().update_role(email, role)
    except Exception as exc:  # noqa: BLE001 - SDK raises a mix of types
        raise click.ClickException(f"Could not set member '{email}': {exc}") from exc
    output.success("%s is %s", email, role)


@members_group.command("remove")
@click.argument("email")
@click.option(
    "--keep-schedules",
    is_flag=True,
    help="Leave the schedules of jobs the member created running.",
)
@click.option("--yes", is_flag=True, help="Remove without asking to confirm.")
@click.pass_context
def members_remove(
    ctx: click.Context, email: str, keep_schedules: bool, yes: bool
) -> None:
    """Remove a member from the active project.

    Hopsworks stops their runs and notebooks. The schedules of jobs they
    created keep starting runs on the owner's resources, so they are paused
    after the removal unless --keep-schedules is given. Their home directory
    is kept.

    \f
    Args:
        ctx: Click context carrying the resolved ``HopsConfig``.
        email: Email address of the member.
        keep_schedules: Leave their job schedules as they are.
        yes: Skip the confirmation prompt.
    """
    project = session.get_project(ctx)
    name = getattr(project, "name", "?")
    if not yes:
        pause = (
            "" if keep_schedules else " The schedules of jobs they created pause too."
        )
        output.confirm(
            f"Remove {email} from {name}?{pause}", action=f"Removing {email}"
        )
    try:
        project.remove_member(email, delete_home_dir=False)
    except Exception as exc:  # noqa: BLE001 - SDK raises a mix of types
        raise click.ClickException(f"Could not remove member '{email}': {exc}") from exc
    output.success("Removed %s from %s", email, name)
    if keep_schedules:
        return
    failed = _pause_schedules_of(project, email)
    if failed:
        raise click.ClickException(
            "Could not pause the schedule of " + ", ".join(failed)
        )


def _pause_schedules_of(project: Any, email: str) -> list[str]:
    """Pause each enabled schedule of a job the user created; the jobs whose pause failed."""
    try:
        jobs = project.get_job_api().get_jobs()
    except Exception as exc:  # noqa: BLE001 - SDK raises a mix of types
        raise click.ClickException(
            f"Removed {email}, but could not read the project's jobs: {exc}"
        ) from exc
    failed = []
    for job in jobs:
        schedule = getattr(job, "job_schedule", None)
        if not (schedule and getattr(schedule, "enabled", False)):
            continue
        if _email_of(getattr(job, "creator", None)) != email.casefold():
            continue
        try:
            job.pause_schedule()
        except Exception:  # noqa: BLE001 - named in the result
            failed.append(job.name)
            continue
        output.success("Paused the schedule of %s", job.name)
    return failed


def _email_of(user: Any) -> str:
    value = (
        user.get("email") if isinstance(user, dict) else getattr(user, "email", None)
    )
    return (value or "").casefold()


@members_group.command("transfer")
@click.argument("email")
@click.option("--yes", is_flag=True, help="Transfer without asking to confirm.")
@click.pass_context
def members_transfer(ctx: click.Context, email: str, yes: bool) -> None:
    """Make another Data owner of the active project its owner.

    Only the owner can do this. The new owner's resources pay for the
    project from then on; the previous owner stays a Data owner.

    \f
    Args:
        ctx: Click context carrying the resolved ``HopsConfig``.
        email: Email address of the Data owner who becomes the owner.
        yes: Skip the confirmation prompt.
    """
    project = session.get_project(ctx)
    name = getattr(project, "name", "?")
    if not yes:
        output.confirm(f"Make {email} the owner of {name}?")
    try:
        project.transfer_ownership(email)
    except Exception as exc:  # noqa: BLE001 - SDK raises a mix of types
        raise click.ClickException(
            f"Could not transfer project '{name}' to '{email}': {exc}"
        ) from exc
    output.success("%s owns %s", email, name)


def _require_auth(ctx: click.Context) -> config.HopsConfig:
    return session.require_auth(ctx)
