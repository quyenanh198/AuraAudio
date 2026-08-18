"""Loopback binding alone does not protect the sidecar: any local process
can reach the port, and a browser page can POST to 127.0.0.1 — CORS blocks
reading the response, not sending the request, which is no protection for
endpoints with side effects."""
import pytest
from aura_api.main import create_app
from fastapi.testclient import TestClient

TOKEN = "s3cret-token"


@pytest.fixture()
def unsecured_client(monkeypatch):
    monkeypatch.delenv("AURA_API_TOKEN", raising=False)
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture()
def secured_client(monkeypatch):
    monkeypatch.setenv("AURA_API_TOKEN", TOKEN)
    with TestClient(create_app()) as client:
        yield client


def test_without_a_token_configured_requests_pass(unsecured_client):
    # Keeps the developer uvicorn workflow and the existing suite working
    # unchanged — the shell is what sets the variable.
    assert unsecured_client.get("/healthz").status_code == 200
    assert unsecured_client.get("/v1/jobs/does-not-exist").status_code != 401


def test_healthz_is_reachable_without_a_token(secured_client):
    # The shell's readiness poll runs before it would send a secret.
    assert secured_client.get("/healthz").status_code == 200


def test_correct_token_is_accepted(secured_client):
    r = secured_client.get("/v1/jobs/does-not-exist", headers={"X-Aura-Token": TOKEN})
    assert r.status_code != 401


def test_missing_token_is_rejected(secured_client):
    assert secured_client.get("/v1/jobs/does-not-exist").status_code == 401


def test_wrong_token_is_rejected(secured_client):
    r = secured_client.get("/v1/jobs/does-not-exist", headers={"X-Aura-Token": "wrong"})
    assert r.status_code == 401


def test_side_effecting_route_is_rejected_without_a_token(secured_client):
    # The case that actually matters: a browser page can send this even
    # though it cannot read the reply.
    r = secured_client.post(
        "/v1/projects",
        json={"title": "t", "instrument": "guitar", "object_key": "k"},
    )
    assert r.status_code == 401


@pytest.mark.parametrize("host", ["evil.example.com", "attacker.test:8000"])
def test_foreign_host_header_is_rejected(secured_client, host):
    # DNS rebinding: a page resolving its own hostname to 127.0.0.1 would
    # otherwise be same-origin with the sidecar.
    r = secured_client.get(
        "/v1/jobs/does-not-exist",
        headers={"X-Aura-Token": TOKEN, "Host": host},
    )
    assert r.status_code == 400


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:8899", "localhost", "localhost:8899"])
def test_loopback_host_headers_are_accepted(secured_client, host):
    r = secured_client.get(
        "/v1/jobs/does-not-exist",
        headers={"X-Aura-Token": TOKEN, "Host": host},
    )
    assert r.status_code != 400
