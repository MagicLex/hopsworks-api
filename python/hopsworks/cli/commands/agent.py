"""``hops agent`` — Python agent deployment lifecycle.

Agents are server-only deployments (no model attached) created from a local
``.py`` script or a directory with ``pyproject.toml``.
``create`` calls ``ms.deploy_agent`` which builds/uploads the code, provisions
a Python environment from the ``python-agent-pipeline`` base image, and
creates or updates a regular Hopsworks deployment.
The remaining verbs (``info``, ``start``, ``stop``, ``query``, ``logs``,
``delete``) mirror ``hops deployment`` but resolve names through an
agent-only lookup that excludes model-backed deployments.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
from pathlib import Path
from typing import Any

import click
from hopsworks.cli import output, session


@click.group("agent")
def agent_group() -> None:
    """Agents: Python agents served as deployments."""


@agent_group.command("list")
@click.pass_context
def agent_list(ctx: click.Context) -> None:
    """List every agent deployment in the active project.

    \f
    Args:
        ctx: Click context.
    """
    ms = _get_model_serving(ctx)
    try:
        deployments = ms.get_deployments()
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not list agents: {exc}") from exc

    rows = []
    for d in deployments or []:
        if not _is_agent(d):
            continue
        rows.append(
            [
                getattr(d, "id", "?"),
                getattr(d, "name", "?"),
                _predictor_attr(d, "environment", "-"),
                _predictor_attr(d, "script_file", "-"),
                _deployment_status(d),
            ]
        )
    output.print_table(["ID", "NAME", "ENVIRONMENT", "SCRIPT", "STATUS"], rows)


@agent_group.command("info")
@click.argument("name")
@click.pass_context
def agent_info(ctx: click.Context, name: str) -> None:
    """Show details for a single agent.

    \f
    Args:
        ctx: Click context.
        name: Agent name.
    """
    agent = _get_agent(ctx, name)

    if output.JSON_MODE:
        output.print_json(_agent_to_dict(agent))
        return

    rows = [
        ["ID", getattr(agent, "id", "?")],
        ["Name", getattr(agent, "name", "?")],
        ["Environment", _predictor_attr(agent, "environment", "-")],
        ["Script", _predictor_attr(agent, "script_file", "-")],
        ["Serving tool", getattr(agent, "serving_tool", "-")],
        ["Model server", getattr(agent, "model_server", "-")],
        ["API protocol", getattr(agent, "api_protocol", "-")],
        ["Description", getattr(agent, "description", "-") or "-"],
        ["Status", _deployment_status(agent)],
    ]
    rows.extend(_git_rows(agent))
    output.print_table(["FIELD", "VALUE"], rows)


@agent_group.command("create")
@click.argument("entry", type=click.Path(exists=True))
@click.option("--name", help="Agent name; defaults to the basename of ENTRY.")
@click.option(
    "--requirements",
    type=click.Path(exists=True),
    help="Local requirements.txt to install into the agent environment.",
)
@click.option(
    "--env",
    "environment",
    help="Python environment name; defaults to the agent name.",
)
@click.option(
    "--upload-dir",
    default="Resources/agents",
    show_default=True,
    help="HopsFS directory under which agent files are placed.",
)
@click.option("--description", default=None, help="Agent description.")
@click.option(
    "--memory",
    type=click.IntRange(min=256),
    default=None,
    help="Memory limit in MB; the platform default otherwise. An agent that loads "
    "a model (e.g. torch and an embedder) needs more than the default 1 GB.",
)
@click.pass_context
def agent_create(
    ctx: click.Context,
    entry: str,
    name: str | None,
    requirements: str | None,
    environment: str | None,
    upload_dir: str,
    description: str | None,
    memory: int | None,
) -> None:
    """Create (or update) an agent from a local script or package.

    `ENTRY` is a `.py` file or a directory containing `pyproject.toml`.
    On re-runs, the latest code is uploaded and the deployment's predictor is
    rewritten; the running state is left untouched (use `start` or invoke
    `restart` via the SDK to roll a running agent onto new code).

    \f
    Args:
        ctx: Click context.
        entry: Local path to script or package.
        name: Agent name; defaults to the basename of `entry`.
        requirements: Optional `requirements.txt` path.
        environment: Python environment name.
        upload_dir: HopsFS upload directory.
        description: Description string.
        memory: Memory limit in MB.
    """
    ms = _get_model_serving(ctx)
    try:
        agent = ms.deploy_agent(
            entry=entry,
            name=name,
            requirements=requirements,
            environment=environment,
            upload_dir=upload_dir,
            description=description,
            resources=_memory_limit(memory),
        )
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(
            f"Could not create agent '{name or entry}': {exc}"
        ) from exc

    output.success("✓ Created agent %s", getattr(agent, "name", name or entry))


@agent_group.command("start")
@click.argument("name")
@click.option(
    "--wait/--no-wait",
    default=True,
    show_default=True,
    help="Wait until the agent is RUNNING.",
)
@click.option(
    "--timeout",
    type=int,
    default=600,
    show_default=True,
    help="Seconds to wait.",
)
@click.pass_context
def agent_start(ctx: click.Context, name: str, wait: bool, timeout: int) -> None:
    """Start an agent and optionally wait for the RUNNING state.

    \f
    Args:
        ctx: Click context.
        name: Agent name.
        wait: Whether to wait for RUNNING.
        timeout: Seconds to wait.
    """
    agent = _get_agent(ctx, name)
    try:
        agent.start(await_running=timeout if wait else 0)
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not start agent '{name}': {exc}") from exc
    output.success("✓ Started agent %s", name)


@agent_group.command("stop")
@click.argument("name")
@click.option(
    "--wait/--no-wait",
    default=True,
    show_default=True,
    help="Wait until the agent is STOPPED.",
)
@click.option(
    "--timeout",
    type=int,
    default=600,
    show_default=True,
    help="Seconds to wait.",
)
@click.pass_context
def agent_stop(ctx: click.Context, name: str, wait: bool, timeout: int) -> None:
    """Stop a running agent.

    \f
    Args:
        ctx: Click context.
        name: Agent name.
        wait: Whether to wait for STOPPED.
        timeout: Seconds to wait.
    """
    agent = _get_agent(ctx, name)
    try:
        agent.stop(await_stopped=timeout if wait else 0)
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not stop agent '{name}': {exc}") from exc
    output.success("✓ Stopped agent %s", name)


@agent_group.command("query")
@click.argument("name")
@click.option(
    "--data",
    help='JSON body to POST, e.g. \'{"prompt": "hello"}\'.',
)
@click.option(
    "--file",
    "file_path",
    type=click.Path(exists=True),
    help="JSON file with the request body.",
)
@click.pass_context
def agent_query(
    ctx: click.Context, name: str, data: str | None, file_path: str | None
) -> None:
    """Send a request to a running agent.

    \f
    Args:
        ctx: Click context.
        name: Agent name.
        data: Inline JSON body.
        file_path: Alternative JSON file source.
    """
    if not data and not file_path:
        raise click.UsageError("Provide --data or --file.")
    payload_str = (
        Path(file_path).read_text(encoding="utf-8") if file_path else (data or "")
    )
    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError as exc:
        raise click.BadParameter(f"Invalid JSON: {exc}") from exc

    agent = _get_agent(ctx, name)
    try:
        response = agent.predict(data=payload)
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not query agent '{name}': {exc}") from exc

    if output.JSON_MODE:
        output.print_json(response)
        return
    click.echo(json.dumps(response, indent=2, default=str))


@agent_group.command("logs")
@click.argument("name")
@click.option(
    "--component",
    default="predictor",
    show_default=True,
    help="Component to fetch logs for (predictor, transformer, ...).",
)
@click.option(
    "--tail", type=int, default=100, show_default=True, help="Number of lines."
)
@click.option(
    "--source",
    type=click.Choice(["opensearch", "kubernetes"]),
    default="kubernetes",
    show_default=True,
    help=(
        "kubernetes: live pod logs. opensearch: deprecated; new backends "
        "serve it from the Kubernetes path as well. Logs of stopped agents "
        "are retrieved with --download."
    ),
)
@click.option("--since", help="ISO-8601 lower bound on log timestamp.")
@click.option("--until", help="ISO-8601 upper bound on log timestamp.")
@click.option(
    "--previous",
    is_flag=True,
    help="Read the previous, terminated container: the crash of a predictor that restarted.",
)
@click.option(
    "-f",
    "--follow",
    is_flag=True,
    help="Stream new log lines as they are written (Ctrl-C to stop).",
)
@click.option(
    "--interval",
    type=float,
    default=2.0,
    show_default=True,
    help="Seconds between polls when --follow is set.",
)
@click.option(
    "--download",
    is_flag=True,
    help=(
        "Download the HopsFS log archives written when the agent was "
        "stopped or deleted. Cannot be combined with --follow or --source."
    ),
)
@click.option(
    "--dir",
    "log_dir",
    type=click.Path(file_okay=False),
    help="With --download, the directory to download into, created if missing; "
    "the working directory by default. Keep logs out of a git work tree, such "
    "as an ML system's directory: in the factory, "
    "${HOPSFS_USER_HOME_DIR:-$HOME}/Logs/factory/<slug>.",
)
@click.pass_context
def agent_logs(
    ctx: click.Context,
    name: str,
    component: str,
    tail: int,
    source: str,
    since: str | None,
    until: str | None,
    previous: bool,
    follow: bool,
    interval: float,
    download: bool,
    log_dir: str | None,
) -> None:
    """Read or follow logs from an agent component.

    \f
    Args:
        ctx: Click context.
        name: Agent name.
        component: Component to query (e.g. `predictor`, `transformer`).
        tail: Number of lines.
        source: `kubernetes` or `opensearch` (deprecated).
        since: ISO-8601 lower bound on log timestamp.
        until: ISO-8601 upper bound on log timestamp.
        previous: Read the previous, terminated container.
        follow: Stream new lines instead of returning a one-shot tail.
        interval: Seconds between polls when following.
        download: Download the HopsFS log archives instead of reading pods.
        log_dir: With `--download`, the directory to download into.
    """
    agent = _get_agent(ctx, name)

    if download:
        source_given = (
            ctx.get_parameter_source("source") is not click.core.ParameterSource.DEFAULT
        )
        if follow or source_given:
            raise click.UsageError(
                "--download cannot be combined with --follow or --source."
            )
        try:
            path = None
            if log_dir is not None:
                path = os.path.expanduser(log_dir)
                os.makedirs(path, exist_ok=True)
            local_paths = agent.download_logs(path=path)
        except Exception as exc:  # noqa: BLE001
            raise click.ClickException(
                f"Could not download the logs of agent '{name}': {exc}"
            ) from exc
        if output.JSON_MODE:
            output.print_json({"paths": local_paths})
            return
        for local_path in local_paths:
            click.echo(local_path)
        return

    if follow and previous:
        raise click.UsageError("--previous cannot be combined with --follow.")
    if follow:
        try:
            for chunk in agent.tail_logs(
                component=component,
                interval=interval,
                source=source,
                since=since or "now",
            ):
                click.echo(chunk, nl=False)
        except KeyboardInterrupt:
            return
        except Exception as exc:  # noqa: BLE001
            raise click.ClickException(
                f"Could not follow the logs of agent '{name}': {exc}"
            ) from exc
        return

    try:
        text = agent.read_logs(
            component=component,
            tail=tail,
            source=source,
            since=since,
            until=until,
            previous=previous,
        )
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(
            f"Could not read the logs of agent '{name}': {exc}"
        ) from exc
    if output.JSON_MODE:
        output.print_json({"component": component, "logs": text})
        return
    if not text and not previous:
        click.echo(
            "<no logs> (a restarted container starts empty: --previous reads the last one)"
        )
        return
    click.echo(text or "<no logs>")


@agent_group.command("delete")
@click.argument("name")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
@click.option("--force", is_flag=True, help="Force-delete even when running.")
@click.pass_context
def agent_delete(ctx: click.Context, name: str, yes: bool, force: bool) -> None:
    """Delete an agent deployment.

    \f
    Args:
        ctx: Click context.
        name: Agent name.
        yes: Skip confirmation when True.
        force: Pass `force=True` to the SDK (stops the agent first if running).
    """
    agent = _get_agent(ctx, name)
    if not yes:
        output.confirm(f"Delete agent '{name}'?")
    try:
        agent.delete(force=force)
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not delete agent '{name}': {exc}") from exc
    output.success("✓ Deleted agent %s", name)


# USD per million tokens (input, output), as of _PRICES_AS_OF.
_PRICES_AS_OF = "2026-11-04"
_PRICES = {
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-4-5": (3.00, 15.00),
}
_SUPPORTED_PROVIDERS = ("anthropic",)
_AGENT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_PY_CASTS = {
    "int": "int",
    "bigint": "int",
    "smallint": "int",
    "tinyint": "int",
    "float": "float",
    "double": "float",
    "decimal": "float",
}


@agent_group.command("init")
@click.argument("name")
@click.option(
    "--reads",
    "reads",
    multiple=True,
    required=True,
    metavar="FG[:VERSION]",
    help="A feature group the agent answers from; repeat it or comma-separate. "
    "The latest version when none is given.",
)
@click.option(
    "--search",
    "search",
    metavar="FG[:VERSION]",
    help="An embedding feature group for a search tool (up to 10 neighbours).",
)
@click.option(
    "--model",
    default="anthropic/claude-sonnet-5-5",
    show_default=True,
    help="The LLM as PROVIDER/MODEL; only anthropic is supported.",
)
@click.option(
    "--key-secret",
    help="The Hopsworks secret holding the provider's API key; PROVIDER_api_key "
    "by default. Check it with `hops secrets list`.",
)
@click.option(
    "--force", is_flag=True, help="Overwrite agents/NAME/agent.py if it exists."
)
@click.pass_context
def agent_init(
    ctx: click.Context,
    name: str,
    reads: tuple[str, ...],
    search: str | None,
    model: str,
    key_secret: str | None,
    force: bool,
) -> None:
    """Write agents/NAME/agent.py: a traced agent over feature groups.

    The script is an `AgentApp` that calls the LLM with tools: per feature
    group a bounded Trino aggregate (50 rows at most) and, when the group has
    a primary key and an online store, a primary-key lookup. The system prompt
    names the groups and their columns. Edit the prompt and tools, then deploy
    with the hint printed at the end. Nothing is deployed here.

    \f
    Args:
        ctx: Click context.
        name: Agent name: a letter first, then letters and digits.
        reads: Feature groups, `name` or `name:version`.
        search: An embedding feature group, `name` or `name:version`.
        model: `provider/model`.
        key_secret: Secret name holding the provider's API key.
        force: Overwrite an existing script.
    """
    if not _AGENT_NAME.fullmatch(name):
        raise click.BadParameter(
            "Use a letter first, then letters and digits.", param_hint="NAME"
        )
    provider, _, model_name = model.partition("/")
    if provider not in _SUPPORTED_PROVIDERS or not model_name:
        raise click.BadParameter(
            f"Use anthropic/<model>; got '{model}'.", param_hint="--model"
        )
    target = Path("agents") / name / "agent.py"
    if target.exists() and not force:
        raise click.ClickException(
            f"{target} already exists. Pass --force to overwrite it."
        )

    from hopsworks.cli.commands import fg as fg_commands  # noqa: PLC0415

    groups = [
        _read_group(ctx, fg_commands, ref, search=False) for ref in _split_refs(reads)
    ]
    search_group = None
    if search:
        if importlib.util.find_spec("hopsworks_agents.embed") is None:
            output.warn(
                "Search needs the embedding endpoint, which this install does not "
                "have (hopsworks_agents.embed): the agent is written without a "
                "search tool."
            )
        else:
            search_group = _read_group(ctx, fg_commands, search, search=True)

    code = _render_agent(
        name, model_name, key_secret or f"{provider}_api_key", groups, search_group
    )
    ast.parse(code)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(code, encoding="utf-8")

    tools = [t["name"] for t in _tool_specs(groups, search_group)]
    if output.JSON_MODE:
        output.print_json({"path": str(target), "tools": tools, "model": model})
        return
    output.success("✓ Wrote %s", target)
    click.echo("Tools: " + ", ".join(tools))
    click.echo(_deploy_hint(name, target, model_name, search_group))


def _split_refs(values: tuple[str, ...]) -> list[str]:
    return [r.strip() for v in values for r in v.split(",") if r.strip()]


def _read_group(ctx: click.Context, fg_commands: Any, ref: str, search: bool) -> dict:
    """What the script needs of one feature group, read with the SDK."""
    fg_name, _, raw_version = ref.partition(":")
    try:
        version = int(raw_version) if raw_version else None
    except ValueError:
        raise click.BadParameter(
            f"Version of '{fg_name}' must be a number.", param_hint="--reads"
        ) from None
    fg = fg_commands._get_fg(ctx, fg_name, version)
    features = getattr(fg, "features", []) or []
    group = {
        "name": fg.name,
        "version": fg.version,
        "description": output.first_line(getattr(fg, "description", ""), ""),
        "columns": [(f.name, f.type) for f in features],
        "primary_key": list(getattr(fg, "primary_key", []) or []),
        "online": bool(getattr(fg, "online_enabled", False)),
        "sample": None,
        "embeddings": [],
    }
    if search:
        index = getattr(fg, "embedding_index", None)
        if index is None:
            raise click.ClickException(
                f"Feature group '{fg.name}' has no embedding index: pass an embedding group to --search."
            )
        group["embeddings"] = [e.name for e in index.get_embeddings()]
        return group
    try:
        rows = fg.select_all().show(3)
        group["sample"] = rows.head(3).to_dict(orient="records")
    except Exception as exc:  # noqa: BLE001 - a preview is a convenience, not a gate
        output.warn(
            "Could not preview %s: %s. Its suggested prompts are generic.", fg.name, exc
        )
    return group


def _table(group: dict) -> str:
    return f"{group['name']}_{group['version']}"


def _tool_specs(groups: list[dict], search_group: dict | None) -> list[dict]:
    """The Anthropic tool definitions, in the order the script lists them."""
    specs = []
    for g in groups:
        specs.append(
            {
                "name": f"aggregate_{g['name']}",
                "description": f"Run one Trino SELECT on {_table(g)} for counts, sums, "
                f"averages or top-k. At most {_MAX_ROWS} rows come back.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": f"A SELECT reading {_table(g)}, Trino dialect.",
                        }
                    },
                    "required": ["sql"],
                },
            }
        )
        if _has_lookup(g):
            specs.append(
                {
                    "name": f"lookup_{g['name']}",
                    "description": f"Read the rows of {g['name']} for one primary key, "
                    "from the online store.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            pk: {"type": "string", "description": f"Value of {pk}."}
                            for pk in g["primary_key"]
                        },
                        "required": g["primary_key"],
                    },
                }
            )
    if search_group:
        specs.append(
            {
                "name": "search",
                "description": f"Find the rows of {search_group['name']} closest in "
                "meaning to a text query.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "k": {
                            "type": "integer",
                            "description": "Rows to return, 1 to 10.",
                        },
                    },
                    "required": ["query"],
                },
            }
        )
    return specs


_MAX_ROWS = 50


def _has_lookup(group: dict) -> bool:
    return bool(group["online"] and group["primary_key"])


def _render_agent(
    name: str,
    model_name: str,
    key_secret: str,
    groups: list[dict],
    search_group: dict | None,
) -> str:
    from hopsworks.cli import scaffold  # noqa: PLC0415

    template = scaffold.load_template("agent_init.py.tmpl")
    tool_code = ""
    for g in groups:
        types = dict(g["columns"])
        casts = {
            pk: _PY_CASTS.get(str(types.get(pk, "")).split("(")[0].lower(), "str")
            for pk in g["primary_key"]
        }
        lookup = ""
        if _has_lookup(g):
            lookup = _fill(
                scaffold.load_template("agent_init_lookup.py.tmpl"),
                FN=g["name"],
                CASTS="{"
                + ", ".join(f"{json.dumps(k)}: {v}" for k, v in casts.items())
                + "}",
            )
        tool_code += _fill(
            scaffold.load_template("agent_init_group.py.tmpl"),
            FN=g["name"],
            FG=json.dumps(g["name"]),
            VERSION=str(g["version"]),
            TABLE=json.dumps(_table(g)),
            LOOKUP=lookup,
        )
    imports = ""
    if search_group:
        imports = "from hopsworks_agents.embed import embed_query\n"
        tool_code += _fill(
            scaffold.load_template("agent_init_search.py.tmpl"),
            FG=json.dumps(search_group["name"]),
            VERSION=str(search_group["version"]),
            SKIP=json.dumps(search_group["embeddings"]),
        )
    specs = _tool_specs(groups, search_group)
    handler_map = (
        "{\n" + "".join(f'    "{s["name"]}": {s["name"]},\n' for s in specs) + "}"
    )
    return _fill(
        template,
        NAME=json.dumps(name),
        NAME_TEXT=name,
        TABLES=", ".join(_table(g) for g in groups),
        IMPORTS=imports,
        PRICE_COMMENT=_price_comment(model_name),
        MODEL=json.dumps(model_name),
        KEY_SECRET=json.dumps(key_secret),
        SYSTEM_PROMPT=_system_prompt(name, groups, search_group),
        DESCRIPTION=json.dumps(
            f"Answers questions about {', '.join(g['name'] for g in groups)}."
        ),
        PROMPTS="[\n"
        + "".join(f"        {json.dumps(p)},\n" for p in _suggested_prompts(groups))
        + "    ]",
        TOOL_CODE=tool_code,
        TOOL_SCHEMAS=json.dumps(specs, indent=4),
        TOOL_HANDLERS=handler_map,
    )


def _fill(template: str, **values: str) -> str:
    for key, value in values.items():
        template = template.replace(f"@@{key}@@", value)
    return re.sub(r"\n{4,}", "\n\n\n", template)


def _system_prompt(name: str, groups: list[dict], search_group: dict | None) -> str:
    lines = [
        f"You are {name}, an assistant that answers questions about the tables below.",
        "Answer with numbers from tool results only, and say so when the data has none.",
        f"Every tool returns at most {_MAX_ROWS} rows: aggregate in SQL instead of listing.",
        "",
    ]
    for g in groups:
        lines.append(
            f"Table {_table(g)} (feature group {g['name']}, version {g['version']})"
            + (f": {g['description']}" if g["description"] else "")
        )
        if g["primary_key"]:
            lines.append(f"  primary key: {', '.join(g['primary_key'])}")
        lines.append(f"  columns: {', '.join(f'{c} ({t})' for c, t in g['columns'])}")
    if search_group:
        lines.append(
            f"Search tool: meaning-based lookup in {search_group['name']}; "
            "cite what it returns. When it answers that search is unavailable, "
            "say so in your answer and answer without it."
        )
    body = "".join(f"        {json.dumps(line)},\n" for line in lines)
    return '"\\n".join(\n    [\n' + body + "    ]\n)"


def _suggested_prompts(groups: list[dict]) -> list[str]:
    first = groups[0]
    prompts = [f"How many rows does {first['name']} have?"]
    sample = (first["sample"] or [None])[0]
    pk = first["primary_key"][0] if first["primary_key"] else None
    if sample and pk and _has_lookup(first):
        prompts.append(f"What do we know about {pk} {sample[pk]}?")
    other = next(
        (c for c, _ in first["columns"] if c not in first["primary_key"]), None
    )
    if other:
        prompts.append(f"What are the most common values of {other}?")
    return prompts


def _price_comment(model_name: str) -> str:
    price = _PRICES.get(model_name)
    if price:
        return (
            f"# Prices, USD per million tokens, as of {_PRICES_AS_OF}: "
            f"{model_name} input {price[0]:.2f}, output {price[1]:.2f}\n"
        )
    return (
        f"# Prices for {model_name} are not in the CLI's table: take them from "
        "the provider before deploying.\n"
    )


def _deploy_hint(
    name: str, target: Path, model_name: str, search_group: dict | None = None
) -> str:
    # `hops fg embed delete` refuses while a deployment's description names its group.
    description = ""
    if search_group:
        from hopsworks.cli import embed_run  # noqa: PLC0415

        description = (
            f'      description="Searches {search_group["name"]}. '
            f'{embed_run.SEARCH}{search_group["name"]}",\n'
        )
    price = _PRICES.get(model_name)
    tracing = '{"enabled": True'
    if price:
        tracing += (
            f', "input_token_price_per_million_tokens": {price[0]}'
            f', "output_token_price_per_million_tokens": {price[1]}'
        )
    else:
        tracing += ', "input_token_price_per_million_tokens": <in>, "output_token_price_per_million_tokens": <out>'
    tracing += "}"
    return (
        f"Edit the system prompt and tools, then deploy with tracing and prices "
        f"(prices for {model_name} as of {_PRICES_AS_OF}):\n"
        "  project = hopsworks.login()\n"
        "  agent = project.get_agent_serving().deploy_agent(\n"
        f'      entry="{target}", name="{name}", environment="python-agent-pipeline",\n'
        f"{description}"
        f"      tracing={tracing})\n"
        "  agent.start(await_running=900)"
    )


def _get_model_serving(ctx: click.Context) -> Any:
    project = session.get_project(ctx)
    return project.get_model_serving()


def _memory_limit(memory: int | None) -> Any:
    """The predictor resources with this memory limit, the rest at their defaults."""
    if memory is None:
        return None
    from hsml.resources import PredictorResources, Resources

    return PredictorResources(limits=Resources(cores=None, memory=memory, gpus=None))


def _get_agent(ctx: click.Context, name: str) -> Any:
    ms = _get_model_serving(ctx)
    try:
        d = ms.get_deployment(name)
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Could not find agent '{name}': {exc}") from exc
    if d is None or not _is_agent(d):
        raise click.ClickException(f"Agent '{name}' not found.")
    return d


def _is_agent(d: Any) -> bool:
    """Identify server-only (agent) deployments with no attached model.

    Prefer the SDK-provided ``has_model`` attribute when available because it
    accounts for the full deployment model attachment state.
    Fall back to the legacy ``model_name`` heuristic for non-SDK objects.
    """
    has_model = getattr(d, "has_model", None)
    if has_model is not None:
        return not bool(has_model)
    return getattr(d, "model_name", None) in (None, "")


def _predictor_attr(d: Any, attr: str, default: Any) -> Any:
    predictor = getattr(d, "predictor", None)
    return getattr(predictor, attr, default) if predictor is not None else default


def _deployment_status(d: Any) -> str:
    """Whether the agent is running, which has to be asked for rather than read.

    A deployment's state is not an attribute on the object: `get_state()` fetches
    it. Reading `status`/`deployment_state`/`_state` off a deployment that had not
    been asked found nothing every time, so `hops agent info` printed `Status -`
    for a pod that was running and answering -- the one line anybody runs the
    command for. The attributes stay as a fallback for an object that carries one.
    """
    try:
        state = d.get_state()
    except Exception:  # noqa: BLE001 -- a status we cannot fetch is not a failed command
        state = None
    status = getattr(state, "status", None)
    if status:
        return str(status)
    for attr in ("status", "deployment_state", "_state"):
        value = getattr(d, attr, None)
        if value:
            return str(value)
    return "-"


def _git_rows(d: Any) -> list[list[Any]]:
    """Git source rows, omitted entirely for an agent deployed from a project file."""
    git_url = _predictor_attr(d, "git_url", None)
    if not git_url:
        return []
    # Branch is optional: with none configured the clone takes the repository's default, and the
    # running deployment reports the name it resolved to.
    branch = _predictor_attr(d, "git_branch", None) or _predictor_attr(
        d, "git_resolved_branch", None
    )
    return [
        ["Git URL", git_url],
        ["Git provider", _predictor_attr(d, "git_provider", None) or "-"],
        ["Git branch", branch or "default branch"],
        [
            "Auto-redeploy",
            "Enabled" if _predictor_attr(d, "git_auto_redeploy", False) else "Disabled",
        ],
        ["Deployed commit", _predictor_attr(d, "git_current_commit", None) or "-"],
    ]


def _agent_to_dict(d: Any) -> dict[str, Any]:
    return {
        "git_url": _predictor_attr(d, "git_url", None),
        "git_provider": _predictor_attr(d, "git_provider", None),
        "git_branch": _predictor_attr(d, "git_branch", None),
        "git_resolved_branch": _predictor_attr(d, "git_resolved_branch", None),
        "git_auto_redeploy": bool(_predictor_attr(d, "git_auto_redeploy", False)),
        "git_current_commit": _predictor_attr(d, "git_current_commit", None),
        "id": getattr(d, "id", None),
        "name": getattr(d, "name", None),
        "environment": _predictor_attr(d, "environment", None),
        "script_file": _predictor_attr(d, "script_file", None),
        "serving_tool": getattr(d, "serving_tool", None),
        "model_server": getattr(d, "model_server", None),
        "api_protocol": getattr(d, "api_protocol", None),
        "description": getattr(d, "description", None),
        "status": _deployment_status(d),
    }
