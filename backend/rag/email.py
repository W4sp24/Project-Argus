"""Parse raw email text (a paste, or the contents of an ``.eml``) into parts.

This lives in ``rag`` rather than in the ingest feature because it now has two
callers that must agree: the email capture route, which turns a paste into task
proposals, and :mod:`backend.rag.extract`, which indexes a dropped ``.eml`` so
it is searchable afterwards. Two copies would drift, and the failure mode of
drift here is silent -- a mail that captures correctly but indexes as garbage.

Stdlib only, deliberately: no MIME library beyond ``email`` is worth a
dependency for what is fundamentally header-splitting.
"""

from __future__ import annotations

from email import message_from_string, policy
from typing import Any


def parse_email(text: str) -> dict[str, Any]:
    """Split raw pasted text / ``.eml`` content into headers + body.

    Returns ``{body, subject, sender, date}``. Text with none of From/Subject/
    Date is not an email at all -- it is treated as a plain body, so pasting a
    bare note still does something sensible rather than erroring.
    """
    message = message_from_string(text, policy=policy.default)
    if not (message["From"] or message["Subject"] or message["Date"]):
        return {"body": text, "subject": None, "sender": None, "date": None}
    body = ""
    try:
        part = message.get_body(preferencelist=("plain",))
        if part is not None:
            body = part.get_content()
    except Exception:  # noqa: BLE001 - malformed MIME; fall back to the payload
        body = ""
    if not body.strip():  # header-only paste or non-MIME body
        body = message.get_payload() if isinstance(message.get_payload(), str) else text
    email_date = None
    try:
        if message["Date"]:
            from email.utils import parsedate_to_datetime

            email_date = parsedate_to_datetime(message["Date"]).date().isoformat()
    except Exception:  # noqa: BLE001 - an unparseable Date is not worth failing over
        email_date = None
    return {
        "body": str(body),
        "subject": str(message["Subject"]) if message["Subject"] else None,
        "sender": str(message["From"]) if message["From"] else None,
        "date": email_date,
    }
