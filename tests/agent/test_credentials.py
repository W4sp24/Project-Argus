"""A broken keyring must never be reported as "you never saved a key".

The bug: ``get_key`` swallowed every exception and returned None, and
``has_key`` was defined in terms of it. A momentarily unavailable Windows
Credential Locker was therefore indistinguishable from an absent key, so the
UI dropped the KEY badge and the agent told the user to "re-add it under
/system" — a key they already had.
"""

from __future__ import annotations

import pytest

from backend.agent.credentials import (
    KEY_ABSENT,
    KEY_PRESENT,
    KEY_UNKNOWN,
    KeyringUnavailableError,
    get_key,
    has_key,
    key_state,
)


class _BrokenKeyring:
    """A keyring backend that is installed but cannot be read."""

    class errors:  # noqa: N801 - mirrors the real module's attribute
        class KeyringLocked(Exception):  # noqa: N818 - mirrors keyring's real name
            pass

    @staticmethod
    def get_password(_service: str, _ref: str) -> str:
        raise _BrokenKeyring.errors.KeyringLocked("the credential store is locked")


@pytest.fixture()
def broken_keyring(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "keyring", _BrokenKeyring)


def test_no_ref_is_absent_not_unknown() -> None:
    """A model that never had a key is a normal, knowable state."""
    assert key_state(None) == KEY_ABSENT
    assert key_state("") == KEY_ABSENT
    assert get_key(None) is None


def test_an_unreadable_keyring_is_unknown_not_absent(broken_keyring) -> None:
    assert key_state("model:something") == KEY_UNKNOWN
    assert has_key("model:something") is False, "back-compat boolean stays conservative"


def test_get_key_raises_rather_than_lying(broken_keyring) -> None:
    with pytest.raises(KeyringUnavailableError, match="could not be read"):
        get_key("model:something")


def test_the_adapter_says_unavailable_not_re_add_it(broken_keyring) -> None:
    """The message the user actually sees must not send them to retype a key."""
    from backend.agent.adapters import AgentError, adapter_for_entry

    entry = {"name": "hosted", "provider": "anthropic-api", "key_ref": "model:hosted"}
    with pytest.raises(AgentError) as excinfo:
        adapter_for_entry(entry)

    assert "keyring could not be read" in str(excinfo.value)
    assert "re-add it" not in str(excinfo.value)


def test_a_real_stored_key_is_present(monkeypatch) -> None:
    import keyring

    from backend.agent.credentials import KEYRING_SERVICE

    keyring.set_password(KEYRING_SERVICE, "model:probe-test", "sk-test")
    try:
        assert key_state("model:probe-test") == KEY_PRESENT
        assert has_key("model:probe-test") is True
    finally:
        keyring.delete_password(KEYRING_SERVICE, "model:probe-test")


def test_a_missing_key_is_absent() -> None:
    assert key_state("model:definitely-not-stored-anywhere") == KEY_ABSENT
