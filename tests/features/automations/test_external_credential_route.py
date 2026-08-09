"""The local routes that describe (and issue) the inbound surface's credential.

These sit on the local ``/api``, which is unauthenticated because it is
loopback-only. The tokens they hand out guard the *public* surface, so the
arrangement only holds if these routes never appear on that public surface —
which is asserted here rather than assumed.

B4: the token is now per n8n instance. The unscoped ``/automations/external``
routes are a compat shim valid only while exactly one instance is registered
(``_only_instance`` — 409 otherwise); the primary surface is the
instance-scoped ``/automations/instances/{instance_id}/external[/token]``
routes.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.features.automations import store
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
def settings(vault: Path) -> Settings:
    return Settings(_vault_path=vault)


@pytest.fixture()
def client(settings: Settings, monkeypatch) -> TestClient:
    _MemoryKeyring.store.clear()
    monkeypatch.setitem(sys.modules, "keyring", _MemoryKeyring)
    return TestClient(create_app(settings))


def _seed_instance(settings: Settings, *, name: str = "home") -> dict[str, Any]:
    """Register an instance directly through the store, bypassing HTTP — the
    n8n API key itself is irrelevant to these tests, only the instance's id."""
    entry = {
        "id": uuid.uuid4().hex,
        "name": name,
        "kind": "REMOTE",
        "base_url": "http://n8n.test",
        "key_ref": store.key_ref_for(name),
    }
    instances = store.load_instances(settings.automations_file)
    instances.append(entry)
    store.save_instances(settings.automations_file, instances)
    return entry


# --- instance-scoped routes ---------------------------------------------------


def test_the_scoped_surface_reports_its_config_without_the_token(
    client: TestClient, settings: Settings
) -> None:
    instance = _seed_instance(settings)
    response = client.get(f"/api/automations/instances/{instance['id']}/external")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is False
    assert body["port"] == 8787
    assert "token" not in body
    assert body["token_state"] in {"present", "absent", "unknown"}


def test_scoped_surface_404s_for_an_unknown_instance(client: TestClient) -> None:
    assert client.get("/api/automations/instances/does-not-exist/external").status_code == 404


def test_issuing_a_scoped_token_returns_it_once_and_stores_it(
    client: TestClient, settings: Settings
) -> None:
    instance = _seed_instance(settings)
    response = client.post(f"/api/automations/instances/{instance['id']}/external/token")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token"]
    assert body["rotated"] is False
    assert body["header_value"] == f"Bearer {body['token']}"

    # It is now present...
    surface = client.get(f"/api/automations/instances/{instance['id']}/external").json()
    assert surface["token_state"] == "present"
    # ...and there is no way to read it back.
    assert "token" not in surface


def test_rotating_a_scoped_token_replaces_the_old_one_immediately(
    client: TestClient, settings: Settings
) -> None:
    """The recovery for a leaked token is to press this, so there must be no
    window in which both the old and the new value verify."""
    from backend.features.external import auth

    instance = _seed_instance(settings)
    first = client.post(f"/api/automations/instances/{instance['id']}/external/token").json()[
        "token"
    ]
    second_response = client.post(
        f"/api/automations/instances/{instance['id']}/external/token"
    ).json()
    assert second_response["rotated"] is True
    assert second_response["token"] != first

    assert auth.resolve_token(second_response["token"], [instance]) == instance["id"]
    assert auth.resolve_token(first, [instance]) is None


def test_issuing_a_token_for_one_instance_does_not_touch_another(
    client: TestClient, settings: Settings
) -> None:
    from backend.features.external import auth

    a = _seed_instance(settings, name="a")
    b = _seed_instance(settings, name="b")

    token_a = client.post(f"/api/automations/instances/{a['id']}/external/token").json()["token"]
    token_b = client.post(f"/api/automations/instances/{b['id']}/external/token").json()["token"]
    assert token_a != token_b

    instances = [a, b]
    assert auth.resolve_token(token_a, instances) == a["id"]
    assert auth.resolve_token(token_b, instances) == b["id"]

    # Rotating a's token leaves b's untouched.
    client.post(f"/api/automations/instances/{a['id']}/external/token")
    assert auth.resolve_token(token_b, instances) == b["id"]


# --- unscoped compat shim: valid only for exactly one instance --------------


def test_the_unscoped_shim_409s_with_zero_instances_registered(client: TestClient) -> None:
    assert client.get("/api/automations/external").status_code == 409
    assert client.post("/api/automations/external/token").status_code == 409


def test_the_unscoped_shim_409s_with_two_instances_registered(
    client: TestClient, settings: Settings
) -> None:
    _seed_instance(settings, name="a")
    _seed_instance(settings, name="b")
    assert client.get("/api/automations/external").status_code == 409
    assert client.post("/api/automations/external/token").status_code == 409


def test_the_unscoped_shim_matches_old_behaviour_for_exactly_one_instance(
    client: TestClient, settings: Settings
) -> None:
    instance = _seed_instance(settings)

    response = client.get("/api/automations/external")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is False
    assert body["port"] == 8787
    assert "token" not in body

    issued = client.post("/api/automations/external/token")
    assert issued.status_code == 200, issued.text
    token_body = issued.json()
    assert token_body["token"]
    assert token_body["rotated"] is False

    # The scoped route agrees: the shim acted on the sole registered instance.
    scoped = client.get(f"/api/automations/instances/{instance['id']}/external").json()
    assert scoped["token_state"] == "present"


def test_the_credential_routes_are_not_on_the_public_surface(vault: Path) -> None:
    """A public endpoint that hands out the credential guarding it would
    defeat the entire point of having one."""
    from backend.features.external.app import create_external_app

    external = TestClient(create_external_app(Settings(_vault_path=vault)))
    assert external.get("/api/automations/external").status_code == 404
    assert external.post("/api/automations/external/token").status_code == 404
    assert external.get("/api/automations/instances/anything/external").status_code == 404
    assert external.post("/api/automations/instances/anything/external/token").status_code == 404
