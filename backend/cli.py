"""Argus command-line interface.

``argus init <path>`` creates a new vault from the bundled template, git-inits
it (groundwork for invariant I2: the writer commits the vault before every
apply), and records ``VAULT_PATH`` in ``.env``. ``reindex``/``watch`` maintain
the RAG index, ``relink`` backfills concept and source links onto notes Argus
wrote before it emitted any, ``connect`` stores connector credentials,
``doctor`` checks the install, ``web`` serves the dashboard, and ``mcp-server``
exposes the vault read-only to an external coding agent.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from backend.core.config import DEFAULT_ENV_FILE, parse_env_file
from backend.core.taxonomy import Taxonomy

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "vault-template"
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# vault-template/'s folders are physically named with Argus's historical PARA
# defaults. "courses" is deliberately absent here — see _ignore_sample_course,
# which excludes 15-Courses/ from the copy entirely rather than relocating it.
_DEFAULT_TAXONOMY_DIRS = {
    "inbox": "00-Inbox",
    "daily": "10-Daily",
    "projects": "20-Projects",
    "areas": "30-Areas",
    "people": "40-People",
    "reference": "50-Reference",
    "journal": "90-Meta",
    "private": "99-Private",
}


class InitError(RuntimeError):
    """Raised when a vault cannot be initialised at the requested path."""


def _ignore_sample_course(_src: str, names: list[str]) -> set[str]:
    """Skip ``vault-template/15-Courses/`` entirely during ``argus init``'s copy.

    That folder exists in the repo only to hold ``CS000/course.md``, the
    reference ``CoursesPanel.renderCourseTemplate`` (web/) is documented to
    mirror — it stays there, untouched, as that reference. Shipping it into
    every fresh vault was the "sample course never goes away" bug: nothing in
    the backend could delete course data at all before
    ``backend/features/study/deletes.py``, so the sample was permanent.
    ``Taxonomy.seed_folders()`` still creates an empty, correctly-named
    courses dir for every taxonomy, sample-free.
    """
    return {"15-Courses"} if "15-Courses" in names else set()


def _relocate_templated_dirs(dest: Path, taxonomy: Taxonomy) -> None:
    """Rename copied template folders onto a taxonomy configured before init.

    vault-template/'s folders are physically named with Argus's historical
    hardcoded defaults (today, only ``30-Areas/assistant-preferences.md`` has
    real content). A user who sets ``VAULT_AREAS_DIR`` etc. in ``.env``
    *before* ever running ``argus init`` would otherwise get that content
    sitting in a folder name their configured taxonomy doesn't recognise —
    invisible to every feature that reads through the taxonomy from then on.
    """
    for field_name, default_name in _DEFAULT_TAXONOMY_DIRS.items():
        configured = getattr(taxonomy, field_name)
        if configured == default_name:
            continue
        source = dest / default_name
        if not source.is_dir():
            continue
        target = dest / configured
        if target.exists():
            continue  # never clobber something already there
        shutil.move(str(source), str(target))


def _run_git(args: list[str], cwd: Path) -> None:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise InitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")


def _ensure_git_identity(cwd: Path) -> None:
    """Give the new repo a committer identity if the machine has none.

    ``git commit`` hard-fails with "Author identity unknown" when neither
    user.name nor user.email is set, which is the default state for anyone who
    installed Git for Windows without configuring it -- i.e. most people who
    are not developers. Without this, creating a vault fails during onboarding.

    Only ever writes repo-local config, and only when git cannot already
    resolve an identity, so an existing global setting is left alone and
    authorship stays correct for people who have one.
    """
    probe = subprocess.run(
        ["git", "var", "GIT_AUTHOR_IDENT"], cwd=cwd, capture_output=True, text=True, check=False
    )
    if probe.returncode == 0:
        return
    _run_git(["config", "user.name", "Argus"], cwd=cwd)
    _run_git(["config", "user.email", "argus@localhost"], cwd=cwd)


def _write_env(env_file: Path, vault_path: Path) -> None:
    """Set VAULT_PATH in ``env_file``, preserving any other keys."""
    values = parse_env_file(env_file)
    values["VAULT_PATH"] = str(vault_path.resolve())
    lines = [f"{key}={value}" for key, value in values.items()]
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def init_vault(dest: Path, env_file: Path = DEFAULT_ENV_FILE) -> Path:
    """Create a new vault at ``dest`` from the template and register it.

    Seed folders come from ``env_file``'s taxonomy, if it already has one
    (e.g. someone set ``VAULT_INBOX_DIR`` etc. before running ``argus init``)
    — otherwise the defaults, identical to every pre-taxonomy vault.
    """
    if dest.exists() and any(dest.iterdir()):
        raise InitError(f"{dest} already exists and is not empty; refusing to overwrite.")
    if not TEMPLATE_DIR.is_dir():
        raise InitError(f"vault template not found at {TEMPLATE_DIR}")

    taxonomy = Taxonomy.from_env(parse_env_file(env_file))
    shutil.copytree(TEMPLATE_DIR, dest, dirs_exist_ok=True, ignore=_ignore_sample_course)
    _relocate_templated_dirs(dest, taxonomy)
    for folder in taxonomy.seed_folders():
        (dest / folder).mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    for note in dest.rglob("*.md"):
        text = note.read_text(encoding="utf-8")
        note.write_text(text.replace("{{date}}", today), encoding="utf-8")

    _run_git(["init"], cwd=dest)
    _ensure_git_identity(dest)
    _run_git(["add", "-A"], cwd=dest)
    _run_git(["commit", "-m", "chore: initial vault from Argus template"], cwd=dest)

    _write_env(env_file, dest)
    return dest


def needs_build(web_dir: Path) -> bool:
    """True when the Next.js production build is absent."""
    return not (web_dir / ".next" / "BUILD_ID").is_file()


def _stop(proc: subprocess.Popen) -> None:
    """Stop a launched server, including its children (npm.cmd wraps node.exe)."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False
        )
    else:
        proc.terminate()


