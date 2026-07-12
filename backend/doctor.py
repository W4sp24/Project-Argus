"""`friday doctor` — is this installation healthy?

Each check reports OK (working), WARN (degraded but usable — e.g. a
connector waiting on credentials), or FAIL (something the user must fix).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from backend.config import Settings

REQUIRED_TABLES = {"suggestions", "tasks_cache", "exams", "attempts", "audit"}


class Check(BaseModel):
    name: str
    status: str  # OK | WARN | FAIL
    detail: str


def _check_vault(settings: Settings) -> list[Check]:
    vault = settings.vault_path
    if not vault.is_dir():
        return [
            Check(name="vault", status="FAIL", detail=f"{vault} does not exist"),
            Check(name="vault-git", status="FAIL", detail="no vault, no git repo"),
        ]
    checks = [Check(name="vault", status="OK", detail=str(vault))]
    if (vault / ".git").is_dir():
        checks.append(Check(name="vault-git", status="OK", detail="pre-apply snapshots ready (I2)"))
    else:
        checks.append(
            Check(
                name="vault-git",
                status="FAIL",
                detail="vault is not a git repository — run `git init` there (I2)",
            )
        )
    return checks


def _check_database(settings: Settings) -> Check:
    try:
        from backend.db import connect, init_schema

        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            tables = {
                row["name"]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            conn.close()
        missing = REQUIRED_TABLES - tables
        if missing:
            return Check(name="database", status="FAIL", detail=f"missing tables: {missing}")
        return Check(name="database", status="OK", detail=str(settings.db_path))
    except Exception as exc:
        return Check(name="database", status="FAIL", detail=str(exc))


def _check_chroma(settings: Settings) -> Check:
    try:
        import chromadb  # noqa: F401
    except ImportError:
        return Check(
            name="chroma",
            status="WARN",
            detail="chromadb not installed — `pip install -e .[rag]` enables chat/RAG",
        )
    chroma_dir = settings.db_path.parent / "chroma"
    return Check(
        name="chroma",
        status="OK",
        detail=f"{chroma_dir} ({'exists' if chroma_dir.is_dir() else 'created on first reindex'})",
    )


def _check_keyring() -> Check:
    try:
        import keyring

        probe = f"probe-{uuid.uuid4().hex[:8]}"
        keyring.set_password("friday-doctor", probe, "ok")
        value = keyring.get_password("friday-doctor", probe)
        keyring.delete_password("friday-doctor", probe)
        if value != "ok":
            return Check(name="keyring", status="WARN", detail="probe read back wrong value")
        return Check(name="keyring", status="OK", detail="OS keyring stores secrets (I4)")
    except Exception as exc:
        return Check(name="keyring", status="WARN", detail=f"keyring unusable: {exc}")


def _check_connector(name: str) -> Check:
    try:
        if name == "gcal":
            from backend.connectors import gcal as connector

            hint = "create OAuth credentials.json, then `friday connect gcal`"
        else:
            from backend.connectors import todoist as connector

            hint = "`friday connect todoist <api-token>`"
        if connector.configured():
            return Check(name=name, status="OK", detail="connected")
        return Check(name=name, status="WARN", detail=f"not connected — {hint}")
    except Exception as exc:
        return Check(name=name, status="WARN", detail=str(exc))


def run_checks(settings: Settings) -> list[Check]:
    """All health checks, in display order. Never creates files for a broken vault."""
    vault_checks = _check_vault(settings)
    vault_ok = vault_checks[0].status == "OK"
    return [
        *vault_checks,
        _check_database(settings)
        if vault_ok
        else Check(name="database", status="FAIL", detail="skipped — vault missing"),
        _check_chroma(settings)
        if vault_ok
        else Check(name="chroma", status="WARN", detail="skipped — vault missing"),
        _check_keyring(),
        _check_connector("gcal"),
        _check_connector("todoist"),
    ]
