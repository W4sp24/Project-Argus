"""n8n workflow definitions -> the typed schema Argus renders.

n8n is the single source of truth for input schemas; nothing about a
workflow's fields is configured a second time inside Argus. This module
inspects a workflow definition (the JSON n8n's API returns for a workflow)
and produces two independent things:

- :func:`parse_workflow` — from the workflow's trigger node, what kind of
  action card it is (a form, a bare button, or not runnable at all) and, for
  a form, the typed field schema to render.
- :func:`validate_widget_payload` — from a *pushed* widget payload (a
  workflow calling back into Argus to update a dashboard tile), whether it
  matches one of the closed set of renderer kinds Argus ships, normalised
  for storage.

Both halves are pure data transformation: no HTTP, no FastAPI, no sqlite.
Markup anywhere in either path (a Custom HTML field, a ``text`` widget body)
is routed through :mod:`backend.features.automations.sanitize` — see that
module's docstring for why there is exactly one sanitiser and both callers
share it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from backend.features.automations.sanitize import sanitize_href, sanitize_html

# ============================================================================
# Part A — trigger -> form schema
# ============================================================================

TriggerKind = Literal["form", "button", "none"]

FieldType = Literal[
    "text",
    "textarea",
    "number",
    "date",
    "email",
    "password",
    "file",
    "dropdown",
    "radio",
    "checkboxes",
    "hidden",
    "custom_html",
]

# n8n's `fieldType` values (case-insensitive, sometimes camelCase) mapped to
# our stable enum. This is deliberately tolerant: n8n's own naming has
# drifted across versions ("checkbox" singular for a field that renders a
# *list* of checkboxes; "hiddenField" camelCase while everything else is
# lowercase), so keys are matched lowercased with no other normalisation.
_FIELD_TYPE_MAP: dict[str, FieldType] = {
    "text": "text",
    "textarea": "textarea",
    "number": "number",
    "date": "date",
    "email": "email",
    "password": "password",
    "file": "file",
    "dropdown": "dropdown",
    "radio": "radio",
    "checkbox": "checkboxes",
    "checkboxes": "checkboxes",
    "hiddenfield": "hidden",
    "hidden": "hidden",
    "html": "custom_html",
}

# Types whose values are picked from an option list.
_OPTION_TYPES = frozenset({"dropdown", "radio", "checkboxes"})

_FORM_TRIGGER_TYPE = "n8n-nodes-base.formTrigger"
_WEBHOOK_TRIGGER_TYPE = "n8n-nodes-base.webhook"


@dataclass(frozen=True)
class FormField:
    """One field of a parsed Form Trigger, normalised out of n8n's shape.

    ``name`` doubles as the submission key: n8n's Form Trigger has no
    separate machine name, only the label typed into the node's UI, so the
    label *is* the field's identity on both sides of the form.
    """

    name: str
    label: str
    type: FieldType
    required: bool = False
    placeholder: str | None = None
    default: str | None = None
    options: tuple[str, ...] | None = None
    multiple: bool = False
    html: str | None = None
    secret: bool = False
    hidden: bool = False
    unrecognized: bool = False
    raw_type: str | None = None


@dataclass(frozen=True)
class AutomationSchema:
    """What Argus needs to render (and, for a form, submit) one workflow's trigger."""

    kind: TriggerKind
    fields: tuple[FormField, ...]
    webhook_id: str | None
    webhook_path: str | None
    basic_auth: bool


def _normalize_field_type(raw_type: Any) -> tuple[FieldType, bool]:
    """The normalised type for a raw ``fieldType`` value, and whether it was unrecognised.

    An unrecognised value degrades to ``"text"`` rather than raising —
    a form with one odd field must still render, not take the whole
    workflow's schema down with it.
    """
    key = str(raw_type).strip().lower() if raw_type is not None else ""
    mapped = _FIELD_TYPE_MAP.get(key)
    if mapped is None:
        return "text", True
    return mapped, False


def _field_options(raw: dict[str, Any]) -> tuple[str, ...] | None:
    """Dropdown/radio/checkbox choices, from ``fieldOptions.values[].option``."""
    field_options = raw.get("fieldOptions")
    if not isinstance(field_options, dict):
        return None
    values = field_options.get("values")
    if not isinstance(values, list):
        return None
    options: list[str] = []
    for entry in values:
        if isinstance(entry, dict) and entry.get("option") is not None:
            options.append(str(entry["option"]))
        elif isinstance(entry, str):
            options.append(entry)
    return tuple(options)


