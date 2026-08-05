"""The external surface's interaction with app startup.

The invariant guarded here is the one every test in this repo depends on:
``create_app`` without a ``scheduler_factory`` must not spawn background work.
Adding a second server to the lifespan is exactly the kind of change that
breaks it, so it is asserted directly rather than inferred from the suite
staying fast.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import DEFAULT_EXTERNAL_PORT, Settings
from backend.features.external.server import start_external_server
from backend.main import create_app


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "20-Projects").mkdir(parents=True)
    return root


def test_a_test_app_binds_no_external_port(vault: Path) -> None:
    """external_enabled defaults to False, so the suite opens no sockets."""
    settings = Settings(_vault_path=vault)
    before = threading.active_count()
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        assert getattr(client.app.state, "external_port", None) is None
    assert threading.active_count() <= before


def test_the_default_port_is_fixed_not_ephemeral() -> None:
    """A tunnel is configured against this port by hand.

    An OS-assigned port would move on every restart and silently break that
    tunnel — the same failure the design flags for ephemeral tunnel hostnames.
    """
    assert DEFAULT_EXTERNAL_PORT != 0
    assert Settings(_vault_path=Path("x")).external_port == DEFAULT_EXTERNAL_PORT


@pytest.mark.anyio
async def test_the_main_api_is_not_reachable_on_the_public_port(vault: Path) -> None:
    """The whole feature's safety argument, asserted against a real socket.

    The external app is a separate FastAPI object whose route table contains
    only /api/external/*. If someone later mounts the main routers onto it, or
    collapses the two apps back into one behind a path check, this goes red.
    """
    import asyncio
    import socket
    import urllib.error
    import urllib.request

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
    probe.close()

    settings = Settings(
        _vault_path=vault, external_enabled=True, external_port=free_port
    )
    server = await start_external_server(settings)
    assert server is not None
    base = f"http://127.0.0.1:{server.port}"

    def status(url: str) -> int:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code

    try:
        await asyncio.sleep(0.4)
        # The external surface itself works...
        assert await asyncio.to_thread(status, f"{base}/api/external/ping") == 204
        # ...and refuses an anonymous read.
        assert await asyncio.to_thread(status, f"{base}/api/external/tasks") == 401
        # ...while nothing from the main application exists here at all.
        for path in (
            "/health",
            "/api/notes",
            "/api/tasks",
            "/api/automations",
            "/api/integrations",
            "/docs",
            "/openapi.json",
        ):
            assert await asyncio.to_thread(status, base + path) == 404, path
    finally:
        await server.stop()


def test_the_external_port_is_not_adjacent_to_the_main_one() -> None:
    """A tunnel pointed one port off should fail, not expose the main API.

    The main app on 8000 is unauthenticated by design; it must not be
    something a fat-fingered tunnel config can reach.
    """
    from backend.core.config import DEFAULT_BACKEND_PORT

    assert abs(DEFAULT_EXTERNAL_PORT - DEFAULT_BACKEND_PORT) > 1


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"
