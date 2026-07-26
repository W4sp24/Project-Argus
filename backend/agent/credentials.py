"""API keys for model registry entries — OS keyring only (invariant I4).

Mirrors :mod:`backend.connectors.todoist` exactly: the secret lives in the OS
keyring, and the only thing that ever reaches disk or an API response is a
*reference* to it. ``.argus/models.json`` stores ``key_ref``; no endpoint in
:mod:`backend.system_api` returns a key, only ``has_key: bool``.

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


def get_key(ref: str | None) -> str | None:
    """The stored key for ``ref``, or None when absent or the keyring is unusable."""
    if not ref:
        return None
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, ref)
    except Exception:  # noqa: BLE001 - callers treat "unavailable" as "not configured"
        return None


def has_key(ref: str | None) -> bool:
    """True when a key is stored for ``ref`` — the only key fact an API may expose."""
    return get_key(ref) is not None


def delete_key(ref: str | None) -> None:
    """Remove a stored key. Best-effort: deleting a model must never fail on this."""
    if not ref:
        return
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, ref)
    except Exception:  # noqa: BLE001 - already absent, or keyring unusable
        pass
