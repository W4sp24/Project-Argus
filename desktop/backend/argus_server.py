"""PyInstaller entry point for the Argus backend inside the desktop shell.

Differences from ``uvicorn backend.main:app`` (the dev path, unchanged):

* **Race-free port handshake.** We bind a socket to port 0 *ourselves* and only
  then print the port to stdout, so by the time Electron reads it the port is
  already held. Probing for a free port and passing it to a child leaves a
  window where something else can take it.
* **Parent-handle watchdog.** When the parent dies for *any* reason --
  including Task Manager "End task", which runs no handlers -- we exit. This is
  the only cleanup layer that survives a force-kill of the parent, so it is the
  one that actually prevents orphaned 700MB Python processes.

  It waits on the parent's process *handle*, deliberately not on stdin. The
  obvious "read stdin until EOF" version deadlocks this app: a thread blocked
  in a stdin read stalls C-extension imports for the whole process, so the
  first request that reaches ``import chromadb`` (which pulls numpy's compiled
  ``_multiarray_umath``) hangs forever and takes every later request with it.
  Verified: with a stdin-blocked thread ``import numpy`` never completes; with
  a thread blocked on ``sleep``, ``Event.wait``, or ``WaitForSingleObject`` it
  completes in 0.13s. ``WaitForSingleObject`` also releases the GIL, so it
  costs nothing while it waits.
* **Explicit protocol implementations.** ``uvicorn``'s default ``auto`` picks
  its http/ws/loop backends through a runtime ``importlib`` that PyInstaller's
  static analyser cannot see, which is the single most common way a frozen
  uvicorn dies at bind time. Naming them pins the imports.

Also carries the diagnostic branches so the onboarding wizard and the release
smoke gate never need a second frozen binary: ``--init`` delegates to
``backend.cli.init_vault`` -- the same code path ``argus init`` uses -- rather
than reimplementing it; ``--doctor`` runs the same checks as ``argus doctor``;
and ``--selftest-imports`` actually imports every lazily-loaded optional
dependency (the gcal/todoist connectors, chromadb, mcp, ...) up front, so a
build that is missing one of them fails here instead of the first time a real
user clicks Connect.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
from pathlib import Path


def _exit_when_parent_dies(parent_pid: int) -> None:
    """Block until ``parent_pid`` terminates, then exit hard.

    Never touches stdin -- see the module docstring for why that deadlocks.
    """
    if sys.platform == "win32":
        import ctypes

        # Win32 constant names, kept as-is for greppability against MSDN.
        SYNCHRONIZE = 0x00100000  # noqa: N806
        INFINITE = 0xFFFFFFFF  # noqa: N806
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, parent_pid)
        if not handle:
            return  # parent already gone, or no rights; other layers cover us
        try:
            kernel32.WaitForSingleObject(handle, INFINITE)
        finally:
            kernel32.CloseHandle(handle)
    else:
        import time

        while True:
            try:
                os.kill(parent_pid, 0)
            except OSError:
                break
            time.sleep(2)
    os._exit(0)


def _emit(payload: dict) -> None:
    """Write one handshake line Electron can parse off stdout."""
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _cmd_init(dest: Path) -> int:
    """Create a vault at ``dest`` and record VAULT_PATH in ARGUS_ENV_FILE."""
    from backend.cli import InitError, init_vault
    from backend.core.config import DEFAULT_ENV_FILE

    try:
        created = init_vault(dest, env_file=DEFAULT_ENV_FILE)
    except InitError as exc:
        _emit({"ok": False, "error": str(exc)})
        return 1
    _emit({"ok": True, "vault": str(created)})
    return 0


def _cmd_doctor() -> int:
    """Run the same checks as ``argus doctor``, as JSON for the wizard.

    ``run_checks`` reaches ``Settings.vault_path``, which raises when no vault
    is configured yet, so guard it -- the wizard calls this on a half-set-up
    machine by definition.
    """
    from backend.core.config import ConfigError, Settings
    from backend.features.system.doctor import run_checks

    try:
        checks = run_checks(Settings.load())
    except ConfigError as exc:
        _emit({"ok": False, "error": str(exc)})
        return 1
    _emit({"ok": True, "checks": [check.model_dump() for check in checks]})
    return 0


def _cmd_selftest_imports() -> int:
    """Import every lazily-loaded optional dependency and report what failed.

    Almost everything interesting in this app -- the gcal/todoist connectors,
    chromadb, sentence-transformers, the mcp bridge -- is imported lazily,
    deep inside a function, precisely so the base app works without them.
    That is also exactly why a frozen build can be missing every one of them
    and still look completely healthy: it answers /health, --doctor comes
    back clean, and the only person who finds out is the user who clicks
    Connect and gets a 501 with no pip to act on it. (v0.2.0 shipped exactly
    like this: `pip install -e ".[rag]"` in CI never installed the `gcal`
    extra, so google_auth_oauthlib was never bundled.) This flag imports the
    whole set up front so a bad freeze fails in the smoke gate instead of in
    someone's support message.
    """
    import importlib

    modules = [
        "google_auth_oauthlib.flow",
        "googleapiclient.discovery",
        "google.oauth2.credentials",
        "google.auth.transport.requests",
        "todoist_api_python.api",
        "chromadb",
        "sentence_transformers",
        "keyring",
        "mcp.server",
        "fsrs",
    ]
    missing = []
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - a frozen build can fail with
            # OSError on a missing DLL, not just ImportError, so this has to
            # catch broadly to keep sweeping the rest of the list.
            missing.append({"module": name, "error": str(exc)})
    _emit({"ok": not missing, "missing": missing})
    return 0 if not missing else 1


def _cmd_serve(host: str, parent_pid: int) -> int:
    import uvicorn

    from backend.main import _production_scheduler, create_app

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, 0))
    sock.listen(128)
    _emit({"ok": True, "port": sock.getsockname()[1]})

    if parent_pid:
        threading.Thread(
            target=_exit_when_parent_dies, args=(parent_pid,), daemon=True
        ).start()

    app = create_app(scheduler_factory=_production_scheduler)
    config = uvicorn.Config(
        app,
        log_config=None,
        http="h11",
        ws="websockets",
        loop="asyncio",
        lifespan="on",
        access_log=False,
    )
    uvicorn.Server(config).run(sockets=[sock])
    return 0


def _cmd_mcp_server() -> int:
    """Serve the vault's read-only tools over stdio, for any MCP client.

    The desktop app ships this binary, not the ``argus`` console script, so
    without this flag a desktop-only user could not run the MCP bridge at all —
    while the README and the /system panel both handed them a copy-pasteable
    ``argus mcp-server`` command.

    Nothing may be printed to stdout here: stdout *is* the MCP transport.
    """
    import asyncio

    from backend.agent.mcp_server import serve_stdio
    from backend.core.config import ConfigError, Settings

    settings = Settings.load()
    try:
        # The access, not the load: Settings.load() returns an unconfigured
        # object and defers ConfigError to first use, so guarding the load
        # caught nothing and an unconfigured vault escaped as a traceback out
        # of asyncio.run below. Same shape as `argus mcp-server` in backend/cli.
        _ = settings.vault_path  # raises ConfigError when VAULT_PATH is unset
    except ConfigError as exc:
        print(f"argus: {exc}", file=sys.stderr)
        return 1
    try:
        asyncio.run(serve_stdio(settings))
    except KeyboardInterrupt:
        return 130
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="argus-backend", add_help=True)
    parser.add_argument("--init", metavar="PATH", help="create a vault and exit")
    parser.add_argument("--doctor", action="store_true", help="print checks as JSON and exit")
    parser.add_argument(
        "--selftest-imports",
        action="store_true",
        help="import every lazily-loaded optional dependency and report failures as JSON",
    )
    parser.add_argument(
        "--mcp-server",
        action="store_true",
        help="serve the vault's read-only tools over stdio (MCP) and exit",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1)")
    parser.add_argument(
        "--parent-pid",
        type=int,
        default=None,
        help="exit when this pid does (defaults to our parent; 0 disables)",
    )
    args = parser.parse_args(argv)

    if args.init:
        return _cmd_init(Path(args.init).expanduser())
    if args.doctor:
        return _cmd_doctor()
    if args.selftest_imports:
        return _cmd_selftest_imports()
    if args.mcp_server:
        return _cmd_mcp_server()
    parent_pid = args.parent_pid if args.parent_pid is not None else os.getppid()
    return _cmd_serve(args.host, parent_pid)


if __name__ == "__main__":
    sys.exit(main())
