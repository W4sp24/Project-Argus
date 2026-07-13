# Argus P5 — Dashboard-First Revamp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the approved P5 spec (`docs/superpowers/specs/2026-07-13-dashboard-revamp-design.md`): a smooth production-mode app whose home is a full dashboard (Layout A) with heatmap, stats, interactive agenda, and a docked mini chat, plus direct vault edit/delete through the single writer, and a reviewed vault cleanup.

**Architecture:** Extend the existing Next.js 14 App-Router frontend (`web/`) and FastAPI backend (`backend/`). All new vault mutations are functions in `backend/writer.py` (invariant I1) with git pre-apply snapshots (I2), exposed via a new thin router `backend/notes_api.py`. Heatmap/activity are new read-only aggregations in `backend/insights.py`/`backend/activity.py`. The chat WebSocket client moves into a React context provider so a dock and the Chat tab share one conversation.

**Tech Stack:** Python 3.12 + FastAPI + pytest, Next.js 14 + React 18 + SWR + Tailwind, Playwright e2e.

## Global Constraints

- Branch: all work on `feat/p5-dashboard-revamp`. Conventional commits. Commit only when the verification step of the current task passes.
- Invariants (verbatim from project docs): **I1** single writer `backend/writer.py`; **I2** git pre-apply snapshot before every vault write; **I3** `99-Private/` + `#no-ai` never indexed/sent; **I5** subscription auth, never set ANTHROPIC_API_KEY; **I6** citations on every RAG answer; **D1** `90-Meta/` is dev-owned, the app never writes it.
- Server-side path refusals for user CRUD: `99-Private/`, `90-Meta/`, and everything in `EXCLUDED_TOP_DIRS` (`backend/rag/paths.py`).
- Python tests: `.venv/Scripts/python.exe -m pytest` from repo root (Windows venv). Lint: `.venv/Scripts/python.exe -m ruff check .`.
- Web: `npm run lint` and `npm run build` from `web/` must pass before any commit that touches `web/`.
- No `gh` CLI on this machine — the PR is opened via the GitHub compare URL (Task 16).
- **Session logging (user requirement):** after completing each task, append a one-line milestone to `90-Meta/sessions/2026/2026-07-13-project-argus.md` in the Scientia vault — via the Obsidian MCP `obsidian_append_content` tool if reachable, else direct file append (D1: dev-owned).
- The real vault `C:\Users\ethan\Documents\Scientia` is NEVER used in tests. Tests use `tmp_path` vaults; e2e uses the throwaway vault from `web/e2e/start-backend.mjs`.

## File Structure

```
backend/
  writer.py            MODIFY  guard + user CRUD ops (Tasks 1–2)
  notes_api.py         CREATE  /api/note + /api/tasks/* CRUD router (Task 3)
  insights.py          MODIFY  heatmap aggregation (Task 4)
  activity.py          CREATE  recent-activity aggregation (Task 5)
  briefing_api.py      MODIFY  /api/insights/heatmap + /api/activity (Tasks 4–5)
  main.py              MODIFY  include notes_api router (Task 3)
  cli.py               MODIFY  `argus web` production launcher (Task 6)
tests/
  test_writer.py       MODIFY  user CRUD op tests (Tasks 1–2)
  test_notes_api.py    CREATE  API tests (Task 3)
  test_insights.py     MODIFY  heatmap tests (Task 4)
  test_activity.py     CREATE  activity tests (Task 5)
  test_cli.py          MODIFY  `argus web` helper tests (Task 6)
web/
  app/page.tsx                       MODIFY  redirect → /dashboard (Task 7)
  app/(dashboard)/dashboard/page.tsx CREATE  Layout-A dashboard (Task 7)
  app/(dashboard)/today/             DELETE  (Task 7)
  app/(dashboard)/layout.tsx         MODIFY  ChatProvider + floating dock (Task 12)
  app/(dashboard)/chat/page.tsx      MODIFY  reuse ChatPanel (Task 12)
  app/(dashboard)/insights/page.tsx  MODIFY  add full-width heatmap (Task 9)
  components/Sidebar.tsx             MODIFY  Today → Dashboard nav (Task 7)
  components/dashboard/BriefingCard.tsx CREATE (Task 7)
  components/dashboard/CaptureCard.tsx  CREATE (Task 7)
  components/dashboard/StatTiles.tsx    CREATE (Task 8)
  components/dashboard/Heatmap.tsx      CREATE (Task 9)
  components/dashboard/AgendaCard.tsx   CREATE (Task 10)
  components/dashboard/ActivityFeed.tsx CREATE (Task 11)
  components/chat/ChatPanel.tsx         CREATE (Task 12)
  components/chat/ChatDock.tsx          CREATE (Task 12)
  lib/api.ts                         MODIFY  new hooks + mutation helper (Tasks 8–11)
  lib/chat.tsx                       CREATE  ChatProvider/useChat (Task 12)
  app/globals.css                    MODIFY  msg-in animation (Task 12)
  scripts/check-bundles.mjs          CREATE  perf budget (Task 13)
  e2e/start-backend.mjs              MODIFY  seed today-due task (Task 14)
  e2e/roundtrip.spec.ts              MODIFY  /today → /dashboard (Task 7)
  e2e/dashboard.spec.ts              CREATE  widget + CRUD e2e (Task 14)
```

---

### Task 1: Writer — user path guard + task-line operations

**Files:**
- Modify: `backend/writer.py`
- Test: `tests/test_writer.py`

**Interfaces:**
- Consumes: existing `_git_snapshot(vault_path, reason)`, `_argus_log(vault_path, line)`, `WriterError`, `EXCLUDED_TOP_DIRS` from `backend.rag.paths`.
- Produces (used by Task 3):
  - `class WriterForbidden(WriterError)`, `class WriterMissing(WriterError)`, `class WriterConflict(WriterError)`
  - `guard_user_path(vault_path: Path, rel_path: str) -> Path` (raises `WriterForbidden`)
  - `toggle_task_line(vault_path: Path, rel_path: str, line_no: int, old_line: str) -> str` — returns the new line text
  - `update_task_line(vault_path: Path, rel_path: str, line_no: int, old_line: str, new_line: str) -> str`
  - `delete_task_line(vault_path: Path, rel_path: str, line_no: int, old_line: str) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_writer.py` (it already has vault fixtures using git-inited `tmp_path` vaults — reuse the existing fixture if one exists; otherwise this local helper):

```python
import subprocess
from pathlib import Path

import pytest

from backend import writer
from backend.writer import (
    WriterConflict,
    WriterForbidden,
    WriterMissing,
    guard_user_path,
)


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    subprocess.run(["git", "init"], cwd=vault, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=vault, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=vault, capture_output=True)
    return vault


def test_guard_rejects_private_meta_and_traversal(tmp_path):
    vault = _make_vault(tmp_path)
    for bad in ("99-Private/x.md", "90-Meta/sessions/x.md", "../escape.md", "C:/abs.md"):
        with pytest.raises(WriterForbidden):
            guard_user_path(vault, bad)


def test_toggle_task_line_checks_and_stamps_done_date(tmp_path):
    vault = _make_vault(tmp_path)
    note = vault / "20-Projects" / "p.md"
    note.parent.mkdir()
    note.write_text("# P\n\n- [ ] ship it 📅 2026-07-20\n", encoding="utf-8")
    new_line = writer.toggle_task_line(vault, "20-Projects/p.md", 3, "- [ ] ship it 📅 2026-07-20")
    assert new_line.startswith("- [x] ship it")
    assert "✅" in new_line
    assert new_line in note.read_text(encoding="utf-8")
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True
    ).stdout
    assert "argus: pre-apply snapshot" in log


def test_toggle_task_line_unchecks_and_strips_done_date(tmp_path):
    vault = _make_vault(tmp_path)
    note = vault / "20-Projects" / "p.md"
    note.parent.mkdir()
    note.write_text("- [x] done thing ✅ 2026-07-12\n", encoding="utf-8")
    new_line = writer.toggle_task_line(vault, "20-Projects/p.md", 1, "- [x] done thing ✅ 2026-07-12")
    assert new_line == "- [ ] done thing"
    assert "✅" not in note.read_text(encoding="utf-8")


def test_task_line_drift_raises_conflict_and_leaves_file(tmp_path):
    vault = _make_vault(tmp_path)
    note = vault / "20-Projects" / "p.md"
    note.parent.mkdir()
    note.write_text("- [ ] real line\n", encoding="utf-8")
    with pytest.raises(WriterConflict):
        writer.update_task_line(vault, "20-Projects/p.md", 1, "- [ ] stale line", "- [ ] new")
    assert note.read_text(encoding="utf-8") == "- [ ] real line\n"


def test_delete_task_line_removes_line(tmp_path):
    vault = _make_vault(tmp_path)
    note = vault / "20-Projects" / "p.md"
    note.parent.mkdir()
    note.write_text("- [ ] keep\n- [ ] drop\n", encoding="utf-8")
    writer.delete_task_line(vault, "20-Projects/p.md", 2, "- [ ] drop")
    assert note.read_text(encoding="utf-8") == "- [ ] keep\n"


def test_task_ops_on_missing_file_raise_missing(tmp_path):
    vault = _make_vault(tmp_path)
    with pytest.raises(WriterMissing):
        writer.toggle_task_line(vault, "20-Projects/nope.md", 1, "- [ ] x")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_writer.py -v -k "guard or toggle or drift or delete_task or missing"`
Expected: FAIL / ERROR with `ImportError: cannot import name 'WriterConflict'`.

- [ ] **Step 3: Implement in `backend/writer.py`**

Add below the `WriterError` class:

```python
class WriterForbidden(WriterError):
    """Write refused: path is outside the user-editable zones (I3/D1)."""


class WriterMissing(WriterError):
    """Write refused: target file does not exist."""


class WriterConflict(WriterError):
    """Write refused: content drifted since the client read it."""
```

Add after `_git_snapshot` (import `EXCLUDED_TOP_DIRS` at top: `from backend.rag.paths import EXCLUDED_TOP_DIRS`, plus `import re` if not present):

```python
def guard_user_path(vault_path: Path, rel_path: str) -> Path:
    """Resolve a vault-relative path for user CRUD; refuse protected zones."""
    candidate = Path(rel_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise WriterForbidden(f"path {rel_path!r} is not vault-relative")
    if candidate.parts and candidate.parts[0] in EXCLUDED_TOP_DIRS:
        raise WriterForbidden(f"{candidate.parts[0]}/ is protected and cannot be edited")
    resolved = (vault_path / candidate).resolve()
    if vault_path.resolve() not in resolved.parents:
        raise WriterForbidden(f"path {rel_path!r} escapes the vault")
    return resolved


def _checked_line(note: Path, rel_path: str, line_no: int, old_line: str) -> list[str]:
    if not note.is_file():
        raise WriterMissing(f"{rel_path} does not exist")
    lines = note.read_text(encoding="utf-8").splitlines()
    if line_no < 1 or line_no > len(lines) or lines[line_no - 1].strip() != old_line.strip():
        raise WriterConflict(f"{rel_path}:{line_no} has changed since you loaded it — refresh")
    return lines


DONE_STAMP_RE = re.compile(r"\s*✅\s*\d{4}-\d{2}-\d{2}")


def toggle_task_line(vault_path: Path, rel_path: str, line_no: int, old_line: str) -> str:
    """Check/uncheck one task checkbox; stamps/strips the ✅ done date."""
    note = guard_user_path(vault_path, rel_path)
    lines = _checked_line(note, rel_path, line_no, old_line)
    line = lines[line_no - 1]
    if "[ ]" in line:
        new_line = line.replace("[ ]", "[x]", 1).rstrip() + f" ✅ {date.today().isoformat()}"
    elif "[x]" in line or "[X]" in line:
        new_line = DONE_STAMP_RE.sub("", line.replace("[X]", "[x]").replace("[x]", "[ ]", 1)).rstrip()
    else:
        raise WriterConflict(f"{rel_path}:{line_no} is not a checkbox task")
    _git_snapshot(vault_path, f"toggle task {rel_path}:{line_no}")
    lines[line_no - 1] = new_line
    note.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _argus_log(vault_path, f"toggled task in {rel_path}:{line_no}")
    return new_line


def update_task_line(
    vault_path: Path, rel_path: str, line_no: int, old_line: str, new_line: str
) -> str:
    """Replace one task line verbatim (user-initiated edit)."""
    note = guard_user_path(vault_path, rel_path)
    lines = _checked_line(note, rel_path, line_no, old_line)
    _git_snapshot(vault_path, f"edit task {rel_path}:{line_no}")
    lines[line_no - 1] = new_line
    note.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _argus_log(vault_path, f"edited task in {rel_path}:{line_no}")
    return new_line


def delete_task_line(vault_path: Path, rel_path: str, line_no: int, old_line: str) -> None:
    """Delete one task line (user-initiated)."""
    note = guard_user_path(vault_path, rel_path)
    lines = _checked_line(note, rel_path, line_no, old_line)
    _git_snapshot(vault_path, f"delete task {rel_path}:{line_no}")
    del lines[line_no - 1]
    note.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    _argus_log(vault_path, f"deleted task in {rel_path}:{line_no}")
```

