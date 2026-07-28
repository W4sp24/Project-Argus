"""Cross-cutting platform: settings, the model registry, storage, scheduling.

Nothing here knows about a specific feature. The dependency rule is one-way —
``backend.features.*`` imports ``backend.core``, never the reverse.
"""
