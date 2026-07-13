"""Argus command-line interface.

Currently one command: ``argus init <path>`` — create a new vault from the
bundled template, git-init it (groundwork for invariant I2: the writer commits
the vault before every apply), and record ``VAULT_PATH`` in ``.env``.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from backend.config import DEFAULT_ENV_FILE, parse_env_file

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "vault-template"
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
EMPTY_FOLDERS = [
    "00-Inbox",
    "10-Daily",
    "15-Courses/CS000/notes",
    "15-Courses/CS000/materials",
    "15-Courses/CS000/study",
    "20-Projects",
    "30-Areas",
    "40-People",
    "50-Reference",
    "99-Private",
]


class InitError(RuntimeError):
    """Raised when a vault cannot be initialised at the requested path."""


def _run_git(args: list[str], cwd: Path) -> None:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise InitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")


def _write_env(env_file: Path, vault_path: Path) -> None:
    """Set VAULT_PATH in ``env_file``, preserving any other keys."""
    values = parse_env_file(env_file)
    values["VAULT_PATH"] = str(vault_path.resolve())
    lines = [f"{key}={value}" for key, value in values.items()]
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def init_vault(dest: Path, env_file: Path = DEFAULT_ENV_FILE) -> Path:
    """Create a new vault at ``dest`` from the template and register it."""
    if dest.exists() and any(dest.iterdir()):
        raise InitError(f"{dest} already exists and is not empty; refusing to overwrite.")
    if not TEMPLATE_DIR.is_dir():
        raise InitError(f"vault template not found at {TEMPLATE_DIR}")

    shutil.copytree(TEMPLATE_DIR, dest, dirs_exist_ok=True)
    for folder in EMPTY_FOLDERS:
        (dest / folder).mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    for note in dest.rglob("*.md"):
        text = note.read_text(encoding="utf-8")
        note.write_text(text.replace("{{date}}", today), encoding="utf-8")

    _run_git(["init"], cwd=dest)
    _run_git(["add", "-A"], cwd=dest)
    _run_git(["commit", "-m", "chore: initial vault from Argus template"], cwd=dest)

    _write_env(env_file, dest)
    return dest


def needs_build(web_dir: Path) -> bool:
    """True when the Next.js production build is absent."""
    return not (web_dir / ".next" / "BUILD_ID").is_file()


def run_web(port: int, backend_port: int, force_build: bool) -> int:
    """Serve the production dashboard: uvicorn + `next start` side by side."""
    npm = shutil.which("npm")
    if npm is None:
        print("npm not found on PATH — install Node.js first", file=sys.stderr)
        return 1
    if force_build or needs_build(WEB_DIR):
        print("Building the dashboard (one-time; rerun with --build after UI changes)…")
        build = subprocess.run([npm, "run", "build"], cwd=WEB_DIR, check=False)
        if build.returncode != 0:
            return build.returncode
    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--port", str(backend_port)],
        cwd=WEB_DIR.parent,
    )
    frontend = subprocess.Popen(
        [npm, "run", "start", "--", "-p", str(port)], cwd=WEB_DIR
    )
    print(f"Argus running: http://localhost:{port} (backend :{backend_port}) — Ctrl-C to stop")
    try:
        while backend.poll() is None and frontend.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for proc in (backend, frontend):
            if proc.poll() is None:
                proc.terminate()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``argus`` console script."""
    parser = argparse.ArgumentParser(prog="argus", description="Argus second-brain CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create a new vault from the template")
    init_parser.add_argument("path", type=Path, help="destination folder for the new vault")
    init_parser.add_argument(
        "--env-file", type=Path, default=DEFAULT_ENV_FILE, help="env file to record VAULT_PATH in"
    )

    reindex_parser = subparsers.add_parser("reindex", help="rebuild the RAG index from scratch")
    reindex_parser.add_argument(
        "--env-file", type=Path, default=DEFAULT_ENV_FILE, help="env file with VAULT_PATH"
    )

    watch_parser = subparsers.add_parser("watch", help="watch the vault and keep the index fresh")
    watch_parser.add_argument(
        "--env-file", type=Path, default=DEFAULT_ENV_FILE, help="env file with VAULT_PATH"
    )

    connect_parser = subparsers.add_parser("connect", help="connect an external service")
    connect_parser.add_argument("service", choices=["gcal", "todoist"])
    connect_parser.add_argument("token", nargs="?", help="API token (todoist only)")

    doctor_parser = subparsers.add_parser("doctor", help="check that this install is healthy")
    doctor_parser.add_argument(
        "--env-file", type=Path, default=DEFAULT_ENV_FILE, help="env file with VAULT_PATH"
    )

    web_parser = subparsers.add_parser("web", help="serve the production dashboard")
    web_parser.add_argument("--port", type=int, default=3000)
    web_parser.add_argument("--backend-port", type=int, default=8000)
    web_parser.add_argument("--build", action="store_true", help="force a rebuild first")

    args = parser.parse_args(argv)

    if args.command == "init":
        try:
            created = init_vault(args.path, args.env_file)
        except InitError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"Vault created at {created.resolve()}")
        print(f"VAULT_PATH written to {args.env_file}")
        return 0

    if args.command in ("reindex", "watch"):
        from backend.config import Settings
        from backend.rag.index import VaultIndex

        settings = Settings.load(args.env_file)
        index = VaultIndex(settings.db_path.parent / "chroma")

        if args.command == "reindex":
            counts = index.reindex_all(settings.vault_path)
            print(f"Indexed {sum(counts.values())} chunks from {len(counts)} files.")
            return 0

        from backend.rag.watcher import watch_vault

        print(f"Watching {settings.vault_path} (Ctrl+C to stop)")
        watch_vault(
            settings.vault_path,
            index,
            on_update=lambda rel, count: print(f"  reindexed {rel} ({count} chunks)"),
        )
        return 0

    if args.command == "doctor":
        from backend.config import ConfigError, Settings
        from backend.doctor import run_checks

        settings = Settings.load(args.env_file)
        try:
            _ = settings.vault_path  # raises ConfigError when VAULT_PATH is unset
        except ConfigError as exc:
            print(f"FAIL vault — {exc}", file=sys.stderr)
            return 1
        checks = run_checks(settings)
        for check in checks:
            print(f"{check.status:<4} {check.name:<10} {check.detail}")
        failed = [check for check in checks if check.status == "FAIL"]
        print(f"\n{'unhealthy' if failed else 'healthy'}: {len(failed)} failure(s)")
        return 1 if failed else 0

    if args.command == "connect":
        if args.service == "gcal":
            from backend.connectors import gcal

            gcal.connect()
            print("Google Calendar connected — token stored in the OS keyring.")
        else:
            if not args.token:
                print("usage: argus connect todoist <api-token>", file=sys.stderr)
                return 1
            from backend.connectors import todoist

            todoist.connect(args.token)
            print("Todoist connected — token stored in the OS keyring.")
        return 0

    if args.command == "web":
        return run_web(args.port, args.backend_port, args.build)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
