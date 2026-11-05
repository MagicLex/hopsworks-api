"""``hops fg embed``: a searchable embedding group over a feature group's text column.

``hops fg embed SOURCE --text COL`` makes or updates the group's Python job
``GROUP_embed`` and starts one execution of it; the build runs in that job
(``embed_run``), never in the caller. ``hops fg embed delete GROUP`` removes
what it made. ``delete`` is the only subcommand, so any other first word is the
source feature group.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import tempfile
import uuid
from typing import Any

import click
from hopsworks.cli import embed_run, output, session


_RUNNER_CODE = (
    '"""Runs `hops` with the job arguments: the script of the job `hops fg embed` makes."""\n'
    "import sys\n\n"
    "from hopsworks.cli.main import cli\n\n"
    'cli.main(args=sys.argv[1:], prog_name="hops")\n'
)
_RUN = "run"
MAX_CONCURRENT = "maxConcurrentExecutions"
PLATFORM_CLASS_KEY = "platformClass"
PLATFORM_CLASS = "embed"
SCHEDULES = ("daily", "hourly")
#: Interval of the daily default, and the floor when no admin has granted a faster one.
DAY_MINUTES = 1440


class _EmbedGroup(click.Group):
    """`hops fg embed SOURCE ...` runs; `hops fg embed delete GROUP` deletes."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if not args or args[0] not in self.commands or args[0] == _RUN:
            args = [_RUN, *args]
        return super().parse_args(ctx, args)