Note the snapshot ordering: **drift check first, snapshot second, write third** — a rejected write must not leave a stray snapshot commit? No: the existing `apply_suggestion` snapshots before validation. Match the new code to the order shown above (validate → snapshot → write), and keep `test_task_line_drift_raises_conflict_and_leaves_file` asserting the file is untouched.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_writer.py -v`
Expected: all PASS (old and new).

- [ ] **Step 5: Lint + full suite, then commit**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m pytest -q
git add backend/writer.py tests/test_writer.py
git commit -m "feat(writer): user path guard + toggle/update/delete task line"
```

---

### Task 2: Writer — note update/delete (compare-and-swap)

**Files:**
- Modify: `backend/writer.py`
- Test: `tests/test_writer.py`

**Interfaces:**
- Consumes: `guard_user_path`, `_git_snapshot`, `_argus_log`, exceptions from Task 1.
- Produces (used by Task 3):
  - `update_note(vault_path: Path, rel_path: str, expected_content: str, new_content: str) -> None` — whole-file compare-and-swap; `WriterConflict` if the file no longer equals `expected_content`.
  - `delete_note(vault_path: Path, rel_path: str) -> None`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_writer.py`)

```python
def test_update_note_cas_applies_and_logs(tmp_path):
    vault = _make_vault(tmp_path)
    note = vault / "00-Inbox" / "n.md"
    note.parent.mkdir()
    note.write_text("old body\n", encoding="utf-8")
    writer.update_note(vault, "00-Inbox/n.md", "old body\n", "new body\n")
    assert note.read_text(encoding="utf-8") == "new body\n"
    daily = vault / "10-Daily"
    assert any("## Argus log" in p.read_text(encoding="utf-8") for p in daily.glob("*.md"))


def test_update_note_conflict_on_drift(tmp_path):
    vault = _make_vault(tmp_path)
    note = vault / "00-Inbox" / "n.md"
    note.parent.mkdir()
    note.write_text("actual\n", encoding="utf-8")
    with pytest.raises(WriterConflict):
        writer.update_note(vault, "00-Inbox/n.md", "what the client saw\n", "new\n")
    assert note.read_text(encoding="utf-8") == "actual\n"


def test_delete_note_removes_file_after_snapshot(tmp_path):
    vault = _make_vault(tmp_path)
    note = vault / "00-Inbox" / "n.md"
    note.parent.mkdir()
    note.write_text("bye\n", encoding="utf-8")
    writer.delete_note(vault, "00-Inbox/n.md")
    assert not note.exists()
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True
    ).stdout
    assert "argus: pre-apply snapshot (delete note 00-Inbox/n.md)" in log


def test_delete_note_refuses_protected_and_missing(tmp_path):
    vault = _make_vault(tmp_path)
    with pytest.raises(WriterForbidden):
        writer.delete_note(vault, "99-Private/secret.md")
    with pytest.raises(WriterMissing):
        writer.delete_note(vault, "00-Inbox/ghost.md")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_writer.py -v -k note`
Expected: FAIL with `AttributeError: module 'backend.writer' has no attribute 'update_note'`.

- [ ] **Step 3: Implement in `backend/writer.py`**

```python
def update_note(vault_path: Path, rel_path: str, expected_content: str, new_content: str) -> None:
    """Replace a note's full content iff it still matches what the client read."""
    note = guard_user_path(vault_path, rel_path)
    if not note.is_file():
        raise WriterMissing(f"{rel_path} does not exist")
    current = note.read_text(encoding="utf-8")
    if current != expected_content:
        raise WriterConflict(f"{rel_path} has changed since you loaded it — refresh")
    _git_snapshot(vault_path, f"edit note {rel_path}")
    note.write_text(new_content, encoding="utf-8")
    _argus_log(vault_path, f"edited note {rel_path}")


def delete_note(vault_path: Path, rel_path: str) -> None:
    """Delete one note (user-initiated); the pre-apply snapshot is the undo."""
    note = guard_user_path(vault_path, rel_path)
    if not note.is_file():
        raise WriterMissing(f"{rel_path} does not exist")
    _git_snapshot(vault_path, f"delete note {rel_path}")
    note.unlink()
    _argus_log(vault_path, f"deleted note {rel_path}")
```

- [ ] **Step 4: Verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_writer.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint + full suite, commit**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m pytest -q
git add backend/writer.py tests/test_writer.py
git commit -m "feat(writer): direct note update/delete with CAS drift check"
```

---

### Task 3: REST API — note + task-line CRUD endpoints

**Files:**
- Create: `backend/notes_api.py`
- Modify: `backend/main.py` (include router)
- Test: `tests/test_notes_api.py`

**Interfaces:**
- Consumes: Task 1–2 writer functions and exceptions; `Settings` (has `.vault_path`); `guard_user_path`.
- Produces (used by Tasks 10, 14 — exact wire shapes):
  - `GET /api/note?path=<rel>` → `{"path": str, "content": str}`; 403 forbidden, 404 missing
  - `PUT /api/note` body `{"path", "expected_content", "new_content"}` → `{"path": str}`; 409 `{"detail": {"message": str, "current_content": str}}`
  - `DELETE /api/note?path=<rel>` → `{"path": str}`
  - `POST /api/tasks/toggle` body `{"path", "line", "old_line"}` → `{"new_line": str}`
  - `POST /api/tasks/line/update` body `{"path", "line", "old_line", "new_line"}` → `{"new_line": str}`
  - `POST /api/tasks/line/delete` body `{"path", "line", "old_line"}` → `{"ok": true}`
  - Error mapping everywhere: `WriterForbidden→403`, `WriterMissing→404`, `WriterConflict→409`, other `WriterError→422`.

- [ ] **Step 1: Write the failing tests** — `tests/test_notes_api.py`:

```python
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app


@pytest.fixture()
def client(tmp_path: Path) -> tuple[TestClient, Path]:
    vault = tmp_path / "vault"
    vault.mkdir()
    subprocess.run(["git", "init"], cwd=vault, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=vault, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=vault, capture_output=True)
    (vault / "00-Inbox").mkdir()
    (vault / "00-Inbox" / "note.md").write_text("hello\n", encoding="utf-8")
    (vault / "20-Projects").mkdir()
    (vault / "20-Projects" / "p.md").write_text("- [ ] task one 📅 2026-07-20\n", encoding="utf-8")
    settings = Settings(vault_path=vault, db_path=tmp_path / "argus.db")
    return TestClient(create_app(settings, chat_runner=lambda m: iter(()))), vault


def test_get_note_content(client):
    api, _ = client
    response = api.get("/api/note", params={"path": "00-Inbox/note.md"})
    assert response.status_code == 200
    assert response.json() == {"path": "00-Inbox/note.md", "content": "hello\n"}


def test_get_note_forbidden_and_missing(client):
    api, _ = client
    assert api.get("/api/note", params={"path": "99-Private/x.md"}).status_code == 403
    assert api.get("/api/note", params={"path": "00-Inbox/ghost.md"}).status_code == 404


def test_put_note_cas_and_conflict(client):
    api, vault = client
    ok = api.put(
        "/api/note",
        json={"path": "00-Inbox/note.md", "expected_content": "hello\n", "new_content": "hi\n"},
    )
    assert ok.status_code == 200
    assert (vault / "00-Inbox" / "note.md").read_text(encoding="utf-8") == "hi\n"
    stale = api.put(
        "/api/note",
        json={"path": "00-Inbox/note.md", "expected_content": "hello\n", "new_content": "x\n"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_content"] == "hi\n"


def test_delete_note(client):
    api, vault = client
    response = api.request("DELETE", "/api/note", params={"path": "00-Inbox/note.md"})
    assert response.status_code == 200
    assert not (vault / "00-Inbox" / "note.md").exists()


def test_toggle_update_delete_task_line(client):
    api, vault = client
    toggled = api.post(
        "/api/tasks/toggle",
        json={"path": "20-Projects/p.md", "line": 1, "old_line": "- [ ] task one 📅 2026-07-20"},
    )
    assert toggled.status_code == 200
    new_line = toggled.json()["new_line"]
    assert new_line.startswith("- [x] task one")

    edited = api.post(
        "/api/tasks/line/update",
        json={
            "path": "20-Projects/p.md",
            "line": 1,
            "old_line": new_line,
            "new_line": "- [ ] task one 📅 2026-07-25",
        },
    )
    assert edited.status_code == 200

    deleted = api.post(
        "/api/tasks/line/delete",
        json={"path": "20-Projects/p.md", "line": 1, "old_line": "- [ ] task one 📅 2026-07-25"},
    )
    assert deleted.status_code == 200
    assert (vault / "20-Projects" / "p.md").read_text(encoding="utf-8").strip() == ""


def test_task_line_conflict_is_409(client):
    api, _ = client
    response = api.post(
        "/api/tasks/toggle",
        json={"path": "20-Projects/p.md", "line": 1, "old_line": "- [ ] something stale"},
    )
    assert response.status_code == 409
```

