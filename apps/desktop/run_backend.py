"""Standalone entrypoint for the PyInstaller-bundled AuraAudio backend.

This script is the concrete Python entrypoint PyInstaller bundles into
`aura-backend`. It cannot simply reference `aura_api.main:app` (an ASGI
app object) because PyInstaller needs a runnable script that boots the
process, so this imports the app object and drives it with `uvicorn.run`.

Fixed port 8317 is locked for this plan (desktop-shell-packaging) so every
later task that needs to reach the bundled backend uses the same literal.

`aura_api.db` requires `DATABASE_URL` (and reads `AURA_DATA_DIR`) at import
time. In the normal dev workflow those come from `.envrc`. A standalone
bundle has no `.envrc`, so this sets sane defaults *before* importing
`aura_api.main` — a SQLite DB file next to the bundled executable — while
still letting an operator override either var beforehand.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    # Running as a PyInstaller bundle: anchor data next to the executable.
    _base_dir = Path(sys.executable).resolve().parent
else:
    _base_dir = Path(__file__).resolve().parent

_data_dir = _base_dir / "data"
os.environ.setdefault("AURA_DATA_DIR", str(_data_dir))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_data_dir / 'aura.db'}")

import uvicorn  # noqa: E402
from aura_api.db import Base, get_engine  # noqa: E402
from aura_api.main import app  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.middleware import Middleware  # noqa: E402
from starlette.middleware.cors import CORSMiddleware  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from starlette.routing import Mount, Route  # noqa: E402

AURA_BACKEND_PORT = 8317

# The Tauri webview loads the frontend from a custom-scheme origin (e.g.
# `tauri://localhost` on Linux/macOS, `http://tauri.localhost` on Windows),
# which is cross-origin from this backend's `http://127.0.0.1:8317`. Without
# CORS headers, the browser accepts the response on the wire (it shows up in
# this process's own access log) but withholds it from the page's `fetch()`,
# which then rejects with a generic network error — confirmed by hand during
# Task 3's desktop-shell-packaging end-to-end verification.
#
# IMPORTANT: the wildcard CORS policy below must NOT be applied to the whole
# `aura_api.main.app` (as an earlier revision of this file did). That app
# also serves real user-data routes (`/v1/jobs/{id}`, `/v1/exports/{id}`,
# `/v1/exports/{id}/download`, etc.) — applying `CORSMiddleware` to the
# entire app would let *any* webpage open in *any* browser tab on the same
# machine (not just this Tauri webview) `fetch()` those routes cross-origin
# and read job/export contents, as long as it can reach 127.0.0.1:8317 and
# guesses/knows an id. That's a real widening of local attack surface that
# only `/healthz` needs.
#
# Fix: build a tiny root ASGI app with two routes, matched in this order:
#   1. `/healthz` — an explicit Starlette `Route` with `CORSMiddleware`
#      attached only to *that route* (Starlette's `Route`/`Mount` both take
#      a `middleware=` kwarg, confirmed against this project's installed
#      starlette by reading `Route.__init__`'s signature directly, not
#      assumed).
#   2. `/` — a `Mount` of the full, unmodified `aura_api.main.app`, with no
#      CORS middleware anywhere on it — identical to how that app behaves in
#      the containerized/networked deployment (same-origin only).
# Because Starlette matches routes in registration order and `/healthz` is
# listed first, a request to `/healthz` is served by the CORS-enabled route
# and never reaches the mounted app's own `/healthz`; every other path falls
# through to the mount, completely un-CORS'd. Verified directly with
# `starlette.testclient.TestClient` (see `apps/desktop/tests/test_cors_scope.py`):
# a cross-origin `GET /healthz` gets `access-control-allow-origin: *`, a
# cross-origin `GET /v1/jobs/{id}` gets no CORS headers at all.
#
# A wildcard origin is acceptable for the `/healthz` route specifically:
# it's the only route reachable this way, returns no user data (just a
# static `{"status": "ok"}`), and this process only ever binds 127.0.0.1.
#
# `/v1/*` (the mounted `aura_api.main.app`) is different: it serves real
# user-data routes, so wildcard CORS is not acceptable there (see the block
# comment above). It still needs to be reachable from the real Tauri
# webview though — same-origin-only (the prior behavior) makes every
# `fetch()` the webview's own JS makes against this backend fail, which is
# exactly the bug `/healthz`'s CORS carve-out above was already patched
# for. The fix is an *exact-origin allowlist*: `CORSMiddleware` wrapping
# only the mounted app, restricted to the specific origins the webview can
# actually run from, never `["*"]`.
#
# Origins in `WEBVIEW_ORIGINS`, each independently verified:
#   - "tauri://localhost" — the PRODUCTION origin on Linux/WebKitGTK and
#     macOS/WKWebView (the platforms this app ships on: Linux .deb and
#     macOS .dmg). Confirmed by reading the installed tauri crate source
#     directly (not assumed): `tauri-2.11.5/src/manager/mod.rs:339-346`,
#     `Manager::tauri_protocol_url` — `if cfg!(windows) ||
#     cfg!(target_os = "android") { http(s)://tauri.localhost } else {
#     tauri://localhost }`. Linux takes the `else` branch, and
#     `get_app_url` (same file, lines 348-367) falls through to
#     `tauri_protocol_url` whenever `frontend_dist` isn't itself a
#     `FrontendDist::Url` — true for this app (`frontendDist` in
#     `tauri.conf.json` is the local `"../web/dist"` path, not a URL) — so
#     the packaged app's webview genuinely loads from `tauri://localhost`
#     in production, not `http://tauri.localhost` (that variant is
#     Windows/Android-only, both out of scope for this app today).
#   - "http://localhost:5173" — the OBSERVED `cargo tauri dev` origin.
#     `devUrl` in `tauri.conf.json` is `http://localhost:5173`, and
#     `get_app_url` (same source file, lines 353-355) uses `devUrl`
#     directly under `#[cfg(dev)]`, so the dev webview loads the page from
#     that literal origin. Empirically confirmed live: a temporary
#     Origin-header logger in this file plus a temporary `fetch()` in
#     `apps/desktop/web/src/App.svelte`, run under
#     `xvfb-run -a cargo tauri dev`, recorded
#     `path=/v1/projects origin=b'http://localhost:5173'` from the real
#     webview (both temporary edits reverted before commit — see
#     task-4-report.md for the full trace).
#   - "http://127.0.0.1:5173" — same dev server, alternate loopback
#     spelling. Not observed directly (the webview only ever requested the
#     `localhost` form above), but included defensively since Vite's dev
#     server binds both `localhost` and `127.0.0.1` on 5173 by default and
#     a browser/webview treats them as distinct origins for CORS purposes
#     even though they resolve to the same host.
#   - "http://tauri.localhost" — the PRODUCTION origin on Windows/Android
#     WebView2, now in scope now that the release workflow builds a
#     Windows `.msi` (roadmap item 2). Confirmed by the same
#     `tauri-2.11.5/src/manager/mod.rs:339-346` `tauri_protocol_url`
#     already cited above for the Linux origin: `if cfg!(windows) ||
#     cfg!(target_os = "android") { http(s)://tauri.localhost } else {
#     tauri://localhost }` — Windows takes the `if` branch (`http`, not
#     `https`, since this app's `tauri.conf.json` sets no dev/prod TLS
#     config), so the packaged Windows app's webview loads from this exact
#     origin in production, the same way `tauri://localhost` does on
#     Linux/WebKitGTK.
WEBVIEW_ORIGINS = [
    "tauri://localhost",  # production webview origin, Linux/WebKitGTK
    "http://tauri.localhost",  # production webview origin, Windows/Android WebView2
    "http://localhost:5173",  # `cargo tauri dev` — observed live, see above
    "http://127.0.0.1:5173",  # same dev server, alternate loopback form
]


async def _cors_healthz(request):  # noqa: ARG001 - Starlette endpoint signature
    return JSONResponse({"status": "ok"})


v1_app = CORSMiddleware(
    app,
    allow_origins=WEBVIEW_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

root_app = Starlette(
    routes=[
        Route(
            "/healthz",
            _cors_healthz,
            methods=["GET"],
            middleware=[
                Middleware(
                    CORSMiddleware,
                    allow_origins=["*"],
                    allow_methods=["GET"],
                    allow_headers=["*"],
                )
            ],
        ),
        Mount("/", app=v1_app),
    ]
)

# A fresh install has no `aura.db` at all (Task 4's app-data-dir resolution
# only creates the *directory*, not the file/schema) — the first request
# would otherwise fail with e.g. `sqlite3.OperationalError: no such table:
# projects`. `Base.metadata.create_all` only creates tables that don't
# already exist; it never alters existing ones, so this is intentionally not
# a migration system. That's fine for this app today: there is exactly one
# schema version in play and no installed base to migrate, so wiring up
# Alembic here would be over-engineering for what this task needs. By the
# time this runs, `from aura_api.main import app` above has already pulled in
# every router (`projects`, `uploads`, `jobs`, `exports`), which import
# `aura_api.models`, which registers every table on `Base.metadata` — so
# every real table is present here, not just a subset.
#
# `get_engine()` (from `aura_api.db`) is used instead of constructing a
# second `create_engine(...)` call here: it reads the same `DATABASE_URL`
# this module already set/inherited above, so schema creation targets
# exactly the same database file uvicorn's app will serve from.
def init_schema() -> None:
    """Create any tables that don't already exist for `DATABASE_URL`.

    Pulled out into its own function (rather than inlined under the
    `__main__` guard below) purely so `apps/desktop/tests/test_schema_init.py`
    can call this exact production code path directly — the guard itself
    only ever runs when this file is executed as a script, which a test
    importing the module never triggers.
    """
    Base.metadata.create_all(get_engine())


if __name__ == "__main__":
    init_schema()
    uvicorn.run(root_app, host="127.0.0.1", port=AURA_BACKEND_PORT)
