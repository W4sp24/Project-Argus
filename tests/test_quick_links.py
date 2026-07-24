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
    sanitize_icon_kind,
    sanitize_icon_value,
    sanitize_label,
    sanitize_url,
    update_link,
)

# A 1x1 transparent PNG as a data URI — the shape the renderer canvas produces.
_PNG_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
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


# --- sanitize_icon_kind ------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_sanitize_icon_kind_none_or_empty_becomes_none(raw) -> None:
    assert sanitize_icon_kind(raw) is None


@pytest.mark.parametrize(("raw", "expected"), [("preset", "preset"), ("IMAGE", "image")])
def test_sanitize_icon_kind_accepts_allowlist_case_insensitively(raw, expected) -> None:
    assert sanitize_icon_kind(raw) == expected


@pytest.mark.parametrize("raw", ["url", "local_path", "svg", "glyph"])
def test_sanitize_icon_kind_rejects_unknown(raw) -> None:
    with pytest.raises(QuickLinksError):
        sanitize_icon_kind(raw)


# --- sanitize_icon_value -----------------------------------------------------


def test_sanitize_icon_value_none_kind_returns_none() -> None:
    assert sanitize_icon_value(None, "anything") is None


def test_sanitize_icon_value_accepts_preset_key() -> None:
    assert sanitize_icon_value("preset", "github") == "github"
    assert sanitize_icon_value("preset", "book-open_2") == "book-open_2"


@pytest.mark.parametrize("bad", ["Bad Key", "has space", "emoji★", "x" * 33, "path/traversal", ""])
def test_sanitize_icon_value_rejects_bad_preset_key(bad) -> None:
    with pytest.raises(QuickLinksError):
        sanitize_icon_value("preset", bad)


def test_sanitize_icon_value_accepts_small_png_data_uri() -> None:
    assert sanitize_icon_value("image", _PNG_DATA_URI) == _PNG_DATA_URI


def test_sanitize_icon_value_rejects_non_png_data_uri() -> None:
    with pytest.raises(QuickLinksError):
        sanitize_icon_value("image", "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=")
    with pytest.raises(QuickLinksError):
        sanitize_icon_value("image", "https://cdn.example.com/logo.png")


def test_sanitize_icon_value_rejects_oversized_image() -> None:
    huge = "data:image/png;base64," + ("A" * 30_000)
    with pytest.raises(QuickLinksError):
        sanitize_icon_value("image", huge)


def test_sanitize_icon_value_rejects_invalid_base64() -> None:
    with pytest.raises(QuickLinksError):
        sanitize_icon_value("image", "data:image/png;base64,not!valid!base64!")


def test_sanitize_icon_value_required_when_kind_set() -> None:
    with pytest.raises(QuickLinksError):
        sanitize_icon_value("preset", None)


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


# --- custom icon roundtrip ---------------------------------------------------


def test_create_link_roundtrips_preset_icon(conn) -> None:
    row = create_link(
        conn,
        label="GitHub",
        url="https://github.com",
        icon=None,
        icon_kind="preset",
        icon_value="github",
    )
    assert row["icon_kind"] == "preset"
    assert row["icon_value"] == "github"
    assert list_links(conn)[0]["icon_value"] == "github"


def test_create_link_roundtrips_image_icon(conn) -> None:
    row = create_link(
        conn,
        label="Custom",
        url="https://a.com",
        icon=None,
        icon_kind="image",
        icon_value=_PNG_DATA_URI,
    )
    assert row["icon_kind"] == "image"
    assert row["icon_value"] == _PNG_DATA_URI


def test_create_link_rejects_bad_icon_before_insert(conn) -> None:
    with pytest.raises(QuickLinksError):
        create_link(
            conn,
            label="Bad",
            url="https://a.com",
            icon=None,
            icon_kind="image",
            icon_value="https://evil.com/x.png",
        )
    assert list_links(conn) == []


def test_update_link_sets_and_clears_custom_icon(conn) -> None:
    created = create_link(
        conn,
        label="Link",
        url="https://a.com",
        icon="★",
        icon_kind="preset",
        icon_value="book",
    )
    # Switch preset -> image.
    updated = update_link(conn, created["id"], icon_kind="image", icon_value=_PNG_DATA_URI)
    assert updated["icon_kind"] == "image"
    assert updated["icon_value"] == _PNG_DATA_URI

    # Clear the custom icon back to the glyph (explicit None clears to NULL).
    cleared = update_link(conn, created["id"], icon_kind=None, icon_value=None)
    assert cleared["icon_kind"] is None
    assert cleared["icon_value"] is None
    assert cleared["icon"] == "★"  # glyph untouched by the icon-pair clear


def test_update_link_reorder_leaves_icon_untouched(conn) -> None:
    created = create_link(
        conn,
        label="Link",
        url="https://a.com",
        icon=None,
        icon_kind="preset",
        icon_value="star",
    )
    # A reorder that only passes sort_order must not clear the icon (the API
    # relies on this by forwarding only the fields the client actually sent).
    update_link(conn, created["id"], sort_order=9)
    row = list_links(conn)[0]
    assert row["icon_kind"] == "preset"
    assert row["icon_value"] == "star"
    assert row["sort_order"] == 9


# --- schema migration --------------------------------------------------------


def test_init_schema_migrates_pre_icon_quick_links_table(tmp_path: Path) -> None:
    """A DB whose quick_links predates the icon columns gains them without loss."""
    connection = connect(tmp_path / "old.db")
    # Simulate the pre-custom-icon schema (no icon_kind/icon_value).
    connection.executescript(
        """
        CREATE TABLE quick_links (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            label      TEXT NOT NULL,
            url        TEXT NOT NULL,
            icon       TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    connection.execute(
        "INSERT INTO quick_links (label, url, icon, sort_order)"
        " VALUES ('Old', 'https://a.com', '★', 1)"
    )
    connection.commit()

    init_schema(connection)  # should ALTER in the new columns, not drop the row

    columns = {row["name"] for row in connection.execute("PRAGMA table_info(quick_links)")}
    assert {"icon_kind", "icon_value"} <= columns

    links = list_links(connection)
    assert len(links) == 1
    assert links[0]["label"] == "Old"
    assert links[0]["icon"] == "★"
    assert links[0]["icon_kind"] is None
    assert links[0]["icon_value"] is None

    # And the migrated table now accepts a custom icon.
    created = create_link(
        connection,
        label="New",
        url="https://b.com",
        icon=None,
        icon_kind="preset",
        icon_value="link",
    )
    assert created["icon_value"] == "link"
    connection.close()
