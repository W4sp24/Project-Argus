"""User-facing features, one package each.

Every package owns a ``router.py`` that builds its ``APIRouter`` plus whatever
domain modules it needs. A feature may import ``core``, ``vault``,
``telemetry``, ``agent``, ``rag`` and ``connectors``; nothing outside this
package may import from it.
"""
