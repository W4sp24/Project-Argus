"""The external surface's own port — that it binds loopback, and only on request."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from backend.core.config import Settings
from backend.features.external.server import (
    BIND_HOST,
    reserve_port,
    start_external_server,
)


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "20-Projects").mkdir(parents=True)
    return root


def test_it_binds_loopback_only() -> None:
    """Never 0.0.0.0: the tunnel reaches loopback, and a routable bind would
    both widen exposure and trigger a Windows firewall prompt."""
    sock = reserve_port(0)
    try:
        host, port = sock.getsockname()
        assert host == "127.0.0.1"
        assert port > 0
    finally:
        sock.close()


def test_reserve_port_returns_a_real_listening_socket() -> None:
    sock = reserve_port(0)
    try:
        port = sock.getsockname()[1]
        with socket.create_connection((BIND_HOST, port), timeout=2) as client:
            assert client is not None
    finally:
        sock.close()


@pytest.mark.anyio
async def test_disabled_by_default(vault: Path) -> None:
    """An install that upgrades into this feature opens no port until asked."""
    settings = Settings(_vault_path=vault)
    assert settings.external_enabled is False
    assert await start_external_server(settings) is None


@pytest.mark.anyio
async def test_a_failed_bind_degrades_instead_of_raising(vault: Path, monkeypatch) -> None:
    """The main application is entirely usable without the inbound surface."""
    from backend.features.external import server as server_module

    def refuse(_port: int = 0, *, host: str = BIND_HOST):
        raise OSError("address in use")

    monkeypatch.setattr(server_module, "reserve_port", refuse)
    settings = Settings(_vault_path=vault, external_enabled=True)
    assert await start_external_server(settings) is None


def test_the_port_cannot_be_hijacked_by_a_second_binder() -> None:
    """A second process must not be able to take over the tunnel-facing port.

    On Windows SO_REUSEADDR would permit exactly that, which is why
    reserve_port uses SO_EXCLUSIVEADDRUSE there instead.
    """
    first = reserve_port(0)
    try:
        port = first.getsockname()[1]
        second = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            second.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with pytest.raises(OSError):
                second.bind((BIND_HOST, port))
        finally:
            second.close()
    finally:
        first.close()


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"
