"""Todoist (read-only in P2). Token lives in the OS keyring (I4).

Setup: Todoist → Settings → Integrations → Developer → API token, then
``argus connect todoist <token>``.
"""

from __future__ import annotations

from backend.connectors import ConnectorUnavailable
from backend.vault.tasks import TaskItem

KEYRING_SERVICE = "argus-todoist"
KEYRING_USER = "token"


def _stored_token() -> str | None:
    import keyring

    return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)


def connection_state() -> str:
    """"present" | "absent" | "unknown" — see :mod:`backend.agent.credentials`.

    An unreadable keyring is not a missing token; saying so would tell the user
    to re-run ``argus connect todoist`` with a token they already stored.
    """
    from backend.agent.credentials import KEY_ABSENT, KEY_PRESENT, KEY_UNKNOWN

    try:
        return KEY_PRESENT if _stored_token() is not None else KEY_ABSENT
    except Exception:  # noqa: BLE001 - keyring backends raise many types
        return KEY_UNKNOWN


def configured() -> bool:
    from backend.agent.credentials import KEY_PRESENT

    return connection_state() == KEY_PRESENT


def connect(token: str) -> None:
    """Store the personal API token in the keyring (I4)."""
    import keyring

    if not token.strip():
        raise ValueError("empty Todoist token")
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, token.strip())


def disconnect() -> None:
    """Forget the stored token. Best-effort — already-absent is success."""
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception:  # noqa: BLE001 - already gone, or the keyring is unusable
        pass


def list_tasks(api=None) -> list[TaskItem]:
    """Open Todoist tasks mapped to Argus's task shape; [] when unconfigured.

    Raises `ConnectorUnavailable` when a token IS stored but the client
    library can't be imported (a broken build) or the API call itself fails
    (expired/rejected token, network outage, a Todoist outage) — see
    `list_tasks_safe` for callers that must degrade instead of raise.
    """
    if api is None:
        try:
            token = _stored_token()
        except Exception as exc:  # noqa: BLE001 - keyring backends raise many types
            # An unreadable keyring is a connector that cannot be used, not a
            # crash: without this the error is not `ConnectorUnavailable`, so
            # it sails through `list_tasks_safe` and turns GET /api/agenda into
            # a 500 -- a dashboard with no tasks at all because one optional
            # connector could not read a token. `connection_state` above
            # already makes the same judgement.
            raise ConnectorUnavailable(f"the OS keyring could not be read: {exc}") from exc
        if token is None:
            return []
        try:
            from todoist_api_python.api import TodoistAPI
        except ImportError as exc:
            raise ConnectorUnavailable(
                f"Todoist client library is not installed: {exc}"
            ) from exc

        api = TodoistAPI(token)

    priority_map = {4: "high", 3: "medium", 2: "low", 1: None}
    items: list[TaskItem] = []
    try:
        results = api.get_tasks()
        # todoist-api-python v3 returns a paginator of lists; v2 returns a flat list.
        pages = results if isinstance(results, list) else list(results)
    except Exception as exc:  # noqa: BLE001 - any API/network failure is "unavailable"
        raise ConnectorUnavailable(f"Todoist request failed: {exc}") from exc
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


def list_tasks_safe(api=None) -> tuple[list[TaskItem], str | None]:
    """`list_tasks`, but for callers that must degrade rather than fail.

    A calendar widget, the task board, or the planner must not take down the
    whole dashboard because Todoist is unreachable or a build is missing the
    client library. Returns ``(tasks, None)`` on success and ``([], message)``
    when the connector is unavailable — ``message`` is short and safe to show
    a user directly.
    """
    try:
        return list_tasks(api), None
    except ConnectorUnavailable as exc:
        return [], str(exc)