class _RunCommand(click.Command):
    def format_usage(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        formatter.write_usage(
            ctx.parent.command_path if ctx.parent else ctx.command_path,
            " ".join(self.collect_usage_pieces(ctx)),
        )


@click.group("embed", cls=_EmbedGroup)
def fg_embed_group() -> None:
    """Embed a text column into a searchable feature group."""


@fg_embed_group.command(_RUN, cls=_RunCommand, hidden=True)
@click.argument("source")
@click.option("--version", type=int, help="Version of SOURCE; the latest when omitted.")
@click.option("--text", "text", required=True, help="The text column to embed.")
@click.option("--to", "to", help="The embedding group; SOURCE_TEXT_emb by default.")
@click.option(
    "--embedding-project",
    envvar="HOPSABLE_EMBEDDING_PROJECT",
    show_envvar=True,
    help="Project serving the embedding deployment.",
)
@click.option(
    "--embedding-deployment",
    envvar="HOPSABLE_EMBEDDING_BUILD_DEPLOYMENT",
    show_envvar=True,
    help="The build embedding deployment's name; embedderbuild by default.",
)
@click.option(
    "--embedding-model",
    envvar="HOPSABLE_EMBEDDING_MODEL",
    show_envvar=True,
    help="The model name sent to the deployment.",
)
@click.option(
    "--chunk-chars",
    type=click.IntRange(min=1),
    default=embed_run.CHUNK_CHARS,
    show_default=True,
    help="Characters per chunk.",
)
@click.option(
    "--chunk-overlap",
    type=click.IntRange(min=0),
    default=embed_run.CHUNK_OVERLAP,
    show_default=True,
    help="Characters each chunk repeats from the one before.",
)
@click.option(
    "--schedule",
    "every",
    is_flag=False,
    flag_value="daily",
    help="Also rebuild on a schedule: daily (the default when given alone), hourly, "
    "or a number of minutes. The minute and hour come from the project and group. "
    "Faster than daily needs an admin grant.",
)
@click.option("--env", "environment", help="Python environment of the job.")
@click.option(
    "--build-here",
    is_flag=True,
    hidden=True,
    help="Run the build in this process: what the job does.",
)
@click.option(
    "--readback-timeout",
    type=click.IntRange(min=0),
    default=embed_run.READBACK_S,
    show_default=True,
    help="Seconds to wait for writes and deletes to read back from both stores.",
)
@click.option(
    "--lease-timeout",
    type=click.IntRange(min=0),
    default=embed_run.LEASE_S,
    show_default=True,
    help="Seconds after which another run's lease on the group is taken over.",
)
@click.pass_context
def fg_embed(
    ctx: click.Context,
    source: str,
    version: int | None,
    text: str,
    to: str | None,
    embedding_project: str | None,
    embedding_deployment: str | None,
    embedding_model: str | None,
    chunk_chars: int,
    chunk_overlap: int,
    every: str | None,
    environment: str | None,
    build_here: bool,
    readback_timeout: int,
    lease_timeout: int,
) -> None:
    """Embed a text column of SOURCE into a searchable feature group.

    The build runs as the Python job GROUP_embed, one execution at a time per
    group. This command creates or updates the job, starts one execution,
    prints its id and returns; it does not wait. Read the job's state with
    `hops job info GROUP_embed` and its log with `hops job logs`. A start the
    platform refuses for capacity exits non-zero and starts nothing: run it
    again after the time it names.

    Each row's text is cut into chunks of --chunk-chars characters that
    overlap by --chunk-overlap; each chunk is a row keyed by (doc_id,
    chunk_ix), doc_id being the source row's primary key, with its text,
    embedding (cosine), content_sha and embedded_at. The model and dimension
    are in the group's description; another model or dimension makes a new
    version. A rerun embeds only changed chunks and deletes the chunks of
    shortened or deleted rows, from both stores. A failed source read changes
    nothing. A source over the supported input (50,000 chunks and 100 MB of
    text unless an admin granted more) is refused with nothing written.

    The embedding deployment is called with your own credentials. Search it
    from an agent with `hops agent init NAME --reads ... --search GROUP`.
    `hops fg embed delete GROUP` removes the group, its job and its schedule.

    \f
    Args:
        ctx: Click context.
        source: Source feature group.
        version: Source version.
        text: Text column.
        to: Embedding group name.
        embedding_project: Project serving the embedding deployment.
        embedding_deployment: Deployment name.
        embedding_model: Model name.
        chunk_chars: Characters per chunk.
        chunk_overlap: Overlap between chunks.
        every: Schedule of the job.
        environment: Python environment of the job.
        build_here: Run the build in this process.
        readback_timeout: Seconds a read-back waits.
        lease_timeout: Seconds before a lease is taken over.
    """
    from hopsworks.cli.commands import fg as fg_commands  # noqa: PLC0415
    from hopsworks_agents import embed as embed_client  # noqa: PLC0415

    if chunk_overlap >= chunk_chars:
        raise click.BadParameter(
            "Must be less than --chunk-chars.", param_hint="--chunk-overlap"
        )
    try:
        endpoint = embed_client.endpoint(
            embedding_project, embedding_deployment, embedding_model, build=True
        )
    except ValueError as exc:
        raise click.UsageError(
            f"{exc} Or pass --embedding-project and --embedding-model."
        ) from exc
    src = fg_commands._get_fg(ctx, source, version)
    if text not in [f.name for f in getattr(src, "features", []) or []]:
        raise click.BadParameter(
            f"Feature group '{src.name}' has no column '{text}'.", param_hint="--text"
        )
    group = (to or f"{src.name}_{text}_emb").lower()
    project = session.get_project(ctx)
    if build_here:
        _build(
            ctx, src, text, group, endpoint, chunk_chars, chunk_overlap,
            readback_timeout, lease_timeout,
        )  # fmt: skip
        return

    cron = schedule_cron(every, project.id, group) if every else None
    args = [
        "fg", "embed", src.name, "--version", str(src.version), "--text", text,
        "--to", group,
        "--embedding-project", endpoint[0],
        "--embedding-deployment", endpoint[1],
        "--embedding-model", endpoint[2],
        "--chunk-chars", str(chunk_chars), "--chunk-overlap", str(chunk_overlap),
        "--readback-timeout", str(readback_timeout),
        "--lease-timeout", str(lease_timeout),
        "--build-here",
    ]  # fmt: skip
    job = _ensure_job(project, group, src, text, args, environment)
    execution = _start(job, group)
    if cron:
        _schedule(job, group, cron, execution)

    summary = {
        "group": group,
        "job": job.name,
        "execution": getattr(execution, "id", None),
        "schedule": cron,
    }
    if output.JSON_MODE:
        output.print_json(summary)
        return
    output.success(
        "✓ Started execution %s of job %s: it builds %s and is not waited for",
        summary["execution"],
        job.name,
        group,
    )
    if cron:
        click.echo(f"Scheduled job {job.name} ({cron})")


def _build(
    ctx, src, text, group, endpoint, chunk_chars, chunk_overlap, readback_s, lease_s
) -> None:
    """The build itself: what the job's execution runs."""
    from hopsworks_agents import embed as embed_client  # noqa: PLC0415

    fs = session.get_feature_store(ctx)
    state = embed_run.State(
        session.get_project(ctx).get_dataset_api(), group, uuid.uuid4().hex
    )

    def embed(texts: list[str]) -> list[list[float]]:
        return embed_client.embed_texts(
            texts,
            project=endpoint[0],
            deployment=endpoint[1],
            model=endpoint[2],
        )

    try:
        max_chunks, max_bytes = embed_run.embed_limits()
        plan = embed_run.run(
            fs=fs,
            source=src,
            text=text,
            group=group,
            model=endpoint[2],
            embed=embed,
            state=state,
            size=chunk_chars,
            overlap=chunk_overlap,
            readback_s=readback_s,
            lease_s=lease_s,
            max_chunks=max_chunks,
            max_bytes=max_bytes,
            log=output.info,
        )
    except embed_run.EmbedError as exc:
        raise click.ClickException(str(exc)) from exc
    summary = {
        "group": group,
        "version": plan.version,
        "created": plan.created,
        "chunks": plan.desired,
        "embedded": plan.embedded,
        "upserted": plan.upserted,
        "deleted": plan.removed,
        "settled": plan.settled,
    }
    if output.JSON_MODE:
        output.print_json(summary)
        return
    output.success(
        "✓ %s v%s: %d chunks, %d embedded, %d deleted",
        group,
        plan.version,
        plan.desired,
        plan.embedded,
        plan.removed,
    )


def _owned(thing: Any, group: str) -> bool:
    return (
        (getattr(thing, "description", "") or "")
        .rstrip()
        .endswith(embed_run.OWNER + group)
    )


def _job_name(group: str) -> str:
    return f"{group}_embed"


def _runner(group: str) -> str:
    """The job's script: one per group, so deleting the group deletes it."""
    return f"{embed_run.STATE_DIR}/{group}_embed_job.py"


def _intervals() -> set[int]:
    """Minutes between builds that repeat evenly: a divisor of an hour, or whole hours dividing a day."""
    return {m for m in range(1, 60) if 60 % m == 0} | {
        60 * h for h in range(1, 25) if 24 % h == 0
    }


def schedule_cron(every: str, project_id: Any, group: str) -> str:
    """The Quartz cron of `--schedule`.

    Daily by default. The minute, and the hour of a daily or multi-hour build, come
    from a stable hash of the project id and the group name, so groups do not all fire
    at once. An interval below a day must reach the floor an admin grants in the
    Hopsworks variable platform_jobs_embed_min_interval_minutes; with none set, a day
    is the floor.
    """
    word = every.strip().lower().removeprefix("@")
    minutes = {"daily": DAY_MINUTES, "hourly": 60}.get(
        word, int(word) if word.isdigit() else 0
    )
    if minutes not in _intervals():
        raise click.BadParameter(
            f"Must be daily, hourly, or minutes that divide an hour or are whole hours "
            f"dividing a day, not '{every}'.",
            param_hint="--schedule",
        )
    if minutes < DAY_MINUTES:
        try:
            floor = embed_run.variable_int(embed_run.VAR_MIN_INTERVAL, DAY_MINUTES)
        except embed_run.EmbedError as exc:
            raise click.ClickException(str(exc)) from exc
        if minutes < floor:
            raise click.ClickException(
                f"A build every {minutes} min is faster than this cluster grants "
                f"({floor} min). Use --schedule daily, or ask an admin for a faster floor."
            )
    digest = hashlib.sha256(f"{project_id}/{group}".encode()).digest()
    minute, hour = digest[0] % 60, digest[1] % 24
    if minutes == DAY_MINUTES:
        return f"0 {minute} {hour} * * ?"
    if minutes == 60:
        return f"0 {minute} * * * ?"
    if minutes < 60:
        return f"0 {digest[0] % minutes}/{minutes} * * * ?"
    step = minutes // 60
    return f"0 {minute} {hour % step}/{step} * * ?"


def _ensure_job(project, group, src, text, args, environment) -> Any:
    """The group's job, made or updated to run `args`, one execution at a time, in the embed class."""
    from hopsworks.cli import versions  # noqa: PLC0415
    from hopsworks.cli.commands.files import makedirs  # noqa: PLC0415

    api = project.get_job_api()
    name = _job_name(group)
    try:
        existing = api.get_job(name)
    except Exception as exc:  # noqa: BLE001
        if not versions.not_found(exc):
            raise click.ClickException(f"Could not find job '{name}': {exc}") from exc
        existing = None
    if existing is not None and not _owned(existing, group):
        raise click.ClickException(
            f"Job '{name}' exists and was not made by hops fg embed: it is left alone and nothing was started."
        )
    dataset = project.get_dataset_api()
    try:
        makedirs(dataset, embed_run.STATE_DIR)
        with tempfile.TemporaryDirectory() as tmp:
            local = os.path.join(tmp, os.path.basename(_runner(group)))
            with open(local, "w", encoding="utf-8") as f:
                f.write(_RUNNER_CODE)
            dataset.upload(local, embed_run.STATE_DIR, overwrite=True)
        config = (
            existing.config if existing is not None else api.get_configuration("PYTHON")
        )
        config["appPath"] = _runner(group)
        config["defaultArgs"] = shlex.join(args)
        config[MAX_CONCURRENT] = 1
        config[PLATFORM_CLASS_KEY] = PLATFORM_CLASS
        if environment:
            config["environmentName"] = environment
        config["description"] = (
            f"Embeds {src.name} v{src.version} {text} into {group}. {embed_run.OWNER}{group}"
        )
        if existing is None:
            return api.create_job(name=name, config=config)
        return existing.save()
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(
            f"Could not prepare job '{name}': {exc}. Nothing was started."
        ) from exc


def start_refusal(exc: BaseException, group: str) -> str | None:
    """The refusal of an execution start, from the server's answer; None for any other failure.

    429: the platform's embed capacity (start rate or inventory) is full. 409: the job
    already has an execution (maxConcurrentExecutions is 1).
    """
    from hopsworks_common.client.exceptions import (  # noqa: PLC0415 - SDK loaded on use
        RestAPIError,
    )

    cause: BaseException | None = exc
    while cause is not None and not isinstance(cause, RestAPIError):
        cause = cause.__cause__
    if cause is None:
        return None
    status = cause.response.status_code
    if status == RestAPIError.STATUS_CODE_CONFLICT:
        return f"a build of {group} is already running"
    if status == 429:
        after = (cause.response.headers.get("Retry-After") or "").strip()
        when = f"{after} s" if after.isdigit() else "a while"
        return f"embed jobs are at capacity; nothing was started; retry after {when}"
    return None


def _start(job: Any, group: str) -> Any:
    """One execution of the job; a refusal exits non-zero, with no retry."""
    try:
        return job.run(await_termination=False)
    except Exception as exc:  # noqa: BLE001
        refusal = start_refusal(exc, group)
        if refusal is not None:
            raise click.ClickException(refusal) from exc
        raise click.ClickException(f"Could not start job '{job.name}': {exc}") from exc


def _schedule(job: Any, group: str, cron: str, execution: Any) -> None:
    try:
        job.schedule(cron_expression=cron, max_active_runs=1)
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(
            f"Could not schedule job '{job.name}': {exc}. "
            f"Execution {getattr(execution, 'id', '?')} was started."
        ) from exc


@fg_embed_group.command("delete")
@click.argument("group")
@click.option("--yes", is_flag=True, help="Skip the interactive confirmation prompt.")
@click.option(
    "--dry-run", is_flag=True, help="List what would be deleted and delete nothing."
)
@click.option(
    "--lease-timeout",
    type=click.IntRange(min=0),
    default=embed_run.LEASE_S,
    show_default=True,
    help="Seconds after which a run's lease no longer blocks the delete.",
)
@click.pass_context
def fg_embed_delete(
    ctx: click.Context, group: str, yes: bool, dry_run: bool, lease_timeout: int
) -> None:
    """Delete an embedding group, its job and its schedule.

    Only what `hops fg embed` made is deleted: the group's versions and the job
    whose descriptions name the group as their owner, the job's schedule, and
    the run state under Resources/hopsable-embed. The source feature group is
    untouched. Refused while an agent names the group or a run holds its lease.

    \f
    Args:
        ctx: Click context.
        group: Embedding group name.
        yes: Skip confirmation.
        dry_run: List only.
        lease_timeout: Seconds before a lease stops blocking.
    """
    import time  # noqa: PLC0415

    from hopsworks.cli import versions  # noqa: PLC0415

    project = session.get_project(ctx)
    fs = session.get_feature_store(ctx)
    dataset = project.get_dataset_api()
    try:
        found = embed_run._versions(fs, group)
    except embed_run.EmbedError as exc:
        raise click.ClickException(str(exc)) from exc
    groups = [fg for fg in found if _owned(fg, group)]
    kept = [fg.version for fg in found if not _owned(fg, group)]

    api = project.get_job_api()
    try:
        job = api.get_job(_job_name(group))
    except Exception as exc:  # noqa: BLE001
        if not versions.not_found(exc):
            raise click.ClickException(
                f"Could not find job '{_job_name(group)}': {exc}"
            ) from exc
        job = None
    if job is not None and not _owned(job, group):
        job = None

    users = _agents_naming(ctx, project, group)
    if users:
        raise click.ClickException(
            f"Agents {', '.join(users)} search '{group}': delete them first "
            "(hops agent delete NAME), then this group."
        )
    state = embed_run.State(dataset, group, "delete")
    held = state._read(state.lease)
    if held and time.time() - held.get("started_at", 0) <= lease_timeout:
        raise click.ClickException(
            f"A run of the embedding of '{group}' holds its lease (run {held.get('run_id')}): "
            "wait for it to end, then delete."
        )
    paths = [state.lease] if held else []
    if dataset.exists(_runner(group)):
        paths.append(_runner(group))
    paths += [
        f"{embed_run.STATE_DIR}/{group}_v{fg.version}"
        for fg in found
        if dataset.exists(f"{embed_run.STATE_DIR}/{group}_v{fg.version}")
    ]

    plan = {
        "group": group,
        "versions": [fg.version for fg in groups],
        "job": getattr(job, "name", None),
        "schedule": bool(job is not None and getattr(job, "job_schedule", None)),
        "files": paths,
        "kept_versions": kept,
    }
    if not groups and job is None and not paths:
        raise click.ClickException(
            f"Nothing made by hops fg embed is named '{group}'."
            + (f" Versions {kept} have no owner mark and are kept." if kept else "")
        )
    if output.JSON_MODE and dry_run:
        output.print_json(plan)
        return
    click.echo(
        f"Group {group} versions {plan['versions'] or 'none'}; "
        f"job {plan['job'] or 'none'}"
        f"{' and its schedule' if plan['schedule'] else ''}; files {paths or 'none'}"
        + (f"; versions {kept} are not owned and are kept" if kept else "")
    )
    if dry_run:
        return
    if not yes:
        output.confirm(
            f"Delete these for '{group}'? Their data cannot be restored.",
            f"Deleting the embedding group '{group}'",
        )
    try:
        if job is not None:
            if getattr(job, "job_schedule", None):
                job.unschedule()
            job.delete()
        for fg in groups:
            fg.delete()
        for path in paths:
            dataset.remove(path)
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(
            f"Could not delete embedding group '{group}': {exc}. Run the delete again for what is left."
        ) from exc
    if output.JSON_MODE:
        output.print_json(plan)
        return
    output.success("✓ Deleted embedding group %s", group)


def _agents_naming(ctx: click.Context, project: Any, group: str) -> list[str]:
    """Agents whose description names the group, or whose script quotes its name."""
    from hopsworks.cli.commands import agent as agent_commands  # noqa: PLC0415

    mark = re.compile(re.escape(embed_run.SEARCH + group) + r"(?![A-Za-z0-9_])")
    quoted = (f'"{group}"', f"'{group}'")
    try:
        deployments = project.get_model_serving().get_deployments() or []
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(
            f"Could not list agents to check that none searches '{group}': {exc}"
        ) from exc
    dataset = project.get_dataset_api()
    users = []
    for d in deployments:
        if not agent_commands._is_agent(d):
            continue
        if mark.search(getattr(d, "description", "") or ""):
            users.append(d.name)
            continue
        script = agent_commands._predictor_attr(d, "script_file", None)
        if not script:
            continue
        try:
            answer = dataset.read_content(script)
            body = getattr(answer, "content", answer) or b""
            code = (
                body.decode("utf-8", "replace")
                if isinstance(body, bytes)
                else str(body)
            )
        except Exception as exc:  # noqa: BLE001
            raise click.ClickException(
                f"Could not read the script of agent '{d.name}' to check it does not search '{group}': {exc}"
            ) from exc
        if any(q in code for q in quoted):
            users.append(d.name)
    return users
