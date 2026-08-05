"""The local routes that describe (and issue) the inbound surface's credential.

These sit on the local ``/api``, which is unauthenticated because it is
loopback-only. The token they hand out guards the *public* surface, so the
arrangement only holds if these routes never appear on that public surface —
which is asserted here rather than assumed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.main import create_app


class _MemoryKeyring:
    """A keyring that lives in a dict, swapped in via sys.modules."""

    store: dict[tuple[str, str], str] = {}

    class errors:  # noqa: N801 - mirrors the real keyring module's shape
        class KeyringError(Exception):
            pass

    @classmethod
    def get_password(cls, service: str, ref: str) -> str | None:
        return cls.store.get((service, ref))

    @classmethod
    def set_password(cls, service: str, ref: str, value: str) -> None:
        cls.store[(service, ref)] = value

    @classmethod
    def delete_password(cls, service: str, ref: str) -> None:
        cls.store.pop((service, ref), None)


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "20-Projects").mkdir(parents=True)
    return root


@pytest.fixture()
def client(vault: Path, monkeypatch) -> TestClient:
    _MemoryKeyring.store.clear()
    monkeypatch.setitem(sys.modules, "keyring", _MemoryKeyring)
    return TestClient(create_app(Settings(_vault_path=vault)))


def test_the_surface_reports_its_config_without_the_token(client: TestClient) -> None:
    response = client.get("/api/automations/external")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is False
    assert body["port"] == 8787
    assert "token" not in body
    assert body["token_state"] in {"present", "absent", "unknown"}


def test_issuing_a_token_returns_it_once_and_stores_it(client: TestClient) -> None:
    response = client.post("/api/automations/external/token")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token"]
    assert body["rotated"] is False
    assert body["header_value"] == f"Bearer {body['token']}"

    # It is now present...
    assert client.get("/api/automations/external").json()["token_state"] == "present"
    # ...and there is no way to read it back.
    assert "token" not in client.get("/api/automations/external").json()


def test_rotating_replaces_the_old_token_immediately(client: TestClient) -> None:
    """The recovery for a leaked token is to press this, so there must be no
    window in which both the old and the new value verify."""
    from backend.features.external import auth

    first = client.post("/api/automations/external/token").json()["token"]
    second_response = client.post("/api/automations/external/token").json()
    assert second_response["rotated"] is True
    assert second_response["token"] != first

    assert auth.verify(second_response["token"]) is True
    assert auth.verify(first) is False


def test_the_credential_routes_are_not_on_the_public_surface(vault: Path) -> None:
    """A public endpoint that hands out the credential guarding it would
    defeat the entire point of having one."""
    from backend.features.external.app import create_external_app

    external = TestClient(create_external_app(Settings(_vault_path=vault)))
    assert external.get("/api/automations/external").status_code == 404
    assert external.post("/api/automations/external/token").status_code == 404
