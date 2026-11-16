"""Thin wrapper around ``hopsworks.login()`` for the CLI.

Importing ``hopsworks`` transitively imports ``hsfs``/``hsml`` which adds ~1s
of startup cost, so every function here defers that import until the caller
actually needs a live SDK connection.
The module also exposes a ``normalize_host()`` helper shared by the token-flow
commands and ``hops login``.
"""

from __future__ import annotations

import io
import logging
import sys
import threading
import urllib.parse
from typing import TYPE_CHECKING, Any, NoReturn

import click
from hopsworks.cli import config


if TYPE_CHECKING:  # pragma: no cover - import only for type hints
    from hopsworks.project import Project


_logger = logging.getLogger(__name__)


def normalize_host(host: str) -> str:
    """Return ``host`` as ``https://<netloc>`` with trailing API paths stripped.

    The SDK stores REST endpoints as ``https://host/hopsworks-api/api``; the
    user may paste any of the above forms. Stripping them here keeps URL
    construction in other modules a simple ``f"{host}/hopsworks-api/api/..."``.

    Args:
        host: Raw host string as typed by the user or loaded from config.

    Returns:
        A canonical ``scheme://netloc`` form with no trailing path.
    """
    if "://" not in host:
        host = "https://" + host
    parsed = urllib.parse.urlparse(host.strip())
    netloc = parsed.netloc or parsed.path
    base = f"{parsed.scheme or 'https'}://{netloc}"
    return base.rstrip("/")


def api_base(host: str) -> str:
    """Return the ``/hopsworks-api/api`` base URL for a given host.

    Args:
        host: Raw or normalized host string.

    Returns:
        A full REST base URL ready for endpoint concatenation.
    """
    return normalize_host(host) + "/hopsworks-api/api"


def refused_key_fix(source: str | None) -> str:
    """Say how to replace a refused API key from ``source``; empty when it has none.

    Args:
        source: Where the key came from (``HopsConfig.api_key_source``).

    Returns:
        A clause starting lowercase, without a final period.
    """
    if not source:
        return ""
    if source == "HOPSWORKS_API_KEY":
        return f"check it, or unset it to use the key in {config.CONFIG_PATH}"
    if source.startswith("--"):
        return "check it"
    return "check it, or run `hops setup --force` to create a new one"


def login_failed(exc: Exception, source: str | None = None) -> NoReturn:
    """Raise the ``ClickException`` that says why a login failed.

    A 401 means the backend refused the API key: the message names where the
    key came from and how to replace it, and is raised ``from None`` so the root
    handler does not explain it again. A 403 means the key is valid but lacks
    access. Either way the backend's own message is kept.

    Args:
        exc: Whatever ``hopsworks.login()`` raised.
        source: Where the key came from (``HopsConfig.api_key_source``).

    Raises:
        click.ClickException: Always.
    """
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status not in (401, 403):
        raise click.ClickException(f"Could not log in: {exc}") from exc
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        body = {}
    detail = body.get("errorMsg") or body.get("usrMsg") or response.reason
    if status == 403:
        raise click.ClickException(
            f"Could not log in: access refused ({detail})"
        ) from exc
    key = f"the API key from {source}" if source else "the API key"
    fix = refused_key_fix(source)
    tail = f". {fix[0].upper()}{fix[1:]}." if fix else ""
    raise click.ClickException(
        f"Could not log in: {key} was refused ({detail}){tail}"
    ) from None


