"""``hops secrets`` — the account's secrets, by name.

A secret's value is never read or printed here: an agent or job reads it
itself through ``hopsworks.get_secrets_api().get(name)``.
"""

from __future__ import annotations

import click
from hopsworks.cli import output, session


@click.group("secrets")
def secrets_group() -> None:
    """Secrets: saved names, never values."""


@secrets_group.command("list")
@click.pass_context
def secrets_list(ctx: click.Context) -> None:
    """List secret names with visibility, scope and added date.

    Values are never shown. Use it to check that a key a job or agent reads,
    such as `anthropic_api_key`, is saved before it runs.

    \f
    Args:
        ctx: Click context.
    """
    import hopsworks  # noqa: PLC0415 - the SDK import is lazy in the CLI

    session.get_project(ctx)
    try:
        found = hopsworks.get_secrets_api().get_secrets()
    except Exception as exc:  # noqa: BLE001 - SDK raises a mix of types
        raise click.ClickException(f"Could not list secrets: {exc}") from exc

    secrets = [
        {
            "name": s.name,
            "visibility": s.visibility,
            "scope": s.scope,
            "added_on": s.created,
        }
        for s in found or []
    ]
    if output.JSON_MODE:
        output.print_json(secrets)
        return
    output.print_table(
        ["NAME", "VISIBILITY", "SCOPE", "ADDED"],
        [
            [
                s["name"],
                s["visibility"] or "-",
                s["scope"] or "-",
                output.format_ts(s["added_on"]),
            ]
            for s in secrets
        ],
    )