def _field_default(raw: dict[str, Any]) -> str | None:
    """A field's default/prefill value.

    n8n calls this ``fieldValue`` on the node (most visibly on Hidden Field,
    where it doubles as the query-param passthrough value); ``default`` is
    accepted too as a more conventional fallback key.
    """
    value = raw.get("fieldValue", raw.get("default"))
    return None if value is None else str(value)


def _parse_one_field(raw: dict[str, Any]) -> FormField:
    raw_type = raw.get("fieldType")
    normalized_type, unrecognized = _normalize_field_type(raw_type)
    label = str(raw.get("fieldLabel", "") or "")

    html_value: str | None = None
    if normalized_type == "custom_html":
        html_value = sanitize_html(str(raw.get("html", "") or ""))

    options = _field_options(raw) if normalized_type in _OPTION_TYPES else None
    multiple = normalized_type == "checkboxes" or bool(raw.get("multiselect"))

    return FormField(
        name=label,
        label=label,
        type=normalized_type,
        required=bool(raw.get("requiredField", False)),
        placeholder=raw.get("placeholder") or None,
        default=_field_default(raw),
        options=options,
        multiple=multiple,
        html=html_value,
        secret=normalized_type == "password",
        hidden=normalized_type == "hidden",
        unrecognized=unrecognized,
        raw_type=None if raw_type is None else str(raw_type),
    )


def parse_form_fields(values: list[dict[str, Any]]) -> tuple[FormField, ...]:
    """Parse ``formFields.values[]`` into ordered :class:`FormField`\\ s.

    Ordering: required fields before optional ones, with each field's
    relative order preserved within its group (Raycast's documented
    guideline, adopted by the Argus UX doc). Hidden fields participate in
    this grouping like any other field — they are excluded from *rendering*
    (the UI skips them) but not filtered out of the returned tuple, since
    their value must still reach the submitted payload.
    """
    parsed = [_parse_one_field(raw) for raw in values if isinstance(raw, dict)]
    required = [f for f in parsed if f.required]
    optional = [f for f in parsed if not f.required]
    return tuple(required + optional)


def _has_basic_auth(parameters: dict[str, Any]) -> bool:
    """Whether the trigger node declares Basic Auth on itself.

    n8n exposes this as an ``authentication`` parameter on the node; matched
    tolerantly (case/format) since exact casing has varied across versions.
    """
    auth = parameters.get("authentication")
    if not isinstance(auth, str):
        return False
    return auth.strip().lower().replace("_", "").replace("-", "") == "basicauth"


def _find_trigger_node(definition: dict[str, Any]) -> dict[str, Any] | None:
    nodes = definition.get("nodes")
    if not isinstance(nodes, list):
        return None
    for node in nodes:
        if isinstance(node, dict) and node.get("type") in (
            _FORM_TRIGGER_TYPE,
            _WEBHOOK_TRIGGER_TYPE,
        ):
            return node
    return None


def parse_workflow(definition: dict[str, Any]) -> AutomationSchema:
    """The schema Argus renders for ``definition``'s trigger.

    - ``n8n-nodes-base.formTrigger`` -> ``kind="form"`` with the parsed fields.
    - ``n8n-nodes-base.webhook`` -> ``kind="button"``, no fields.
    - Anything else (schedule, cron, manual, none at all, ...) -> ``kind="none"``:
      the workflow may still own a dashboard widget via pushed payloads, but
      it is not something Argus can trigger from a card.

    For a form trigger, the URL is built by the caller from ``webhook_id``
    (n8n's ``/form/{webhookId}`` route). ``webhookId`` is not reliably
    present on every n8n version's exported node — when it's missing this
    still returns a full schema, just with ``webhook_id=None``, so the
    caller can mark the card as needing re-setup instead of crashing or
    quietly building a broken URL.
    """
    node = _find_trigger_node(definition)
    if node is None:
        return AutomationSchema(
            kind="none", fields=(), webhook_id=None, webhook_path=None, basic_auth=False
        )

    parameters = node.get("parameters")
    parameters = parameters if isinstance(parameters, dict) else {}
    webhook_id = node.get("webhookId")
    webhook_id = str(webhook_id) if webhook_id else None
    basic_auth = _has_basic_auth(parameters)

    if node.get("type") == _WEBHOOK_TRIGGER_TYPE:
        path = parameters.get("path")
        return AutomationSchema(
            kind="button",
            fields=(),
            webhook_id=webhook_id,
            webhook_path=str(path) if path else None,
            basic_auth=basic_auth,
        )

    # Form Trigger.
    form_fields = parameters.get("formFields")
    values = form_fields.get("values") if isinstance(form_fields, dict) else None
    fields = parse_form_fields(values if isinstance(values, list) else [])
    return AutomationSchema(
        kind="form",
        fields=fields,
        webhook_id=webhook_id,
        webhook_path=None,
        basic_auth=basic_auth,
    )


