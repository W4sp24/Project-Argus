"""Tests for the debounce queue (no watchdog observer needed)."""

from backend.rag.watcher import DebounceQueue


def test_debounce_holds_until_quiet() -> None:
    queue = DebounceQueue(debounce=2.0)
    queue.push("10-Daily/2026-07-12.md")

    assert queue.pop_ready(now=1.0) == []


def test_debounce_flushes_after_window_and_dedupes() -> None:
    queue = DebounceQueue(debounce=0.0)
    queue.push("a.md")
    queue.push("a.md")
    queue.push("b.md")

    ready = queue.pop_ready()
    assert sorted(ready) == ["a.md", "b.md"]
    assert queue.pop_ready() == [], "flush must clear the queue"


def test_debounce_ignores_non_indexable_paths() -> None:
    queue = DebounceQueue(debounce=0.0)
    queue.push("99-Private/diary.md")
    queue.push(".obsidian/workspace.json")

    assert queue.pop_ready() == []