Check `tests/test_api.py` for how `Settings` is constructed in existing tests and copy that construction style if it differs from `Settings(vault_path=..., db_path=...)`.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_notes_api.py -v`
Expected: FAIL — 404s (routes don't exist).

- [ ] **Step 3: Implement `backend/notes_api.py`**

```python
"""User-initiated vault CRUD: thin HTTP layer over the single writer (I1)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend import writer
from backend.config import Settings
from backend.writer import (
    WriterConflict,
    WriterError,
    WriterForbidden,
    WriterMissing,
    guard_user_path,
)


class NoteContent(BaseModel):
    path: str
    content: str


class NoteUpdate(BaseModel):
    path: str
    expected_content: str
    new_content: str


class TaskLineRef(BaseModel):
    path: str
    line: int
    old_line: str


class TaskLineUpdate(TaskLineRef):
    new_line: str


class NewLine(BaseModel):
    new_line: str


def _raise_http(exc: WriterError, current_content: str | None = None) -> None:
    if isinstance(exc, WriterForbidden):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, WriterMissing):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, WriterConflict):
        detail: object = {"message": str(exc), "current_content": current_content}
        raise HTTPException(status_code=409, detail=detail) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


def build_notes_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/note", response_model=NoteContent)
    def get_note(path: str) -> NoteContent:
        try:
            resolved = guard_user_path(settings.vault_path, path)
        except WriterError as exc:
            _raise_http(exc)
        if not resolved.is_file():
            raise HTTPException(status_code=404, detail=f"{path} does not exist")
        return NoteContent(path=path, content=resolved.read_text(encoding="utf-8"))

    @router.put("/note", response_model=NoteContent)
    def put_note(request: NoteUpdate) -> NoteContent:
        try:
            writer.update_note(
                settings.vault_path, request.path, request.expected_content, request.new_content
            )
        except WriterConflict as exc:
            current = (settings.vault_path / request.path).read_text(encoding="utf-8")
            _raise_http(exc, current_content=current)
        except WriterError as exc:
            _raise_http(exc)
        return NoteContent(path=request.path, content=request.new_content)

    @router.delete("/note")
    def remove_note(path: str) -> dict:
        try:
            writer.delete_note(settings.vault_path, path)
        except WriterError as exc:
            _raise_http(exc)
        return {"path": path}

    @router.post("/tasks/toggle", response_model=NewLine)
    def toggle(request: TaskLineRef) -> NewLine:
        try:
            new_line = writer.toggle_task_line(
                settings.vault_path, request.path, request.line, request.old_line
            )
        except WriterError as exc:
            _raise_http(exc)
        return NewLine(new_line=new_line)

    @router.post("/tasks/line/update", response_model=NewLine)
    def edit_line(request: TaskLineUpdate) -> NewLine:
        try:
            new_line = writer.update_task_line(
                settings.vault_path, request.path, request.line, request.old_line, request.new_line
            )
        except WriterError as exc:
            _raise_http(exc)
        return NewLine(new_line=new_line)

    @router.post("/tasks/line/delete")
    def drop_line(request: TaskLineRef) -> dict:
        try:
            writer.delete_task_line(
                settings.vault_path, request.path, request.line, request.old_line
            )
        except WriterError as exc:
            _raise_http(exc)
        return {"ok": True}

    return router
```

In `backend/main.py`, after the tasks router include (`app.include_router(build_tasks_router(resolved))`), add:

```python
    from backend.notes_api import build_notes_router

    app.include_router(build_notes_router(resolved))
```

- [ ] **Step 4: Verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_notes_api.py tests/test_api.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint + full suite, commit**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m pytest -q
git add backend/notes_api.py backend/main.py tests/test_notes_api.py
git commit -m "feat(api): note + task-line CRUD endpoints over the single writer"
```

---

### Task 4: Heatmap backend

**Files:**
- Modify: `backend/insights.py`, `backend/briefing_api.py`
- Test: `tests/test_insights.py`

**Interfaces:**
- Consumes: `_completions_by_day(settings)` (existing), `EXCLUDED_TOP_DIRS`, attempts table (`created_at`), vault git log.
- Produces (used by Task 9):
  - `class HeatmapDay(BaseModel): date: str; total: int; tasks: int; notes: int; study: int; captures: int`
  - `class HeatmapResponse(BaseModel): days: list[HeatmapDay]` (371 days, oldest → newest)
  - `heatmap_summary(settings, conn, today: date | None = None) -> HeatmapResponse`
  - `GET /api/insights/heatmap` → `HeatmapResponse`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_insights.py`; copy the file's existing settings/conn fixture pattern — it already builds tmp vaults for `insights_summary` tests):

```python
import subprocess
from datetime import date

from backend.insights import HEATMAP_DAYS, heatmap_summary


def test_heatmap_counts_tasks_notes_study_captures(settings_and_conn):
    settings, conn = settings_and_conn  # adapt name to the file's existing fixture
    vault = settings.vault_path
    subprocess.run(["git", "init"], cwd=vault, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=vault, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=vault, capture_output=True)

    today = date(2026, 7, 13)
    (vault / "20-Projects").mkdir(exist_ok=True)
    (vault / "20-Projects" / "p.md").write_text(
        "- [x] done ✅ 2026-07-13\n- [x] older ✅ 2026-07-10\n", encoding="utf-8"
    )
    (vault / "00-Inbox").mkdir(exist_ok=True)
    (vault / "00-Inbox" / "capture-2026-07-13.md").write_text(
        "- [ ] captured thing ➕ 2026-07-13\n", encoding="utf-8"
    )
    conn.execute(
        "INSERT INTO exams (course, title, questions_json) VALUES ('CS', 'T', '[]')"
    )
    conn.execute(
        "INSERT INTO attempts (exam_id, score, total, answers_json, created_at)"
        " VALUES (1, 8, 10, '[]', '2026-07-13 10:00:00')"
    )
    conn.commit()

    result = heatmap_summary(settings, conn, today=today)
    assert len(result.days) == HEATMAP_DAYS
    assert result.days[-1].date == "2026-07-13"
    latest = result.days[-1]
    assert latest.tasks == 1
    assert latest.captures == 1
    assert latest.study == 1
    assert latest.total == latest.tasks + latest.notes + latest.study + latest.captures
    by_date = {d.date: d for d in result.days}
    assert by_date["2026-07-10"].tasks == 1


def test_heatmap_excludes_private(settings_and_conn):
    settings, conn = settings_and_conn
    vault = settings.vault_path
    subprocess.run(["git", "init"], cwd=vault, capture_output=True, check=True)
    (vault / "99-Private").mkdir(exist_ok=True)
    (vault / "99-Private" / "secret.md").write_text("- [x] secret ✅ 2026-07-13\n", encoding="utf-8")
    result = heatmap_summary(settings, conn, today=date(2026, 7, 13))
    assert result.days[-1].tasks == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_insights.py -v -k heatmap`
Expected: FAIL with ImportError (`HEATMAP_DAYS`).

- [ ] **Step 3: Implement in `backend/insights.py`**

Add imports `import subprocess` and `from pathlib import PurePosixPath`; then:

```python
HEATMAP_DAYS = 371  # 53 weeks — a GitHub-style year grid
CAPTURE_STAMP_RE = re.compile(r"➕\s*(\d{4}-\d{2}-\d{2})")


class HeatmapDay(BaseModel):
    date: str
    total: int
    tasks: int
    notes: int
    study: int
    captures: int


class HeatmapResponse(BaseModel):
    days: list[HeatmapDay]


def _note_touches_by_day(settings: Settings) -> dict[str, int]:
    """Notes created/edited per day, from the vault's git history (I2 keeps it rich)."""
    result = subprocess.run(
        ["git", "log", f"--since={HEATMAP_DAYS + 1} days ago", "--date=short",
         "--pretty=format:@%ad", "--name-only"],
        cwd=settings.vault_path, capture_output=True, text=True, check=False,
    )
    counts: dict[str, int] = {}
    day: str | None = None
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if line.startswith("@"):
            day = line[1:]
            continue
        if not line or day is None:
            continue
        path = PurePosixPath(line)
        if path.suffix != ".md" or (path.parts and path.parts[0] in EXCLUDED_TOP_DIRS):
            continue
        counts[day] = counts.get(day, 0) + 1
    return counts


def _captures_by_day(settings: Settings) -> dict[str, int]:
    counts: dict[str, int] = {}
    inbox = settings.vault_path / "00-Inbox"
    if inbox.is_dir():
        for note in inbox.glob("*.md"):
            try:
                text = note.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for stamp in CAPTURE_STAMP_RE.findall(text):
                counts[stamp] = counts.get(stamp, 0) + 1
    return counts


