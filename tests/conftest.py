"""Repo-wide test fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from backend.core.taxonomy import Taxonomy, active_taxonomy, set_active_taxonomy


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