# --- workflow tag helpers ----------------------------------------------------

TAG_ASYNC = "argus:async"
TAG_CONFIRM = "argus:confirm"


def _tag_names(definition: dict[str, Any]) -> set[str]:
    """The workflow's tag names, tolerant of both n8n tag shapes.

    n8n's API has returned tags as ``[{"id": ..., "name": "argus"}]`` and,
    in older/lighter responses, as plain ``["argus"]``. Tagging is the
    *entire* registration mechanism for widgets and action flags, so both
    shapes must work identically.
    """
    tags = definition.get("tags")
    if not isinstance(tags, list):
        return set()
    names: set[str] = set()
    for tag in tags:
        if isinstance(tag, dict):
            name = tag.get("name")
            if name:
                names.add(str(name))
        elif isinstance(tag, str):
            names.add(tag)
    return names


def has_tag(definition: dict[str, Any], name: str) -> bool:
    """Whether the workflow carries tag ``name`` (either n8n tag shape)."""
    return name in _tag_names(definition)


def is_async_workflow(definition: dict[str, Any]) -> bool:
    """Whether the workflow is tagged ``argus:async``.

    Fire-and-forget: the result arrives later via a run/widget push, not in
    the trigger's HTTP response.
    """
    return has_tag(definition, TAG_ASYNC)


def requires_confirmation(definition: dict[str, Any]) -> bool:
    """Whether the workflow is tagged ``argus:confirm`` (Argus must confirm before firing it)."""
    return has_tag(definition, TAG_CONFIRM)


# ============================================================================
# Part C — widget payload validation
# ============================================================================


class WidgetValidationError(ValueError):
    """Raised when a pushed widget payload doesn't match its declared kind's shape.

    The message always names the offending field so the API layer's 422 (it
    is built there, not here — this module never imports FastAPI) is
    actionable rather than a generic "bad payload".
    """


VALID_WIDGET_KINDS: tuple[str, ...] = ("metric", "list", "table", "timeline", "text", "chart")

_VALID_KINDS_STR = ", ".join(VALID_WIDGET_KINDS)

_VALID_CHART_KINDS = ("line", "bar")


@dataclass(frozen=True)
class ValidatedWidget:
    """A pushed widget payload, validated and normalised for storage.

    Split to mirror :func:`backend.features.automations.store.upsert_widget`'s
    signature: ``kind``/``payload`` are the columns of the same name, and
    ``title``/``expected_interval_seconds`` are the two columns that
    function separately COALESCEs on. ``payload`` carries only the
    kind-specific fields (no ``widget``/``title``/``expected_interval_seconds``
    inside it) — those are already broken out.
    """

    kind: str
    payload: dict[str, Any]
    title: str | None
    expected_interval_seconds: int | None


def _require_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise WidgetValidationError(f"'{field_name}' is required and must be a non-empty string")
    return value


