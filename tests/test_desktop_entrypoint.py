"""Tests for desktop/backend/argus_server.py — the frozen app's entry point.

This module is not importable as part of the ``backend`` package (it is the
PyInstaller entry script), so it is exercised the way the desktop app and the
smoke gate exercise it: as a subprocess.

The case covered here is the one that broke the v0.2.0 release build. Every
other entry point resolves ``VAULT_PATH`` eagerly, but ``--mcp-server`` wrapped
``Settings.load()`` in the ``ConfigError`` handler instead of the *access* that
actually raises -- ``Settings.load`` returns an unconfigured object and defers
the error to first use. An unconfigured vault therefore escaped as a raw
traceback out of ``asyncio.run`` rather than the intended one-line message.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY = REPO_ROOT / "desktop" / "backend" / "argus_server.py"


def run_entry(args: list[str], env_file: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run the entry point with an explicit env file, from the repo root."""
    return subprocess.run(
        [sys.executable, str(ENTRY), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "ARGUS_ENV_FILE": str(env_file)},
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


@pytest.fixture()
def unconfigured_env_file(tmp_path: Path) -> Path:
    """An env file that parses cleanly but sets no VAULT_PATH."""
    env_file = tmp_path / "config.env"
    env_file.write_text("BACKEND_PORT=8000\n", encoding="utf-8")
    return env_file


def test_mcp_server_reports_an_unconfigured_vault_without_a_traceback(
    unconfigured_env_file: Path,
) -> None:
    """A desktop user who has not made a vault yet must get a readable error.

    They reach this by pasting the /system panel's MCP command into their
    coding agent before finishing setup. A traceback there is indistinguishable
    from a broken install, and stdout is the MCP transport -- so the message has
    to arrive on stderr, alone, with a non-zero exit.
    """
    result = run_entry(["--mcp-server"], unconfigured_env_file)

    assert result.returncode == 1, f"expected a clean exit 1, got {result.returncode}"
    assert "Traceback" not in result.stderr, f"crashed instead of reporting:\n{result.stderr}"
    assert "VAULT_PATH" in result.stderr
    # stdout is the protocol stream; a stray line there corrupts the handshake.
    assert result.stdout == ""
