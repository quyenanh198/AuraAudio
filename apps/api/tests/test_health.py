from fastapi.testclient import TestClient

from aura_api.main import create_app


def test_healthz_returns_ok():
    client = TestClient(create_app())
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
