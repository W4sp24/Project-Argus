"""Repo-wide test fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from backend.core.taxonomy import Taxonomy, active_taxonomy, set_active_taxonomy


@pytest.fixture(scope="session", autouse=True)
def _git_identity_for_tests() -> Iterator[None]:
    """Give git an identity, so the suite does not depend on the machine's.

    Invariant I2 means most write paths run ``git commit`` (``_git_snapshot``),
    and several fixtures build a throwaway vault with ``git init`` + an initial
    commit under ``check=True``. Git refuses to commit without a ``user.name``
    and ``user.email``, and a **GitHub-hosted runner has neither** — so the
    whole suite passed on a developer machine, where a global identity happens
    to exist, and collapsed the first time CI ran it: 14 failed, 493 passed and
    52 setup errors, all of them "Author identity unknown".

    Reproduce that state locally with::

        GIT_CONFIG_GLOBAL=/nonexistent GIT_CONFIG_SYSTEM=/nonexistent pytest

    These four environment variables are what git reads *before* any config
    file, so they fix it without writing to the developer's real ``.gitconfig``
    and without every vault fixture having to remember a pair of
    ``git config`` calls (only ``tests/features/insights`` ever did).
    """
    identity = {
        "GIT_AUTHOR_NAME": "Argus Tests",
        "GIT_AUTHOR_EMAIL": "tests@argus.invalid",
        "GIT_COMMITTER_NAME": "Argus Tests",
        "GIT_COMMITTER_EMAIL": "tests@argus.invalid",
    }
    previous = {key: os.environ.get(key) for key in identity}
    os.environ.update(identity)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(autouse=True)
def _reset_active_taxonomy() -> Iterator[None]:
    """Reset the process-level taxonomy fallback around every test.

    ``backend.core.taxonomy.active_taxonomy()`` is module-level mutable state
    (see that module's docstring for the full tradeoff). Any test that calls
    ``Settings.load`` against an env file with custom ``VAULT_*_DIR`` keys —
    or calls :func:`~backend.core.taxonomy.set_active_taxonomy` directly —
    would otherwise leak that taxonomy into whichever test runs next, since
    pytest reuses one process for the whole run.

    Each test starts from the documented default (exactly what a fresh
    process sees) and the previous value is restored on teardown, so this
    fixture is also transparent to any test that never touches taxonomy at
    all.
    """
    previous = active_taxonomy()
    set_active_taxonomy(Taxonomy())
    try:
        yield
    finally:
        set_active_taxonomy(previous)
