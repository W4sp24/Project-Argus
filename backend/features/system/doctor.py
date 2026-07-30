"""`argus doctor` — is this installation healthy?

Each check reports OK (working), WARN (degraded but usable — e.g. a
connector waiting on credentials), or FAIL (something the user must fix).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from backend.connectors import client_library_importable
from backend.core.config import Settings

REQUIRED_TABLES = {
    "suggestions",
    "tasks_cache",
    "exams",
    "attempts",
    "audit",
    "token_usage",
    "cli_usage",
    "cli_usage_files",
}


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
        from backend.core.db import connect, init_schema

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


def _check_config_files(settings: Settings) -> Check:
    """Did any registry fail to parse and get quarantined?

    :mod:`backend.core.jsonstore` renames an unreadable ``models.json`` (or any
    sibling registry) to ``*.corrupt`` rather than letting the next save bury
    it. That recovers the bytes, but only this check tells the user it happened
    — otherwise their models simply appear to have vanished.
    """
    from backend.core.jsonstore import corrupt_files

    quarantined = corrupt_files(settings.db_path.parent)
    if not quarantined:
        return Check(name="config-files", status="OK", detail="registries parse cleanly")
    names = ", ".join(path.name for path in quarantined)
    return Check(
        name="config-files",
        status="WARN",
        detail=(
            f"unreadable and set aside: {names} (in {settings.db_path.parent}). "
            "Anything registered there was not loaded — open the file to recover "
            "it, or re-add the entries and delete it."
        ),
    )


def _check_keyring() -> Check:
    try:
        import keyring

        probe = f"probe-{uuid.uuid4().hex[:8]}"
        keyring.set_password("argus-doctor", probe, "ok")
        value = keyring.get_password("argus-doctor", probe)
        keyring.delete_password("argus-doctor", probe)
        if value != "ok":
            return Check(name="keyring", status="WARN", detail="probe read back wrong value")
        return Check(name="keyring", status="OK", detail="OS keyring stores secrets (I4)")
    except Exception as exc:
        return Check(name="keyring", status="WARN", detail=f"keyring unusable: {exc}")


def _check_ollama() -> Check:
    """Is a local model server reachable?

    WARN, never FAIL: Ollama is one of several ways to run Argus (hosted APIs
    and Claude Code are the others), so its absence is a missing option, not a
    broken install — same reasoning as the gcal/todoist connector checks.
    """
    from backend.agent.hardware import ollama_available, ollama_base_url, ollama_reachable

    url = ollama_base_url()
    try:
        running = ollama_reachable()
    except Exception as exc:  # noqa: BLE001 - a failed probe is not a failed install
        return Check(name="ollama", status="WARN", detail=f"could not probe {url}: {exc}")
    if running:
        return Check(name="ollama", status="OK", detail=f"local models available at {url}")
    if ollama_available():
        return Check(
            name="ollama",
            status="WARN",
            detail=f"installed but not running — start Ollama to use local models ({url})",
        )
    return Check(
        name="ollama",
        status="WARN",
        detail="not installed — optional, only needed to run models on this PC",
    )


def _check_connector(name: str) -> Check:
    from backend.agent.credentials import KEY_PRESENT, KEY_UNKNOWN

    try:
        if name == "gcal":
            from backend.connectors import gcal as connector

            hint = "create OAuth credentials.json, then `argus connect gcal`"
            client_module = "google_auth_oauthlib.flow"
            missing_detail = "this build shipped without Google Calendar support — update Argus"
        else:
            from backend.connectors import todoist as connector

            hint = "`argus connect todoist <api-token>`"
            client_module = "todoist_api_python.api"
            missing_detail = "this build shipped without Todoist support — update Argus"
        state = connector.connection_state()
        if state == KEY_PRESENT:
            return Check(name=name, status="OK", detail="connected")
        if state == KEY_UNKNOWN:
            # Distinct from "not connected" on purpose: re-running the connect
            # flow will not fix a keyring that cannot be read.
            return Check(
                name=name,
                status="WARN",
                detail="cannot tell — the OS keyring is unreadable; see the keyring check",
            )
        # No token stored. That is normally just an unconfigured connector
        # (WARN — the user hasn't run `connect` yet), but if the client
        # library itself is not importable, no amount of running `connect`
        # will fix it: the build was frozen without the dependency (see
        # release.yml's `pip install -e ".[rag,gcal]"` and the spec's
        # required_metadata() section). That is a broken build, not a user
        # configuration choice, so it is FAIL, not WARN.
        if not client_library_importable(client_module):
            return Check(name=name, status="FAIL", detail=missing_detail)
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
        _check_config_files(settings)
        if vault_ok
        else Check(name="config-files", status="WARN", detail="skipped — vault missing"),
        _check_keyring(),
        _check_ollama(),
        _check_connector("gcal"),
        _check_connector("todoist"),
    ]
