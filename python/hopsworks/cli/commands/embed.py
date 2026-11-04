"""``hops fg embed``: a searchable embedding group over a feature group's text column.

``hops fg embed SOURCE --text COL`` runs the embedding (``embed_run``);
``hops fg embed delete GROUP`` removes what it made. ``delete`` is the only
subcommand, so any other first word is the source feature group.
"""

from __future__ import annotations

import os
import re
import shlex
import tempfile
import uuid
from typing import Any

import click
from hopsworks.cli import embed_run, output, session


_RUNNER_CODE = (
    '"""Runs `hops` with the job arguments: the job `hops fg embed --schedule` creates."""\n'
    "import sys\n\n"
    "from hopsworks.cli.main import cli\n\n"
    'cli.main(args=sys.argv[1:], prog_name="hops")\n'
)
_RUN = "run"


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
    envvar="HOPSABLE_EMBEDDING_DEPLOYMENT",
    show_envvar=True,
    help="The embedding deployment's name.",
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
    "cron",
    help="Also run it on this cron (Quartz or @daily-style) as a Python job.",
)
@click.option("--env", "environment", help="Python environment of the scheduled job.")
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
    cron: str | None,
    environment: str | None,
    readback_timeout: int,
    lease_timeout: int,
) -> None:
    """Embed a text column of SOURCE into a searchable feature group.

    Each row's text is cut into chunks of --chunk-chars characters that
    overlap by --chunk-overlap; each chunk is a row keyed by (doc_id,
    chunk_ix), doc_id being the source row's primary key, with its text,
    embedding (cosine), content_sha and embedded_at. The model and dimension
    are in the group's description; another model or dimension makes a new
    version. A rerun embeds only changed chunks and deletes the chunks of
    shortened or deleted rows, from both stores. A failed source read changes
    nothing.

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
        cron: Schedule for a job running this command.
        environment: Python environment of the job.
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
            embedding_project, embedding_deployment, embedding_model
        )
    except ValueError as exc:
        raise click.UsageError(
            f"{exc} Or pass --embedding-project, --embedding-deployment and --embedding-model."
        ) from exc
    src = fg_commands._get_fg(ctx, source, version)
    if text not in [f.name for f in getattr(src, "features", []) or []]:
        raise click.BadParameter(
            f"Feature group '{src.name}' has no column '{text}'.", param_hint="--text"
        )
    group = (to or f"{src.name}_{text}_emb").lower()
    project = session.get_project(ctx)
    fs = session.get_feature_store(ctx)
    state = embed_run.State(project.get_dataset_api(), group, uuid.uuid4().hex)

    def embed(texts: list[str]) -> list[list[float]]:
        return embed_client.embed_texts(
            texts,
            project=endpoint[0],
            deployment=endpoint[1],
            model=endpoint[2],
        )

    try:
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
            readback_s=readback_timeout,
            lease_s=lease_timeout,
            log=output.info,
        )
    except embed_run.EmbedError as exc:
        raise click.ClickException(str(exc)) from exc

    job_name = None
    if cron:
        args = [
            "fg", "embed", src.name, "--version", str(src.version), "--text", text,
            "--to", group,
            "--embedding-project", endpoint[0],
            "--embedding-deployment", endpoint[1],
            "--embedding-model", endpoint[2],
            "--chunk-chars", str(chunk_chars), "--chunk-overlap", str(chunk_overlap),
            "--readback-timeout", str(readback_timeout),
            "--lease-timeout", str(lease_timeout),
        ]  # fmt: skip
        job_name = _schedule(project, group, src, text, args, cron, environment)

    summary = {
        "group": group,
        "version": plan.version,
        "created": plan.created,
        "chunks": plan.desired,
        "embedded": plan.embedded,
        "upserted": plan.upserted,
        "deleted": plan.removed,
        "settled": plan.settled,
        "job": job_name,
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
    if job_name:
        click.echo(f"Scheduled job {job_name} ({cron})")


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


def _schedule(project, group, src, text, args, cron, environment) -> str:
    from hopsworks.cli import versions  # noqa: PLC0415
    from hopsworks.cli.commands.files import makedirs  # noqa: PLC0415
    from hopsworks.cli.commands.job import _expand_cron_alias  # noqa: PLC0415

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
            f"Job '{name}' exists and was not made by hops fg embed: the group is written, no job is scheduled."
        )
    dataset = project.get_dataset_api()
    try:
        makedirs(dataset, embed_run.STATE_DIR)
        with tempfile.TemporaryDirectory() as tmp:
            local = os.path.join(tmp, os.path.basename(_runner(group)))
            with open(local, "w", encoding="utf-8") as f:
                f.write(_RUNNER_CODE)
            dataset.upload(local, embed_run.STATE_DIR, overwrite=True)
        config = api.get_configuration("PYTHON")
        config["appPath"] = _runner(group)
        config["defaultArgs"] = shlex.join(args)
        if environment:
            config["environmentName"] = environment
        config["description"] = (
            f"Embeds {src.name} v{src.version} {text} into {group}. {embed_run.OWNER}{group}"
        )
        job = api.create_job(name=name, config=config)
        job.schedule(cron_expression=_expand_cron_alias(cron))
    except click.ClickException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(
            f"Could not schedule job '{name}': {exc}. The group is written."
        ) from exc
    return name


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
