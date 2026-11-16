"""``hops job`` — Hopsworks jobs (reads + lifecycle writes).

Run, stop, schedule, unschedule, and log retrieval all go through the SDK
``Job``/``Execution`` classes. History uses ``job.get_executions()`` and
follows the same memoized-session pattern as the rest of the CLI.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import tempfile
from typing import TYPE_CHECKING, Any

import click
from hopsworks.cli import output, session, waits
from hopsworks.cli.commands.alert import job_alert_group
from hopsworks.cli.commands.files import makedirs


if TYPE_CHECKING:
    from datetime import datetime

    from hopsworks_common.execution import Execution


START_TIMEOUT = 600

_start_timeout_option = click.option(
    "--start-timeout",
    type=int,
    default=START_TIMEOUT,
    show_default=True,
    help="Seconds to wait for the execution to reach RUNNING; past it the command fails and leaves the execution as is.",
)


def _user_label(value: Any) -> str:
    """Render a Hopsworks user reference (dict / str / object) as one string.

    The REST API returns ``creator`` as a dict (``{username, firstname, ...}``)
    on some endpoints and as a username string on others. Pick the most useful
    field; fall back to ``"-"`` rather than the dict's repr.
    """
    if not value:
        return "-"
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return (
            value.get("username")
            or value.get("email")
            or f"{value.get('firstname', '')} {value.get('lastname', '')}".strip()
            or "-"
        )
    return getattr(value, "username", None) or getattr(value, "email", None) or "-"


def _ts_label(value: Any) -> str:
    """Render a Hopsworks timestamp (epoch ms / ISO string / None) as ISO-8601.

    Job endpoints return ``creationTime`` as epoch milliseconds; some other
    endpoints return ISO strings. Normalize to ISO-8601 UTC for display.
    """
    if value in (None, "", "-"):
        return "-"
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10**12 else value
        from datetime import datetime as _dt  # noqa: PLC0415
        from datetime import timezone as _tz

        return _dt.fromtimestamp(seconds, tz=_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


@click.group("job")
def job_group() -> None:
    """Jobs: Python, Spark and Docker programs the cluster runs."""


@job_group.group("schedule")
def job_schedule_group() -> None:
    """The cron schedule of a job."""


@job_group.group("tag")
def job_tag_group() -> None:
    """Tags (name/value pairs) on a job."""


job_group.add_command(job_alert_group)


@job_group.command("list")
@click.pass_context
def job_list(ctx: click.Context) -> None:
    """List every job defined in the active project.

    \f
    Args:
        ctx: Click context.
    """
    project = session.get_project(ctx)
    try:
        jobs = project.get_job_api().get_jobs()
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not list jobs: {exc}") from exc

    rows = []
    for j in jobs or []:
        rows.append(
            [
                getattr(j, "id", "?"),
                getattr(j, "name", "?"),
                getattr(j, "job_type", "-"),
                _user_label(getattr(j, "creator", None)),
                _ts_label(getattr(j, "creation_time", None)),
            ]
        )
    output.print_table(["ID", "NAME", "TYPE", "CREATOR", "CREATED"], rows)


@job_group.command("info")
@click.argument("name")
@click.pass_context
def job_info(ctx: click.Context, name: str) -> None:
    """Show the configuration of a single job.

    \f
    Args:
        ctx: Click context.
        name: Job name.
    """
    project = session.get_project(ctx)
    try:
        job = project.get_job_api().get_job(name)
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not find job '{name}': {exc}") from exc
    if job is None:
        raise click.ClickException(f"Job '{name}' not found.")

    if output.JSON_MODE:
        output.print_json(_job_to_dict(job))
        return

    rows = [
        ["ID", getattr(job, "id", "?")],
        ["Name", getattr(job, "name", "?")],
        ["Type", getattr(job, "job_type", "-")],
        ["Creator", _user_label(getattr(job, "creator", None))],
        ["Created", _ts_label(getattr(job, "creation_time", None))],
        ["Description", output.first_line(getattr(job, "description", None))],
        ["Tags", output.format_mapping(output.read_tags(job))],
    ]
    output.print_table(["FIELD", "VALUE"], rows)


def _job_to_dict(job: Any) -> dict[str, Any]:
    config = getattr(job, "config", None)
    creator = getattr(job, "creator", None)
    if creator is not None and not isinstance(creator, (str, dict)):
        creator = {
            k: v
            for k, v in (
                ("username", getattr(creator, "username", None)),
                ("email", getattr(creator, "email", None)),
                ("firstname", getattr(creator, "firstname", None)),
                ("lastname", getattr(creator, "lastname", None)),
            )
            if v is not None
        } or _user_label(creator)
    return {
        "id": getattr(job, "id", None),
        "name": getattr(job, "name", None),
        "type": getattr(job, "job_type", None),
        "creator": creator,
        "creation_time": getattr(job, "creation_time", None),
        "description": getattr(job, "description", None),
        "tags": output.read_tags(job),
        "config": config.to_dict() if hasattr(config, "to_dict") else config,
    }


# region Write commands


@job_group.command("create")
@click.argument("name")
@click.option(
    "--type",
    "job_type",
    type=click.Choice(["PYTHON", "PYSPARK", "SPARK", "DOCKER"], case_sensitive=False),
    required=True,
    help="Job type.",
)
@click.option(
    "--app-path", "app_path", required=True, help="HDFS/HopsFS path to the main file."
)
@click.option(
    "--env",
    "environment",
    help="Python environment name (sets environmentName; otherwise the type default).",
)
@click.option(
    "--args",
    "app_args",
    help='Arguments passed to the program, split as shell words: quote a value that holds spaces ("2026-10-16 00:00:00").',
)
@click.option("--description", help="Free-form description of the job.")
@click.pass_context
def job_create(
    ctx: click.Context,
    name: str,
    job_type: str,
    app_path: str,
    environment: str | None,
    app_args: str | None,
    description: str | None,
) -> None:
    """Create a job from a program on HopsFS.

    \f
    Args:
        ctx: Click context.
        name: Job name.
        job_type: One of `PYTHON`/`PYSPARK`/`SPARK`/`DOCKER`.
        app_path: HopsFS path to the script or JAR.
        environment: Python environment the job runs in; the type's default when omitted.
        app_args: Optional argument string passed to the job.
        description: Optional free-form description.
    """
    project = session.get_project(ctx)
    api = project.get_job_api()
    try:
        config = api.get_configuration(job_type.upper())
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not load default config: {exc}") from exc
    config["appPath"] = app_path
    if environment:
        config["environmentName"] = environment
    if app_args:
        config["defaultArgs"] = app_args
    if description:
        config["description"] = description
    try:
        job = api.create_job(name=name, config=config)
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not create job: {exc}") from exc
    output.success("✓ Created job %s", getattr(job, "name", name))


@job_group.command("deploy")
@click.argument("name")
@click.argument("script")
@click.option(
    "--type",
    "job_type",
    type=click.Choice(["PYTHON", "PYSPARK", "SPARK", "DOCKER"], case_sensitive=False),
    default="PYTHON",
    show_default=True,
    help="Job type.",
)
@click.option(
    "--env",
    "environment",
    help="Python environment name (sets environmentName; otherwise the type default).",
)
@click.option(
    "--args",
    "app_args",
    help='Arguments passed to the program, split as shell words: quote a value that holds spaces ("2026-10-16 00:00:00").',
)
@click.option(
    "--cron",
    help="Schedule (Quartz cron or @hourly/@daily/@weekly/@monthly shorthand).",
)
@click.option("--run", "do_run", is_flag=True, help="Run once after deploying.")
@click.option(
    "--wait/--no-wait",
    default=False,
    show_default=True,
    help="With --run, wait until the execution finishes.",
)
@click.option(
    "--upload-dir",
    default=None,
    help="HopsFS dir to upload a local script to (default: Resources/jobs/<name>).",
)
@click.option(
    "--overwrite/--no-overwrite",
    default=True,
    show_default=True,
    help="Overwrite the uploaded script if it exists: a redeploy runs the new script.",
)
@_start_timeout_option
@click.pass_context
def job_deploy(
    ctx: click.Context,
    name: str,
    script: str,
    job_type: str,
    environment: str | None,
    app_args: str | None,
    cron: str | None,
    do_run: bool,
    wait: bool,
    upload_dir: str | None,
    overwrite: bool,
    start_timeout: int,
) -> None:
    """Upload, configure, schedule and run a job in one step.

    One-shot for what `create` + `schedule` + `run` do separately.
    `SCRIPT` is either a local file (uploaded to HopsFS) or an existing HopsFS path.

    \f
    Args:
        ctx: Click context.
        name: Job name (created or updated).
        script: Local file to upload, or an existing HopsFS path.
        job_type: One of PYTHON/PYSPARK/SPARK/DOCKER.
        environment: Python environment name; the type default if omitted.
        app_args: Argument string passed to the job.
        cron: Schedule; Quartz or a shorthand. No schedule if omitted.
        do_run: Run once after deploying.
        wait: With `do_run`, block until the execution terminates.
        upload_dir: HopsFS directory for a local script upload.
        overwrite: Overwrite an existing uploaded script.
        start_timeout: With `wait`, seconds to wait for the execution to reach RUNNING.
    """
    project = session.get_project(ctx)
    api = project.get_job_api()

    # 1. Resolve the app path — upload when a local file was given.
    app_path = script
    if os.path.isfile(script):
        dataset = project.get_dataset_api()
        dest_dir = upload_dir or f"Resources/jobs/{name}"
        try:
            makedirs(dataset, dest_dir)
            uploaded = dataset.upload(
                local_path=script, upload_path=dest_dir, overwrite=overwrite
            )
        except Exception as exc:  # noqa: BLE001
            raise click.ClickException(f"Could not upload script: {exc}") from exc
        app_path = uploaded or f"{dest_dir}/{os.path.basename(script)}"
        output.success("✓ Uploaded %s -> %s", os.path.basename(script), app_path)

    # 2. Build the config, including the environment the CLI otherwise can't set.
    try:
        config = api.get_configuration(job_type.upper())
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not load default config: {exc}") from exc
    config["appPath"] = app_path
    if app_args:
        config["defaultArgs"] = app_args
    if environment:
        config["environmentName"] = environment

    # 3. Create or update the job.
    try:
        job = api.create_job(name=name, config=config)
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not deploy job: {exc}") from exc
    output.success("✓ Deployed job %s (env=%s)", name, environment or "default")

    # 4. Schedule.
    if cron:
        cron_expr = _expand_cron_alias(cron)
        try:
            job.schedule(cron_expression=cron_expr)
        except Exception as exc:  # noqa: BLE001
            raise click.ClickException(
                f"Could not schedule job '{name}': {exc}"
            ) from exc
        output.success("✓ Scheduled %s (%s)", name, cron_expr)

    # 5. Run.
    if do_run:
        _run(job, name, wait, "run", start_timeout=start_timeout, args=app_args)


LOG_TAIL = 50


def _run(
    job: Any,
    name: str,
    wait: bool,
    verb: str,
    window: str = "",
    start_timeout: int = START_TIMEOUT,
    **run_args: Any,
) -> Any:
    """Run `job` once, report it, and on a failed wait print its log tails and exit non-zero."""
    try:
        execution = job.run(await_termination=False, **run_args)
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not {verb} job '{name}': {exc}") from exc
    if wait and execution is not None:
        execution = _await(job, name, execution, None, window, start_timeout)
    _report_run(name, execution, wait, window)
    return execution


def _await(
    job: Any,
    name: str,
    execution: Any,
    timeout: int | None,
    window: str = "",
    start_timeout: int = START_TIMEOUT,
) -> Any:
    """The execution once it ends; a failure prints its log tails and exits 1.

    Prints each state of the execution. The wait for RUNNING is bounded by `start_timeout`,
    the whole wait by `timeout` (unbounded when None).
    """
    watch = waits.Watch(
        f"{name}{window}: execution #{execution.id}", start_timeout, _POLL_SECONDS
    )
    final = _follow(job, name, execution.id, watch, timeout)
    # The SDK waits for the logs of the types that write them after the final state.
    if final.success is not None:
        from hopsworks_common.client.exceptions import (  # noqa: PLC0415 - SDK loaded on use
            JobExecutionException,
        )

        left = None if timeout is None else max(0.0, timeout - watch.elapsed())
        if left != 0:
            with contextlib.suppress(JobExecutionException):
                final.await_termination(left)
    if final.success is False:
        _failed(name, final, window, watch.last_reason)
    if final.success is None:
        from hopsworks_common.constants import JOBS  # noqa: PLC0415 - SDK loaded on use

        if final.state in JOBS.FINAL_STATES:
            raise click.ClickException(
                f"{name}{window}: execution #{final.id} {final.state}, but its final "
                f"status is still {final.final_status} after {watch.elapsed():.0f} s."
            )
        raise click.ClickException(
            f"{name}{window}: execution #{final.id} still {final.state} after {timeout} s."
        )
    return final


# States of an execution waiting for its pod: scheduling (QUEUED) or pulling the image (DEPLOYING).
_STARTING = ("INITIALIZING", "QUEUED", "DEPLOYING")
_STARTED = ("RUNNING", "AGGREGATING_LOGS")
_POLL_SECONDS = 3.0
# A Spark execution's success comes from its final status, which YARN may write after its state.
_SETTLE_SECONDS = 30.0


def _execution_json(name: str, ident: int, kube: bool) -> dict:
    """The execution as the backend returns it; with `kube`, with its pod's KubeMonitorDTO when there is one."""
    from hopsworks_common import client  # noqa: PLC0415 - SDK loaded on use
    from hopsworks_common.client.exceptions import (  # noqa: PLC0415 - SDK loaded on use
        RestAPIError,
    )

    _client = client._get_instance()
    path = ["project", _client._project_id, "jobs", name, "executions", ident]
    if kube:
        try:
            return (
                _client._send_request(
                    "GET", path, query_params={"expand": "kube_monitor"}
                )
                or {}
            )
        except RestAPIError as exc:
            # A backend without the kube_monitor expansion answers 404.
            if exc.response.status_code != 404:
                raise
    return _client._send_request("GET", path) or {}


