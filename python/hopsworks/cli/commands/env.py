"""``hops env`` — manage the project's Python environments.

Wraps ``project.get_environment_api()``. Lists existing environments,
clones from a base environment (the SDK's ``create_environment`` call —
the API uses "create from base" rather than a dedicated "clone"), and
installs a requirements.txt into a target environment. Every command
that triggers a backend installation step blocks until it finishes
(``await_creation=True`` / ``await_installation=True``) and prints a
heads-up warning, since installs can take several minutes.
"""

from __future__ import annotations

import os
from typing import Any

import click
from hopsworks.cli import output, session
from hopsworks.cli.commands.files import makedirs


@click.group("env")
def env_group() -> None:
    """Python environments jobs, apps and deployments run in."""


# RESTCodes.PythonErrorCode.ENVIRONMENT_CHANGE_FORBIDDEN: only the environment's creator or a Data owner changes it.
_CHANGE_FORBIDDEN = 300029
_COPY_SUFFIX = "-app-env"
# environment.name is varchar(128) in Hopsworks.
_MAX_NAME = 128


def copy_name(env_name: str, username: str) -> str:
    """The caller's own copy of a shared environment.

    Args:
        env_name: The shared environment.
        username: The caller's Hopsworks username.

    Returns:
        `<env>-app-env-<user>`, `<env>` being `env_name` without a trailing `-app-env`.
    """
    return f"{env_name.removesuffix(_COPY_SUFFIX)}{_COPY_SUFFIX}-{username}"


def change_forbidden(exc: Exception) -> bool:
    """Whether Hopsworks refused to change an environment the caller did not create.

    Args:
        exc: What the SDK raised.

    Returns:
        True for error 300029.
    """
    return getattr(exc, "error_code", None) == _CHANGE_FORBIDDEN


def current_username() -> str:
    import hopsworks

    try:
        return hopsworks.get_users_api()._get_current_user().username
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not resolve your username: {exc}") from exc


def own_copy(api: Any, source: str, name: str) -> Any:
    """The caller's copy of `source`, cloned first when it does not exist.

    A copy whose build failed is deleted and cloned again. A clone is deleted
    only once its own build failed: a refused request, a timeout or a lost
    answer leaves it, since it may be another run's copy or still building.
    Runs of one user share the copy: one waits for the copy another clones.

    Args:
        api: The project's environment API.
        source: The environment to clone.
        name: The copy's name, from `copy_name`.

    Returns:
        The copy.
    """
    if len(name) > _MAX_NAME:
        raise click.ClickException(
            f"Only its creator or a Data owner can change environment '{source}', and "
            f"your copy's name '{name}' would be {len(name)} characters, over the "
            f"{_MAX_NAME} Hopsworks allows: use a shorter name."
        )
    try:
        env = api.get_environment(name)
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(
            f"Could not find environment '{name}': {exc}"
        ) from exc
    if env is not None and not _build_failed(env):
        return env
    if env is not None:
        output.warn("Your copy %s did not finish building: cloning it again.", name)
        # Read again right before the delete: another run may have cloned it again.
        env = _read(api, name)
        if env is not None and _build_failed(env):
            try:
                env.delete()
            except Exception as exc:  # noqa: BLE001
                raise click.ClickException(
                    f"Your copy '{name}' did not build and could not be deleted: {exc}. "
                    f"Delete it with `hops env delete {name} --yes`, then run this again."
                ) from exc
            env = None
    if env is None:
        output.info(
            "Only its creator or a Data owner can change environment %s: cloning it "
            "to your own copy %s; this takes a few minutes.",
            source,
            name,
        )
        try:
            env = api.create_environment(
                name, source_environment_name=source, await_creation=False
            )
        except Exception as exc:  # noqa: BLE001
            # Another run of yours may have cloned it first.
            env = _read(api, name)
            if env is None or _build_failed(env):
                raise click.ClickException(
                    f"Could not clone environment '{source}' to '{name}': {exc}"
                ) from exc
    try:
        try:
            api.await_creation(name)
        except Exception as exc:
            # Another run deleted the copy while it built and may be cloning it again.
            if _status(exc) != 404:
                raise
            env = _read(api, name)
            if env is None or _build_failed(env):
                raise
            api.await_creation(name)
    except Exception as exc:  # noqa: BLE001
        failure = f"Could not clone environment '{source}' to '{name}': {exc}"
        half = _read(api, name)
        if half is None or not _build_failed(half):
            raise click.ClickException(
                f"{failure}. The copy is kept: it may still be building; run this again later."
            ) from exc
        try:
            half.delete()
        except Exception as cleanup:  # noqa: BLE001
            output.warn(
                "Could not delete the unfinished copy %s: %s. Delete it with "
                "`hops env delete %s --yes`.",
                name,
                cleanup,
                name,
            )
        raise click.ClickException(failure) from exc
    return env


