"""The second FastAPI app — the only one a tunnel is ever pointed at.

**This separation is the most important safety property in the automations
feature, and it is structural rather than remembered.**

The alternative was one app with a path-prefix check in middleware. Under that
design a single routing mistake anywhere in Argus — a router mounted at the
wrong prefix, a catch-all added years from now by someone who never read this
file — publishes the entire vault through a public URL. The check has to be
got right at every future endpoint, forever, by everyone.

Here the external routes live on their own ``FastAPI`` object whose route table
contains nothing else, served on its own port. The main app is never bound to
that port, so a bug in the main app is simply not reachable from the internet.
There is nothing to remember and nothing to get right later.

Two further deliberate omissions:

- **No CORS.** This surface is for machine callers. A browser has no business
  here, and an ``Access-Control-Allow-Origin`` header would be the first step
  toward one.
- **No cookies, no session.** The only credential is a bearer token checked in
  constant time, so there is no ambient authority a CSRF could ride on.
"""

from __future__ import annotations

from fastapi import FastAPI

from backend.core.config import Settings
from backend.features.external import auth
from backend.features.external.router import build_external_router


def create_external_app(
    settings: Settings, *, limiter: auth.RateLimiter | None = None
) -> FastAPI:
    """Build the external app. Mounts the external router and nothing else."""
    app = FastAPI(
        title="Argus external surface",
        description="Capture and read only. No agent invocation, no action triggers.",
        docs_url=None,  # no interactive docs on a public surface
        redoc_url=None,
        openapi_url=None,
    )
    app.include_router(build_external_router(settings, limiter=limiter))
    return app
