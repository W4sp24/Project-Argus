"""External read connectors: Google Calendar and Todoist (P2 read-only)."""

from __future__ import annotations


class ConnectorUnavailable(RuntimeError):  # noqa: N818 - name matches the spec this fixes
    """This connector cannot answer right now.

    Distinct from "not configured" — the normal, expected ``[]`` a connector
    returns before the user has ever connected it. ``ConnectorUnavailable``
    covers the cases where the user DID connect it and Argus still can't get
    data: a broken build missing the client library, an expired or rejected
    credential, or an unreachable API. Callers that must degrade rather than
    fail (a calendar widget, the planner's context, the task board) catch
    this and continue with a short note instead of a 500.
    """


def client_library_importable(module_name: str) -> bool:
    """Can a connector's client library actually be imported right now?

    A cheap, local signal for "is this build missing the optional
    dependency" that never touches the network — used both by
    ``argus doctor`` (:mod:`backend.features.system.doctor`) and
    ``/api/integrations`` (:mod:`backend.features.integrations.router`) to
    tell a broken build apart from a connector the user simply hasn't
    connected yet, without a live round-trip to Todoist or Google. Catches
    ``Exception`` broadly, not just ``ImportError``: a frozen build can fail
    with ``OSError`` on a missing DLL rather than a clean
    ``ModuleNotFoundError``.
    """
    import importlib

    try:
        importlib.import_module(module_name)
        return True
    except Exception:  # noqa: BLE001 - any failure means "not usable"
        return False
