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
registry. There is exactly one token — Argus registers exactly one external
surface — so the keyring reference is a fixed string, not derived from a
name.
"""

from __future__ import annotations

import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from backend.agent.credentials import (
    KEY_ABSENT,
    KeyringUnavailableError,
    get_key,
    key_state,
    store_key,
)

#: The one keyring reference this whole module reads and writes.
TOKEN_REF = "external:token"

#: Bytes of entropy in a generated token (secrets.token_urlsafe measures its
#: argument in bytes before base64url-encoding it, not output characters).
TOKEN_BYTES = 32

#: 256 KiB — a JSON body over this is refused before it is parsed (413).
MAX_BODY_BYTES = 256 * 1024

#: Token bucket shape: 60 requests/minute.
RATE_LIMIT_CAPACITY = 60
RATE_LIMIT_WINDOW_SECONDS = 60.0


def generate_token() -> str:
    """Generate a new 32-random-byte, URL-safe bearer token and persist it.

    Overwrites whatever was stored before — see :func:`rotate_token`, which is
    just this function under a name that says what calling it again does.
    """
    token = secrets.token_urlsafe(TOKEN_BYTES)
    store_key(TOKEN_REF, token)
    return token


def get_token() -> str | None:
    """The currently active token, or ``None`` when none has been generated
    (or the keyring cannot be read — a broken keyring must never look like a
    valid token to a caller foolish enough to compare against ``None``, so
    this is deliberately not the same as "token would verify")."""
    try:
        return get_key(TOKEN_REF)
    except KeyringUnavailableError:
        return None


def rotate_token() -> str:
    """Generate a new token, invalidating the old one immediately.

    ``store_key`` (via the keyring) replaces the credential outright — there
    is no window where both the old and new token verify, and no separate
    "delete old, then write new" step that could leave neither stored.
    """
    return generate_token()


def token_state() -> str:
    """Tri-state presence of a stored token — see :func:`backend.agent.credentials.key_state`."""
    return key_state(TOKEN_REF)


def verify(presented: str | None) -> bool:
    """Constant-time check of a presented token against the stored one.

    Uses :func:`hmac.compare_digest` so a wrong-but-close guess takes no
    longer to reject than a wildly wrong one. Every failure path here —
    absent header, malformed header, wrong token, a keyring that cannot be
    read — returns the same ``False``; the caller (the auth dependency in
    :mod:`backend.features.external.router`) turns every ``False`` into the
    same 401 body, so none of these reasons is distinguishable from outside.
    """
    if not presented:
        return False
    try:
        actual = get_key(TOKEN_REF)
    except KeyringUnavailableError:
        return False
    if not actual:
        return False
    # Compare as bytes, not str: hmac.compare_digest raises TypeError on a
    # str containing non-ASCII, and this input arrives straight off a public
    # network. A token of "é" must be a 401 like every other wrong token, not
    # a 500 that tells an attacker they found an unhandled path.
    return hmac.compare_digest(presented.encode("utf-8"), actual.encode("utf-8"))


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
    "TOKEN_REF",
    "RateLimiter",
    "generate_token",
    "get_token",
    "rotate_token",
    "token_state",
    "verify",
]