def _follow(
    job: Any, name: str, ident: int, watch: waits.Watch, timeout: int | None
) -> Execution:
    """Print each state of the execution until it is final with its success known, or `timeout` passes.

    Returns the execution as the last poll read it. `watch` bounds the wait for RUNNING;
    `timeout` None is unbounded.
    """
    from hopsworks_common.constants import JOBS  # noqa: PLC0415 - SDK loaded on use
    from hopsworks_common.execution import (  # noqa: PLC0415 - SDK loaded on use
        Execution,
    )

    started = False
    kube = True
    ended_at = None
    while True:
        body = _execution_json(name, ident, kube)
        execution = Execution.from_response_json(body, job)
        state = execution.state or "?"
        kube = state in _STARTING
        watch.see(state, waits.kube_reason(body.get("kubeMonitor")) if kube else None)
        if state in _STARTED:
            started = True
            watch.forget_reason()
        # Spark and Ray set the final status before they aggregate the logs, and a Spark
        # execution's success reads the final status: only a final state ends the wait.
        if state in JOBS.FINAL_STATES:
            if execution.success is not None:
                return execution
            ended_at = watch.elapsed() if ended_at is None else ended_at
            if watch.elapsed() - ended_at >= _SETTLE_SECONDS:
                return execution
        elif not started and watch.expired():
            watch.fail(
                f"The execution is left as is: `hops job stop {name}` ends it, "
                "or raise --start-timeout."
            )
        if timeout is not None and watch.elapsed() >= timeout:
            return execution
        watch.sleep()


