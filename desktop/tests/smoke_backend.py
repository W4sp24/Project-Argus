"""Tier-1 smoke test: exercise the backend entry point end to end.

Runs against either the frozen exe or the plain Python module, so the same
script gates CI (`--target dist/backend/argus-backend.exe`) and local work
(`--target desktop/backend/argus_server.py`).

Every request here exists to force an import chain that PyInstaller can break
*silently* -- a frozen build that answers /health can still die the moment you
touch keyring, chromadb's SQL migrations, or pdfminer's cmap tables. Failures
in a packaged app surface as a blank screen with no console, so this is the
cheapest place to catch them.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT = 120


class Result:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, ok, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}{f' - {detail}' if detail else ''}", flush=True)

    @property
    def failed(self) -> list[str]:
        return [name for name, ok, _ in self.rows if not ok]


def _base_cmd(target: Path) -> list[str]:
    return [sys.executable, str(target)] if target.suffix == ".py" else [str(target)]


def _cwd_for(target: Path) -> Path:
    # The .py entry needs the repo root on sys.path; the exe is self-contained.
    return target.parent.parent.parent if target.suffix == ".py" else target.parent


def _launch(target: Path, env_file: Path) -> subprocess.Popen:
    env = {**os.environ, "ARGUS_ENV_FILE": str(env_file)}
    return subprocess.Popen(
        _base_cmd(target),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=_cwd_for(target),
        env=env,
    )


def _make_vault(target: Path, workdir: Path, result: "Result") -> Path | None:
    """Create a throwaway vault via the entry point's own --init branch.

    Doubles as a test of two things freezing loves to break: that
    vault-template/ landed inside _MEIPASS where cli.py's TEMPLATE_DIR expects
    it, and that git shells out correctly from the frozen exe.
    """
    env_file = workdir / "config.env"
    vault = workdir / "vault"
    proc = subprocess.run(
        [*_base_cmd(target), "--init", str(vault)],
        capture_output=True,
        text=True,
        cwd=_cwd_for(target),
        env={**os.environ, "ARGUS_ENV_FILE": str(env_file)},
        timeout=180,
    )
    line = (proc.stdout or "").strip().splitlines()
    payload = {}
    if line:
        try:
            payload = json.loads(line[-1])
        except json.JSONDecodeError:
            pass
    ok = bool(payload.get("ok"))
    result.check(
        "--init creates a vault",
        ok,
        payload.get("error") or (proc.stderr or "").strip()[-300:] if not ok else str(vault),
    )
    if not ok:
        return None
    result.check("  template files copied", (vault / "Welcome.md").is_file())
    result.check("  vault git-initialised", (vault / ".git").is_dir())
    return env_file


def _handshake(proc: subprocess.Popen) -> int | None:
    """Read the first stdout line and pull the port out of it."""
    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            return None
        line = proc.stdout.readline()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue  # stray log line before the handshake
        return payload.get("port")
    return None


def _get(port: int, path: str, timeout: int = 60) -> tuple[int, str]:
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read(400).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(400).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, repr(exc)


def _post(port: int, path: str, timeout: int = 120) -> tuple[int, str]:
    url = f"http://127.0.0.1:{port}{path}"
    request = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(2000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(2000).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, repr(exc)


def _watchdog_dies_with_parent(target: Path) -> bool:
    """Force-kill a stand-in parent; the backend must exit on its own."""
    dummy = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(600)"],
        stdin=subprocess.DEVNULL,
    )
    child = subprocess.Popen(
        [*_base_cmd(target), "--parent-pid", str(dummy.pid)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        cwd=_cwd_for(target),
    )
    try:
        if _handshake(child) is None:
            return False
        dummy.kill()
        dummy.wait(timeout=10)
        try:
            child.wait(timeout=25)
            return True
        except subprocess.TimeoutExpired:
            return False
    finally:
        for proc in (child, dummy):
            if proc.poll() is None:
                proc.kill()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, help="exe or argus_server.py")
    parser.add_argument(
        "--skip-heavy",
        action="store_true",
        help="skip the search call that loads torch (slow on a cold cache)",
    )
    parser.add_argument(
        "--env-file",
        help="existing config.env to use; default creates a throwaway vault",
    )
    args = parser.parse_args()
    target = Path(args.target).resolve()

    result = Result()
    print(f"Smoke-testing {target}", flush=True)

    workdir = Path(tempfile.mkdtemp(prefix="argus-smoke-"))
    if args.env_file:
        env_file = Path(args.env_file).resolve()
    else:
        env_file = _make_vault(target, workdir, result)
        if env_file is None:
            print("\nFAILED: could not create a test vault")
            return 1

    started = time.time()
    proc = _launch(target, env_file)
    port = _handshake(proc)

    if port is None:
        print("FATAL: no port handshake. stderr follows:", flush=True)
        proc.kill()
        print(proc.stderr.read()[-4000:])
        return 1
    result.check("handshake", True, f"port {port} in {time.time() - started:.1f}s")

    try:
        status, _ = _get(port, "/health")
        result.check("GET /health", status == 200, f"HTTP {status}")

        # Validates keyring backends, sqlite schema, and the chromadb import
        # in one call -- all three resolve through entry points or data files
        # that vanish when frozen.
        status, body = _post(port, "/api/doctor")
        result.check("POST /api/doctor", status == 200, f"HTTP {status}")
        if status == 200:
            try:
                checks = {c["name"]: c["status"] for c in json.loads(body)}
            except (json.JSONDecodeError, TypeError, KeyError):
                checks = {}
            for name in ("keyring", "database", "chroma"):
                got = checks.get(name, "MISSING")
                result.check(f"  doctor:{name}", got == "OK", got)

        status, _ = _get(port, "/api/notes")
        result.check("GET /api/notes", status == 200, f"HTTP {status}")

        status, _ = _get(port, "/api/tasks")
        result.check("GET /api/tasks", status == 200, f"HTTP {status}")

        status, _ = _get(port, "/api/usage/cli?range=today")
        result.check("GET /api/usage/cli", status == 200, f"HTTP {status}")

        if not args.skip_heavy:
            # Forces chromadb + sentence-transformers + torch to actually load.
            status, body = _get(port, "/api/search?q=test", timeout=300)
            result.check("GET /api/search (torch)", status == 200, f"HTTP {status}")
    finally:
        proc.kill()
        proc.wait(timeout=10)

    # The orphan guard, tested the way it actually fails in the wild: give the
    # backend a stand-in parent, force-kill that parent (no handlers run, just
    # like Task Manager "End task"), and require the backend to notice.
    result.check("parent-death watchdog", _watchdog_dies_with_parent(target))

    shutil.rmtree(workdir, ignore_errors=True)

    print()
    if result.failed:
        print(f"FAILED: {', '.join(result.failed)}")
        return 1
    print(f"All {len(result.rows)} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
