"""A real HTTP server standing in for Hopsworks, with the real SDK client pointed at it.

Tests that assert the outgoing request use it instead of a mocked client: the
request is built, sent and recorded the way production sends it.
"""

from __future__ import annotations

import contextlib
import http.server
import json
import threading
from typing import TYPE_CHECKING

import requests
from hopsworks_common import client


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


PROJECT_ID = 119
API = f"/hopsworks-api/api/project/{PROJECT_ID}"

Answer = tuple[int, dict | None]


class _Client(client.base.Client):
    """The SDK's base client over a plain session, no login."""

    def __init__(self, base_url: str):
        self._base_url = base_url
        self._session = requests.Session()
        self._auth = None
        self._verify = False
        self._connected = True
        self._project_id = PROJECT_ID
        self._project_name = "demo"

    def _is_external(self) -> bool:
        return False


@contextlib.contextmanager
def hopsworks_server(
    answer: Callable[[str, str, dict | None], Answer],
) -> Iterator[list[tuple[str, str, dict | None]]]:
    """Serve `answer(method, path, json_body)` and install a real client for it.

    Yields:
        The requests received, as (method, path, json body) in order.
    """
    seen: list[tuple[str, str, dict | None]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def _serve(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = json.loads(raw) if raw else None
            path = self.path.split("?")[0]
            seen.append((self.command, path, body))
            status, payload = answer(self.command, path, body)
            data = json.dumps(payload).encode() if payload is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = _serve  # noqa: N815

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    previous = client._client
    client._client = _Client(f"http://127.0.0.1:{server.server_port}")
    try:
        yield seen
    finally:
        client._client = previous
        server.shutdown()
        server.server_close()