def run_web(port: int, backend_port: int, force_build: bool) -> int:
    """Serve the production dashboard: uvicorn + `next start` side by side."""
    npm = shutil.which("npm")
    if npm is None:
        print("npm not found on PATH — install Node.js first", file=sys.stderr)
        return 1
    if force_build or needs_build(WEB_DIR):
        print("Building the dashboard (one-time; rerun with --build after UI changes)…")
        try:
            build = subprocess.run([npm, "run", "build"], cwd=WEB_DIR, check=False)
            if build.returncode != 0:
                return build.returncode
        except KeyboardInterrupt:
            return 130
    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--port", str(backend_port)],
        cwd=WEB_DIR.parent,
    )
    frontend = subprocess.Popen([npm, "run", "start", "--", "-p", str(port)], cwd=WEB_DIR)
    print(f"Argus running: http://localhost:{port} (backend :{backend_port}) — Ctrl-C to stop")
    try:
        while backend.poll() is None and frontend.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        _stop(backend)
        _stop(frontend)
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

    relink_parser = subparsers.add_parser(
        "relink", help="re-derive concept and source links for every note Argus wrote"
    )
    relink_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing anything",
    )
    relink_parser.add_argument(
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

    mcp_parser = subparsers.add_parser(
        "mcp-server", help="expose your vault to a coding agent over MCP (read-only)"
    )
    mcp_parser.add_argument(
        "--env-file", type=Path, default=DEFAULT_ENV_FILE, help="env file with VAULT_PATH"
    )

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
        from backend.core.config import Settings
        from backend.rag.index import VaultIndex

        settings = Settings.load(args.env_file)
        index = VaultIndex(settings.db_path.parent / "chroma", taxonomy=settings.taxonomy)

        if args.command == "reindex":
            result = index.reindex_all(settings.vault_path)
            print(f"Indexed {result.total_chunks} chunks from {result.files} files.")
            if result.errors:
                print(f"{len(result.errors)} file(s) failed:", file=sys.stderr)
                for rel_path, message in result.errors.items():
                    print(f"  {rel_path}: {message}", file=sys.stderr)
            return 1 if result.errors else 0

        from backend.rag.watcher import watch_vault

        print(f"Watching {settings.vault_path} (Ctrl+C to stop)")
        watch_vault(
            settings.vault_path,
            index,
            on_update=lambda rel, count: print(f"  reindexed {rel} ({count} chunks)"),
            taxonomy=settings.taxonomy,
        )
        return 0

    if args.command == "relink":
        from backend.core.config import Settings
        from backend.core.db import connect, init_schema
        from backend.features.ingest import store
        from backend.features.notes.relink import relinkable_notes, run_relink_job
        from backend.rag.index import make_index_factory

        settings = Settings.load(args.env_file)
        paths = relinkable_notes(settings.vault_path, taxonomy=settings.taxonomy)
        if not paths:
            print("Nothing to relink — no note carries `generated_by: argus`.")
            return 0
        print(f"{'Would relink' if args.dry_run else 'Relinking'} {len(paths)} note(s)…")
        # Recorded on the same job store the app's own relink uses, so a run
        # started from a terminal still shows up in the history panel and
        # still holds the index slot against a concurrent ingest.
        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            job_id = store.create_job(conn, target="", filenames=[], kind="relink")
        finally:
            conn.close()
        # Built the way backend/main.py builds it, not the way `reindex`
        # above does: `run_relink_job` takes a zero-arg factory so the ~7s
        # model load stays lazy and one instance serves every note.
        run_relink_job(
            job_id,
            settings=settings,
            index_factory=make_index_factory(
                settings.db_path.parent / "chroma", taxonomy=settings.taxonomy
            ),
            dry_run=args.dry_run,
        )
        conn = connect(settings.db_path)
        try:
            job = store.get_job(conn, job_id) or {}
        finally:
            conn.close()
        rewritten = len([item for item in job.get("items", []) if item["stage"] == "done"])
        if args.dry_run:
            print(f"Dry run complete — {rewritten} of {len(paths)} would change; nothing written.")
        else:
            print(f"Relinked {rewritten} note(s); {len(paths) - rewritten} already current.")
        if job.get("error"):
            print(job["error"], file=sys.stderr)
        return 1 if job.get("status") in ("failed", "partial") else 0

    if args.command == "doctor":
        from backend.core.config import ConfigError, Settings
        from backend.features.system.doctor import run_checks

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

    if args.command == "mcp-server":
        import asyncio

        from backend.agent.mcp_server import serve_stdio
        from backend.core.config import ConfigError, Settings

        settings = Settings.load(args.env_file)
        try:
            _ = settings.vault_path  # raises ConfigError when VAULT_PATH is unset
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        # stdout is the MCP transport here, so every human-readable line must
        # go to stderr or it corrupts the protocol stream.
        print(f"Argus MCP server on stdio — vault {settings.vault_path}", file=sys.stderr)
        try:
            asyncio.run(serve_stdio(settings))
        except KeyboardInterrupt:
            return 130
        return 0

    if args.command == "web":
        return run_web(args.port, args.backend_port, args.build)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
