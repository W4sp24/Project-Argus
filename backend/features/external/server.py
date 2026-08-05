"""Serving the external app on its own port.

The port is the boundary (see :mod:`backend.features.external.app`), so this
module's job is to bind one and nothing else.

Three deliberate choices:

**Bound to 127.0.0.1, never 0.0.0.0.** The tunnel connects to loopback and
forwards from outside; Argus itself never listens on a routable interface. This
also means Windows raises no firewall prompt on first bind, which a 0.0.0.0
bind would — a dialog most people click through without reading is a poor place
to put a security decision.

**Off unless explicitly enabled.** ``settings.external_enabled`` defaults to
False, so an existing install that upgrades into this feature opens no port at
all until someone asks for it.

**Same process, separate server.** The design document says "separate process
boundary"; this runs a second :class:`uvicorn.Server` as an asyncio task in the
same process instead. The property that actually holds the line is a distinct
app object whose route table contains only ``/api/external/*`` — identical
either way. Same-process avoids a second frozen binary, a second stdout
handshake, and a second supervised child in the Electron shell, none of which
buys any additional isolation from the internet.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from dataclasses import dataclass, field

import uvicorn

from backend.core.config import Settings
from backend.features.external.app import create_external_app

logger = logging.getLogger("argus.external")

#: Always loopback. See the module docstring.
BIND_HOST = "127.0.0.1"


def reserve_port(port: int = 0, *, host: str = BIND_HOST) -> socket.socket:
    """Bind and return a listening socket.

    Binding here and handing the *socket* to uvicorn (rather than probing for a
    free port and passing the number) closes the window in which something else
    could take the port in between. This mirrors what
    ``desktop/backend/argus_server.py`` already does for the main server.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # SO_REUSEADDR means opposite things on the two platforms. On POSIX it
    # only skips the TIME_WAIT delay. On Windows it lets a *different* process
    # bind an address already in use and start receiving its traffic — so on
    # the one port a tunnel points at, it is a hijacking primitive rather than
    # a convenience. SO_EXCLUSIVEADDRUSE is the Windows way to say what
    # SO_REUSEADDR means everywhere else.
    #
    # (backend/argus_server.py sets SO_REUSEADDR unconditionally. That server
    # binds an ephemeral port and is never exposed, so the exposure differs;
    # this is not a claim that it is wrong there.)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows only
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(128)
    return sock


@dataclass
class ExternalServer:
    """A running external surface: the bound port and the task serving it."""

    port: int
    _server: uvicorn.Server
    _socket: socket.socket
    _task: asyncio.Task | None = field(default=None, repr=False)

    async def stop(self) -> None:
        """Ask the server to exit and wait for it, then release the socket."""
        self._server.should_exit = True
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(self._task, timeout=5.0)
        with contextlib.suppress(OSError):
            self._socket.close()


async def start_external_server(settings: Settings) -> ExternalServer | None:
    """Start the external surface, or return ``None`` when it is disabled.

    Never raises. A failure to bind must degrade to "the inbound surface is
    unavailable" and be logged — it must not take down the main application,
    which is entirely usable without it.
    """
    if not settings.external_enabled:
        return None

    try:
        sock = reserve_port(settings.external_port)
    except OSError:
        logger.exception(
            "external surface could not bind port %s; inbound automations are off",
            settings.external_port,
        )
        return None

    port = sock.getsockname()[1]
    config = uvicorn.Config(
        create_external_app(settings),
        log_config=None,
        # Explicit protocol implementations: uvicorn's "auto" resolves these
        # with a runtime importlib lookup that PyInstaller cannot see, which
        # is why the frozen main server pins them too.
        http="h11",
        ws="none",  # the external surface has no websockets, by design
        loop="asyncio",
        lifespan="off",
        access_log=False,
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(sockets=[sock]))
    logger.info("external surface listening on %s:%s", BIND_HOST, port)
    return ExternalServer(port=port, _server=server, _socket=sock, _task=task)