def login(
    host: str,
    api_key_value: str | None = None,
    project: str | None = None,
    engine: str | None = None,
    internal: bool = False,
    hostname_verification: bool = False,
    project_id: int | None = None,
) -> Project:
    """Call ``hopsworks.login()`` with a stable argument shape.

    Separated from ``hopsworks.login`` so the CLI can swap in test doubles and
    so the heavy SDK import stays lazy.

    Internal-mode behaviour: when ``internal`` is true (the CLI is running
    inside a Hopsworks pod), call ``hopsworks.login()`` with no host/port/key
    arguments so the SDK's own env-var detection (``REST_ENDPOINT`` +
    ``$SECRETS_DIR/token.jwt``) takes the auth path. Passing ``host``/``port``
    explicitly works in current SDK versions because it ignores them when
    ``REST_ENDPOINT`` is set, but relying on that is a footgun: a future SDK
    refactor that honours the explicit args would silently break in-pod auth.

    Args:
        host: Hopsworks host; ``https://`` prefix and trailing paths tolerated.
            Defaults port to 443 for ``https://`` (or no scheme), 80 for
            ``http://``.
        api_key_value: API key; omit to let the SDK pick up its own env/cache.
        project: Default project to attach to.
        engine: SDK engine; defaults to ``python``. Left unset, the SDK picks
            ``spark`` whenever pyspark is importable and then needs a Spark
            session configured for Hopsworks, which a laptop never has.
        internal: When True, call ``hopsworks.login()`` with no args so the
            SDK reads its in-pod credentials from environment variables.
        hostname_verification: Whether to verify Hopsworks' TLS certificate;
            False skips verification (the SDK default). Passed straight through
            to ``hopsworks.login(hostname_verification=...)``.
        project_id: Id of the project to attach to; takes precedence over ``project``,
            since several projects may share a name.

    Returns:
        The authenticated SDK ``Project`` object.
    """
    import hopsworks  # noqa: PLC0415 - intentionally lazy

    engine = engine or "python"
    if internal:
        # Let the SDK do its own ``REST_ENDPOINT`` + ``$SECRETS_DIR/token.jwt``
        # discovery. ``project``/``engine`` still apply at the SDK layer.
        kwargs: dict[str, Any] = {"hostname_verification": hostname_verification}
        if project:
            kwargs["project"] = project
        if engine:
            kwargs["engine"] = engine
        return _quiet_login(hopsworks, kwargs)

    parsed = urllib.parse.urlparse(host if "://" in host else "https://" + host)
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc or parsed.path
    default_port = 80 if scheme == "http" else 443
    port = parsed.port or default_port
    hostname = netloc.split(":")[0]

    kwargs = {
        "host": hostname,
        "port": port,
        "hostname_verification": hostname_verification,
    }
    if api_key_value:
        kwargs["api_key_value"] = api_key_value
    if project_id:
        kwargs["project_id"] = project_id
    elif project:
        kwargs["project"] = project
    if engine:
        kwargs["engine"] = engine
    return _quiet_login(hopsworks, kwargs)


class _ThreadSilencedStdout(io.TextIOBase):
    """Stdout that discards writes from threads that asked for silence.

    ``contextlib.redirect_stdout`` swaps ``sys.stdout`` for the whole process, so
    a login in a background thread (``hops factory run`` logs in while the user answers
    its first question) would swallow the main thread's prompts.
    """

    _silenced = threading.local()

    def __init__(self, real: Any) -> None:
        self.real = real

    def write(self, text: str) -> int:
        if getattr(self._silenced, "on", False):
            return len(text)
        return self.real.write(text)

    def flush(self) -> None:
        self.real.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.real, name)


def _quiet_login(hopsworks: Any, kwargs: dict[str, Any]) -> Project:
    """``hopsworks.login`` without its ``print`` banner.

    The SDK prints "Logged in to project, explore it here <url>" to stdout on
    every login. A CLI reports through ``output.*`` and its stdout carries the
    command payload (``--json``, pipelines), so the banner is discarded, for
    the calling thread only.
    """
    if not isinstance(sys.stdout, _ThreadSilencedStdout):
        sys.stdout = _ThreadSilencedStdout(sys.stdout)
    _ThreadSilencedStdout._silenced.on = True
    try:
        return hopsworks.login(**kwargs)
    finally:
        _ThreadSilencedStdout._silenced.on = False


def verify(
    host: str,
    api_key_value: str,
    project: str | None = None,
    hostname_verification: bool = False,
    project_id: int | None = None,
) -> Project:
    """Run a minimal login to confirm ``api_key_value`` works against ``host``.

    Raises whatever ``hopsworks.login()`` raises on failure so the caller can
    surface a specific error message to the user.

    Args:
        host: Hopsworks host.
        api_key_value: API key to validate.
        project: Optional project to attach to.
        hostname_verification: Whether to verify Hopsworks' TLS certificate.
        project_id: Optional id of the project to attach to; takes precedence over ``project``.

    Returns:
        The authenticated SDK ``Project`` object.
    """
    return login(
        host=host,
        api_key_value=api_key_value,
        project=project,
        hostname_verification=hostname_verification,
        project_id=project_id,
    )
