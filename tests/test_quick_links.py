"""Tests for backend/quick_links.py: URL/label/icon sanitization and CRUD."""

from pathlib import Path

import pytest

from backend.db import connect, init_schema
from backend.quick_links import (
    QuickLinksError,
    create_link,
    delete_link,
    list_links,
    sanitize_icon,
    sanitize_label,
    sanitize_url,
    update_link,
)

# --- fixtures --------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path: Path):
    connection = connect(tmp_path / "argus.db")
    init_schema(connection)
    yield connection
    connection.close()


# --- sanitize_url: accept & normalize ---------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://example.com", "https://example.com"),
        ("example.com", "https://example.com"),
    ],
)
def test_sanitize_url_accepts_and_normalizes_exact(raw: str, expected: str) -> None:
    assert sanitize_url(raw) == expected


def test_sanitize_url_upgrades_http_to_https() -> None:
    result = sanitize_url("http://example.com/x")
    assert result.startswith("https://")
    assert result == "https://example.com/x"


def test_sanitize_url_preserves_host_case_and_path() -> None:
    result = sanitize_url("EXAMPLE.com/SomePath")
    assert result.startswith("https://")
    assert "EXAMPLE.com" in result
    assert "SomePath" in result


# --- sanitize_url: reject ----------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "java\tscript:alert(1)",
        "java\nscript:alert(1)",
        "data:text/html,x",
        "file:///etc/passwd",
        "vbscript:msgbox",
        "mailto:a@b.com",
        "tel:+1",
        "//evil.com",
        "ftp://host/x",
        # Control-char obfuscated dangerous scheme: the embedded NUL is
        # stripped before the scheme check runs, so this collapses into a
        # plain "javascript:" URI and must still be rejected.
        "java\x00script:alert(1)",
    ],
)
def test_sanitize_url_rejects(raw: str) -> None:
    with pytest.raises(QuickLinksError):
        sanitize_url(raw)


def test_sanitize_url_rejects_none() -> None:
    with pytest.raises(QuickLinksError):
        sanitize_url(None)  # type: ignore[arg-type]


# --- sanitize_label ----------------------------------------------------------


def test_sanitize_label_trims_whitespace() -> None:
    assert sanitize_label("  Google  ") == "Google"


def test_sanitize_label_collapses_internal_whitespace() -> None:
    # The implementation joins on split(), so internal runs of whitespace
    # collapse to single spaces (not just a ticket assumption -- verified
    # against the actual `" ".join(raw.split())` behavior).
    assert sanitize_label("My   Link\tName") == "My Link Name"


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_sanitize_label_rejects_empty_or_whitespace(raw: str) -> None:
    with pytest.raises(QuickLinksError):
        sanitize_label(raw)


def test_sanitize_label_rejects_none() -> None:
    with pytest.raises(QuickLinksError):
        sanitize_label(None)  # type: ignore[arg-type]


def test_sanitize_label_caps_very_long_input() -> None:
    long_label = "x" * 500
    result = sanitize_label(long_label)
    assert len(result) == 80  # _MAX_LABEL_LEN in backend/quick_links.py
    assert result == "x" * 80


# --- sanitize_icon -----------------------------------------------------------


@pytest.mark.parametrize("raw", [None, ""])
def test_sanitize_icon_none_or_empty_becomes_none(raw) -> None:
    assert sanitize_icon(raw) is None


def test_sanitize_icon_single_glyph_passes_through() -> None:
    assert sanitize_icon("\U0001f4ce") == "\U0001f4ce"


def test_sanitize_icon_whitespace_only_becomes_none() -> None:
    assert sanitize_icon("   ") is None


def test_sanitize_icon_caps_overly_long_input() -> None:
    result = sanitize_icon("x" * 100)
    assert len(result) == 8  # _MAX_ICON_LEN in backend/quick_links.py
    assert result == "x" * 8


# --- CRUD + reorder roundtrip -------------------------------------------------


def test_create_link_assigns_increasing_sort_order(conn) -> None:
    first = create_link(conn, label="Google", url="https://google.com", icon=None)
    second = create_link(conn, label="Bing", url="https://bing.com", icon=None)

    assert first["sort_order"] == 1
    assert second["sort_order"] == 2


def test_create_link_bad_url_raises_before_insert(conn) -> None:
    with pytest.raises(QuickLinksError):
        create_link(conn, label="Bad", url="javascript:alert(1)", icon=None)

    assert list_links(conn) == []


def test_list_links_returns_ordered_by_sort_order(conn) -> None:
    create_link(conn, label="First", url="https://a.com", icon=None)
    create_link(conn, label="Second", url="https://b.com", icon=None)
    create_link(conn, label="Third", url="https://c.com", icon=None)

    links = list_links(conn)
    assert [link["label"] for link in links] == ["First", "Second", "Third"]
    assert [link["sort_order"] for link in links] == [1, 2, 3]


def test_update_link_changes_label(conn) -> None:
    created = create_link(conn, label="Old Label", url="https://a.com", icon=None)

    updated = update_link(conn, created["id"], label="New Label")

    assert updated["label"] == "New Label"
    assert list_links(conn)[0]["label"] == "New Label"


def test_update_link_swaps_sort_order_and_list_reflects_it(conn) -> None:
    first = create_link(conn, label="First", url="https://a.com", icon=None)
    second = create_link(conn, label="Second", url="https://b.com", icon=None)

    # Swap: give "First" a higher sort_order than "Second".
    update_link(conn, first["id"], sort_order=5)

    links = list_links(conn)
    assert [link["label"] for link in links] == ["Second", "First"]
    assert links[0]["id"] == second["id"]
    assert links[1]["sort_order"] == 5


def test_delete_link_removes_row(conn) -> None:
    first = create_link(conn, label="First", url="https://a.com", icon=None)
    second = create_link(conn, label="Second", url="https://b.com", icon=None)

    delete_link(conn, first["id"])

    remaining = list_links(conn)
    assert len(remaining) == 1
    assert remaining[0]["id"] == second["id"]


def test_update_link_nonexistent_id_raises(conn) -> None:
    with pytest.raises(QuickLinksError, match="quick link not found"):
        update_link(conn, 99999, label="Nope")


def test_delete_link_nonexistent_id_raises(conn) -> None:
    # delete_link calls _get_link_row first, which raises QuickLinksError
    # for an unknown id -- same documented behavior as update_link.
    with pytest.raises(QuickLinksError, match="quick link not found"):
        delete_link(conn, 99999)
