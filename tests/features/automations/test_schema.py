"""Tests for the n8n trigger -> form schema parser and widget payload validation."""

from __future__ import annotations

from typing import Any

import pytest

from backend.features.automations import schema

# --- fixtures -----------------------------------------------------------------


def _field(field_type: str, label: str, *, required: bool = False, **extra: Any) -> dict[str, Any]:
    return {"fieldLabel": label, "fieldType": field_type, "requiredField": required, **extra}


def _form_trigger_node(values: list[dict[str, Any]], **params_extra: Any) -> dict[str, Any]:
    return {
        "type": "n8n-nodes-base.formTrigger",
        "webhookId": "form-webhook-id",
        "parameters": {"formFields": {"values": values}, **params_extra},
    }


def _webhook_node(**params_extra: Any) -> dict[str, Any]:
    return {
        "type": "n8n-nodes-base.webhook",
        "webhookId": "webhook-id-1",
        "parameters": {"path": "run-it", **params_extra},
    }


# All 12 field types, one realistic n8n-shaped export.
_ALL_TWELVE_VALUES = [
    _field("text", "Name"),
    _field("textarea", "Notes"),
    _field("number", "Count"),
    _field("date", "When"),
    _field("email", "Email"),
    _field("password", "Secret"),
    _field("file", "Attachment"),
    _field(
        "dropdown", "Choice", fieldOptions={"values": [{"option": "One"}, {"option": "Two"}]}
    ),
    _field(
        "radio", "Pick", fieldOptions={"values": [{"option": "Red"}, {"option": "Blue"}]}
    ),
    _field(
        "checkbox",
        "Toppings",
        fieldOptions={"values": [{"option": "Cheese"}, {"option": "Olives"}]},
    ),
    _field("hiddenField", "TraceId", fieldValue="abc123"),
    _field("html", "Banner", html="<b>hi</b><script>alert(1)</script>"),
]

_TYPE_BY_LABEL = {
    "Name": "text",
    "Notes": "textarea",
    "Count": "number",
    "When": "date",
    "Email": "email",
    "Secret": "password",
    "Attachment": "file",
    "Choice": "dropdown",
    "Pick": "radio",
    "Toppings": "checkboxes",
    "TraceId": "hidden",
    "Banner": "custom_html",
}


# --- Part A: trigger detection -------------------------------------------------


def test_all_twelve_field_types_parse_to_the_right_normalised_type() -> None:
    fields = schema.parse_form_fields(_ALL_TWELVE_VALUES)
    by_label = {f.label: f.type for f in fields}
    assert by_label == _TYPE_BY_LABEL
    assert len(fields) == 12


def test_workflow_with_no_recognised_trigger_is_not_runnable() -> None:
    definition = {"nodes": [{"type": "n8n-nodes-base.cron", "parameters": {}}]}
    result = schema.parse_workflow(definition)
    assert result.kind == "none"
    assert result.fields == ()
    assert result.webhook_id is None


def test_workflow_with_no_nodes_at_all_is_not_runnable() -> None:
    assert schema.parse_workflow({}).kind == "none"


def test_webhook_trigger_is_a_button_with_zero_fields_and_the_right_path() -> None:
    definition = {"nodes": [_webhook_node(path="fire-me")]}
    result = schema.parse_workflow(definition)
    assert result.kind == "button"
    assert result.fields == ()
    assert result.webhook_path == "fire-me"
    assert result.webhook_id == "webhook-id-1"


def test_form_trigger_missing_webhook_id_still_parses_with_none() -> None:
    node = _form_trigger_node([_field("text", "Name")])
    del node["webhookId"]
    definition = {"nodes": [node]}

    result = schema.parse_workflow(definition)

    assert result.kind == "form"
    assert result.webhook_id is None
    assert len(result.fields) == 1


def test_form_trigger_returns_kind_form() -> None:
    definition = {"nodes": [_form_trigger_node([_field("text", "Name", required=True)])]}
    result = schema.parse_workflow(definition)
    assert result.kind == "form"
    assert result.webhook_id == "form-webhook-id"


# --- ordering -------------------------------------------------------------


def test_required_before_optional_preserving_relative_order_within_group() -> None:
    values = [
        _field("text", "opt1", required=False),
        _field("text", "req1", required=True),
        _field("text", "opt2", required=False),
        _field("text", "req2", required=True),
    ]
    fields = schema.parse_form_fields(values)
    assert [f.label for f in fields] == ["req1", "req2", "opt1", "opt2"]