def _optional_str(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise WidgetValidationError(f"'{field_name}' must be a string")
    return value


def _validate_href(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise WidgetValidationError(f"'{field_name}' must be a string")
    safe = sanitize_href(value)
    if safe is None:
        raise WidgetValidationError(f"'{field_name}' has a disallowed URL scheme")
    return safe


#: Priority vocabulary, identical to `backend.vault.tasks.PRIORITY_MARKS`' names.
#: A pushed task and a vault task must be comparable by the same consumer, so a
#: workflow has to speak Argus's vocabulary rather than its own service's
#: numbering — the translation belongs in the template's expression, where the
#: source system's encoding is actually known.
_VALID_PRIORITIES = ("highest", "high", "medium", "low")

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _optional_date(value: Any, field_name: str) -> str | None:
    """An ISO ``YYYY-MM-DD`` date, or ``None``.

    Validated rather than passed through because every consumer compares these
    lexicographically against `date.isoformat()` (see
    `backend.features.tasks.router.agenda`). A `"tomorrow"` or `"12/07/2026"`
    that reached the store would not raise anywhere — it would just sort wrong,
    forever, in a panel nobody thinks to distrust.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not _ISO_DATE_RE.match(value):
        raise WidgetValidationError(f"'{field_name}' must be an ISO date (YYYY-MM-DD)")
    return value


def _optional_priority(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value.lower() not in _VALID_PRIORITIES:
        raise WidgetValidationError(
            f"'{field_name}' must be one of: {', '.join(_VALID_PRIORITIES)}"
        )
    return value.lower()


def _optional_tags(value: Any, field_name: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise WidgetValidationError(f"'{field_name}' must be a list of strings")
    return [t for t in value if t]


def _optional_bool(value: Any, field_name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise WidgetValidationError(f"'{field_name}' must be a boolean")
    return value


def _optional_id(value: Any, field_name: str) -> str | None:
    """An opaque upstream record id, normalised to a string.

    Ints are accepted and coerced: whether a service's ids arrive as `12345`
    or `"12345"` depends on the service *and* on whether the workflow's
    expression happened to stringify them. Rejecting one shape would make the
    action round-trip work or not work for reasons the user cannot see.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise WidgetValidationError(f"'{field_name}' must be a string or integer")
    text = str(value)
    return text or None


def _validate_title_and_interval(payload: dict[str, Any]) -> tuple[str | None, int | None]:
    title = _optional_str(payload.get("title"), "title")

    interval = payload.get("expected_interval_seconds")
    if interval is not None and (
        isinstance(interval, bool) or not isinstance(interval, int) or interval <= 0
    ):
        raise WidgetValidationError("'expected_interval_seconds' must be a positive integer")
    return title, interval


def _validate_metric(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "label": _require_str(payload.get("label"), "label"),
    }
    if "value" not in payload or payload.get("value") is None:
        raise WidgetValidationError("'value' is required")
    out["value"] = payload["value"]
    if (sub := _optional_str(payload.get("sub"), "sub")) is not None:
        out["sub"] = sub
    if (state := _optional_str(payload.get("state"), "state")) is not None:
        out["state"] = state
    return out


def _validate_list(payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("items")
    if not isinstance(items, list):
        raise WidgetValidationError("'items' must be a list")
    out_items: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise WidgetValidationError(f"items[{idx}] must be an object")
        entry: dict[str, Any] = {"text": _require_str(item.get("text"), f"items[{idx}].text")}
        if (sub := _optional_str(item.get("sub"), f"items[{idx}].sub")) is not None:
            entry["sub"] = sub
        if (href := _validate_href(item.get("href"), f"items[{idx}].href")) is not None:
            entry["href"] = href
        if (state := _optional_str(item.get("state"), f"items[{idx}].state")) is not None:
            entry["state"] = state
        # Task-shaped fields. Optional, because a `list` widget is still a
        # generic list — a weather forecast pushes text/sub and nothing here.
        # But `sources._tasks_from_list` maps this kind onto TaskItem for
        # TASKS.DUE, and everything it needs to do that has to survive this
        # function: a whitelist that drops `due` doesn't degrade the panel,
        # it empties it (the agenda filters undated external tasks out).
        if (due := _optional_date(item.get("due"), f"items[{idx}].due")) is not None:
            entry["due"] = due
        if (prio := _optional_priority(item.get("priority"), f"items[{idx}].priority")) is not None:
            entry["priority"] = prio
        if (tags := _optional_tags(item.get("tags"), f"items[{idx}].tags")) is not None:
            entry["tags"] = tags
        if (done := _optional_bool(item.get("done"), f"items[{idx}].done")) is not None:
            entry["done"] = done
        # The upstream record's own id — the handle an action workflow needs to
        # act back on this row (close the Todoist task the user just ticked).
        # Without it a pushed list is strictly read-only.
        if (item_id := _optional_id(item.get("id"), f"items[{idx}].id")) is not None:
            entry["id"] = item_id
        out_items.append(entry)
    return {"items": out_items}


def _validate_table(payload: dict[str, Any]) -> dict[str, Any]:
    columns = payload.get("columns")
    if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
        raise WidgetValidationError("'columns' must be a list of strings")
    rows = payload.get("rows")
    if not isinstance(rows, list) or not all(isinstance(r, list) for r in rows):
        raise WidgetValidationError("'rows' must be a list of lists")
    for idx, row in enumerate(rows):
        if len(row) != len(columns):
            raise WidgetValidationError(
                f"rows[{idx}] has {len(row)} cell(s) but there are {len(columns)} column(s) "
                "(a ragged table is a malformed payload)"
            )
    return {"columns": list(columns), "rows": [list(r) for r in rows]}


def _validate_timeline(payload: dict[str, Any]) -> dict[str, Any]:
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise WidgetValidationError("'entries' must be a list")
    out_entries: list[dict[str, Any]] = []
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise WidgetValidationError(f"entries[{idx}] must be an object")
        parsed_entry: dict[str, Any] = {
            "at": _require_str(entry.get("at"), f"entries[{idx}].at"),
            "text": _require_str(entry.get("text"), f"entries[{idx}].text"),
        }
        if (sub := _optional_str(entry.get("sub"), f"entries[{idx}].sub")) is not None:
            parsed_entry["sub"] = sub
        # `end` is not validated as a date: `at` isn't either, and a timeline
        # entry legitimately carries a datetime, a bare date, or (for a
        # non-calendar timeline) some other ordering key. Requiring a shape
        # here would reject payloads the renderer handles fine.
        if (end := _optional_str(entry.get("end"), f"entries[{idx}].end")) is not None:
            parsed_entry["end"] = end
        if (all_day := _optional_bool(entry.get("all_day"), f"entries[{idx}].all_day")) is not None:
            parsed_entry["all_day"] = all_day
        out_entries.append(parsed_entry)
    return {"entries": out_entries}


def _validate_text(payload: dict[str, Any]) -> dict[str, Any]:
    body = _require_str(payload.get("body"), "body")
    # react-markdown doesn't emit raw HTML from markdown on its own, but a
    # pushed payload could still smuggle literal tags into the body — route
    # it through the one shared sanitiser regardless, so markup really has
    # exactly one path through this codebase.
    return {"body": sanitize_html(body)}


def _validate_chart(payload: dict[str, Any]) -> dict[str, Any]:
    chart_kind = payload.get("kind")
    if chart_kind not in _VALID_CHART_KINDS:
        raise WidgetValidationError(
            f"'kind' must be one of: {', '.join(_VALID_CHART_KINDS)} (got {chart_kind!r})"
        )
    series = payload.get("series")
    if not isinstance(series, list):
        raise WidgetValidationError("'series' must be a list")
    out_series: list[dict[str, Any]] = []
    for idx, item in enumerate(series):
        if not isinstance(item, dict):
            raise WidgetValidationError(f"series[{idx}] must be an object")
        label = _require_str(item.get("label"), f"series[{idx}].label")
        points = item.get("points")
        if not isinstance(points, list) or not all(
            isinstance(p, list) and len(p) == 2 for p in points
        ):
            raise WidgetValidationError(f"series[{idx}].points must be a list of [x, y] pairs")
        out_series.append({"label": label, "points": [list(p) for p in points]})
    return {"kind": chart_kind, "series": out_series}


_KIND_VALIDATORS = {
    "metric": _validate_metric,
    "list": _validate_list,
    "table": _validate_table,
    "timeline": _validate_timeline,
    "text": _validate_text,
    "chart": _validate_chart,
}


def validate_widget_payload(payload: dict[str, Any]) -> ValidatedWidget:
    """Validate and normalise a pushed widget payload, or raise :class:`WidgetValidationError`.

    ``payload`` declares its own renderer via a ``widget`` key naming one of
    the closed set of kinds Argus ships (:data:`VALID_WIDGET_KINDS`). An
    unknown or missing kind fails loudly here, at push time, with a message
    naming the valid kinds — the alternative is a widget that silently
    renders as an empty box, discovered hours later.
    """
    if not isinstance(payload, dict):
        raise WidgetValidationError("payload must be an object")

    kind = payload.get("widget")
    validator = _KIND_VALIDATORS.get(kind) if isinstance(kind, str) else None
    if validator is None:
        raise WidgetValidationError(
            f"unknown widget kind {kind!r}; must be one of: {_VALID_KINDS_STR}"
        )

    title, expected_interval_seconds = _validate_title_and_interval(payload)
    normalized_payload = validator(payload)

    return ValidatedWidget(
        kind=kind,
        payload=normalized_payload,
        title=title,
        expected_interval_seconds=expected_interval_seconds,
    )
