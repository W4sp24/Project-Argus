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
import contextlib
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


def annotate(message: str) -> None:
    """Emit a GitHub Actions error annotation.

    Annotations show on the run summary page without signing in, whereas raw
    step logs do not -- so this is what makes a CI failure diagnosable from
    outside the repo. No-op when not running under Actions.
    """
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    flat = " ".join(str(message).split())[:900]
    print(f"::error title=smoke_backend::{flat}", flush=True)


class Result:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, ok, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}{f' - {detail}' if detail else ''}", flush=True)
        if not ok:
            annotate(f"{name}: {detail}" if detail else name)

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


def _make_vault(target: Path, workdir: Path, result: Result) -> Path | None:
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
        with contextlib.suppress(json.JSONDecodeError):
            payload = json.loads(line[-1])
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


def _get(port: int, path: str, timeout: int = 60, limit: int = 400) -> tuple[int, str]:
    """GET a path. ``limit`` caps the body read — raise it when parsing JSON.

    The 400-byte default keeps failure output readable for checks that only
    look at the status code. A truncated body is not valid JSON, so any check
    that parses one must pass a limit large enough for the whole payload.
    """
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read(limit).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(limit).decode("utf-8", "replace")
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


def _tail(text: str, limit: int = 600) -> str:
    """The *end* of a traceback — where the exception actually is.

    Truncating from the front yields nothing but "Traceback (most recent call
    last)" and a few frame headers, which is how a ConfigError escaping
    ``--mcp-server`` reached CI as an unreadable annotation. Annotations are the
    only failure detail visible without a login, so they have to carry the tail.
    """
    flat = text.strip()
    return flat[-limit:].strip() if len(flat) > limit else flat


