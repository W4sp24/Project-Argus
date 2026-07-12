"""Todoist (read-only in P2). Token lives in the OS keyring (I4).

Setup: Todoist → Settings → Integrations → Developer → API token, then
``friday connect todoist <token>``.
"""

from __future__ import annotations

from backend.tasks.parser import TaskItem

KEYRING_SERVICE = "friday-todoist"
KEYRING_USER = "token"


def _stored_token() -> str | None:
    import keyring

    return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)


def configured() -> bool:
    try:
        return _stored_token() is not None
    except Exception:
        return False


def connect(token: str) -> None:
    """Store the personal API token in the keyring (I4)."""
    import keyring

    if not token.strip():
        raise ValueError("empty Todoist token")
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, token.strip())


def list_tasks(api=None) -> list[TaskItem]:
    """Open Todoist tasks mapped to FRIDAY's task shape; [] when unconfigured."""
    if api is None:
        token = _stored_token()
        if token is None:
            return []
        from todoist_api_python.api import TodoistAPI

        api = TodoistAPI(token)

    priority_map = {4: "high", 3: "medium", 2: "low", 1: None}
    items: list[TaskItem] = []
    results = api.get_tasks()
    # todoist-api-python v3 returns a paginator of lists; v2 returns a flat list.
    pages = results if isinstance(results, list) else list(results)
    flat = (
        pages if pages and not isinstance(pages[0], list) else [t for page in pages for t in page]
    )
    for task in flat:
        due = getattr(getattr(task, "due", None), "date", None)
        items.append(
            TaskItem(
                text=task.content,
                due=str(due) if due else None,
                priority=priority_map.get(getattr(task, "priority", 1)),
                tags=list(getattr(task, "labels", []) or []),
                source="todoist",
            )
        )
    return items