def test_hidden_fields_remain_in_the_returned_tuple() -> None:
    values = [
        _field("text", "visible", required=True),
        _field("hiddenField", "hid", required=False, fieldValue="q"),
    ]
    fields = schema.parse_form_fields(values)
    labels = {f.label for f in fields}
    assert "hid" in labels
    hidden_field = next(f for f in fields if f.label == "hid")
    assert hidden_field.hidden is True
    assert hidden_field.default == "q"


# --- options / special handling -----------------------------------------


def test_dropdown_options_extracted_from_field_options_values() -> None:
    values = [
        _field(
            "dropdown", "Choice", fieldOptions={"values": [{"option": "A"}, {"option": "B"}]}
        )
    ]
    fields = schema.parse_form_fields(values)
    assert fields[0].options == ("A", "B")


def test_radio_options_extracted() -> None:
    values = [
        _field("radio", "Pick", fieldOptions={"values": [{"option": "Red"}, {"option": "Blue"}]})
    ]
    fields = schema.parse_form_fields(values)
    assert fields[0].options == ("Red", "Blue")


def test_checkbox_options_extracted_and_marked_multiple() -> None:
    values = [
        _field(
            "checkbox",
            "Toppings",
            fieldOptions={"values": [{"option": "Cheese"}, {"option": "Olives"}]},
        )
    ]
    fields = schema.parse_form_fields(values)
    assert fields[0].options == ("Cheese", "Olives")
    assert fields[0].multiple is True


def test_password_field_marked_secret() -> None:
    fields = schema.parse_form_fields([_field("password", "Secret")])
    assert fields[0].secret is True


def test_custom_html_field_is_sanitized_before_leaving_this_module() -> None:
    fields = schema.parse_form_fields(
        [_field("html", "Banner", html="<b>hi</b><script>alert(1)</script>")]
    )
    assert fields[0].html == "<b>hi</b>"
    assert "script" not in fields[0].html


def test_unrecognized_field_type_degrades_to_text_rather_than_raising() -> None:
    fields = schema.parse_form_fields([_field("some-future-type", "Mystery")])
    assert len(fields) == 1
    assert fields[0].type == "text"
    assert fields[0].unrecognized is True


@pytest.mark.parametrize(
    "raw_type",
    ["TEXT", "Textarea", "DROPDOWN", "HiddenField", "HIDDENFIELD", "Html", "Checkbox"],
)
def test_field_type_matching_is_case_insensitive(raw_type: str) -> None:
    fields = schema.parse_form_fields([_field(raw_type, "x")])
    assert fields[0].unrecognized is False


# --- basic auth ------------------------------------------------------------


def test_basic_auth_detected_on_form_trigger() -> None:
    node = _form_trigger_node([_field("text", "Name")], authentication="basicAuth")
    result = schema.parse_workflow({"nodes": [node]})
    assert result.basic_auth is True


def test_basic_auth_absent_by_default() -> None:
    node = _form_trigger_node([_field("text", "Name")])
    result = schema.parse_workflow({"nodes": [node]})
    assert result.basic_auth is False


def test_basic_auth_detected_on_webhook_trigger() -> None:
    node = _webhook_node(authentication="basicAuth")
    result = schema.parse_workflow({"nodes": [node]})
    assert result.basic_auth is True


# --- tag helpers -------------------------------------------------------------


def test_has_tag_dict_shape() -> None:
    definition = {"tags": [{"id": "1", "name": "argus"}, {"id": "2", "name": "argus:async"}]}
    assert schema.has_tag(definition, "argus") is True
    assert schema.has_tag(definition, "argus:async") is True
    assert schema.has_tag(definition, "argus:confirm") is False


def test_has_tag_plain_string_shape() -> None:
    definition = {"tags": ["argus", "argus:confirm"]}
    assert schema.has_tag(definition, "argus:confirm") is True
    assert schema.has_tag(definition, "argus:async") is False


def test_has_tag_no_tags_key() -> None:
    assert schema.has_tag({}, "argus") is False


def test_is_async_workflow_and_requires_confirmation_predicates() -> None:
    definition = {"tags": [{"name": "argus:async"}]}
    assert schema.is_async_workflow(definition) is True
    assert schema.requires_confirmation(definition) is False

    definition2 = {"tags": ["argus:confirm"]}
    assert schema.is_async_workflow(definition2) is False
    assert schema.requires_confirmation(definition2) is True


