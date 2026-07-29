"""API keys for model registry entries — OS keyring only (invariant I4).

Mirrors :mod:`backend.connectors.todoist` exactly: the secret lives in the OS
keyring, and the only thing that ever reaches disk or an API response is a
*reference* to it. ``.argus/models.json`` stores ``key_ref``; no endpoint in
:mod:`backend.features.system.router` returns a key, only ``has_key: bool``.

Keyring failures are surfaced, not swallowed. The frozen desktop backend has a
documented history of keyring breaking silently under PyInstaller
(``desktop/README.md``), and a hosted model whose key vanished should say so at
registration time rather than fail deep inside someone's first chat turn.
"""

from __future__ import annotations

KEYRING_SERVICE = "argus-models"


class CredentialError(RuntimeError):
    """The keyring could not store or retrieve a model's API key."""


def key_ref_for(name: str) -> str:
    """The keyring reference stored in ``models.json`` for a registry entry."""
    return f"model:{name}"


def store_key(ref: str, key: str) -> None:
    """Persist an API key under ``ref``. Raises :class:`CredentialError` on failure."""
    if not key.strip():
        raise CredentialError("empty API key")
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, ref, key.strip())
    except Exception as exc:  # noqa: BLE001 - re-raised with an actionable message
        raise CredentialError(
            f"could not store the API key in the OS keyring: {exc}. "
            "Run `argus doctor` — the keyring check explains how to fix this."
        ) from exc


#: ``has_key``'s three answers. "unknown" is the one that used to be missing:
#: a keyring that cannot be read is not the same as a key that was never saved.
KEY_PRESENT = "present"
KEY_ABSENT = "absent"
KEY_UNKNOWN = "unknown"


class KeyringUnavailableError(RuntimeError):
    """The OS keyring could not be read — say so, do not report "no key"."""


def get_key(ref: str | None) -> str | None:
    """The stored key for ``ref``, or None when none is stored.

    Raises :class:`KeyringUnavailableError` when the keyring itself is broken. It
    used to return None for that too, so a momentarily unavailable Windows
    Credential Locker told the user "no API key stored — re-add it", and they
    re-entered a key they already had.
    """
    if not ref:
        return None
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, ref)
    except Exception as exc:  # noqa: BLE001 - keyring backends raise many types
        raise KeyringUnavailableError(
            f"the OS keyring could not be read: {exc}. "
            "Your stored keys are probably fine — run `argus doctor` to check."
        ) from exc


def key_state(ref: str | None) -> str:
    """Whether a key is stored for ``ref`` — the only key fact an API may expose.

    Tri-state on purpose: see :data:`KEY_UNKNOWN`.
    """
    if not ref:
        return KEY_ABSENT
    try:
        return KEY_PRESENT if get_key(ref) is not None else KEY_ABSENT
    except KeyringUnavailableError:
        return KEY_UNKNOWN


def has_key(ref: str | None) -> bool:
    """Back-compat boolean. Prefer :func:`key_state`, which cannot lie."""
    return key_state(ref) == KEY_PRESENT


def delete_key(ref: str | None) -> None:
    """Remove a stored key. Best-effort: deleting a model must never fail on this."""
    if not ref:
        return
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, ref)
    except Exception:  # noqa: BLE001 - already absent, or keyring unusable
        pass
