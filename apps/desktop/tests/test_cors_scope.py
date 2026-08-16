"""Proves the CORS scoping fix for `apps/desktop/run_backend.py`.

Regression test for a review finding on Task 3 (desktop-shell-packaging):
an earlier revision applied `CORSMiddleware` with `allow_origins=["*"]`
directly to the *entire* `aura_api.main.app` object, which also serves real
user-data routes (`/v1/jobs/{id}`, `/v1/exports/{id}`, etc.). That let any
cross-origin webpage read those routes' responses as long as it could reach
127.0.0.1:8317 and knew/guessed an id.

The fix scopes CORS to only the `/healthz` route (see `run_backend.py`'s
inline comment for the mechanism). This test proves both halves of the
acceptance bar directly against `run_backend.root_app`, simulating a
cross-origin request the way a browser would: by sending a `GET` with an
`Origin` header that does not match the server's own origin and checking
for the `Access-Control-Allow-Origin` response header, exactly as the
browser itself checks it before releasing the response body to page JS.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# `run_backend.py` sets these env var defaults at import time (see its
# module docstring) before importing `aura_api.main`, which requires them.
# Point them at a throwaway location so this test never touches real data.
os.environ.setdefault("AURA_DATA_DIR", "/tmp/aura-desktop-cors-test-data")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/aura-desktop-cors-test-data/aura.db")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run_backend  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

CROSS_ORIGIN = "http://example.com"


def _client() -> TestClient:
    # `raise_server_exceptions=False`: the `/v1/*` routes hit a real
    # SQLAlchemy session against a throwaway, migration-less sqlite file, so
    # they 500 (no such table) rather than a clean 404 for an unknown id.
    # That's irrelevant to what this test proves — CORS header
    # presence/absence — and a real browser would see the same "opaque,
    # can't read the body" outcome for a 500 as for a 404 when the CORS
    # header is missing, so asserting on headers rather than the exact
    # status code keeps this test focused and independent of DB setup.
    return TestClient(run_backend.root_app, raise_server_exceptions=False)


def test_healthz_is_reachable_cross_origin() -> None:
    """The desktop placeholder page's own fetch() must keep working."""
    response = _client().get("/healthz", headers={"Origin": CROSS_ORIGIN})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers.get("access-control-allow-origin") == "*"


def test_v1_routes_are_not_reachable_cross_origin() -> None:
    """Real user-data routes must NOT carry any CORS headers.

    A missing Access-Control-Allow-Origin header is what makes the browser
    withhold the response body from cross-origin page JS, even though the
    server still answers on the wire (same as the same-origin/networked
    deployment's existing behavior).
    """
    client = _client()

    for path in (
        "/v1/jobs/some-job-id",
        "/v1/exports/some-export-id",
        "/v1/exports/some-export-id/download",
    ):
        response = client.get(path, headers={"Origin": CROSS_ORIGIN})

        assert "access-control-allow-origin" not in response.headers
        assert "access-control-allow-credentials" not in response.headers


def test_v1_routes_still_respond_same_origin() -> None:
    """Scoping CORS must not break the routes themselves (same-origin)."""
    response = _client().get("/v1/jobs/some-job-id")

    # No CORS applied means no *browser-enforced* opacity for a same-origin
    # caller (e.g. curl, or the Tauri webview's own same-origin requests
    # were it to call this route) — the route resolves and answers
    # normally, it just isn't reachable cross-origin. Any non-404-from-
    # routing status (200, or 500 from the throwaway migration-less test
    # DB — see `_client()`) proves the route was actually reached and
    # routed to the real handler, not swallowed by the mount; only a
    # 404 with no response body would indicate routing itself was broken.
    assert response.status_code in (200, 500)
