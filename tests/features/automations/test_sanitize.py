"""Tests for the allowlist HTML sanitiser.

Security-relevant, so thorough on purpose: every case here is a concrete
"this string must never survive as live markup/script" assertion, not just
a shape check.
"""

from __future__ import annotations

import pytest

from backend.features.automations.sanitize import (
    is_safe_href,
    sanitize_href,
    sanitize_html,
    sanitize_src,
)

# --- script/style: dropped with their entire content -------------------------


def test_script_tag_and_body_removed_entirely() -> None:
    out = sanitize_html("<script>alert(1)</script>")
    assert out == ""
    assert "script" not in out
    assert "alert" not in out


def test_style_tag_and_body_removed_entirely() -> None:
    out = sanitize_html("<style>body { color: red; }</style>")
    assert out == ""
    assert "color" not in out


def test_script_surrounded_by_safe_content_only_drops_the_script() -> None:
    out = sanitize_html("<p>before</p><script>alert(1)</script><p>after</p>")
    assert "alert" not in out
    assert "<script" not in out
    assert "<p>before</p>" in out
    assert "<p>after</p>" in out


# --- event-handler attributes -------------------------------------------------


def test_img_onerror_stripped_but_img_kept() -> None:
    out = sanitize_html('<img src="x.png" onerror="alert(1)">')
    assert "onerror" not in out
    assert "<img" in out
    assert 'src="x.png"' in out


def test_on_star_handlers_stripped_across_several_tags() -> None:
    raw = (
        '<div onclick="a()">d</div>'
        '<span onmouseover="b()">s</span>'
        '<a href="https://x.com" onfocus="c()">a</a>'
        '<table onload="d()"><tr onhover="e()"><td>t</td></tr></table>'
    )
    out = sanitize_html(raw)
    for handler in ("onclick", "onmouseover", "onfocus", "onload", "onhover"):
        assert handler not in out
    assert "<div>d</div>" in out
    assert "<span>s</span>" in out
    assert '<a href="https://x.com">a</a>' in out


# --- dangerous URL schemes: href/src ------------------------------------------


def test_javascript_href_dropped_or_neutralized() -> None:
    out = sanitize_html('<a href="javascript:alert(1)">click</a>')
    assert "javascript:" not in out
    assert "click" in out  # link text survives, just not the dangerous target


def test_entity_obfuscated_javascript_scheme_is_caught() -> None:
    out = sanitize_html('<a href="java&#115;cript:alert(1)">click</a>')
    assert "javascript:" not in out
    assert "java" not in out or "href" not in out  # no live javascript: href


def test_whitespace_obfuscated_javascript_scheme_is_caught() -> None:
    out = sanitize_html('<a href="java\tscript:alert(1)">click</a>')
    assert "javascript:" not in out


def test_data_url_rejected() -> None:
    out = sanitize_html('<a href="data:text/html,<script>alert(1)</script>">x</a>')
    assert "data:" not in out
    assert "<script" not in out


def test_protocol_relative_url_rejected() -> None:
    out = sanitize_html('<a href="//evil.com/steal">x</a>')
    assert "//evil.com" not in out


def test_https_and_mailto_permitted() -> None:
    out = sanitize_html('<a href="https://example.com/page">x</a>')
    assert 'href="https://example.com/page"' in out

    out2 = sanitize_html('<a href="mailto:someone@example.com">x</a>')
    assert 'href="mailto:someone@example.com"' in out2


def test_sanitize_href_scheme_allowlist() -> None:
    assert sanitize_href("https://example.com") == "https://example.com"
    assert sanitize_href("http://example.com") == "http://example.com"
    assert sanitize_href("mailto:a@b.com") == "mailto:a@b.com"
    assert sanitize_href("javascript:alert(1)") is None
    assert sanitize_href("data:text/html,x") is None
    assert sanitize_href("//evil.com") is None
    assert sanitize_href("vbscript:msgbox(1)") is None


def test_sanitize_src_rejects_mailto() -> None:
    # mailto is only meaningful for a link target, never a subresource load.
    assert sanitize_src("https://example.com/x.png") == "https://example.com/x.png"
    assert sanitize_src("mailto:a@b.com") is None


def test_is_safe_href_matches_sanitize_href() -> None:
    assert is_safe_href("https://example.com") is True
    assert is_safe_href("javascript:alert(1)") is False


# --- allowed tags survive, unknown tags drop but keep text -------------------


def test_allowed_formatting_tags_survive_with_text() -> None:
    raw = "<p>para</p><b>bold</b><i>italic</i><ul><li>item</li></ul>"
    out = sanitize_html(raw)
    assert "<p>para</p>" in out
    assert "<b>bold</b>" in out
    assert "<i>italic</i>" in out
    assert "<ul><li>item</li></ul>" in out


def test_unknown_tags_dropped_but_text_kept() -> None:
    out = sanitize_html("<bogus>hello</bogus><weird-thing>world</weird-thing>")
    assert "<bogus" not in out
    assert "<weird-thing" not in out
    assert "hello" in out
    assert "world" in out


def test_disallowed_structural_tags_dropped_with_or_without_content() -> None:
    for tag, attrs in (
        ("iframe", ' src="https://evil.com"'),
        ("object", ' data="https://evil.com"'),
        ("embed", ' src="https://evil.com"'),
        ("form", ' action="https://evil.com"'),
        ("input", ' type="text"'),
        ("link", ' rel="stylesheet" href="https://evil.com"'),
        ("meta", ' http-equiv="refresh"'),
        ("base", ' href="https://evil.com"'),
        ("svg", ""),
    ):
        out = sanitize_html(f"<{tag}{attrs}>payload</{tag}>")
        assert f"<{tag}" not in out