# --- Part C: widget payload validation ----------------------------------------


def test_valid_metric_payload() -> None:
    v = schema.validate_widget_payload({"widget": "metric", "label": "CPU", "value": 42})
    assert v.kind == "metric"
    assert v.payload == {"label": "CPU", "value": 42}


def test_valid_list_payload() -> None:
    v = schema.validate_widget_payload(
        {
            "widget": "list",
            "items": [{"text": "a", "sub": "s", "href": "https://x.com", "state": "ok"}],
        }
    )
    assert v.kind == "list"
    assert v.payload["items"][0]["href"] == "https://x.com"


def test_list_item_keeps_its_task_fields() -> None:
    """These are whitelisted, not passed through. Anything absent from
    `_validate_list` is dropped silently, and `sources._tasks_from_list` reads
    every one of these — a drop here empties TASKS.DUE rather than degrading
    it, because the agenda filters out an external task with no `due`."""
    v = schema.validate_widget_payload(
        {
            "widget": "list",
            "items": [
                {
                    "text": "a",
                    "due": "2026-08-05",
                    "priority": "highest",
                    "tags": ["work", "urgent"],
                    "done": False,
                    "id": "7291",
                }
            ],
        }
    )
    item = v.payload["items"][0]
    assert item["due"] == "2026-08-05"
    assert item["priority"] == "highest"
    assert item["tags"] == ["work", "urgent"]
    assert item["done"] is False
    assert item["id"] == "7291"


def test_list_item_id_accepts_an_integer_and_normalises_it() -> None:
    """Whether a service's ids arrive as `12345` or `"12345"` depends on the
    service *and* on whether the workflow's expression stringified them —
    rejecting one shape would make completion work or not for invisible
    reasons."""
    v = schema.validate_widget_payload({"widget": "list", "items": [{"text": "a", "id": 12345}]})
    assert v.payload["items"][0]["id"] == "12345"


def test_list_item_priority_is_normalised_and_a_foreign_scale_is_rejected() -> None:
    """Todoist's own `4` must be translated in the workflow, not here: this
    module cannot know which service's scale a number came from."""
    v = schema.validate_widget_payload(
        {"widget": "list", "items": [{"text": "a", "priority": "HIGH"}]}
    )
    assert v.payload["items"][0]["priority"] == "high"

    with pytest.raises(schema.WidgetValidationError, match=r"items\[0\]\.priority"):
        schema.validate_widget_payload({"widget": "list", "items": [{"text": "a", "priority": 4}]})


def test_list_item_due_must_be_an_iso_date() -> None:
    """Consumers compare `due` lexicographically against `date.isoformat()`, so
    prose would not raise anywhere — it would just sort wrong forever."""
    with pytest.raises(schema.WidgetValidationError, match=r"items\[0\]\.due"):
        schema.validate_widget_payload(
            {"widget": "list", "items": [{"text": "a", "due": "tomorrow"}]}
        )


def test_list_item_tags_must_be_strings() -> None:
    with pytest.raises(schema.WidgetValidationError, match=r"items\[0\]\.tags"):
        schema.validate_widget_payload({"widget": "list", "items": [{"text": "a", "tags": [1, 2]}]})


def test_list_item_still_drops_unknown_fields() -> None:
    """Widening the whitelist must not turn it into a pass-through."""
    v = schema.validate_widget_payload(
        {"widget": "list", "items": [{"text": "a", "assignee": "someone", "project_id": 9}]}
    )
    assert v.payload["items"][0] == {"text": "a"}


def test_timeline_entry_keeps_end_and_all_day() -> None:
    """Without `end` every event is zero-length: no duration label, and
    `insights._event_hours` counts a full calendar as 0 hours."""
    v = schema.validate_widget_payload(
        {
            "widget": "timeline",
            "entries": [
                {
                    "at": "2026-08-05T09:00:00",
                    "end": "2026-08-05T09:30:00",
                    "text": "Standup",
                    "all_day": False,
                }
            ],
        }
    )
    entry = v.payload["entries"][0]
    assert entry["end"] == "2026-08-05T09:30:00"
    assert entry["all_day"] is False


def test_timeline_all_day_must_be_a_boolean() -> None:
    with pytest.raises(schema.WidgetValidationError, match=r"entries\[0\]\.all_day"):
        schema.validate_widget_payload(
            {"widget": "timeline", "entries": [{"at": "x", "text": "y", "all_day": "yes"}]}
        )


