"""Regression tests for the Todoist connector's failure containment.

Before this fix, ``list_tasks()`` propagated any import or API/network
failure straight out of the connector. Once a token was stored, that meant
every caller (`/api/agenda`, `/api/tasks`, the planner) started returning
HTTP 500 the moment Todoist rejected the token or the process couldn't
reach api.todoist.com — see `backend.connectors.ConnectorUnavailable` for
the containment this module now provides.
"""

from __future__ import annotations

import pytest

from backend.connectors import ConnectorUnavailable, todoist
from backend.vault.tasks import TaskItem


class _FakeDue:
    def __init__(self, date: str) -> None:
        self.date = date


class _FakeTask:
    def __init__(self, content: str, due: str | None = None, priority: int = 1,
                 labels: list[str] | None = None) -> None:
        self.content = content
        self.due = _FakeDue(due) if due else None
        self.priority = priority
        self.labels = labels or []


class _FakeApi:
    def __init__(self, tasks: list[_FakeTask]) -> None:
        self._tasks = tasks

    def get_tasks(self):
        return self._tasks


class _RaisingApi:
    def get_tasks(self):
        raise RuntimeError("401 Unauthorized")


def test_list_tasks_unconfigured_returns_empty(monkeypatch):
    """No stored token -> [] without ever touching the Todoist client."""
    monkeypatch.setattr(todoist, "_stored_token", lambda: None)

    assert todoist.list_tasks() == []


def test_list_tasks_maps_injected_fake_api():
    """An injected ``api`` is used as-is; the whole test suite depends on this."""
    fake = _FakeApi([_FakeTask("Buy milk", due="2026-08-01", priority=4, labels=["errand"])])

    tasks = todoist.list_tasks(fake)

    assert tasks == [
        TaskItem(
            text="Buy milk",
            due="2026-08-01",
            priority="high",
            tags=["errand"],
            source="todoist",
        )
    ]


def test_list_tasks_wraps_api_failure_in_connector_unavailable():
    """A token IS stored (or injected) but the API call fails — must not be a bare 500."""
    with pytest.raises(ConnectorUnavailable):
        todoist.list_tasks(_RaisingApi())


def test_list_tasks_safe_returns_message_instead_of_raising():
    tasks, message = todoist.list_tasks_safe(_RaisingApi())

    assert tasks == []
    assert message is not None and "Todoist" in message


def test_list_tasks_safe_returns_tasks_and_no_message_on_success():
    fake = _FakeApi([_FakeTask("Buy milk")])

    tasks, message = todoist.list_tasks_safe(fake)

    assert message is None
    assert tasks[0].text == "Buy milk"


def test_list_tasks_wraps_missing_client_library(monkeypatch):
    """A stored token but no importable Todoist client is a broken build, not a
    silent []: it must surface as `ConnectorUnavailable`, mirroring the gcal
    regression test for the same lazy-import guard."""
    monkeypatch.setattr(todoist, "_stored_token", lambda: "some-token")

    import builtins

    real_import = builtins.__import__

    def blocking_import(name, *args, **kwargs):
        if name == "todoist_api_python" or name.startswith("todoist_api_python."):
            raise ImportError(f"{name} is not installed (simulated broken build)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocking_import)

    with pytest.raises(ConnectorUnavailable):
        todoist.list_tasks()