# --- attribute allowlisting per tag -------------------------------------------


def test_attributes_outside_the_allowlist_are_stripped() -> None:
    out = sanitize_html('<div id="x" class="y" data-foo="z">hi</div>')
    assert out == "<div>hi</div>"


def test_table_cell_colspan_rowspan_survive() -> None:
    out = sanitize_html('<table><tr><td colspan="2" rowspan="3">c</td></tr></table>')
    assert 'colspan="2"' in out
    assert 'rowspan="3"' in out


def test_colspan_rejects_non_numeric_value() -> None:
    out = sanitize_html('<td colspan="2;javascript:alert(1)">c</td>')
    assert "colspan" not in out
    assert "javascript" not in out
    assert out == "<td>c</td>"


# --- malformed/nested markup never raises -------------------------------------


def test_malformed_unclosed_tag_soup_does_not_raise() -> None:
    out = sanitize_html("<b><i>unclosed<div>still going")
    assert isinstance(out, str)


def test_broken_attribute_soup_does_not_raise() -> None:
    out = sanitize_html('<a href="unterminated <b>bold</a>')
    assert isinstance(out, str)


def test_nested_script_smuggling_does_not_yield_a_live_script_tag() -> None:
    out = sanitize_html("<scr<script>ipt>alert(1)</script>")
    assert "<script>" not in out.lower()
    assert "<script " not in out.lower()


def test_double_script_close_smuggling_does_not_raise_or_execute() -> None:
    out = sanitize_html("<script>a</script><script>alert(1)</script>")
    assert "alert" not in out
    assert "<script" not in out


def test_empty_and_none_like_input() -> None:
    assert sanitize_html("") == ""


def test_text_content_is_escaped() -> None:
    out = sanitize_html("<p>1 < 2 & 3 > 1</p>")
    assert "<p>" in out
    # the literal '<' / '&' inside the text node must not reopen a tag
    assert "1 &lt; 2 &amp; 3 &gt; 1" in out


def test_void_elements_self_close() -> None:
    out = sanitize_html("<p>a<br>b<hr>c</p>")
    assert "<br />" in out
    assert "<hr />" in out


# A single sweep asserting that no known-dangerous token survives ANY payload.
# The per-behaviour tests above check specific mechanisms; this one is the
# blunt backstop — if a future change to the parser reintroduces a live
# handler, scheme or executable tag through some path nobody thought to name,
# this catches it without needing a test written for that specific path.
_XSS_PAYLOADS = (
    "<script>alert(1)</script>",
    "<SCRIPT>alert(1)</SCRIPT>",
    "<scr<script>ipt>alert(1)</scr</script>ipt>",
    "<img src=x onerror=alert(1)>",
    "<img src=x OnErRoR=alert(1)>",
    '<a href="javascript:alert(1)">x</a>',
    '<a href="java&#115;cript:alert(1)">x</a>',
    '<a href="java\tscript:alert(1)">x</a>',
    '<a href="java\nscript:alert(1)">x</a>',
    '<a href="JaVaScRiPt:alert(1)">x</a>',
    '<a href=" javascript:alert(1)">x</a>',
    '<a href="&#106;avascript:alert(1)">x</a>',
    '<a href="data:text/html,<script>alert(1)</script>">x</a>',
    '<a href="//evil.com">x</a>',
    '<a href="vbscript:msgbox(1)">x</a>',
    '<iframe src="javascript:alert(1)"></iframe>',
    "<svg/onload=alert(1)>",
    "<body onload=alert(1)>hi</body>",
    '<div style="background:url(javascript:alert(1))">x</div>',
    '<style>@import "evil.css";</style>',
    '<form action="x"><input name=a></form>',
    '<object data="evil.swf"></object>',
    '<a href="x" onclick="alert(1)">x</a>',
    "<p onmouseover=alert(1)>hover</p>",
    "<!--[if IE]><script>alert(1)</script><![endif]-->",
    "<a href=javascript:alert(1)>unquoted</a>",
    '<img src="x" alt="&quot;><script>alert(1)</script>">',
)

_FORBIDDEN_TOKENS = (
    "javascript:",
    "vbscript:",
    "data:text/html",
    "onerror",
    "onload",
    "onclick",
    "onmouseover",
    "<script",
    "<style",
    "<iframe",
    "<object",
    "<form",
    "<input",
    "<svg",
)


@pytest.mark.parametrize("payload", _XSS_PAYLOADS)
def test_no_dangerous_token_survives_any_payload(payload: str) -> None:
    out = sanitize_html(payload).lower()
    leaked = [token for token in _FORBIDDEN_TOKENS if token in out]
    assert not leaked, f"{payload!r} leaked {leaked} as {out!r}"


def test_legitimate_markup_survives_the_sweep() -> None:
    """The sweep above must not pass merely by destroying everything."""
    out = sanitize_html(
        '<p>hi <a href="https://ok.com">link</a> '
        '<img src="https://ok.com/i.png" alt="pic"></p>'
    )
    assert 'href="https://ok.com"' in out
    assert 'src="https://ok.com/i.png"' in out
    assert "hi" in out and "link" in out