def test_timeline_entry_still_drops_unknown_fields() -> None:
    v = schema.validate_widget_payload(
        {"widget": "timeline", "entries": [{"at": "x", "text": "y", "organizer": "someone"}]}
    )
    assert v.payload["entries"][0] == {"at": "x", "text": "y"}


def test_valid_table_payload() -> None:
    v = schema.validate_widget_payload(
        {"widget": "table", "columns": ["a", "b"], "rows": [[1, 2], [3, 4]]}
    )
    assert v.payload == {"columns": ["a", "b"], "rows": [[1, 2], [3, 4]]}


def test_valid_timeline_payload() -> None:
    v = schema.validate_widget_payload(
        {"widget": "timeline", "entries": [{"at": "2026-01-01T00:00:00Z", "text": "did a thing"}]}
    )
    assert v.payload["entries"][0]["text"] == "did a thing"


def test_valid_text_payload() -> None:
    v = schema.validate_widget_payload({"widget": "text", "body": "**hello**"})
    assert v.payload == {"body": "**hello**"}


def test_valid_chart_payload() -> None:
    v = schema.validate_widget_payload(
        {
            "widget": "chart",
            "kind": "line",
            "series": [{"label": "s1", "points": [[0, 1], [1, 2]]}],
        }
    )
    assert v.payload["kind"] == "line"


def test_title_and_expected_interval_carried_for_every_kind() -> None:
    v = schema.validate_widget_payload(
        {
            "widget": "metric",
            "label": "x",
            "value": 1,
            "title": "My Metric",
            "expected_interval_seconds": 120,
        }
    )
    assert v.title == "My Metric"
    assert v.expected_interval_seconds == 120


def test_unknown_widget_kind_names_the_valid_kinds() -> None:
    with pytest.raises(schema.WidgetValidationError) as exc_info:
        schema.validate_widget_payload({"widget": "bogus"})
    message = str(exc_info.value)
    for kind in schema.VALID_WIDGET_KINDS:
        assert kind in message


def test_missing_widget_kind_names_the_valid_kinds() -> None:
    with pytest.raises(schema.WidgetValidationError):
        schema.validate_widget_payload({"label": "x", "value": 1})


def test_ragged_table_rejected() -> None:
    with pytest.raises(schema.WidgetValidationError):
        schema.validate_widget_payload(
            {"widget": "table", "columns": ["a", "b"], "rows": [[1, 2], [3]]}
        )


@pytest.mark.parametrize("bad_interval", [-1, 0, 1.5, "60", True])
def test_bad_expected_interval_seconds_rejected(bad_interval: Any) -> None:
    with pytest.raises(schema.WidgetValidationError):
        schema.validate_widget_payload(
            {
                "widget": "metric",
                "label": "x",
                "value": 1,
                "expected_interval_seconds": bad_interval,
            }
        )


def test_positive_int_expected_interval_seconds_accepted() -> None:
    v = schema.validate_widget_payload(
        {"widget": "metric", "label": "x", "value": 1, "expected_interval_seconds": 30}
    )
    assert v.expected_interval_seconds == 30


def test_bad_chart_kind_rejected() -> None:
    with pytest.raises(schema.WidgetValidationError):
        schema.validate_widget_payload({"widget": "chart", "kind": "pie", "series": []})


def test_javascript_href_inside_list_item_rejected() -> None:
    with pytest.raises(schema.WidgetValidationError):
        schema.validate_widget_payload(
            {"widget": "list", "items": [{"text": "a", "href": "javascript:alert(1)"}]}
        )


def test_metric_missing_required_field_rejected() -> None:
    with pytest.raises(schema.WidgetValidationError):
        schema.validate_widget_payload({"widget": "metric", "value": 1})


def test_list_items_not_a_list_rejected() -> None:
    with pytest.raises(schema.WidgetValidationError):
        schema.validate_widget_payload({"widget": "list", "items": "nope"})


def test_empty_list_items_is_valid() -> None:
    v = schema.validate_widget_payload({"widget": "list", "items": []})
    assert v.payload == {"items": []}


def test_payload_not_a_dict_rejected() -> None:
    with pytest.raises(schema.WidgetValidationError):
        schema.validate_widget_payload([])  # type: ignore[arg-type]
