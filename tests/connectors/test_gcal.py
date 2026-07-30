"""Regression test for the gcal lazy-import guard (Phase H fix).

``backend.connectors.gcal`` is an optional connector: ``google-api-python-client``
/ ``google-auth`` / ``google-auth-oauthlib`` live behind the ``[gcal]`` extra
(pyproject.toml), not the base install. Before the fix, ``_service()`` imported
those libs unconditionally at the top of the function — every call to
``list_events`` (from ``/api/agenda`` and ``/api/insights``) raised ImportError
in a fresh venv even when gcal was never configured. The fix checks the stored
token first and only imports the google libs once one exists.
"""

from __future__ import annotations

import builtins
from datetime import date

import pytest

from backend.connectors import gcal


def test_list_events_unconfigured_does_not_import_google_libs(monkeypatch):
    """No stored token -> [] without ever touching the google client libs."""
    monkeypatch.setattr(gcal, "_stored_token", lambda: None)

    real_import = builtins.__import__

    def blocking_import(name, *args, **kwargs):
        if name == "google" or name.startswith(("google.", "googleapiclient")):
            raise ImportError(f"{name} is not installed (simulated fresh venv)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocking_import)

    assert gcal.list_events(date.today()) == []


def test_configured_false_without_google_libs(monkeypatch):
    """configured() never needs the google client libs either — token-only."""
    monkeypatch.setattr(gcal, "_stored_token", lambda: None)

    real_import = builtins.__import__

    def blocking_import(name, *args, **kwargs):
        if name == "google" or name.startswith(("google.", "googleapiclient")):
            raise ImportError(f"{name} is not installed (simulated fresh venv)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocking_import)

    assert gcal.configured() is False


def test_legacy_credentials_file_is_env_anchored_not_cwd(monkeypatch, tmp_path):
    """`legacy_credentials_file` follows the env file's directory, not the CWD.

    This is the regression test for the bug this branch fixes:
    ``CREDENTIALS_FILE = Path("credentials.json")`` used to resolve against
    the process's current working directory, which is a nonsense, unwritable
    location in the packaged desktop app. ``DEFAULT_ENV_FILE`` is computed
    once at import time from ``os.environ["ARGUS_ENV_FILE"]``, so
    ``monkeypatch.setenv`` alone would not move it here -- the module-level
    constant already holds whatever value import saw. Patch the constant
    directly, following ``backend.core.config``'s own convention.
    """
    from backend.core import config

    other_cwd = tmp_path / "somewhere-unwritable"
    other_cwd.mkdir()
    env_dir = tmp_path / "env-anchor"
    env_dir.mkdir()

    monkeypatch.chdir(other_cwd)
    monkeypatch.setattr(config, "DEFAULT_ENV_FILE", env_dir / ".env")

    resolved = gcal.legacy_credentials_file()

    assert resolved == env_dir / "credentials.json"
    assert resolved != other_cwd / "credentials.json", (
        "must not resolve against the process CWD"
    )


def test_service_imports_google_libs_only_once_a_token_exists(monkeypatch):
    """A stored token DOES require the google libs — that failure is expected
    and actionable (the user connected gcal but the extra isn't installed)."""
    monkeypatch.setattr(gcal, "_stored_token", lambda: "not-real-json")

    real_import = builtins.__import__

    def blocking_import(name, *args, **kwargs):
        if name == "google" or name.startswith(("google.", "googleapiclient")):
            raise ImportError(f"{name} is not installed (simulated fresh venv)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocking_import)

    with pytest.raises(ImportError):
        gcal._service()
