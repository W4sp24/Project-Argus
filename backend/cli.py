"""FRIDAY command-line interface.

Currently one command: ``friday init <path>`` — create a new vault from the
bundled template, git-init it (groundwork for invariant I2: the writer commits
the vault before every apply), and record ``VAULT_PATH`` in ``.env``.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from backend.config import DEFAULT_ENV_FILE, parse_env_file

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "vault-template"
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
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
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
    _run_git(["commit", "-m", "chore: initial vault from FRIDAY template"], cwd=dest)

    _write_env(env_file, dest)
    return dest


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``friday`` console script."""
    parser = argparse.ArgumentParser(prog="friday", description="FRIDAY second-brain CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create a new vault from the template")
    init_parser.add_argument("path", type=Path, help="destination folder for the new vault")
    init_parser.add_argument(
        "--env-file", type=Path, default=DEFAULT_ENV_FILE, help="env file to record VAULT_PATH in"
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
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
