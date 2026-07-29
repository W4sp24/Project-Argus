"""The Obsidian vault as a resource: the single write path and its readers.

Every mutation of user notes goes through :mod:`backend.vault.writer` and
nothing else (invariant I1). :mod:`backend.vault.paths` holds the privacy
policy — which zones are indexable — and is consumed by ``backend.rag``.
"""
