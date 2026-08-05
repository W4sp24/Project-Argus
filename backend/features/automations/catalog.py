"""The shipped workflow template gallery: bundled n8n workflow JSON that a
user can install into their own n8n instance with one click.

Nothing here talks to n8n or FastAPI — that is
:mod:`backend.features.automations.router`'s job (the two ``/templates``
routes). This module is pure: read the bundled JSON, describe it
(:class:`Template`), and substitute the two placeholders every template's
callback URLs are written against (:func:`render_definition`).

**Why every trigger sets an explicit respond mode.** A Form Trigger or
Webhook node's default response behaviour in n8n is *Immediately* — it
answers the HTTP caller with 200 the instant the trigger fires, before any
downstream node (the HTTP Request that actually does the work, the Google
Calendar node that actually writes the event) has run. Argus's run dispatch
(``router._dispatch_result``) treats a 2xx response as the workflow having
*succeeded*; it has no way to distinguish "the workflow finished and
succeeded" from "the workflow just started and n8n said 200 out of habit".
That makes *Immediately* the one silently-wrong failure mode in this whole
feature: every action would report success on the wire regardless of what
actually happened downstream, and nothing in Argus could ever detect it.
So every bundled template that uses a Form Trigger sets
``responseMode: "lastNode"`` (n8n's "Workflow Finishes" option) and every
template that uses a Webhook node sets ``responseMode: "responseNode"``
(the response is sent explicitly, once the workflow chooses to send it).
This is enforced by ``tests/features/automations/test_catalog.py`` per
template, by name, so a new template cannot forget it and pass anyway.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

# Resolved relative to this file, not the process CWD. `list_templates` /
# `load_definition` must work identically from a PyInstaller-frozen build,
# where there is no repo checkout and the CWD is wherever the packaged .exe
# happened to be launched from — `__file__` still resolves correctly inside
# a frozen bundle because PyInstaller preserves the package's on-disk layout
# under `_MEIPASS` (or the app's install dir for a onedir build).
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

TemplateKind = Literal["display", "action"]

#: Placeholders every template's JSON carries in place of the real callback
#: URL/token, substituted at install time by `render_definition`.
_PLACEHOLDER_URL = "{{ARGUS_URL}}"
_PLACEHOLDER_TOKEN = "{{ARGUS_TOKEN}}"
#: The prefix `render_definition`'s post-substitution guard scans for — catches
#: an unsubstituted `{{ARGUS_URL}}` / `{{ARGUS_TOKEN}}` *and* any future
#: `{{ARGUS_*}}` placeholder someone adds to a template without also adding
#: it to this module's substitution list.
_PLACEHOLDER_PREFIX = "{{ARGUS_"


class UnknownTemplate(KeyError):  # noqa: N818 - name mandated by the chunk brief
    """Raised by `load_definition`/`render_definition` for an unrecognised template id."""

    def __init__(self, template_id: str) -> None:
        super().__init__(template_id)
        self.template_id = template_id

    def __str__(self) -> str:
        return f"unknown template {self.template_id!r}"


class TemplateRenderError(ValueError):
    """Raised when a rendered template still contains an `{{ARGUS_...}}` placeholder.

    This must be unreachable in practice (every placeholder this module knows
    about is substituted before the check runs), which is exactly why it
    exists: it is the backstop against a template author adding a new
    `{{ARGUS_SOMETHING}}` marker to a template JSON file without teaching
    `render_definition` about it. Installing a workflow with a literal,
    un-substituted placeholder baked into its URL/header would fail *later*,
    the first time n8n tries to actually call out — silently, from Argus's
    point of view. Raising here instead means that failure happens at
    install time, loudly, in a place a developer will actually see it.
    """


@dataclass(frozen=True)
class Template:
    """One entry in the shipped gallery — the catalog metadata for a bundled
    n8n workflow, kept separate from the workflow JSON itself (`load_definition`)
    so nothing non-standard has to be smuggled into a document that gets
    POSTed verbatim to n8n's own API.
    """

    id: str
    name: str
    description: str
    #: "display" (pushes/refreshes a dashboard widget) or "action" (does a
    #: real-world thing — sends a form onward, writes a calendar event).
    kind: TemplateKind
    #: The widget slug this template's push target is (`kind="display"` only).
    widget_slug: str | None
    #: What Argus-native code path this template supersedes, if any — e.g.
    #: "backend/connectors/gcal.py". `None` for a genuinely new capability.
    replaces: str | None
    #: Credential names the user must grant *in n8n* before this template is
    #: useful (e.g. `("Google Calendar",)`). Never a secret Argus holds or
    #: could hold — see `router.install_template`'s docstring for why that
    #: hand-off has to happen in n8n's own UI and can't be automated away.
    requires: tuple[str, ...]


#: Static catalog metadata, keyed by template id. Deliberately not folded
#: into the JSON files themselves: those are real n8n workflow documents,
#: POSTed to n8n's `/workflows` endpoint mostly as-is (after
#: `render_definition`'s substitution), and an unrecognised top-level key is
#: one more way for a real n8n instance to reject the request. `name` is not
#: repeated here — it is read from each definition's own top-level `"name"`
#: at catalog-build time, so the gallery and the installed workflow can never
#: disagree about what the workflow is called.
_TEMPLATE_META: dict[str, dict[str, Any]] = {
    "google-calendar": {
        "description": (
            "Polls Google Calendar on a schedule and pushes your upcoming events "
            "onto the dashboard as a timeline widget. Replaces the periodic reads "
            "backend/connectors/gcal.py used to do."
        ),
        "kind": "display",
        "widget_slug": "calendar",
        "replaces": "backend/connectors/gcal.py",
        "requires": ("Google Calendar",),
    },
    "todoist": {
        "description": (
            "Polls Todoist on a schedule and pushes your active tasks onto the "
            "dashboard as a list widget. Replaces the periodic reads "
            "backend/connectors/todoist.py used to do."
        ),
        "kind": "display",
        "widget_slug": "tasks",
        "replaces": "backend/connectors/todoist.py",
        "requires": ("Todoist",),
    },
    "weather": {
        "description": (
            "Polls a public weather API (Open-Meteo — no API key required) and "
            "pushes the current temperature onto the dashboard as a metric "
            "widget. A new capability; nothing in Argus read weather before."
        ),
        "kind": "display",
        "widget_slug": "weather",
        "replaces": None,
        "requires": (),
    },
    "mobile-capture": {
        "description": (
            "A form, reachable from your phone, for jotting a quick note with "
            "optional tags straight into Argus. A new capability."
        ),
        "kind": "action",
        "widget_slug": None,
        "replaces": None,
        "requires": (),
    },
    "calendar-insert": {
        "description": (
            "A form for adding a new Google Calendar event — the write side of "
            "the suggestion-approval path in backend/vault/writer.py, which "
            "today calls gcal.insert_event directly. Once backend/connectors/ "
            "is deleted, this template is the *only* thing left that can write "
            "an event back to a calendar; there is no read-only fallback for "
            "the write path the way there is for the display templates above."
        ),
        "kind": "action",
        "widget_slug": None,
        "replaces": "backend/connectors/gcal.py",
        "requires": ("Google Calendar",),
    },
}

#: Stable iteration order for `list_templates` — dict insertion order of
#: `_TEMPLATE_META` above, named explicitly rather than relied upon so the
#: two can't silently drift apart if one is reordered without the other.
_TEMPLATE_IDS: tuple[str, ...] = (
    "google-calendar",
    "todoist",
    "weather",
    "mobile-capture",
    "calendar-insert",
)


def _template_path(template_id: str) -> Path:
    return TEMPLATES_DIR / f"{template_id}.json"


def load_definition(template_id: str) -> dict[str, Any]:
    """The raw, un-rendered n8n workflow JSON for ``template_id``.

    Read lazily on every call rather than cached at import time: this is a
    handful of small on-disk files, re-parsed occasionally (gallery list,
    one install click) — not a hot path worth caching, and caching would
    mean a corrected template file on disk wouldn't take effect without a
    process restart.
    """
    if template_id not in _TEMPLATE_META:
        raise UnknownTemplate(template_id)
    path = _template_path(template_id)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise UnknownTemplate(template_id) from exc
    return json.loads(text)


def list_templates() -> tuple[Template, ...]:
    """The full shipped gallery, in a stable order.

    Each entry's `name` is read from its bundled JSON's own top-level
    `"name"` field; everything else comes from `_TEMPLATE_META`.
    """
    templates = []
    for template_id in _TEMPLATE_IDS:
        definition = load_definition(template_id)
        meta = _TEMPLATE_META[template_id]
        name = str(definition.get("name") or template_id)
        templates.append(
            Template(
                id=template_id,
                name=name,
                description=meta["description"],
                kind=meta["kind"],
                widget_slug=meta["widget_slug"],
                replaces=meta["replaces"],
                requires=meta["requires"],
            )
        )
    return tuple(templates)


def render_definition(template_id: str, *, callback_url: str, token: str) -> dict[str, Any]:
    """``load_definition(template_id)`` with `{{ARGUS_URL}}`/`{{ARGUS_TOKEN}}`
    substituted throughout, wherever in the structure they appear.

    **Approach: dump the whole definition to JSON text, do the substitution
    on the text, parse it back.** The brief for this considered walking the
    node tree by hand instead; that was rejected in favour of the
    dump/replace/load round trip for two reasons: every placeholder in every
    bundled template sits inside a JSON *string* value (a URL, a header
    value built from `"Bearer {{ARGUS_TOKEN}}"`) — never as a bare/structural
    value on its own — so there is no risk here of the naive-string-replace
    failure mode the brief warned about (corrupting a non-string value by
    turning it into unparseable text); and a whole-document text substitution
    is one code path that is trivially correct for a value nested at any
    depth, instead of a recursive walk that has to be re-verified against
    every template's shape.

    The substituted values are run through `json.dumps(...)[1:-1]` before
    being spliced in — that produces exactly the JSON-string-escaped form of
    the value (quotes, backslashes, control characters all correctly
    escaped) without its surrounding quote characters, which is what belongs
    in place of a placeholder that is *already* sitting inside an existing
    pair of quotes in the dumped text. Skipping this step is the one way
    this approach could actually corrupt the document — a token containing a
    literal `"` or `\\` spliced in unescaped would terminate the JSON string
    early — so it is not optional.
    """
    definition = load_definition(template_id)
    text = json.dumps(definition, ensure_ascii=False)

    safe_url = json.dumps(callback_url, ensure_ascii=False)[1:-1]
    safe_token = json.dumps(token, ensure_ascii=False)[1:-1]
    text = text.replace(_PLACEHOLDER_URL, safe_url).replace(_PLACEHOLDER_TOKEN, safe_token)

    if _PLACEHOLDER_PREFIX in text:
        raise TemplateRenderError(
            f"template {template_id!r} still contains an unsubstituted "
            f"{_PLACEHOLDER_PREFIX}...}}}} placeholder after rendering — "
            "installing it would silently post to nowhere"
        )

    return json.loads(text)


__all__ = [
    "TEMPLATES_DIR",
    "Template",
    "TemplateKind",
    "TemplateRenderError",
    "UnknownTemplate",
    "list_templates",
    "load_definition",
    "render_definition",
]