def _study_by_day(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in conn.execute("SELECT created_at FROM attempts"):
        day = row["created_at"][:10]
        counts[day] = counts.get(day, 0) + 1
    return counts


def heatmap_summary(
    settings: Settings, conn: sqlite3.Connection, today: date | None = None
) -> HeatmapResponse:
    today = today or date.today()
    tasks = _completions_by_day(settings)
    notes = _note_touches_by_day(settings)
    study = _study_by_day(conn)
    captures = _captures_by_day(settings)
    days: list[HeatmapDay] = []
    for offset in range(HEATMAP_DAYS - 1, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        t, n, s, c = tasks.get(day, 0), notes.get(day, 0), study.get(day, 0), captures.get(day, 0)
        days.append(
            HeatmapDay(date=day, total=t + n + s + c, tasks=t, notes=n, study=s, captures=c)
        )
    return HeatmapResponse(days=days)
```

In `backend/briefing_api.py`, import `HeatmapResponse, heatmap_summary` from `backend.insights` and add inside `build_briefing_router`:

```python
    @router.get("/insights/heatmap", response_model=HeatmapResponse)
    def insights_heatmap() -> HeatmapResponse:
        conn = connect(settings.db_path)
        init_schema(conn)
        try:
            return heatmap_summary(settings, conn)
        finally:
            conn.close()
```

- [ ] **Step 4: Verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_insights.py tests/test_briefing_api.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint + full suite, commit**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m pytest -q
git add backend/insights.py backend/briefing_api.py tests/test_insights.py
git commit -m "feat(insights): GitHub-style productivity heatmap API"
```

---

### Task 5: Recent-activity backend

**Files:**
- Create: `backend/activity.py`
- Modify: `backend/briefing_api.py`
- Test: `tests/test_activity.py`

**Interfaces:**
- Consumes: `list_notes(vault_path)` from `backend.notes` (returns `NoteInfo{path,title,folder,modified}` newest first); `suggestions` table (`id, kind, status, applied_at`); `attempts` join `exams` (`course, created_at`).
- Produces (used by Task 11):
  - `class ActivityEvent(BaseModel): when: str; kind: str; title: str; path: str | None` — `kind` ∈ `"note" | "approval" | "exam"`
  - `recent_activity(settings, conn, limit: int = 15) -> list[ActivityEvent]` — merged, newest first
  - `GET /api/activity` → `list[ActivityEvent]`

- [ ] **Step 1: Write the failing test** — `tests/test_activity.py`:

```python
from pathlib import Path

import pytest

from backend.activity import recent_activity
from backend.config import Settings
from backend.db import connect, init_schema


@pytest.fixture()
def env(tmp_path: Path):
    vault = tmp_path / "vault"
    (vault / "20-Projects").mkdir(parents=True)
    (vault / "20-Projects" / "thesis.md").write_text("# Thesis\n", encoding="utf-8")
    settings = Settings(vault_path=vault, db_path=tmp_path / "argus.db")
    conn = connect(settings.db_path)
    init_schema(conn)
    yield settings, conn
    conn.close()


def test_activity_merges_notes_approvals_exams_newest_first(env):
    settings, conn = env
    conn.execute(
        "INSERT INTO suggestions (kind, payload_json, rationale, status, applied_at)"
        " VALUES ('task', '{}', 'move it', 'applied', '2026-07-12 10:00:00')"
    )
    conn.execute("INSERT INTO exams (course, title, questions_json) VALUES ('ES101', 'Plates', '[]')")
    conn.execute(
        "INSERT INTO attempts (exam_id, score, total, answers_json, created_at)"
        " VALUES (1, 3, 10, '[]', '2026-07-12 11:00:00')"
    )
    conn.commit()

    events = recent_activity(settings, conn, limit=10)
    kinds = {event.kind for event in events}
    assert {"note", "approval", "exam"} <= kinds
    whens = [event.when for event in events]
    assert whens == sorted(whens, reverse=True)


def test_activity_respects_limit(env):
    settings, conn = env
    assert len(recent_activity(settings, conn, limit=1)) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_activity.py -v`
Expected: FAIL with `ModuleNotFoundError: backend.activity`.

- [ ] **Step 3: Implement `backend/activity.py`**

```python
"""Recent-activity feed: what happened lately, merged from vault + db (read-only)."""

from __future__ import annotations

import sqlite3

from pydantic import BaseModel

from backend.config import Settings
from backend.notes import list_notes


class ActivityEvent(BaseModel):
    when: str  # ISO timestamp, sortable
    kind: str  # note | approval | exam
    title: str
    path: str | None = None


def recent_activity(
    settings: Settings, conn: sqlite3.Connection, limit: int = 15
) -> list[ActivityEvent]:
    events: list[ActivityEvent] = []

    for note in list_notes(settings.vault_path)[:limit]:
        events.append(
            ActivityEvent(when=note.modified, kind="note", title=note.title, path=note.path)
        )

    for row in conn.execute(
        "SELECT kind, rationale, applied_at FROM suggestions"
        " WHERE status = 'applied' AND applied_at IS NOT NULL"
        " ORDER BY applied_at DESC LIMIT ?",
        (limit,),
    ):
        events.append(
            ActivityEvent(
                when=row["applied_at"], kind="approval",
                title=f"approved {row['kind']}: {row['rationale'][:80]}",
            )
        )

    for row in conn.execute(
        "SELECT exams.course, attempts.score, attempts.total, attempts.created_at"
        " FROM attempts JOIN exams ON exams.id = attempts.exam_id"
        " ORDER BY attempts.created_at DESC LIMIT ?",
        (limit,),
    ):
        events.append(
            ActivityEvent(
                when=row["created_at"], kind="exam",
                title=f"{row['course']} practice exam {row['score']}/{row['total']}",
            )
        )

    events.sort(key=lambda event: event.when, reverse=True)
    return events[:limit]
```

In `backend/briefing_api.py` add (import `ActivityEvent, recent_activity` from `backend.activity`):

```python
    @router.get("/activity", response_model=list[ActivityEvent])
    def activity() -> list[ActivityEvent]:
        conn = connect(settings.db_path)
        init_schema(conn)
        try:
            return recent_activity(settings, conn)
        finally:
            conn.close()
```

Note: `NoteInfo.modified` and the SQLite timestamps are both ISO-ish strings; if the mixed sort proves inconsistent in the test, normalize with `event.when.replace(" ", "T")[:19]` before sorting — apply that inside `recent_activity`.

- [ ] **Step 4: Verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_activity.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + full suite, commit**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m pytest -q
git add backend/activity.py backend/briefing_api.py tests/test_activity.py
git commit -m "feat(activity): recent-activity feed API"
```

---

### Task 6: `argus web` — production launcher

**Files:**
- Modify: `backend/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: existing argparse CLI in `backend/cli.py` (has `init`, `reindex`, `watch`, `doctor`, `connect` subcommands — read `main()` there and add `web` in the same style).
- Produces: `argus web [--port 3000] [--backend-port 8000] [--build]` — builds the Next app when `web/.next/BUILD_ID` is missing (or `--build` passed), then runs uvicorn + `next start` together; Ctrl-C stops both. Helper `needs_build(web_dir: Path) -> bool`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_cli.py`):

```python
from backend.cli import needs_build


def test_needs_build_true_when_no_build_id(tmp_path):
    assert needs_build(tmp_path) is True


def test_needs_build_false_when_build_id_exists(tmp_path):
    (tmp_path / ".next").mkdir()
    (tmp_path / ".next" / "BUILD_ID").write_text("abc", encoding="utf-8")
    assert needs_build(tmp_path) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli.py -v -k needs_build`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement in `backend/cli.py`**

```python
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


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
```

Add imports `shutil` and `time` at the top. Wire the subcommand into `main()` following the existing subparser pattern:

```python
    web_parser = subparsers.add_parser("web", help="serve the production dashboard")
    web_parser.add_argument("--port", type=int, default=3000)
    web_parser.add_argument("--backend-port", type=int, default=8000)
    web_parser.add_argument("--build", action="store_true", help="force a rebuild first")
```

and in the dispatch: `if args.command == "web": return run_web(args.port, args.backend_port, args.build)`.

Also check `web/next.config.mjs`: the `/api` rewrite to `127.0.0.1:8000` must apply in production mode too (rewrites do run under `next start`; just confirm the config doesn't gate them behind `NODE_ENV === "development"` — if it does, remove the gate).

- [ ] **Step 4: Verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Manual smoke, lint, commit**

Run `.venv/Scripts/python.exe -m backend.cli web` once, confirm both servers come up and `http://localhost:3000` loads, Ctrl-C stops both. Then:

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m pytest -q
git add backend/cli.py tests/test_cli.py
git commit -m "feat(cli): argus web — production build + start for daily use"
```

---

### Task 7: Dashboard route swap + extracted Briefing/Capture cards

**Files:**
- Create: `web/app/(dashboard)/dashboard/page.tsx`, `web/components/dashboard/BriefingCard.tsx`, `web/components/dashboard/CaptureCard.tsx`
- Modify: `web/app/page.tsx`, `web/components/Sidebar.tsx`, `web/e2e/roundtrip.spec.ts`
- Delete: `web/app/(dashboard)/today/page.tsx` (whole `today/` dir)

**Interfaces:**
- Consumes: `GlassCard` (`label`, `title`, `className`, children), `fetcher` from `@/lib/api`, existing `/api/briefing`, `/api/briefing/run`, `/api/capture`.
- Produces: `<BriefingCard />` (self-fetching, collapsible; collapse state in `localStorage` key `argus-briefing-collapsed-<date>`), `<CaptureCard onCaptured?: () => void />`. Dashboard page shell with the Layout-A grid and placeholder slots that Tasks 8–12 fill (each later task replaces one clearly marked placeholder).

- [ ] **Step 1: Create `web/components/dashboard/BriefingCard.tsx`** — move the briefing logic out of the old Today page verbatim, plus collapse:

```tsx
"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import GlassCard from "@/components/GlassCard";
import { fetcher } from "@/lib/api";

interface Briefing {
  date: string;
  path: string;
  markdown: string;
}

/** Minimal renderer for the briefing's markdown subset (bold labels + bullets). */
function BriefingBody({ markdown }: { markdown: string }) {
  const bold = (text: string) =>
    text.split(/\*\*(.+?)\*\*/g).map((part, i) =>
      i % 2 === 1 ? (
        <strong key={i} className="font-display text-primary-soft">
          {part}
        </strong>
      ) : (
        part
      ),
    );
  return (
    <div className="space-y-1.5 text-sm text-ink-muted">
      {markdown.split("\n").map((line, i) => {
        const trimmed = line.trim();
        if (!trimmed) return null;
        if (trimmed.startsWith("- ")) {
          return (
            <p key={i} className="flex items-baseline gap-2 pl-1">
              <span className="text-ink-faint">•</span>
              <span>{bold(trimmed.slice(2))}</span>
            </p>
          );
        }
        return <p key={i}>{bold(trimmed)}</p>;
      })}
    </div>
  );
}

export default function BriefingCard() {
  const {
    data: briefing,
    error: briefingMissing,
    mutate: refreshBriefing,
  } = useSWR<Briefing>("/api/briefing", fetcher, { shouldRetryOnError: false });
  const [generating, setGenerating] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const storageKey = `argus-briefing-collapsed-${new Date().toISOString().slice(0, 10)}`;

  useEffect(() => {
    setCollapsed(localStorage.getItem(storageKey) === "1");
  }, [storageKey]);

  function toggleCollapsed() {
    const next = !collapsed;
    setCollapsed(next);
    localStorage.setItem(storageKey, next ? "1" : "0");
  }

  async function generateBriefing() {
    setGenerating(true);
    try {
      await fetch("/api/briefing/run", { method: "POST" });
      await refreshBriefing();
    } finally {
      setGenerating(false);
    }
  }

  return (
    <GlassCard label="BRIEFING" title="Your morning briefing">
      {briefing ? (
        <>
          {!collapsed && <BriefingBody markdown={briefing.markdown} />}
          <p className="mt-3 font-mono text-[11px] text-ink-faint">
            written to {briefing.path} ·{" "}
            <button
              onClick={toggleCollapsed}
              className="text-primary-soft underline-offset-2 hover:underline"
            >
              {collapsed ? "expand" : "collapse"}
            </button>{" "}
            ·{" "}
            <button
              onClick={generateBriefing}
              disabled={generating}
              className="text-primary-soft underline-offset-2 hover:underline disabled:opacity-40"
            >
              {generating ? "composing…" : "run again"}
            </button>
          </p>
        </>
      ) : briefingMissing ? (
        <div className="flex flex-wrap items-center gap-4">
          <p className="text-sm text-ink-muted">
            No briefing yet today — Argus writes one into your daily note at 07:00, or on demand.
          </p>
          <button
            onClick={generateBriefing}
            disabled={generating}
            className="shrink-0 rounded-xl bg-gradient-to-r from-primary to-accent px-4 py-2 font-display text-sm text-white disabled:opacity-40"
          >
            {generating ? "Composing…" : "Generate now"}
          </button>
        </div>
      ) : (
        <p className="text-sm text-ink-faint">Loading…</p>
      )}
    </GlassCard>
  );
}
```

- [ ] **Step 2: Create `web/components/dashboard/CaptureCard.tsx`** — the capture form from the old Today page:

```tsx
"use client";

import { useState } from "react";
import GlassCard from "@/components/GlassCard";

export default function CaptureCard({ onCaptured }: { onCaptured?: () => void }) {
  const [capture, setCapture] = useState("");
  const [status, setStatus] = useState<string | null>(null);

  async function submitCapture(event: React.FormEvent) {
    event.preventDefault();
    const text = capture.trim();
    if (!text) return;
    setCapture("");
    const response = await fetch("/api/capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const payload = await response.json();
    setStatus(response.ok ? `Captured → ${payload.path}` : `Capture failed: ${payload.detail}`);
    onCaptured?.();
    setTimeout(() => setStatus(null), 5000);
  }

  return (
    <GlassCard label="CAPTURE" title="Quick capture">
      <form onSubmit={submitCapture} className="flex gap-2">
        <input
          value={capture}
          onChange={(event) => setCapture(event.target.value)}
          placeholder="e.g. email prof about thesis"
          className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/5 px-4 py-2.5 text-sm placeholder:text-ink-faint focus:border-primary-soft/50 focus:outline-none"
        />
        <button
          type="submit"
          disabled={!capture.trim()}
          className="shrink-0 rounded-xl bg-gradient-to-r from-primary to-accent px-4 py-2.5 font-display text-sm text-white disabled:opacity-40"
        >
          Save
        </button>
      </form>
      {status && <p className="mt-3 font-mono text-[11px] text-primary-soft">{status}</p>}
    </GlassCard>
  );
}
```

- [ ] **Step 3: Create `web/app/(dashboard)/dashboard/page.tsx`** — Layout-A shell with placeholders:

```tsx
"use client";

import GlassCard from "@/components/GlassCard";
import BriefingCard from "@/components/dashboard/BriefingCard";
import CaptureCard from "@/components/dashboard/CaptureCard";

function formatToday(): string {
  return new Date().toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 5) return "Burning the midnight oil";
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

export default function DashboardPage() {
  return (
    <>
      <header className="mb-8 animate-rise">
        <p className="eyebrow mb-2">{`// DASHBOARD · ${formatToday()}`}</p>
        <h1 className="font-display text-3xl font-semibold tracking-tight md:text-4xl">
          {greeting()}, <span className="gradient-text">Ethan</span>.
        </h1>
      </header>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        {/* Left column: the day */}
        <div className="flex min-w-0 flex-col gap-4">
          <BriefingCard />
          {/* Task 8 replaces this placeholder with <StatTiles /> */}
          <GlassCard label="STATS" title="Productivity">
            <p className="text-sm text-ink-faint">Stats coming online…</p>
          </GlassCard>
          {/* Task 10 replaces this placeholder with <AgendaCard /> */}
          <GlassCard label="AGENDA" title="Schedule">
            <p className="text-sm text-ink-faint">Agenda coming online…</p>
          </GlassCard>
          {/* Task 9 replaces this placeholder with <Heatmap /> */}
          <GlassCard label="ACTIVITY" title="A year at a glance">
            <p className="text-sm text-ink-faint">Heatmap coming online…</p>
          </GlassCard>
          <CaptureCard />
        </div>

        {/* Right rail: activity + chat dock */}
        <div className="flex min-w-0 flex-col gap-4">
          {/* Task 11 replaces this placeholder with <ActivityFeed /> */}
          <GlassCard label="RECENT" title="Latest activity">
            <p className="text-sm text-ink-faint">Feed coming online…</p>
          </GlassCard>
          {/* Task 12 replaces this placeholder with <ChatPanel variant="dock" /> */}
          <GlassCard label="CHAT" title="Ask Argus" className="lg:sticky lg:top-4">
            <p className="text-sm text-ink-faint">Mini chat coming online…</p>
          </GlassCard>
        </div>
      </div>
    </>
  );
}
```

- [ ] **Step 4: Rewire routes and nav**

- `web/app/page.tsx`: change `redirect("/today")` → `redirect("/dashboard")`.
- `web/components/Sidebar.tsx`: in `NAV_ITEMS[0]` change `href: "/today", name: "Today"` → `href: "/dashboard", name: "Dashboard"` (keep the icon); change the logo `Link href="/today"` → `href="/dashboard"`.
- Delete the `web/app/(dashboard)/today/` directory (`git rm -r "web/app/(dashboard)/today"`). The old page's Agenda/Top-3/Recent-notes cards are superseded by Tasks 8–11.
- `web/e2e/roundtrip.spec.ts`: change `await page.goto("/today")` → `await page.goto("/dashboard")` (the capture placeholder text is unchanged).

- [ ] **Step 5: Verify**

Run: `cd web && npm run lint && npm run build`
Expected: both clean; build lists `/dashboard` and no `/today`.

- [ ] **Step 6: Commit**

```bash
git add -A web
git commit -m "feat(web): dashboard-first home replaces Today (Layout A shell)"
```

---

### Task 8: Stat tiles

**Files:**
- Create: `web/components/dashboard/StatTiles.tsx`
- Modify: `web/lib/api.ts`, `web/app/(dashboard)/dashboard/page.tsx`

**Interfaces:**
- Consumes: `GET /api/insights` (`InsightsSummary`: `completion_trend: [{date, completed}]`, `overdue: [{date, count}]`, `calendar: [{date, event_hours, focus_hours}]`, `study: {streak_days, courses}`), `GET /api/agenda` (`tasks` = overdue+today merged).
- Produces: `<StatTiles />` — five linked tiles: Due today (→ /tasks), Overdue (→ /tasks), Done today (→ /insights), Study streak (→ /study), Focus hours today (→ /insights).

- [ ] **Step 1: Add hooks to `web/lib/api.ts`**

```ts
export interface InsightsSummary {
  completion_trend: { date: string; completed: number }[];
  overdue: { date: string; count: number }[];
  calendar: { date: string; event_hours: number; focus_hours: number }[];
  study: { streak_days: number; courses: { course: string; attempts: { date: string; pct: number }[] }[] };
  configured: { gcal: boolean };
}

/** Insights rollup for stat tiles and charts. */
export function useInsights() {
  return useSWR<InsightsSummary>("/api/insights", fetcher);
}
```

- [ ] **Step 2: Create `web/components/dashboard/StatTiles.tsx`**

```tsx
"use client";

import Link from "next/link";
import useSWR from "swr";
import { fetcher, useInsights } from "@/lib/api";

interface AgendaLite {
  tasks: { due: string | null }[];
}

function Tile({ href, label, value, unit }: { href: string; label: string; value: string | number; unit?: string }) {
  return (
    <Link
      href={href}
      className="glass glass-hover flex min-w-0 flex-col gap-1 px-4 py-3"
      prefetch={true}
    >
      <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
        {label}
      </span>
      <span className="font-display text-2xl font-semibold text-ink">
        {value}
        {unit && <span className="ml-1 text-sm font-normal text-ink-muted">{unit}</span>}
      </span>
    </Link>
  );
}

export default function StatTiles() {
  const { data: insights } = useInsights();
  const { data: agenda } = useSWR<AgendaLite>("/api/agenda", fetcher);

  const today = new Date().toISOString().slice(0, 10);
  const dueToday = agenda?.tasks.filter((task) => task.due === today).length ?? "–";
  const overdue = agenda ? agenda.tasks.length - (typeof dueToday === "number" ? dueToday : 0) : "–";
  const doneToday =
    insights?.completion_trend.find((day) => day.date === today)?.completed ?? "–";
  const streak = insights?.study.streak_days ?? "–";
  const focus = insights?.calendar[insights.calendar.length - 1]?.focus_hours ?? "–";

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
      <Tile href="/tasks" label="due today" value={dueToday} />
      <Tile href="/tasks" label="overdue" value={overdue} />
      <Tile href="/insights" label="done today" value={doneToday} />
      <Tile href="/study" label="streak" value={streak} unit="days" />
      <Tile href="/insights" label="focus" value={focus} unit="h" />
    </div>
  );
}
```

- [ ] **Step 3: Wire into the dashboard page** — in `web/app/(dashboard)/dashboard/page.tsx`, import `StatTiles` and replace the STATS placeholder `GlassCard` with `<StatTiles />`.

- [ ] **Step 4: Verify + commit**

Run: `cd web && npm run lint && npm run build` — clean.

```bash
git add web/components/dashboard/StatTiles.tsx web/lib/api.ts "web/app/(dashboard)/dashboard/page.tsx"
git commit -m "feat(web): dashboard stat tiles"
```

---

### Task 9: Heatmap component

**Files:**
- Create: `web/components/dashboard/Heatmap.tsx`
- Modify: `web/lib/api.ts`, `web/app/(dashboard)/dashboard/page.tsx`, `web/app/(dashboard)/insights/page.tsx`

**Interfaces:**
- Consumes: `GET /api/insights/heatmap` (Task 4 shape).
- Produces: `<Heatmap />` — 53×7 SVG grid, metric filter (`all | tasks | notes | study | captures`), hover breakdown line. Test id `data-testid="heatmap"`; each cell `data-date="YYYY-MM-DD"` and `data-count="<n>"` (Task 14 asserts on these).

- [ ] **Step 1: Add hook to `web/lib/api.ts`**

```ts
export interface HeatmapDay {
  date: string;
  total: number;
  tasks: number;
  notes: number;
  study: number;
  captures: number;
}

/** 53 weeks of daily productivity events for the GitHub-style grid. */
export function useHeatmap() {
  return useSWR<{ days: HeatmapDay[] }>("/api/insights/heatmap", fetcher);
}
```

- [ ] **Step 2: Create `web/components/dashboard/Heatmap.tsx`**

```tsx
"use client";

import { useMemo, useState } from "react";
import GlassCard from "@/components/GlassCard";
import { HeatmapDay, useHeatmap } from "@/lib/api";

const METRICS = ["all", "tasks", "notes", "study", "captures"] as const;
type Metric = (typeof METRICS)[number];

const CELL = 11;
const GAP = 3;

function countFor(day: HeatmapDay, metric: Metric): number {
  return metric === "all" ? day.total : day[metric];
}

/** 0–4 intensity on the purple ramp, quantized against the visible max. */
function level(count: number, max: number): number {
  if (count === 0 || max === 0) return 0;
  return Math.min(4, Math.ceil((count / max) * 4));
}

const RAMP = [
  "rgba(255,255,255,0.05)",
  "rgba(139,92,246,0.25)",
  "rgba(139,92,246,0.45)",
  "rgba(167,139,250,0.7)",
  "rgba(196,181,253,0.95)",
];

export default function Heatmap() {
  const { data, error } = useHeatmap();
  const [metric, setMetric] = useState<Metric>("all");
  const [hover, setHover] = useState<HeatmapDay | null>(null);

  const { weeks, max } = useMemo(() => {
    const days = data?.days ?? [];
    // Pad the front so columns start on Sunday.
    const lead = days.length ? new Date(`${days[0].date}T00:00:00`).getDay() : 0;
    const cells: (HeatmapDay | null)[] = [...Array(lead).fill(null), ...days];
    const weeks: (HeatmapDay | null)[][] = [];
    for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
    const max = Math.max(0, ...days.map((day) => countFor(day, metric)));
    return { weeks, max };
  }, [data, metric]);

  return (
    <GlassCard label="ACTIVITY" title="A year at a glance">
      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        {METRICS.map((option) => (
          <button
            key={option}
            onClick={() => setMetric(option)}
            className={`rounded-lg px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider transition-colors ${
              metric === option
                ? "bg-primary/25 text-primary-soft"
                : "text-ink-faint hover:bg-white/5 hover:text-ink-muted"
            }`}
          >
            {option}
          </button>
        ))}
      </div>
      {error && <p className="text-sm text-ink-muted">Couldn’t load activity — is the backend up?</p>}
      <div className="overflow-x-auto pb-1" data-testid="heatmap">
        <svg
          width={weeks.length * (CELL + GAP)}
          height={7 * (CELL + GAP)}
          role="img"
          aria-label="Productivity heatmap, one cell per day"
        >
          {weeks.map((week, x) =>
            week.map(
              (day, y) =>
                day && (
                  <rect
                    key={day.date}
                    x={x * (CELL + GAP)}
                    y={y * (CELL + GAP)}
                    width={CELL}
                    height={CELL}
                    rx={2.5}
                    fill={RAMP[level(countFor(day, metric), max)]}
                    data-date={day.date}
                    data-count={countFor(day, metric)}
                    onMouseEnter={() => setHover(day)}
                    onMouseLeave={() => setHover(null)}
                  >
                    <title>{`${day.date} — ${day.total} events`}</title>
                  </rect>
                ),
            ),
          )}
        </svg>
      </div>
      <p className="mt-2 h-4 font-mono text-[11px] text-ink-faint">
        {hover
          ? `${hover.date}: ${hover.tasks} tasks · ${hover.notes} notes · ${hover.study} study · ${hover.captures} captures`
          : "hover a day for the breakdown"}
      </p>
    </GlassCard>
  );
}
```

- [ ] **Step 3: Wire in** — dashboard page: replace the ACTIVITY heatmap placeholder with `<Heatmap />`. Insights page: import `Heatmap` (plain import — it's SVG, no recharts) and add `<Heatmap />` as the first full-width panel; follow that page's existing grid classes.

- [ ] **Step 4: Verify + commit**

Run: `cd web && npm run lint && npm run build` — clean. Then start backend + `npm run dev`, load `/dashboard`, confirm the grid renders with real vault data and the filter buttons change intensities.

```bash
git add web/components/dashboard/Heatmap.tsx web/lib/api.ts "web/app/(dashboard)/dashboard/page.tsx" "web/app/(dashboard)/insights/page.tsx"
git commit -m "feat(web): GitHub-style activity heatmap on dashboard + insights"
```

---

### Task 10: Interactive agenda (check-off / edit / delete, optimistic)

**Files:**
- Create: `web/components/dashboard/AgendaCard.tsx`
- Modify: `web/lib/api.ts`, `web/app/(dashboard)/dashboard/page.tsx`

**Interfaces:**
- Consumes: `GET /api/agenda` (tasks carry `path`, `line`, `text`, `due`, `priority`, `source`; `path`/`line` are null for todoist items), Task 3 endpoints.
- Produces: `<AgendaCard />`; `mutateJSON(url, body, method)` helper in `lib/api.ts` that throws `ApiError { status, payload }`.

- [ ] **Step 1: Add mutation helper to `web/lib/api.ts`**

```ts
export class ApiError extends Error {
  status: number;
  payload: unknown;
  constructor(status: number, payload: unknown, message: string) {
    super(message);
    this.status = status;
    this.payload = payload;
  }
}

/** JSON mutation helper — throws ApiError with the response payload on non-2xx. */
export async function mutateJSON<T>(
  url: string,
  body: unknown,
  method: "POST" | "PUT" | "DELETE" = "POST",
): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = (payload as { detail?: unknown }).detail;
    const message =
      typeof detail === "string"
        ? detail
        : ((detail as { message?: string })?.message ?? `Request failed: ${response.status}`);
    throw new ApiError(response.status, payload, message);
  }
  return payload as T;
}
```

- [ ] **Step 2: Create `web/components/dashboard/AgendaCard.tsx`**

```tsx
"use client";

