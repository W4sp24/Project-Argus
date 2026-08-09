"""The one allowlist HTML sanitiser in this repo.

There is no bleach/nh3/DOMPurify anywhere in Argus, and none may be added
(hard constraint on this package). Two callers need one, and their needs
look contradictory until you notice they're the same rule stated twice:

- A Custom HTML **form field** (:mod:`backend.features.automations.schema`)
  is allowed to render markup — that's the point of the field.
- A ``text`` **widget** payload is markdown, rendered client-side by
  react-markdown, which doesn't emit raw HTML on its own — but if a pushed
  payload smuggles literal HTML tags into that markdown body, they must not
  survive as live markup either.

Both positions are only consistent if markup is *never* trusted and *only
ever* rendered through this one function. So both callers — the Custom HTML
field and the ``text`` widget's body — route through :func:`sanitize_html`,
and nothing in this codebase renders raw HTML any other way.

Implemented on stdlib :class:`html.parser.HTMLParser` — an allowlist parser,
not a denylist regex. Unknown tags are dropped but their text content is
kept; ``script``/``style`` are dropped *with* their content (a regex
denylist over the source string would be trivially bypassed; a real
tokenizer is not). Malformed markup never raises — worst case it degrades to
escaped plain text.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

# --- tag/attribute allowlists ------------------------------------------------

# Inert formatting/structure only. Nothing that can load a subresource,
# execute script, or submit data (no form/input/iframe/object/embed/svg/...).
ALLOWED_TAGS = frozenset(
    {
        "p",
        "br",
        "hr",
        "b",
        "strong",
        "i",
        "em",
        "u",
        "s",
        "code",
        "pre",
        "blockquote",
        "ul",
        "ol",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "span",
        "div",
        "a",
        "img",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "small",
    }
)

# Self-closing; never pushed onto the open-tag stack, never given a closing tag.
VOID_TAGS = frozenset({"br", "hr", "img"})

# Dropped along with everything between their start and end tag. Keeping the
# *text* of a <script> body (while dropping only the tag) would re-emit the
# payload as visible text at best and be re-parsed as script by a
# subsequent, less careful consumer at worst — so the content goes too.
_DROP_WITH_CONTENT = frozenset({"script", "style"})

# Every other unrecognised or explicitly-excluded tag (input, iframe, object,
# embed, form, link, meta, base, svg, ...) is dropped but its children are
# still walked/emitted — "unknown tags are dropped, their text is kept".

# Per-tag attribute allowlist. Every tag not listed here keeps zero
# attributes at all when it's rendered (not even class/style/id).
ALLOWED_ATTRS: dict[str, frozenset[str]] = {
    "a": frozenset({"href"}),
    "img": frozenset({"src", "alt"}),
    "th": frozenset({"colspan", "rowspan"}),
    "td": frozenset({"colspan", "rowspan"}),
}

# --- URL scheme allowlisting --------------------------------------------------

# ASCII control characters (0x00-0x1F, 0x7F), stripped from anywhere in a URL
# candidate before the scheme is examined. Defeats whitespace-obfuscated
# schemes like "java\tscript:" or "java\nscript:", which otherwise collapse
# into a live "javascript:" once naively trimmed.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")

# A leading URI scheme, e.g. "javascript:", "https:", "mailto:".
_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")

# href on <a>: links may point at the web or an email address.
HREF_SCHEMES = frozenset({"http", "https", "mailto"})
# src on <img>: a subresource load never wants mailto:.
SRC_SCHEMES = frozenset({"http", "https"})


def _clean_url_candidate(value: str) -> str:
    """Entity-decode, then strip control chars/whitespace, in that order.

    Order matters: ``java&#115;cript:`` must be entity-decoded to
    ``javascript:`` *before* the scheme is read, and ``java\\tscript:``
    needs its embedded tab stripped for the same reason. Decoding first and
    stripping second catches both, including combinations of the two.
    """
    decoded = html.unescape(value)
    return _CONTROL_CHARS_RE.sub("", decoded).strip()


def _sanitize_url(value: str, *, schemes: frozenset[str]) -> str | None:
    """The safe form of ``value`` if its scheme is in ``schemes``, else ``None``.

    Protocol-relative URLs (``//evil.com``) are always rejected regardless
    of ``schemes`` — they inherit the embedding page's scheme, which is
    exactly the ambiguity an allowlist is meant to remove. A URL with no
    scheme at all (a relative path or a bare ``#anchor``) is passed through
    unchanged: it cannot smuggle a dangerous scheme.
    """
    cleaned = _clean_url_candidate(value)
    if not cleaned:
        return None
    if cleaned.startswith("//"):
        return None
    match = _SCHEME_RE.match(cleaned)
    if match is None:
        return cleaned
    scheme = match.group(1).lower()
    if scheme not in schemes:
        return None
    return cleaned


def sanitize_href(value: str) -> str | None:
    """The safe form of an ``href``/link target, or ``None`` if its scheme isn't allowed.

    Shared by :func:`sanitize_html` (for ``<a href>``) and
    :mod:`backend.features.automations.schema`'s widget-payload validation
    (for ``list`` item ``href`` values) — one scheme allowlist, one place.
    """
    return _sanitize_url(value, schemes=HREF_SCHEMES)


def sanitize_src(value: str) -> str | None:
    """The safe form of an ``src``/subresource target, or ``None`` if its scheme isn't allowed."""
    return _sanitize_url(value, schemes=SRC_SCHEMES)


def is_safe_href(value: str) -> bool:
    """True iff :func:`sanitize_href` would accept ``value``."""
    return sanitize_href(value) is not None


# --- the parser ---------------------------------------------------------------


class _AllowlistSanitizer(HTMLParser):
    """Walks the token stream and re-emits only allowlisted tags/attributes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._open_stack: list[str] = []
        # When set, we're inside a dropped-with-content element (script/style);
        # the int tracks nesting depth so a same-named descendant is handled.
        self._skip_tag: str | None = None
        self._skip_depth = 0

    # -- helpers --

    def _filtered_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        allowed = ALLOWED_ATTRS.get(tag, frozenset())
        if not allowed:
            return ""
        parts: list[str] = []
        for name, value in attrs:
            name_lower = name.lower()
            if name_lower not in allowed or value is None:
                continue
            if tag == "a" and name_lower == "href":
                safe = sanitize_href(value)
                if safe is None:
                    continue
                value = safe
            elif tag == "img" and name_lower == "src":
                safe = sanitize_src(value)
                if safe is None:
                    continue
                value = safe
            elif name_lower in ("colspan", "rowspan"):
                if not value.strip().isdigit():
                    continue
                value = value.strip()
            parts.append(f'{name_lower}="{html.escape(value, quote=True)}"')
        return (" " + " ".join(parts)) if parts else ""

    # -- HTMLParser hooks --

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()

        if self._skip_tag is not None:
            if tag == self._skip_tag:
                self._skip_depth += 1
            return

        if tag in _DROP_WITH_CONTENT:
            self._skip_tag = tag
            self._skip_depth = 1
            return

        if tag not in ALLOWED_TAGS:
            # Unknown/excluded tag: drop the tag itself, keep walking so its
            # text content is still emitted.
            return

        attr_str = self._filtered_attrs(tag, attrs)
        if tag in VOID_TAGS:
            self._out.append(f"<{tag}{attr_str} />")
        else:
            self._out.append(f"<{tag}{attr_str}>")
            self._open_stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        if self._skip_tag is not None:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth <= 0:
                    self._skip_tag = None
            return

        if tag in VOID_TAGS or tag not in ALLOWED_TAGS:
            return

        # Only close a tag that's actually open at the top of the stack.
        # A stray/mismatched end tag in broken soup is silently ignored
        # rather than corrupting the output or raising.
        if self._open_stack and self._open_stack[-1] == tag:
            self._open_stack.pop()
            self._out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skip_tag is not None:
            return
        self._out.append(html.escape(data, quote=False))

    def handle_comment(self, data: str) -> None:
        pass  # comments are dropped, not re-emitted

    def handle_decl(self, decl: str) -> None:
        pass  # <!DOCTYPE ...>, <![CDATA[...]]>, etc. — dropped

    def unknown_decl(self, data: str) -> None:
        pass

    def get_output(self) -> str:
        # Close anything still open at end-of-input (unterminated tag soup)
        # so the result is always well-formed.
        while self._open_stack:
            self._out.append(f"</{self._open_stack.pop()}>")
        return "".join(self._out)


def sanitize_html(raw: str) -> str:
    """Sanitise ``raw`` HTML down to the allowlisted tag/attribute subset.

    Never raises: malformed input degrades to safe (possibly fully-escaped)
    output rather than a crash. See the module docstring for why this is the
    *only* place markup is ever rendered from in this codebase.
    """
    if not raw:
        return ""
    parser = _AllowlistSanitizer()
    try:
        parser.feed(raw)
        parser.close()
        return parser.get_output()
    except Exception:
        # Should not happen — HTMLParser itself doesn't raise on malformed
        # markup — but a sanitiser must never be the thing that 500s, so
        # fall back to fully-escaped plain text rather than propagate.
        return html.escape(raw, quote=False)