def _ended_as(execution: Execution) -> str:
    """The state that says how the execution ended, read as Execution.success reads it."""
    spark = (execution.job_type or "").lower() in ("spark", "pyspark")
    if spark and execution.final_status not in (None, "UNDEFINED"):
        return execution.final_status
    return execution.state


def _failed(
    name: str, execution: Execution, window: str = "", kube_reason: str | None = None
) -> None:
    """Print the last lines of a failed execution's stdout and stderr, then exit 1.

    The failure names how the pod's container ended when the backend wrote it in stderr.log,
    else `kube_reason`, the last reason the pod gave while the wait followed it.
    """
    ident = execution.id
    reason = None
    tmp = tempfile.mkdtemp(prefix="hops-joblogs-")
    try:
        stdout_path, stderr_path = execution.download_logs(path=tmp)
        for stream, path in (("stdout", stdout_path), ("stderr", stderr_path)):
            if path is None:
                click.echo(f"(no {stream}.log)", err=True)
                continue
            text = _read_log(path, LOG_TAIL)
            if text.strip():
                click.echo(f"=== {stream}, last {LOG_TAIL} lines ===", err=True)
                click.echo(text.rstrip("\n"), err=True)
        reason = _pod_exit(stderr_path)
    except Exception as exc:  # noqa: BLE001 - the failure is reported either way
        click.echo(f"(logs not readable: {exc})", err=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    reason = reason or kube_reason
    because = f": {reason.rstrip('.')}" if reason else ""
    raise click.ClickException(
        f"{name}{window}: execution #{ident} {_ended_as(execution)}{because}. "
        f"Full logs: hops job logs {name} --execution {ident} --stdout"
    )


# The pod is gone once its execution ends, and the execution keeps no exit code. KubeJobsMonitor
# writes how the container ended as the first line of stderr.log: after a termination whose pod
# could not upload its own logs (an OOM kill), "Job failed with exit code: 137, Reason:
# OOMKilled"; after a container-waiting timeout, "Job failed with: ErrImagePull - <message>".
_POD_EXIT = re.compile(
    r"Job failed with exit code: (?P<code>\d+)(?:, Reason: (?P<reason>[^,\n]+))?"
    r"|Job failed with: (?P<waiting>[^\n]+)"
)


def _pod_exit(path: str | None) -> str | None:
    """How the pod's container ended, as the backend wrote it in stderr.log, or None."""
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as fd:
        match = _POD_EXIT.match(fd.readline())
    if not match:
        return None
    if match["waiting"]:
        reason, _, message = match["waiting"].partition(" - ")
        return f"{reason}: {message}" if message else reason
    code, reason = match["code"], match["reason"]
    return f"{reason} (exit code {code})" if reason else f"exit code {code}"


def _report_run(name: str, execution: Any, waited: bool, window: str = "") -> None:
    """Say whether the execution finished (after a wait) or only started."""
    if execution is None:
        output.warn(
            "A materialization of %s is already running; nothing started.", name
        )
        return
    ident = getattr(execution, "id", "?")
    if waited:
        final = getattr(execution, "final_status", None) or getattr(
            execution, "state", "?"
        )
        output.success("✓ %s finished%s: execution #%s %s", name, window, ident, final)
    else:
        output.success(
            "✓ %s started%s: execution #%s, %s",
            name,
            window,
            ident,
            getattr(execution, "state", "?"),
        )


_DT_FORMATS = ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"]


@job_group.command("run")
@click.argument("name")
@click.option(
    "--args",
    "app_args",
    help='Argument string passed to this execution, split as shell words: quote a value that holds spaces ("2026-10-16 00:00:00").',
)
@click.option(
    "--wait/--no-wait",
    default=False,
    show_default=True,
    help="Wait until the execution finishes.",
)
@click.option(
    "--start-time",
    type=click.DateTime(formats=_DT_FORMATS),
    default=None,
    help="ISO timestamp for HOPS_START_TIME (data interval start).",
)
@click.option(
    "--end-time",
    type=click.DateTime(formats=_DT_FORMATS),
    default=None,
    help="ISO timestamp for HOPS_END_TIME (data interval end). Requires --start-time.",
)
@_start_timeout_option
@click.pass_context
def job_run(
    ctx: click.Context,
    name: str,
    app_args: str | None,
    wait: bool,
    start_time: datetime | None,
    end_time: datetime | None,
    start_timeout: int,
) -> None:
    """Run a job once now.

    `--start-time` / `--end-time` are passed straight through to the
    SDK and surface inside the job as `HOPS_START_TIME` /
    `HOPS_END_TIME`. Useful when you want to nudge a single run to
    consume a specific data window without creating a schedule.

    \f
    Args:
        ctx: Click context.
        name: Job name.
        app_args: Argument string passed to this execution.
        wait: Block until the execution terminates.
        start_time: ISO timestamp for `HOPS_START_TIME`.
        end_time: ISO timestamp for `HOPS_END_TIME`.
        start_timeout: With `wait`, seconds to wait for the execution to reach RUNNING.
    """
    if end_time is not None and start_time is None:
        raise click.UsageError("--end-time requires --start-time.")
    job = _get_job(ctx, name)
    execution = _run(
        job,
        name,
        wait,
        "run",
        start_timeout=start_timeout,
        args=app_args,
        start_time=start_time,
        end_time=end_time,
    )
    if output.JSON_MODE:
        output.print_json(_execution_to_dict(execution))


@job_group.command("backfill")
@click.argument("name")
@click.option(
    "--start-time",
    type=click.DateTime(formats=_DT_FORMATS),
    required=True,
    help="ISO timestamp for the start of the backfill window (HOPS_START_TIME).",
)
@click.option(
    "--end-time",
    type=click.DateTime(formats=_DT_FORMATS),
    required=True,
    help="ISO timestamp for the end of the backfill window (HOPS_END_TIME).",
)
@click.option(
    "--args",
    "app_args",
    help='Argument string passed to this execution, split as shell words: quote a value that holds spaces ("2026-10-16 00:00:00").',
)
@click.option(
    "--wait/--no-wait",
    "wait",
    default=True,
    show_default=True,
    help="Block until the execution terminates (default: yes).",
)
@_start_timeout_option
@click.pass_context
def job_backfill(
    ctx: click.Context,
    name: str,
    start_time: datetime,
    end_time: datetime,
    app_args: str | None,
    wait: bool,
    start_timeout: int,
) -> None:
    """Run a job once over a start/end time window.

    Run `name` once over an explicit [start_time, end_time] window.

    Same SDK call as `run` (which sets `HOPS_START_TIME` /
    `HOPS_END_TIME` env vars on the execution); the difference is that
    both timestamps are required, signalling intent to backfill a fixed
    historical interval rather than a normal one-off run.

    \f
    Args:
        ctx: Click context.
        name: Job name.
        start_time: ISO timestamp for the start of the backfill window.
        end_time: ISO timestamp for the end of the backfill window.
        app_args: Argument string passed to this execution.
        wait: Block until the execution terminates.
        start_timeout: With `wait`, seconds to wait for the execution to reach RUNNING.
    """
    if end_time <= start_time:
        raise click.UsageError("--end-time must be strictly after --start-time.")
    job = _get_job(ctx, name)
    execution = _run(
        job,
        name,
        wait,
        "backfill",
        f" for {start_time.isoformat()} to {end_time.isoformat()}",
        start_timeout,
        args=app_args,
        start_time=start_time,
        end_time=end_time,
    )
    if output.JSON_MODE:
        output.print_json(_execution_to_dict(execution))


@job_group.command("wait")
@click.argument("name")
@click.option(
    "--execution",
    "execution_id",
    type=int,
    help="Specific execution ID; defaults to the most recent.",
)
@click.option(
    "--timeout",
    type=int,
    default=None,
    help="Seconds to wait before giving up; unbounded by default.",
)
@_start_timeout_option
@click.pass_context
def job_wait(
    ctx: click.Context,
    name: str,
    execution_id: int | None,
    timeout: int | None,
    start_timeout: int,
) -> None:
    """Wait for an execution to end.

    A failed execution prints the last lines of its logs and exits 1.

    \f
    Args:
        ctx: Click context.
        name: Job name.
        execution_id: Specific execution; latest if omitted.
        timeout: Seconds to wait; unbounded when omitted.
        start_timeout: Seconds to wait for the execution to reach RUNNING.
    """
    job = _get_job(ctx, name)
    executions = _executions(job)
    if not executions:
        raise click.ClickException(f"No executions for job '{name}'.")
    target = executions[0]
    if execution_id is not None:
        matches = [e for e in executions if getattr(e, "id", None) == execution_id]
        if not matches:
            raise click.ClickException(f"Execution #{execution_id} not found.")
        target = matches[0]
    final = _await(job, name, target, timeout, start_timeout=start_timeout)
    _report_run(name, final, True)
    if output.JSON_MODE:
        output.print_json(_execution_to_dict(final))


@job_group.command("stop")
@click.argument("name")
@click.pass_context
def job_stop(ctx: click.Context, name: str) -> None:
    """Stop the latest execution of a job.

    \f
    Args:
        ctx: Click context.
        name: Job name.
    """
    executions = _executions(_get_job(ctx, name))
    if not executions:
        raise click.ClickException(f"No executions for job '{name}'.")
    latest = executions[0]
    try:
        latest.stop()
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not stop job '{name}': {exc}") from exc
    output.success("✓ Stopped execution %s of %s", getattr(latest, "id", "?"), name)


def _log_dir(path: str | None) -> str | None:
    if path is None:
        return None
    path = os.path.expanduser(path)
    os.makedirs(path, exist_ok=True)
    return path


@job_group.command("logs")
@click.argument("name")
@click.option(
    "--execution",
    "execution_id",
    type=int,
    help="Specific execution ID; defaults to the most recent.",
)
@click.option(
    "--stdout",
    "to_stdout",
    is_flag=True,
    help="Print the log content to the terminal instead of downloading log "
    "directories into the working directory.",
)
@click.option(
    "--dir",
    "log_dir",
    type=click.Path(file_okay=False),
    help="Directory to download into, created if missing; the working directory "
    "by default. Keep logs out of a git work tree, such as an ML system's "
    "directory: in the factory, ${HOPSFS_USER_HOME_DIR:-$HOME}/Logs/factory/<slug>.",
)
@click.option(
    "--tail",
    type=int,
    help="With --stdout, print only the last N lines of each stream.",
)
@click.pass_context
def job_logs(
    ctx: click.Context,
    name: str,
    execution_id: int | None,
    to_stdout: bool,
    log_dir: str | None,
    tail: int | None,
) -> None:
    """Read stdout/stderr logs for a job execution.

    By default this downloads `stdout.log` / `stderr.log` into a
    `logs-job-<name>-exec-<id>_*` directory in the working directory, or in
    `--dir`, and prints the paths. Pass `--stdout` to print the content to the terminal
    instead, leaving no files behind.

    \f
    Args:
        ctx: Click context.
        name: Job name.
        execution_id: Specific execution; latest if omitted.
        to_stdout: Print content to the terminal instead of downloading files.
        log_dir: Directory to download into instead of the working directory.
        tail: With `--stdout`, keep only the last N lines of each stream.
    """
    executions = _executions(_get_job(ctx, name))
    if not executions:
        raise click.ClickException(f"No executions for job '{name}'.")
    target = executions[0]
    if execution_id is not None:
        matches = [e for e in executions if getattr(e, "id", None) == execution_id]
        if not matches:
            raise click.ClickException(f"Execution #{execution_id} not found.")
        target = matches[0]

    if to_stdout:
        tmp = tempfile.mkdtemp(prefix="hops-joblogs-")
        try:
            stdout_path, stderr_path = target.download_logs(path=tmp)
            out, err = _read_log(stdout_path, tail), _read_log(stderr_path, tail)
        except Exception as exc:  # noqa: BLE001
            raise click.ClickException(f"Could not read logs: {exc}") from exc
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if output.JSON_MODE:
            output.print_json({"stdout": out, "stderr": err})
            return
        output.info("=== stdout ===")
        click.echo(out or "(empty)")
        output.info("=== stderr ===")
        click.echo(err or "(empty)")
        return

    try:
        stdout_path, stderr_path = target.download_logs(path=_log_dir(log_dir))
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not download logs: {exc}") from exc

    if output.JSON_MODE:
        output.print_json({"stdout": stdout_path, "stderr": stderr_path})
        return
    output.info("stdout: %s", stdout_path or "<none>")
    output.info("stderr: %s", stderr_path or "<none>")
    if log_dir is None:
        output.info(
            "(downloaded to the working directory; use --stdout to print instead)"
        )


@job_group.command("history")
@click.argument("name")
@click.pass_context
def job_history(ctx: click.Context, name: str) -> None:
    """List past executions of a job, newest first.

    List past executions of a job (newest first), with how long each ran.

    \f
    Args:
        ctx: Click context.
        name: Job name.
    """
    executions = _executions(_get_job(ctx, name))
    rows = [
        [
            getattr(e, "id", "?"),
            getattr(e, "state", "?"),
            getattr(e, "final_status", "-"),
            getattr(e, "submission_time", "-"),
            _duration_s(e),
        ]
        for e in executions
    ]
    output.print_table(["ID", "STATE", "FINAL", "SUBMITTED", "DURATION_S"], rows)


def _duration_s(execution: Any) -> int | str:
    """Seconds the execution ran, from the millisecond duration the backend reports."""
    duration = getattr(execution, "duration", None)
    return round(duration / 1000) if isinstance(duration, (int, float)) else "-"


# Quartz cron (sec min hour day-of-month month day-of-week) for the common
# crontab shorthands. day-of-month and day-of-week are mutually exclusive in
# Quartz, hence the `?` placeholder in one of the two.
_CRON_ALIASES = {
    "@hourly": "0 0 * * * ?",
    "@daily": "0 0 0 * * ?",
    "@midnight": "0 0 0 * * ?",
    "@weekly": "0 0 0 ? * SUN",
    "@monthly": "0 0 0 1 * ?",
    "@yearly": "0 0 0 1 1 ?",
    "@annually": "0 0 0 1 1 ?",
}


def _expand_cron_alias(cron: str) -> str:
    """Translate ``@daily``-style shorthands to a Quartz cron expression."""
    return _CRON_ALIASES.get(cron.strip().lower(), cron)


@job_schedule_group.command("set")
@click.argument("name")
@click.argument("cron")
@click.option(
    "--start-time",
    type=click.DateTime(formats=_DT_FORMATS),
    help="ISO timestamp for the first trigger (e.g. 2026-04-26T09:00:00).",
)
@click.option(
    "--end-time",
    type=click.DateTime(formats=_DT_FORMATS),
    help="ISO timestamp for the last trigger.",
)
@click.option(
    "--start-offset-seconds",
    "start_offset_seconds",
    type=int,
    default=None,
    help=(
        "Per-fire offset for HOPS_START_TIME (data window start). Current servers "
        "refuse negative values ('must be non-negative'); prefer omitting it and "
        "deriving a look-back in the program from HOPS_END_TIME. Default "
        "(omitted) = previous cron fire (last execution time)."
    ),
)
@click.option(
    "--end-offset-seconds",
    "end_offset_seconds",
    type=int,
    default=None,
    help=(
        "Per-fire offset for HOPS_END_TIME (data window end). Current servers no "
        "longer honour it; HOPS_END_TIME is the cron fire time."
    ),
)
@click.option(
    "--catchup/--no-catchup",
    "catchup",
    default=False,
    show_default=True,
    help="Replay missed fires on recovery (one execution per missed interval).",
)
@click.option(
    "--max-active-runs",
    "max_active_runs",
    type=int,
    default=1,
    show_default=True,
    help="Upper bound on concurrent executions of this job.",
)
@click.option(
    "--max-catchup-runs",
    "max_catchup_runs",
    type=int,
    default=None,
    help="Upper bound on missed intervals to replay during catchup (most-recent wins).",
)
@click.pass_context
def job_schedule(
    ctx: click.Context,
    name: str,
    cron: str,
    start_time: datetime | None,
    end_time: datetime | None,
    start_offset_seconds: int | None,
    end_offset_seconds: int | None,
    catchup: bool,
    max_active_runs: int,
    max_catchup_runs: int | None,
) -> None:
    """Set the cron schedule of a job.

    The cron interval is the firing cadence. The data window the job
    consumes per fire is the previous fire to this one; current servers
    refuse a negative `--start-offset-seconds` and ignore
    `--end-offset-seconds`, so a program that needs another window derives
    it from `HOPS_END_TIME`. `--catchup`
    replays missed intervals after an outage; `--max-catchup-runs`
    caps how many missed intervals are replayed.

    \f
    Args:
        ctx: Click context.
        name: Job name.
        cron: Quartz cron expression, or a shorthand (@hourly, @daily,
            @midnight, @weekly, @monthly, @yearly).
        start_time: ISO timestamp for the first trigger.
        end_time: ISO timestamp for the last trigger.
        start_offset_seconds: Per-fire offset for `HOPS_START_TIME`.
        end_offset_seconds: Per-fire offset for `HOPS_END_TIME`.
        catchup: Replay missed fires on recovery.
        max_active_runs: Upper bound on concurrent executions.
        max_catchup_runs: Upper bound on missed intervals to replay during catchup.
    """
    job = _get_job(ctx, name)
    cron = _expand_cron_alias(cron)
    try:
        schedule = job.schedule(
            cron_expression=cron,
            start_time=start_time,
            end_time=end_time,
            catchup=catchup,
            max_active_runs=max_active_runs,
            start_time_offset_seconds=start_offset_seconds,
            end_time_offset_seconds=end_offset_seconds,
            max_catchup_runs=max_catchup_runs,
        )
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not schedule job '{name}': {exc}") from exc
    output.success("✓ Scheduled %s (%s)", name, cron)
    if output.JSON_MODE:
        output.print_json(_schedule_to_dict(schedule))


@job_schedule_group.command("info")
@click.argument("name")
@click.pass_context
def job_schedule_info(ctx: click.Context, name: str) -> None:
    """Print the currently attached schedule, if any.

    \f
    Args:
        ctx: Click context.
        name: Job name.
    """
    job = _get_job(ctx, name)
    schedule = getattr(job, "job_schedule", None)
    if schedule is None:
        output.info("No schedule attached to %s.", name)
        return
    if output.JSON_MODE:
        output.print_json(_schedule_to_dict(schedule))
        return
    rows = [
        ["Cron", getattr(schedule, "cron_expression", "-")],
        ["Start", getattr(schedule, "start_date_time", "-")],
        ["End", getattr(schedule, "end_date_time", "-")],
        ["Enabled", getattr(schedule, "enabled", "-")],
    ]
    output.print_table(["FIELD", "VALUE"], rows)


@job_schedule_group.command("delete")
@click.argument("name")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
@click.pass_context
def job_unschedule(ctx: click.Context, name: str, yes: bool) -> None:
    """Remove the schedule from a job.

    \f
    Args:
        ctx: Click context.
        name: Job name.
        yes: Skip confirmation when True.
    """
    job = _get_job(ctx, name)
    if not yes:
        output.confirm(f"Remove the schedule of job '{name}'?")
    try:
        job.unschedule()
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(
            f"Could not delete the schedule of job '{name}': {exc}"
        ) from exc
    output.success("✓ Removed schedule from %s", name)


@job_group.command("delete")
@click.argument("name")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.pass_context
def job_delete(ctx: click.Context, name: str, yes: bool) -> None:
    """Delete a job with its executions and their logs.

    \f
    Args:
        ctx: Click context.
        name: Job name.
        yes: Skip confirmation when True.
    """
    job = _get_job(ctx, name)
    if not yes:
        output.confirm(f"Delete job '{name}'?")
    try:
        job.delete()
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not delete job '{name}': {exc}") from exc
    output.success("✓ Deleted job %s", name)


@job_tag_group.command("list")
@click.argument("name")
@click.pass_context
def job_tags(ctx: click.Context, name: str) -> None:
    """Show all tags attached to a job.

    \f
    Args:
        ctx: Click context.
        name: Job name.
    """
    job = _get_job(ctx, name)
    try:
        tags = job.get_tags_metadata()
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not read tags: {exc}") from exc
    output.render_tags(tags)


@job_tag_group.command("add")
@click.argument("name")
@click.argument("tag")
@click.option("--value", required=True, help="Tag value, JSON or a plain string.")
@click.pass_context
def job_add_tag(ctx: click.Context, name: str, tag: str, value: str) -> None:
    """Attach a tag (name/value pair) to a job.

    \f
    Args:
        ctx: Click context.
        name: Job name.
        tag: Tag name (a registered tag schema).
        value: Tag value, JSON or a plain string.
    """
    job = _get_job(ctx, name)
    try:
        job.add_tag(name=tag, value=output.parse_tag_value(value))
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not add tag: {exc}") from exc
    output.success("✓ Added tag '%s' to %s", tag, name)


@job_tag_group.command("remove")
@click.argument("name")
@click.argument("tag")
@click.pass_context
def job_remove_tag(ctx: click.Context, name: str, tag: str) -> None:
    """Remove a tag from a job.

    \f
    Args:
        ctx: Click context.
        name: Job name.
        tag: Tag name.
    """
    job = _get_job(ctx, name)
    try:
        job.delete_tag(tag)
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not remove tag: {exc}") from exc
    output.success("✓ Removed tag '%s' from %s", tag, name)


@job_group.command("update")
@click.argument("name")
@click.option("--description", required=True, help="New description of the job.")
@click.pass_context
def job_update(ctx: click.Context, name: str, description: str) -> None:
    """Update the description of a job.

    \f
    Args:
        ctx: Click context.
        name: Job name.
        description: New description.
    """
    job = _get_job(ctx, name)
    try:
        job.description = description
        job.save()
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not update description: {exc}") from exc
    output.success("✓ Updated description of %s", name)


def _read_log(path: str | None, tail: int | None) -> str:
    """Read a downloaded log file, optionally keeping only the last ``tail`` lines."""
    if not path or not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as fd:
        lines = fd.readlines()
    if tail is not None and tail >= 0:
        lines = lines[-tail:]
    return "".join(lines)


def _get_job(ctx: click.Context, name: str) -> Any:
    project = session.get_project(ctx)
    try:
        job = project.get_job_api().get_job(name)
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not find job '{name}': {exc}") from exc
    if job is None:
        raise click.ClickException(f"Job '{name}' not found.")
    return job


def _executions(job: Any) -> list[Any]:
    try:
        execs = list(job.get_executions() or [])
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not list executions: {exc}") from exc
    execs.sort(key=lambda e: getattr(e, "id", 0), reverse=True)
    return execs


def _execution_to_dict(execution: Any) -> dict[str, Any]:
    return {
        "id": getattr(execution, "id", None),
        "state": getattr(execution, "state", None),
        "final_status": getattr(execution, "final_status", None),
        "submission_time": getattr(execution, "submission_time", None),
        "duration_s": _duration_s(execution),
    }


def _schedule_to_dict(schedule: Any) -> dict[str, Any]:
    to_dict = getattr(schedule, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return {
        "cron_expression": getattr(schedule, "cron_expression", None),
        "start_date_time": getattr(schedule, "start_date_time", None),
        "end_date_time": getattr(schedule, "end_date_time", None),
    }