def _read(api: Any, name: str) -> Any:
    try:
        return api.get_environment(name)
    except Exception:  # noqa: BLE001
        return None


def _status(exc: Exception) -> int | None:
    return getattr(getattr(exc, "response", None), "status_code", None)


def _build_failed(env: Any) -> bool:
    # Only the build: a failed install into a finished copy keeps the copy.
    return any(c.op == "CREATE" and c.status == "FAILED" for c in env.commands or [])


def _api(ctx: click.Context) -> Any:
    return session.get_project(ctx).get_environment_api()


def _row(env: Any) -> dict[str, Any]:
    return {
        "name": getattr(env, "name", None),
        "python_version": getattr(env, "python_version", None),
        "description": output.first_line(getattr(env, "description", None) or ""),
    }


@env_group.command("list")
@click.pass_context
def env_list(ctx: click.Context) -> None:
    """List Python environments in the current project.

    \f
    Args:
        ctx: Click context.
    """
    envs = _api(ctx).get_environments() or []
    rows = [_row(e) for e in envs]
    if output.JSON_MODE:
        output.print_json(rows)
        return
    output.print_table(
        ["NAME", "PYTHON_VERSION", "DESCRIPTION"],
        [
            [r["name"] or "", r["python_version"] or "", r["description"] or ""]
            for r in rows
        ],
    )


@env_group.command("info")
@click.argument("name")
@click.pass_context
def env_info(ctx: click.Context, name: str) -> None:
    """Show a Python environment and the libraries installed in it.

    \f
    Args:
        ctx: Click context.
        name: Environment name.
    """
    env = _api(ctx).get_environment(name)
    if env is None:
        raise click.ClickException(f"No environment {name!r}: `hops env list` shows them.")
    libraries = env.library_versions or {}
    if output.JSON_MODE:
        output.print_json({**_row(env), "libraries": libraries})
        return
    output.print_table(["LIBRARY", "VERSION"], [[k, v or ""] for k, v in sorted(libraries.items())])


@env_group.command("clone")
@click.argument("new_name")
@click.option(
    "--from",
    "source",
    default="python-feature-pipeline",
    show_default=True,
    help="Existing environment name to clone from.",
)
@click.option(
    "--description",
    "description",
    default=None,
    help="Description for the new environment.",
)
@click.pass_context
def env_clone(
    ctx: click.Context,
    new_name: str,
    source: str,
    description: str | None,
) -> None:
    """Clone SOURCE into a new environment NEW_NAME.

    A project environment is cloned with its libraries; a built-in one is
    created from its base image. Blocks until the backend finishes
    provisioning, which usually takes several minutes.

    \f
    Args:
        ctx: Click context.
        new_name: Name for the new environment.
        source: Existing environment to clone from.
        description: Optional description for the new environment.
    """
    api = _api(ctx)
    try:
        source_env = api.get_environment(source)
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(
            f"Could not find environment '{source}': {exc}"
        ) from exc
    if source_env is None:
        raise click.ClickException(
            f"No environment named '{source}'. Run `hops env list` to see what exists."
        )
    if source_env.preinstalled is False:
        origin = {"source_environment_name": source}
    else:
        origin = {"base_environment_name": source}
    output.warn(
        "Cloning '%s' to '%s' — this can take several minutes. Waiting for "
        "the backend to finish provisioning before returning.",
        source,
        new_name,
    )
    try:
        env = api.create_environment(
            new_name,
            description=description,
            await_creation=True,
            **origin,
        )
    except Exception as exc:  # noqa: BLE001 - SDK raises a mix of types
        raise click.ClickException(
            f"Could not clone environment '{source}' to '{new_name}': {exc}"
        ) from exc
    payload = _row(env)
    if output.JSON_MODE:
        output.print_json(payload)
    else:
        output.success(f"Created environment '{payload['name']}'")
        output.print_table(
            ["FIELD", "VALUE"],
            [[k.upper(), str(v) if v is not None else ""] for k, v in payload.items()],
        )