def _mcp_server_starts(target: Path, env_file: Path) -> tuple[bool, str]:
    """Does ``--mcp-server`` actually serve, or die on a missing import?

    The bridge's ``mcp`` imports are all lazy and in-function, which PyInstaller
    cannot see. Without ``collect_submodules("mcp")`` in the spec, the frozen
    exe accepts the flag and then dies with ``ModuleNotFoundError: mcp`` — and
    the only user who would ever find out is one following the README.

    Probed with a real MCP ``initialize`` over stdio rather than by starting and
    killing it, because a process that exits instantly on a bad import and one
    that is quietly waiting for input look identical from the outside.

    ``env_file`` is not optional: the bridge resolves VAULT_PATH before it
    serves, so without the throwaway vault's env file this check only ever
    proved that an unconfigured backend refuses to start.
    """
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "argus-smoke", "version": "0"},
            },
        }
    )
    proc = subprocess.Popen(
        [*_base_cmd(target), "--mcp-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=_cwd_for(target),
        env={**os.environ, "ARGUS_ENV_FILE": str(env_file)},
    )
    try:
        out, err = proc.communicate(input=request + "\n", timeout=180)
    except subprocess.TimeoutExpired:
        proc.kill()
        return False, "no response to initialize within 180s"
    if "ModuleNotFoundError" in err:
        # The exact failure the spec's hiddenimports exist to prevent.
        return False, err.strip().splitlines()[-1][:200]
    if '"result"' in out and "serverInfo" in out:
        return True, "handshake ok"
    return False, _tail(err) or _tail(out) or "no handshake"


def _connector_imports(target: Path) -> tuple[bool, str]:
    """Does the frozen build actually have the gcal/todoist client libraries?

    This is the check that would have caught v0.2.0 shipping without
    google_auth_oauthlib: that release answered /health, passed --doctor, and
    passed every other check here, because nothing exercised either
    connector's lazy, in-function import. ``--selftest-imports`` imports them
    (and the rest of the lazily-loaded optional stack) up front and reports
    failures as one JSON line, the same handshake shape ``desktop/main.js``
    parses for every other one-shot command.
    """
    try:
        proc = subprocess.run(
            [*_base_cmd(target), "--selftest-imports"],
            capture_output=True,
            text=True,
            cwd=_cwd_for(target),
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, "no response within 120s"

    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    if not lines:
        return False, f"no stdout (exit {proc.returncode}); stderr: {_tail(proc.stderr or '')}"
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError:
        return False, f"unparseable stdout: {_tail(lines[-1])}"

    if proc.returncode != 0 and payload.get("ok"):
        # Contradiction between the exit code and the payload -- report both
        # rather than silently trusting one.
        return False, f"exit {proc.returncode} but payload claimed ok=true"

    missing = payload.get("missing") or []
    if missing:
        names = ", ".join(str(entry.get("module", "?")) for entry in missing)
        return False, f"missing: {names}"
    return bool(payload.get("ok")), "all optional dependencies importable"


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
        stderr = proc.stderr.read()[-4000:]
        print(stderr)
        annotate(f"no port handshake; stderr tail: {stderr[-600:]}")
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

        # Routes added after the first frozen build shipped. A stale
        # resources/backend answers 200 on everything above and 404 here, which
        # is exactly how the packaged app came to pair a current dashboard with
        # a day-old backend and show an empty usage panel. Every router mounted
        # under the system router gets a check so that mismatch fails the build
        # instead of reaching someone's install.
        status, body = _get(port, "/api/usage/agents?range=today", limit=20_000)
        result.check("GET /api/usage/agents", status == 200, f"HTTP {status}")
        if status == 200:
            try:
                agents = [a["id"] for a in (json.loads(body).get("agents") or [])]
            except (json.JSONDecodeError, TypeError, KeyError):
                agents = []
            for name in ("claude-code", "claude-subagents", "codex"):
                present = name in agents
                result.check(f"  agents:{name}", present, "present" if present else "MISSING")

        status, _ = _get(port, "/api/usage/agents/formats")
        result.check("GET /api/usage/agents/formats", status == 200, f"HTTP {status}")

        status, _ = _get(port, "/api/integrations")
        result.check("GET /api/integrations", status == 200, f"HTTP {status}")

        # The model registry, and behind it the hardware probe. Worth its own
        # check because backend/agent/hardware.py reaches for ctypes
        # (GlobalMemoryStatusEx) and subprocess (nvidia-smi) -- exactly the
        # kinds of runtime resolution that freezing breaks silently. A probe
        # that returns None here would degrade the whole local-model picker to
        # "unknown" in the packaged app while still answering 200.
        status, body = _get(port, "/api/models/catalog", limit=20_000)
        result.check("GET /api/models/catalog", status == 200, f"HTTP {status}")
        if status == 200:
            try:
                payload = json.loads(body)
            except (json.JSONDecodeError, TypeError):
                payload = {}
            models = payload.get("models") or []
            result.check("  catalog:models listed", len(models) > 0, f"{len(models)} entries")
            ram = (payload.get("hardware") or {}).get("ram_gb")
            result.check("  catalog:RAM detected", bool(ram), f"{ram} GB")
            verdicts = {entry.get("verdict") for entry in models}
            result.check(
                "  catalog:fit verdicts scored",
                bool(verdicts) and verdicts != {"unknown"},
                ", ".join(sorted(v for v in verdicts if v)),
            )

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

    # The MCP bridge, which the desktop build could not run at all until the
    # --mcp-server flag and the spec's mcp hiddenimports landed together.
    ok, detail = _mcp_server_starts(target, env_file)
    result.check("--mcp-server serves over stdio", ok, detail)

    # Would have caught v0.2.0 shipping without google_auth_oauthlib: every
    # check above passes on a build missing both connector client libraries,
    # because nothing else actually imports them.
    ok, detail = _connector_imports(target)
    result.check("connector imports", ok, detail)

    shutil.rmtree(workdir, ignore_errors=True)

    print()
    if result.failed:
        print(f"FAILED: {', '.join(result.failed)}")
        return 1
    print(f"All {len(result.rows)} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