import { useState } from "react";
import useSWR from "swr";
import GlassCard from "@/components/GlassCard";
import { fetcher, mutateJSON } from "@/lib/api";

interface CalendarEvent {
  title: string;
  start: string;
  end: string;
  all_day: boolean;
}

interface AgendaTask {
  text: string;
  done: boolean;
  due: string | null;
  priority: string | null;
  source: string;
  path: string | null;
  line: number | null;
}

interface Agenda {
  date: string;
  events: CalendarEvent[];
  tasks: AgendaTask[];
  configured: { gcal: boolean; todoist: boolean };
}

function eventTime(iso: string): string {
  if (!iso.includes("T")) return "all day";
  return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

export default function AgendaCard() {
  const { data: agenda, mutate } = useSWR<Agenda>("/api/agenda", fetcher);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [toast, setToast] = useState<string | null>(null);

  function flash(message: string) {
    setToast(message);
    setTimeout(() => setToast(null), 4000);
  }

  async function withRawLine(
    task: AgendaTask,
    action: (raw: string) => Promise<void>,
  ): Promise<void> {
    if (!task.path || !task.line) return;
    // Read the exact current line so the server drift check compares like with like.
    const response = await fetch(`/api/note?path=${encodeURIComponent(task.path)}`);
    const note = (await response.json()) as { content: string };
    const raw = note.content.split("\n")[task.line - 1] ?? "";
    await action(raw);
  }

  async function toggle(task: AgendaTask) {
    // Optimistic: flip locally, reconcile after the API call.
    mutate(
      (current) =>
        current && {
          ...current,
          tasks: current.tasks.map((item) =>
            item.path === task.path && item.line === task.line
              ? { ...item, done: !item.done }
              : item,
          ),
        },
      { revalidate: false },
    );
    try {
      await withRawLine(task, async (raw) => {
        await mutateJSON("/api/tasks/toggle", { path: task.path, line: task.line, old_line: raw });
      });
    } catch (error) {
      flash(error instanceof Error ? error.message : "Toggle failed");
    }
    mutate();
  }

  async function saveEdit(task: AgendaTask) {
    const text = draft.trim();
    setEditing(null);
    if (!text) return;
    try {
      await withRawLine(task, async (raw) => {
        const suffix = raw.includes("📅") ? "" : task.due ? ` 📅 ${task.due}` : "";
        const newLine = raw.replace(task.text, text) || `- [ ] ${text}${suffix}`;
        await mutateJSON("/api/tasks/line/update", {
          path: task.path,
          line: task.line,
          old_line: raw,
          new_line: newLine,
        });
      });
    } catch (error) {
      flash(error instanceof Error ? error.message : "Edit failed");
    }
    mutate();
  }

  async function remove(task: AgendaTask) {
    if (!window.confirm(`Delete “${task.text}”? A git snapshot makes this undoable.`)) return;
    try {
      await withRawLine(task, async (raw) => {
        await mutateJSON("/api/tasks/line/delete", {
          path: task.path,
          line: task.line,
          old_line: raw,
        });
      });
    } catch (error) {
      flash(error instanceof Error ? error.message : "Delete failed");
    }
    mutate();
  }

  return (
    <GlassCard label="AGENDA" title="Schedule">
      {agenda && agenda.events.length === 0 && (
        <p className="mb-2 text-sm text-ink-muted">
          {agenda.configured.gcal ? "Nothing on the calendar today." : "Google Calendar not connected."}
        </p>
      )}
      <ul className="space-y-2">
        {(agenda?.events ?? []).map((event, i) => (
          <li key={i} className="flex items-center gap-3">
            <span className="w-20 shrink-0 font-mono text-[11px] text-primary-soft">
              {eventTime(event.start)}
            </span>
            <span className="h-8 w-px bg-gradient-to-b from-primary/60 to-accent/40" />
            <span className="text-sm">{event.title}</span>
          </li>
        ))}
      </ul>

      <div className="mt-4 border-t border-white/5 pt-3">
        <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
          due · overdue
        </p>
        {agenda && agenda.tasks.length === 0 && (
          <p className="text-sm text-ink-muted">Nothing due. Capture something?</p>
        )}
        <ul className="space-y-1.5">
          {(agenda?.tasks ?? []).map((task, i) => {
            const key = `${task.path}:${task.line}:${i}`;
            const editable = task.source === "vault" && task.path && task.line;
            return (
              <li key={key} className="group flex items-center gap-2 text-sm">
                <button
                  aria-label={task.done ? "Mark not done" : "Mark done"}
                  disabled={!editable}
                  onClick={() => toggle(task)}
                  className="text-ink-faint transition-colors hover:text-primary-soft disabled:opacity-40"
                >
                  {task.done ? "◉" : "○"}
                </button>
                {editing === key ? (
                  <form
                    className="flex min-w-0 flex-1 gap-2"
                    onSubmit={(event) => {
                      event.preventDefault();
                      saveEdit(task);
                    }}
                  >
                    <input
                      autoFocus
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                      onBlur={() => setEditing(null)}
                      className="min-w-0 flex-1 rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-sm focus:border-primary-soft/50 focus:outline-none"
                    />
                  </form>
                ) : (
                  <span className={`min-w-0 flex-1 truncate ${task.done ? "text-ink-faint line-through" : "text-ink-muted"}`}>
                    {task.text}
                    {task.due && <span className="ml-2 font-mono text-[10px] text-ink-faint">{task.due}</span>}
                  </span>
                )}
                {editable && editing !== key && (
                  <span className="hidden shrink-0 gap-2 group-hover:flex">
                    <button
                      aria-label="Edit task"
                      onClick={() => {
                        setEditing(key);
                        setDraft(task.text);
                      }}
                      className="font-mono text-[10px] text-ink-faint hover:text-primary-soft"
                    >
                      edit
                    </button>
                    <button
                      aria-label="Delete task"
                      onClick={() => remove(task)}
                      className="font-mono text-[10px] text-ink-faint hover:text-accent"
                    >
                      delete
                    </button>
                  </span>
                )}
                {task.source === "todoist" && (
                  <span className="rounded bg-white/5 px-1.5 font-mono text-[10px] text-ink-faint">todoist</span>
                )}
              </li>
            );
          })}
        </ul>
      </div>
      {toast && <p className="mt-3 font-mono text-[11px] text-accent">{toast}</p>}
    </GlassCard>
  );
}
```

Implementation note: `withRawLine` fetches the note's current content and takes the exact raw line at `task.line` as `old_line` — this is what makes the server drift check meaningful rather than comparing against the parser's cleaned `text`.

- [ ] **Step 3: Wire in** — replace the AGENDA placeholder in the dashboard page with `<AgendaCard />`.

- [ ] **Step 4: Verify + commit**

Run: `cd web && npm run lint && npm run build` — clean. Manual: check a task off on `/dashboard`, confirm instant flip, the ✅ stamp in the vault file, and the `argus: pre-apply snapshot (toggle task …)` commit in the vault git log. Behavioral e2e lands in Task 14.

```bash
git add web/components/dashboard/AgendaCard.tsx web/lib/api.ts "web/app/(dashboard)/dashboard/page.tsx"
git commit -m "feat(web): interactive agenda — optimistic check-off, edit, delete"
```

---

### Task 11: Activity feed component

**Files:**
- Create: `web/components/dashboard/ActivityFeed.tsx`
- Modify: `web/lib/api.ts`, `web/app/(dashboard)/dashboard/page.tsx`

**Interfaces:**
- Consumes: `GET /api/activity` (Task 5), `GET /api/vault` (`{name}`) for obsidian:// links, `DELETE /api/note` + `mutateJSON` (Tasks 3, 10).
- Produces: `<ActivityFeed />` in the right rail, including the spec's "delete captured inbox notes" affordance: a delete button on `note` events whose path starts with `00-Inbox/`.

- [ ] **Step 1: Add hook to `web/lib/api.ts`**

```ts
export interface ActivityEvent {
  when: string;
  kind: "note" | "approval" | "exam";
  title: string;
  path: string | null;
}

/** Latest vault edits, approvals, and exam attempts, newest first. */
export function useActivity() {
  return useSWR<ActivityEvent[]>("/api/activity", fetcher);
}
```

- [ ] **Step 2: Create `web/components/dashboard/ActivityFeed.tsx`**

```tsx
"use client";

import useSWR from "swr";
import GlassCard from "@/components/GlassCard";
import { fetcher, mutateJSON, useActivity } from "@/lib/api";

const KIND_BADGE: Record<string, string> = {
  note: "text-primary-soft",
  approval: "text-signal",
  exam: "text-accent",
};

function relative(when: string): string {
  const then = new Date(when.replace(" ", "T"));
  const minutes = Math.max(0, Math.round((Date.now() - then.getTime()) / 60000));
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 60 * 24) return `${Math.round(minutes / 60)}h ago`;
  return `${Math.round(minutes / (60 * 24))}d ago`;
}

