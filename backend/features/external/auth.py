"""Bearer-token auth, rate limiting, and body-size enforcement for the
inbound external surface.

This module is the second of the three things holding the line described in
the package docstring: a publicly-resolvable tunnel points at
:func:`backend.features.external.app.create_external_app`, and everything it
serves must be provably safe to expose. Nothing here talks to FastAPI
directly (that's :mod:`backend.features.external.router` /
:mod:`backend.features.external.app`) — this module is pure token/rate-limit
logic so it can be unit tested without spinning up an app.

Token storage mirrors :mod:`backend.connectors.todoist` and every model API
key: the OS keyring only (invariant I4), never a file, never the JSON
registry. **Multi-instance (B4):** Argus can register N n8n instances, each
with its own bearer token, so the keyring reference is derived from the
instance id (:func:`token_ref_for`) rather than a single fixed string.
Revoking one instance's token (deleting the instance, or rotating its token)
must never affect any other instance's — every function here that reads or
writes a token takes ``instance_id`` and touches only that instance's
keyring entry.
"""

from __future__ import annotations

import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from backend.agent.credentials import (
    KEY_ABSENT,
    KeyringUnavailableError,
    get_key,
    key_state,
    store_key,
)


def token_ref_for(instance_id: str) -> str:
    """Keyring reference for one n8n instance's inbound bearer token."""
    return f"external:token:{instance_id}"

#: Bytes of entropy in a generated token (secrets.token_urlsafe measures its
#: argument in bytes before base64url-encoding it, not output characters).
TOKEN_BYTES = 32

#: 256 KiB — a JSON body over this is refused before it is parsed (413).
MAX_BODY_BYTES = 256 * 1024

#: Token bucket shape: 60 requests/minute.
RATE_LIMIT_CAPACITY = 60
RATE_LIMIT_WINDOW_SECONDS = 60.0


def generate_token(instance_id: str) -> str:
    """Generate a new 32-random-byte, URL-safe bearer token for ``instance_id``
    and persist it.

    Overwrites whatever was stored before for this instance only — see
    :func:`rotate_token`, which is just this function under a name that says
    what calling it again does. Another instance's token, stored under its
    own :func:`token_ref_for`, is untouched.
    """
    token = secrets.token_urlsafe(TOKEN_BYTES)
    store_key(token_ref_for(instance_id), token)
    return token


def get_token(instance_id: str) -> str | None:
    """``instance_id``'s currently active token, or ``None`` when none has
    been generated (or the keyring cannot be read — a broken keyring must
    never look like a valid token to a caller foolish enough to compare
    against ``None``, so this is deliberately not the same as "token would
    verify")."""
    try:
        return get_key(token_ref_for(instance_id))
    except KeyringUnavailableError:
        return None


def rotate_token(instance_id: str) -> str:
    """Generate a new token for ``instance_id``, invalidating its old one
    immediately.

    ``store_key`` (via the keyring) replaces the credential outright — there
    is no window where both the old and new token verify, and no separate
    "delete old, then write new" step that could leave neither stored. Only
    ``instance_id``'s keyring entry is touched.
    """
    return generate_token(instance_id)


def token_state(instance_id: str) -> str:
    """Tri-state presence of ``instance_id``'s stored token — see
    :func:`backend.agent.credentials.key_state`."""
    return key_state(token_ref_for(instance_id))


def resolve_token(presented: str | None, instances: list[dict[str, Any]]) -> str | None:
    """Which registered instance, if any, ``presented`` authenticates as.

    Returns that instance's ``id``, or ``None`` when it matches none of them
    (including when ``instances`` is empty).

    **Constant-time against every candidate, not just until the first hit.**
    Returning as soon as one instance's token matches would leak, via timing,
    which instance matched and how many were checked before it — an attacker
    holding a valid token for instance C could learn how many instances
    precede it in the registry, or narrow down which slot their guess landed
    in, purely from response latency. So every instance's token is compared
    with :func:`hmac.compare_digest`, and only after the full pass does this
    function decide what to return; nothing here short-circuits on a match.

    Compares bytes, not `str`, for the same reason :func:`hmac.compare_digest`
    always has here: it raises ``TypeError`` on a `str` containing non-ASCII,
    and ``presented`` arrives straight off a public network. A token of "é"
    must fail to resolve like any other wrong token, not raise and become a
    500 that tells an attacker they found an unhandled path.
    """
    if not presented:
        return None
    presented_bytes = presented.encode("utf-8")

    matched: str | None = None
    for instance in instances:
        instance_id = instance.get("id")
        if not instance_id:
            continue
        try:
            actual = get_key(token_ref_for(instance_id))
        except KeyringUnavailableError:
            actual = None
        if not actual:
            continue
        if hmac.compare_digest(presented_bytes, actual.encode("utf-8")):
            matched = instance_id
    return matched


@dataclass
class RateLimiter:
    """A single global token bucket: 60 requests/minute, injected clock.

    One bucket for the whole surface, not one per caller — Argus registers
    exactly one external instance, so there is exactly one legitimate caller,
    and a global bucket is what makes "repeated auth failures are rate
    limited too" trivially true: every request ticks the same bucket before
    its own auth is checked, whether that request ever had a valid token or
    not.

    ``clock`` defaults to :func:`time.monotonic` (never wall-clock — immune
    to NTP jumps and DST). Tests inject a fake clock instead of sleeping.
    """

    capacity: int = RATE_LIMIT_CAPACITY
    window_seconds: float = RATE_LIMIT_WINDOW_SECONDS
    clock: Callable[[], float] = field(default=time.monotonic)
    _tokens: float = field(init=False, repr=False)
    _last: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._tokens = float(self.capacity)
        self._last = self.clock()

    def allow(self) -> bool:
        """Consume one token if available; returns whether the request may proceed."""
        now = self.clock()
        elapsed = max(0.0, now - self._last)
        self._last = now
        refill_rate = self.capacity / self.window_seconds
        self._tokens = min(self.capacity, self._tokens + elapsed * refill_rate)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


__all__ = [
    "KEY_ABSENT",
    "MAX_BODY_BYTES",
    "RATE_LIMIT_CAPACITY",
    "RATE_LIMIT_WINDOW_SECONDS",
    "RateLimiter",
    "generate_token",
    "get_token",
    "resolve_token",
    "rotate_token",
    "token_ref_for",
    "token_state",
]
