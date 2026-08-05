"""The authenticated inbound surface (backend/features/external/).

Everything here answers requests that arrive from outside the machine —
n8n workflows calling back through a tunnel. Three things hold that line:
the port boundary (this app is never mounted alongside the local dashboard
API — see :mod:`backend.features.external.app`), the bearer token
(:mod:`backend.features.external.auth`), and the I3 privacy boundary on every
read endpoint (:mod:`backend.vault.privacy`, applied in
:mod:`backend.features.external.router`).
"""

from __future__ import annotations