export default function ActivityFeed() {
  const { data: events, mutate } = useActivity();
  const { data: vault } = useSWR<{ name: string }>("/api/vault", fetcher);

  async function removeNote(path: string) {
    if (!window.confirm(`Delete ${path}? A git snapshot makes this undoable.`)) return;
    try {
      await mutateJSON(`/api/note?path=${encodeURIComponent(path)}`, undefined, "DELETE");
    } catch {
      // Feed refresh below surfaces the current truth either way.
    }
    mutate();
  }

  return (
    <GlassCard label="RECENT" title="Latest activity">
      {!events && <p className="text-sm text-ink-faint">Loading…</p>}
      {events && events.length === 0 && <p className="text-sm text-ink-muted">All quiet.</p>}
      <ul className="divide-y divide-white/5">
        {(events ?? []).map((event, i) => (
          <li key={i} className="flex items-baseline gap-2 py-2 text-sm">
            <span className={`shrink-0 font-mono text-[10px] uppercase ${KIND_BADGE[event.kind] ?? ""}`}>
              {event.kind}
            </span>
            {event.path && vault ? (
              <a
                href={`obsidian://open?vault=${encodeURIComponent(vault.name)}&file=${encodeURIComponent(event.path)}`}
                className="min-w-0 flex-1 truncate text-ink-muted underline-offset-2 hover:text-ink hover:underline"
              >
                {event.title}
              </a>
            ) : (
              <span className="min-w-0 flex-1 truncate text-ink-muted">{event.title}</span>
            )}
            {event.kind === "note" && event.path?.startsWith("00-Inbox/") && (
              <button
                aria-label={`Delete ${event.path}`}
                onClick={() => removeNote(event.path!)}
                className="shrink-0 font-mono text-[10px] text-ink-faint hover:text-accent"
              >
                delete
              </button>
            )}
            <span className="shrink-0 font-mono text-[10px] text-ink-faint">{relative(event.when)}</span>
          </li>
        ))}
      </ul>
    </GlassCard>
  );
}
```

- [ ] **Step 3: Wire in** — replace the RECENT placeholder in the dashboard page with `<ActivityFeed />`.

- [ ] **Step 4: Verify + commit**

Run: `cd web && npm run lint && npm run build` — clean.

```bash
git add web/components/dashboard/ActivityFeed.tsx web/lib/api.ts "web/app/(dashboard)/dashboard/page.tsx"
git commit -m "feat(web): recent-activity feed in dashboard right rail"
```

---

### Task 12: Shared chat provider, dock, and animation revision

**Files:**
- Create: `web/lib/chat.tsx`, `web/components/chat/ChatPanel.tsx`, `web/components/chat/ChatDock.tsx`
- Modify: `web/app/(dashboard)/layout.tsx`, `web/app/(dashboard)/chat/page.tsx`, `web/app/(dashboard)/dashboard/page.tsx`, `web/app/globals.css`

**Interfaces:**
- Consumes: `/ws/chat` frames (`{type:"delta",text} | {type:"done"} | {type:"error",detail}`), `POST /api/plan`, `GET /api/vault`.
- Produces:
  - `ChatProvider` (context) + `useChat(): { messages: ChatMessage[]; busy: boolean; offline: boolean; send: (text: string) => void }` where `ChatMessage = { role: "user" | "argus"; text: string; pending?: boolean }`
  - `<ChatPanel variant="dock" | "full" />` — message thread + composer, both surfaces share provider state
  - `<ChatDock />` — floating button + pop-over panel, hidden on `/dashboard` and `/chat`
  - CSS: `.animate-msg-in` (150ms fade/slide, disabled under reduced motion)

- [ ] **Step 1: Create `web/lib/chat.tsx`** — the chat page's state/ws logic, lifted verbatim into a provider:

```tsx
"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";