@env_group.command("delete")
@click.argument("name")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def env_delete(ctx: click.Context, name: str, yes: bool) -> None:
    """Delete the project environment NAME.

    Only its creator or a Data owner can delete it, and not while a job,
    app, deployment or Jupyter server runs on it.

    \f
    Args:
        ctx: Click context.
        name: Environment to delete.
        yes: Skip confirmation when True.
    """
    api = _api(ctx)
    try:
        env = api.get_environment(name)
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(
            f"Could not find environment '{name}': {exc}"
        ) from exc
    if env is None:
        raise click.ClickException(
            f"No environment named '{name}'. Run `hops env list` to see what exists."
        )
    if not yes:
        output.confirm(f"Delete environment '{name}'?")
    try:
        env.delete()
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(
            f"Could not delete environment '{name}': {exc}"
        ) from exc
    output.success("✓ Deleted environment %s", name)


@env_group.command("install")
@click.argument("env_name")
@click.option(
    "-f",
    "--file",
    "requirements_file",
    required=True,
    help="requirements.txt: a local file (uploaded to HopsFS) or an existing "
    "HopsFS path.",
)
@click.option(
    "--upload-dir",
    default=None,
    help="HopsFS dir to upload a local requirements file to "
    "(default: Resources/environments/<env_name>).",
)
@click.option(
    "--overwrite/--no-overwrite",
    default=True,
    show_default=True,
    help="Overwrite the uploaded requirements file if it exists.",
)
@click.pass_context
def env_install(
    ctx: click.Context,
    env_name: str,
    requirements_file: str,
    upload_dir: str | None,
    overwrite: bool,
) -> None:
    """Install a requirements.txt into ENV_NAME.

    When ENV_NAME is shared and only its creator or a Data owner can change
    it, the install goes into your own copy, `<ENV_NAME>-app-env-<user>`
    (ENV_NAME without a trailing `-app-env`), cloned from ENV_NAME the first time.

    `--file` is either a local file — uploaded to HopsFS first, since the
    backend can only read paths inside the project — or a path that already
    exists in the project (e.g. `Users/<username>/requirements.txt`).

    Blocks until the install completes. This usually takes several
    minutes — conda/pip resolves the dependencies and the resulting
    libraries are committed to the environment image.

    \f
    Args:
        ctx: Click context.
        env_name: Environment to install into.
        requirements_file: Local file to upload, or an existing HopsFS path.
        upload_dir: HopsFS directory for a local requirements upload.
        overwrite: Overwrite an existing uploaded requirements file.
    """
    project = session.get_project(ctx)
    api = project.get_environment_api()
    try:
        env = api.get_environment(env_name)
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(
            f"Could not find environment '{env_name}': {exc}"
        ) from exc
    if env is None:
        raise click.ClickException(
            f"No environment named '{env_name}'. Run `hops env list` to see what exists."
        )

    # Resolve the requirements path — upload when a local file was given, so the
    # backend (which resolves against /Projects/<project>/) can read it. Same
    # convention as `hops job deploy` resolving its script argument.
    remote_path = requirements_file
    if os.path.isfile(requirements_file):
        dataset = project.get_dataset_api()
        dest_dir = upload_dir or f"Resources/environments/{env_name}"
        try:
            makedirs(dataset, dest_dir)
            uploaded = dataset.upload(
                local_path=requirements_file, upload_path=dest_dir, overwrite=overwrite
            )
        except Exception as exc:  # noqa: BLE001
            raise click.ClickException(f"Could not upload requirements: {exc}") from exc
        remote_path = uploaded or f"{dest_dir}/{os.path.basename(requirements_file)}"
        output.success(
            "✓ Uploaded %s -> %s", os.path.basename(requirements_file), remote_path
        )

    output.warn(
        "Installing '%s' into '%s' — this can take several minutes. Waiting for "
        "the backend to finish before returning.",
        remote_path,
        env_name,
    )
    try:
        env.install_requirements(remote_path, await_installation=True)
    except Exception as exc:  # noqa: BLE001
        if not change_forbidden(exc):
            raise click.ClickException(
                f"Could not install into environment '{env_name}': {exc}"
            ) from exc
        source, env_name = env_name, copy_name(env_name, current_username())
        env = own_copy(api, source, env_name)
        try:
            env.install_requirements(remote_path, await_installation=True)
        except Exception as copy_exc:  # noqa: BLE001
            raise click.ClickException(
                f"Could not install into environment '{env_name}': {copy_exc}"
            ) from copy_exc
        output.warn(
            "Installed into your copy '%s', not '%s', which only its creator or a "
            "Data owner can change: a job, app or deployment sees these packages "
            "only when it runs on '%s'.",
            env_name,
            source,
            env_name,
        )
    if output.JSON_MODE:
        output.print_json({"environment": env_name, "installed": remote_path})
    else:
        output.success(f"Installed '{remote_path}' into environment '{env_name}'")