export interface ChatMessage {
  role: "user" | "argus";
  text: string;
  pending?: boolean;
}

interface ChatState {
  messages: ChatMessage[];
  busy: boolean;
  offline: boolean;
  send: (text: string) => void;
}

const ChatContext = createContext<ChatState | null>(null);

export function useChat(): ChatState {
  const state = useContext(ChatContext);
  if (!state) throw new Error("useChat must be used inside <ChatProvider>");
  return state;
}

export function ChatProvider({ children }: { children: React.ReactNode }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [offline, setOffline] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => () => socketRef.current?.close(), []);

  async function runPlanner(instruction: string) {
    setBusy(true);
    setMessages((prev) => [
      ...prev,
      { role: "user", text: `/plan ${instruction}` },
      { role: "argus", text: "", pending: true },
    ]);
    try {
      const response = await fetch("/api/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instruction: instruction || "Plan my day" }),
      });
      const payload = await response.json();
      const text = response.ok
        ? `Planned! ${payload.created} suggestion${payload.created === 1 ? "" : "s"} waiting on the Review page.`
        : `Planning failed: ${payload.detail}`;
      setMessages((prev) => [...prev.slice(0, -1), { role: "argus", text }]);
    } catch {
      setMessages((prev) => [
        ...prev.slice(0, -1),
        { role: "argus", text: "Planning failed — is the backend running?" },
      ]);
    }
    setBusy(false);
  }

  function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    if (message.startsWith("/plan")) {
      runPlanner(message.replace(/^\/plan\s*/, ""));
      return;
    }
    setBusy(true);
    setOffline(false);
    setMessages((prev) => [
      ...prev,
      { role: "user", text: message },
      { role: "argus", text: "", pending: true },
    ]);

    const ws = new WebSocket(`ws://${window.location.hostname}:8000/ws/chat`);
    socketRef.current = ws;
    ws.onopen = () => ws.send(JSON.stringify({ message }));
    ws.onmessage = (event) => {
      const frame = JSON.parse(event.data);
      if (frame.type === "delta") {
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          next[next.length - 1] = { ...last, text: last.text + frame.text, pending: false };
          return next;
        });
      } else if (frame.type === "done") {
        setBusy(false);
        ws.close();
      } else if (frame.type === "error") {
        setMessages((prev) => {
          const next = [...prev];
          next[next.length - 1] = {
            role: "argus",
            text: `Something went wrong: ${frame.detail}`,
            pending: false,
          };
          return next;
        });
        setBusy(false);
        ws.close();
      }
    };
    ws.onerror = () => {
      setOffline(true);
      setBusy(false);
      setMessages((prev) => prev.slice(0, -1));
    };
  }

  return (
    <ChatContext.Provider value={{ messages, busy, offline, send }}>
      {children}
    </ChatContext.Provider>
  );
}
```

- [ ] **Step 2: Create `web/components/chat/ChatPanel.tsx`** — thread + composer used by both surfaces (citation rendering moved here from the chat page):

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { useChat } from "@/lib/chat";

const EXAMPLES = [
  "What did I write about algorithms?",
  "Summarize my recent daily notes.",
  "What's in my inbox folder?",
];

/** Split answer text into plain segments and [vault/path.md] citation chips. */
function renderWithCitations(text: string, vaultName: string) {
  const parts = text.split(/(\[[^\[\]\n]+?\.(?:md|pdf|pptx|docx)(?:\s+(?:p\.|slide\s)?\d+)?\])/g);
  return parts.map((part, i) => {
    const match = part.match(/^\[([^\[\]]+?)(?:\s+(?:p\.|slide\s)?\d+)?\]$/);
    if (!match) return <span key={i}>{part}</span>;
    const path = match[1];
    return (
      <a
        key={i}
        href={`obsidian://open?vault=${encodeURIComponent(vaultName)}&file=${encodeURIComponent(path)}`}
        className="animate-msg-in mx-0.5 inline-block rounded-md bg-primary/20 px-1.5 py-0.5 font-mono text-[11px] text-primary-soft transition-colors hover:bg-primary/35"
        title={`Open ${path} in Obsidian`}
      >
        {path.split("/").pop()}
      </a>
    );
  });
}

export default function ChatPanel({ variant }: { variant: "dock" | "full" }) {
  const { data: vault } = useSWR<{ name: string }>("/api/vault", fetcher);
  const { messages, busy, offline, send } = useChat();
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  const compact = variant === "dock";

  return (
    <div className={`flex min-h-0 flex-1 flex-col ${compact ? "" : "glass"}`}>
      <div className={`min-h-0 flex-1 space-y-3 overflow-y-auto ${compact ? "pr-1" : "p-5"}`}>
        {messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-3 py-4">
            <p className={`text-center text-ink-muted ${compact ? "text-xs" : "text-sm"}`}>
              Every answer cites the note it came from.
            </p>
            {!compact && (
              <div className="flex flex-wrap justify-center gap-2">
                {EXAMPLES.map((example) => (
                  <button
                    key={example}
                    onClick={() => send(example)}
                    className="rounded-xl border border-white/10 bg-white/[0.03] px-4 py-2 text-sm text-ink-muted transition-colors hover:border-primary-soft/30 hover:text-ink"
                  >
                    {example}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {messages.map((message, i) => (
          <div
            key={i}
            className={`animate-msg-in flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[85%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed ${
                message.role === "user"
                  ? "bg-gradient-to-r from-primary/30 to-accent/20 text-ink"
                  : "border border-white/10 bg-white/[0.04] text-ink-muted"
              }`}
            >
              {message.pending ? (
                <span className="flex gap-1.5 py-1" aria-label="Argus is thinking">
                  {[0, 1, 2].map((dot) => (
                    <span
                      key={dot}
                      className="h-1.5 w-1.5 animate-breathe rounded-full bg-primary-soft"
                      style={{ animationDelay: `${dot * 0.2}s` }}
                    />
                  ))}
                </span>
              ) : (
                <span className="whitespace-pre-wrap">
                  {renderWithCitations(message.text, vault?.name ?? "vault")}
                </span>
              )}
            </div>
          </div>
        ))}
        {offline && (
          <p className="text-center text-xs text-accent">
            Can’t reach Argus — is the backend running on :8000?
          </p>
        )}
        <div ref={bottomRef} />
      </div>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          send(input);
          setInput("");
        }}
        className={compact ? "pt-2" : "border-t border-white/10 p-3"}
      >
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder={busy ? "Argus is answering…" : "Ask your vault"}
            disabled={busy}
            className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm placeholder:text-ink-faint focus:border-primary-soft/50 focus:outline-none disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={busy || !input.trim()}
            className="shrink-0 rounded-xl bg-gradient-to-r from-primary to-accent px-4 py-2 font-display text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            Send
          </button>
        </div>
      </form>
    </div>
  );
}
```

- [ ] **Step 3: Create `web/components/chat/ChatDock.tsx`** — floating bubble on non-dashboard pages:

```tsx
"use client";

import { usePathname } from "next/navigation";
import { useState } from "react";
import ChatPanel from "@/components/chat/ChatPanel";

/** Floating chat button + pop-over; hidden where a chat surface already exists. */
export default function ChatDock() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  if (pathname.startsWith("/dashboard") || pathname.startsWith("/chat")) return null;

  return (
    <div className="fixed bottom-20 right-4 z-30 md:bottom-6 md:right-6">
      {open && (
        <div className="glass animate-msg-in mb-3 flex h-[28rem] w-[min(22rem,calc(100vw-2rem))] flex-col p-3">
          <ChatPanel variant="dock" />
        </div>
      )}
      <button
        aria-label={open ? "Close chat" : "Open chat"}
        onClick={() => setOpen((value) => !value)}
        className="ml-auto flex h-12 w-12 items-center justify-center rounded-full bg-gradient-to-br from-primary to-accent text-white shadow-[0_4px_24px_rgba(139,92,246,0.5)] transition-transform hover:scale-105"
      >
        <svg width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.7" viewBox="0 0 24 24">
          <path d="M21 12a8 8 0 0 1-8 8H4l2.5-2.5A8 8 0 1 1 21 12Z" />
        </svg>
      </button>
    </div>
  );
}
```

- [ ] **Step 4: Wire the provider and surfaces**

`web/app/(dashboard)/layout.tsx` becomes:

```tsx
import Sidebar from "@/components/Sidebar";
import ChatDock from "@/components/chat/ChatDock";
import { ChatProvider } from "@/lib/chat";

export default function DashboardLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <ChatProvider>
      <div className="min-h-dvh">
        <Sidebar />
        <main className="px-4 pb-24 pt-6 md:ml-64 md:px-8 md:pb-8 md:pt-8">
          <div className="mx-auto max-w-6xl">{children}</div>
        </main>
        <ChatDock />
      </div>
    </ChatProvider>
  );
}
```

`web/app/(dashboard)/chat/page.tsx` shrinks to the header + `<ChatPanel variant="full" />` (delete the moved logic):

```tsx
"use client";

import ChatPanel from "@/components/chat/ChatPanel";

export default function ChatPage() {
  return (
    <div className="flex h-[calc(100dvh-8rem)] flex-col md:h-[calc(100dvh-4rem)]">
      <header className="mb-4 animate-rise">
        <p className="eyebrow mb-2">{`// CHAT`}</p>
        <h1 className="font-display text-3xl font-semibold tracking-tight md:text-4xl">
          Ask your <span className="gradient-text">second brain</span>
        </h1>
      </header>
      <ChatPanel variant="full" />
    </div>
  );
}
```

Dashboard page: replace the CHAT placeholder with:

```tsx
<GlassCard label="CHAT" title="Ask Argus" className="flex max-h-[32rem] min-h-[20rem] flex-col lg:sticky lg:top-4">
  <ChatPanel variant="dock" />
</GlassCard>
```

(import `ChatPanel` at the top of the page).

- [ ] **Step 5: Add the animation to `web/app/globals.css`**

```css
/* Chat message entrance — transform/opacity only (cheap under backdrop-blur) */
@keyframes msg-in {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
.animate-msg-in {
  animation: msg-in 150ms ease-out both;
}
```

and add `.animate-msg-in` to the existing `prefers-reduced-motion` block's selector list.

- [ ] **Step 6: Verify + commit**

Run: `cd web && npm run lint && npm run build` — clean. Manual: send a message in the dashboard dock, navigate to `/chat` — the conversation is there; navigate to `/tasks` — floating bubble shows the same thread.

```bash
git add web/lib/chat.tsx web/components/chat "web/app/(dashboard)/layout.tsx" "web/app/(dashboard)/chat/page.tsx" "web/app/(dashboard)/dashboard/page.tsx" web/app/globals.css
git commit -m "feat(chat): shared conversation provider, dashboard dock, entrance animations"
```

---

### Task 13: Perf budget guard + interaction polish

**Files:**
- Create: `web/scripts/check-bundles.mjs`
- Modify: `web/package.json`

**Interfaces:**
- Produces: `npm run perf:budget` — runs `next build`, parses the route table, fails if any route's First Load JS exceeds 135 kB.

- [ ] **Step 1: Create `web/scripts/check-bundles.mjs`**

```js
/**
 * Perf budget: every route's First Load JS must stay under BUDGET_KB.
 * Parses `next build` output (the only place Next reports per-route size).
 */
import { execSync } from "node:child_process";

const BUDGET_KB = 135;

const out = execSync("npx next build", { encoding: "utf-8", stdio: ["ignore", "pipe", "inherit"] });
console.log(out);

const failures = [];
for (const line of out.split("\n")) {
  // e.g. "├ ○ /dashboard    12.3 kB    128 kB"
  const match = line.match(/[○ƒλ●]\s+(\/\S*)\s+[\d.]+\s*k?B\s+([\d.]+)\s*kB/);
  if (!match) continue;
  const [, route, firstLoad] = match;
  if (parseFloat(firstLoad) > BUDGET_KB) failures.push(`${route}: ${firstLoad} kB > ${BUDGET_KB} kB`);
}

if (failures.length > 0) {
  console.error(`\nPerf budget FAILED:\n  ${failures.join("\n  ")}`);
  process.exit(1);
}
console.log(`\nPerf budget OK — all routes ≤ ${BUDGET_KB} kB first-load JS.`);
```

- [ ] **Step 2: Add the script to `web/package.json`** — in `"scripts"`: `"perf:budget": "node scripts/check-bundles.mjs"`.

- [ ] **Step 3: Run it**

Run: `cd web && npm run perf:budget`
Expected: `Perf budget OK`. If `/dashboard` exceeds the budget, the offender is almost certainly an accidental eager import (recharts or react-markdown) — the dashboard must not import recharts (the heatmap is plain SVG); fix by removing/`next/dynamic`-wrapping the import and rerun.

- [ ] **Step 4: Interaction audit (measured)**

With `argus web` running (production mode), use Playwright MCP (`browser_navigate` to `http://localhost:3000/dashboard`, `browser_evaluate`) to check:
- `document.querySelectorAll('[class*="backdrop-blur"], .glass').length` — the dashboard should stay under ~12 concurrent glass surfaces; if the stat tiles pushed it over, change `Tile` to use a plain `rounded-xl border border-white/10 bg-white/[0.04]` (no `.glass`, no blur) — visually near-identical over the static aurora.
- Click nav links and a task checkbox; confirm no visible >100ms freeze (the optimistic mutate from Task 10 covers the checkbox).

Apply the Tile de-blur if needed; otherwise no change.

- [ ] **Step 5: Commit**

```bash
git add web/scripts/check-bundles.mjs web/package.json
git commit -m "perf(web): first-load JS budget guard + dashboard blur audit"
```

---

### Task 14: Playwright e2e — dashboard, CRUD, dock

**Files:**
- Modify: `web/e2e/start-backend.mjs`
- Create: `web/e2e/dashboard.spec.ts`

**Interfaces:**
- Consumes: everything shipped in Tasks 1–12; the throwaway-vault harness; Heatmap test ids from Task 9 (`data-testid="heatmap"`, `rect[data-date][data-count]`).

- [ ] **Step 1: Seed richer data in `web/e2e/start-backend.mjs`** — replace the static `e2e.md` write with:

```js
function localToday() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

fs.writeFileSync(
  path.join(vault, "20-Projects", "e2e.md"),
  `# E2E\n\n- [ ] Move the meeting 📅 2026-07-20\n- [ ] E2E check me off 📅 ${localToday()}\n- [x] already done ✅ ${localToday()}\n`,
  "utf-8",
);
```

- [ ] **Step 2: Create `web/e2e/dashboard.spec.ts`**

```ts
import { expect, test } from "@playwright/test";
import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const vault = path.join(__dirname, ".workdir", "vault");

function localToday(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

test("dashboard renders all widgets", async ({ page }) => {
  await page.goto("/dashboard");
  await expect(page.getByText("Your morning briefing")).toBeVisible();
  await expect(page.getByText("due today")).toBeVisible(); // stat tile
  await expect(page.getByText("Schedule")).toBeVisible(); // agenda
  await expect(page.getByTestId("heatmap")).toBeVisible();
  await expect(page.getByText("Latest activity")).toBeVisible();
  await expect(page.getByText("Ask Argus")).toBeVisible(); // chat dock card
});

test("heatmap counts the seeded completion", async ({ page }) => {
  await page.goto("/dashboard");
  const cell = page.locator(`[data-testid="heatmap"] rect[data-date="${localToday()}"]`);
  await expect(cell).toHaveCount(1);
  // Seeded: one ✅ today (tasks) — count must be at least 1 on the "all" metric.
  const count = Number(await cell.getAttribute("data-count"));
  expect(count).toBeGreaterThanOrEqual(1);
});

test("check-off writes ✅ to the vault after a git snapshot", async ({ page }) => {
  await page.goto("/dashboard");
  const row = page.getByText("E2E check me off").locator("..");
  await row.getByRole("button", { name: "Mark done" }).click();

  const file = path.join(vault, "20-Projects", "e2e.md");
  await expect
    .poll(() => fs.readFileSync(file, "utf-8"))
    .toContain(`- [x] E2E check me off 📅 ${localToday()} ✅ ${localToday()}`);

  const gitLog = execSync("git log --oneline", { cwd: vault, encoding: "utf-8" });
  expect(gitLog).toContain("argus: pre-apply snapshot (toggle task 20-Projects/e2e.md");
});

test("task delete removes the line, snapshot first", async ({ page }) => {
  await page.goto("/dashboard");
  page.on("dialog", (dialog) => dialog.accept());
  const row = page.getByText("Move the meeting").locator("..");
  await row.hover();
  await row.getByRole("button", { name: "Delete task" }).click();

  const file = path.join(vault, "20-Projects", "e2e.md");
  await expect.poll(() => fs.readFileSync(file, "utf-8")).not.toContain("Move the meeting");
  const gitLog = execSync("git log --oneline", { cwd: vault, encoding: "utf-8" });
  expect(gitLog).toContain("argus: pre-apply snapshot (delete task 20-Projects/e2e.md");
});

test("chat thread persists between dock and chat tab", async ({ page }) => {
  await page.goto("/dashboard");
  // No live agent in e2e: the ws will error, but the user message must survive
  // in shared state across surfaces (provider-level persistence).
  await page.getByPlaceholder("Ask your vault").fill("hello from the dock");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByRole("link", { name: "Chat" }).click();
  await expect(page).toHaveURL(/\/chat/);
  await expect(page.getByText("hello from the dock")).toBeVisible();
});
```

Note on the chat test: `ChatProvider.send` removes the pending bubble on ws error but keeps the user message — that's exactly what's asserted. If the seeded backend CAN'T accept ws (connection refused → `onerror`), the user message still renders; if this proves flaky, assert on the offline notice (`Can’t reach Argus`) instead.

- [ ] **Step 3: Run the suite**

Run: `cd web && npx playwright test`
Expected: `roundtrip.spec.ts` + `dashboard.spec.ts` all pass. Iterate on selectors if a locator is ambiguous (prefer adding `data-testid` to components over complex locators).

- [ ] **Step 4: Full verification sweep + commit**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m pytest -q
cd web && npm run lint && npm run build && npx playwright test
git add web/e2e
git commit -m "test(e2e): dashboard widgets, vault CRUD roundtrip, shared chat state"
```

---

### Task 15: Vault cleanup & structure audit (GATED — requires Ethan's confirmation)

**Files:** none in the repo — this operates on the real vault `C:\Users\ethan\Documents\Scientia` (git-committed there).

- [ ] **Step 1: Enumerate candidates (read-only)** — list and present to Ethan:
  - `15-Courses/CS000/` — QA test course (from the drag-drop bugfix session)
  - Synthetic session stubs inside `90-Meta/sessions/2026/2026-07-12-project-argus.md` (the `abc12345-test-session` and `f4f3cb33-loop-iter2` stub sections — narratives stay)
  - Stray root-level daily notes `2026-07-06.md`, `2026-07-12.md` → propose moving into `10-Daily/` **after** checking: (a) Obsidian's daily-note folder setting, (b) that `10-Daily/2026-07-12.md` doesn't already exist (it does — the briefing wrote there; if both exist, propose merging root content into the `10-Daily` one)
  - Per-folder relevance table: note count + last-modified for each top-level folder (`00-Inbox, 10-Daily, 15-Courses, 20-Projects, 30-Areas, 40-People, 50-Reference, 90-Meta, 99-Private`) — flag only empty/never-used folders; `90-Meta` and `99-Private` are always kept
- [ ] **Step 2: STOP and show Ethan the exact list. Proceed only per-item on his yes.**
- [ ] **Step 3: Apply approved items** — one vault git commit: `git add -A && git commit -m "argus: P5 vault cleanup (approved list)"` in the vault. Deletions of real note content use `git rm`/move so history preserves everything.
- [ ] **Step 4: Verify** — `argus doctor` still 5 OK / 2 WARN (credential WARNs), dashboard loads, `argus reindex` clean.
- [ ] **Step 5: Log the cleanup** to the session journal (MCP if up, direct write otherwise).

---

### Task 16: Docs, decisions, push, PR

**Files:**
- Modify: `docs/BUILD_LOG.md`, `docs/BUILD_STATE.md`, `README.md`

- [ ] **Step 1: Record decisions** in `docs/BUILD_LOG.md` following the existing D-series format: D-034 direct user CRUD via writer with CAS drift checks (vs review-queue for humans); D-035 heatmap counts tasks+notes+study+captures with git-history note events; D-036 `argus web` production launcher as the daily entry point; D-037 shared ChatProvider with dock/tab surfaces.
- [ ] **Step 2: Update `README.md`** — replace `npm run dev`-based daily-use instructions with `argus web`; document the Dashboard home and vault edit/delete; keep dev-mode instructions under a Development section. Update `docs/BUILD_STATE.md` phase ledger with P5.
- [ ] **Step 3: Final full verification**

```bash
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m pytest -q
cd web && npm run lint && npm run perf:budget && npx playwright test
```

All green, then:

```bash
git add docs README.md
git commit -m "docs: P5 exit — decisions D-034..D-037, README dashboard-first"
git push -u origin feat/p5-dashboard-revamp
```

- [ ] **Step 4: Open the PR** (no `gh` on this machine) — give Ethan the compare URL:
`https://github.com/W4sp24/Project-Argus/compare/main...feat/p5-dashboard-revamp?expand=1` with a body summarizing the spec link, the task list, and test evidence. End the body with: `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- [ ] **Step 5: Final session-journal narrative** into `90-Meta/sessions/2026/2026-07-13-project-argus.md` (MCP preferred, direct write fallback).
