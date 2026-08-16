"""Regression test for `run_backend.init_schema()` (Task 4, desktop-shell-packaging).

The bug: a fresh desktop-app install has no `aura.db` at all (only the data
*directory* gets created, never the schema), so the first `POST /v1/projects`
(and, as this test also demonstrates, `GET /v1/jobs/{id}`) 500ed with
`sqlite3.OperationalError: no such table: projects`. The fix (commit
`826cced`) added `Base.metadata.create_all(get_engine())`, which
`run_backend.py` now exposes as the importable `init_schema()` function so it
can be called directly instead of only inline under `if __name__ ==
"__main__":`.

This test must exercise that real function against a genuinely fresh SQLite
file, not a reimplementation of the `create_all` line copy-pasted here. It
runs everything in a subprocess with a controlled environment rather than
`import run_backend` in-process, for a load-bearing reason: `aura_api.db`
binds its module-level `engine`/`SessionLocal` to whatever `DATABASE_URL` is
set the *first* time it is imported anywhere in the test process (see that
module's `engine = get_engine()` at import time). Because
`test_cors_scope.py` in this same directory also imports `run_backend` (and
therefore `aura_api.db`) and may be collected first, an in-process test here
could silently observe a *different* test's already-bound engine/session
instead of a real fresh database — a subprocess gets its own untouched
process state, so the app's request handlers are guaranteed to be talking to
the same fresh file this test just pointed `DATABASE_URL` at.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parent.parent

# Every table `aura_api.models` registers on `Base.metadata`. If schema
# creation silently regressed to only creating a subset (e.g. an import got
# reordered so a router/model module stopped being reached before
# `init_schema()` runs), this catches it even if the one probed HTTP route
# happens to still work.
EXPECTED_TABLES = {
    "projects",
    "media_assets",
    "transcription_jobs",
    "stage_artifacts",
    "score_revisions",
    "exports",
}

# Executed in a fresh subprocess (see module docstring for why). Calls the
# real `run_backend.init_schema()` — not a copy of its body — and drives the
# real `run_backend.root_app` through `TestClient` both before and after, so
# the parent test can assert the exact before/after behavior change the fix
# is responsible for.
_SUBPROCESS_SCRIPT = textwrap.dedent(
    """
    import json
    import sys

    sys.path.insert(0, sys.argv[1])

    import run_backend
    from starlette.testclient import TestClient

    client = TestClient(run_backend.root_app, raise_server_exceptions=False)

    # Before schema creation: the DB file has no tables at all yet, so this
    # must fail the same way the shipped bug failed (a 500 from the
    # uncaught `sqlite3.OperationalError: no such table`), never a clean 404.
    pre_response = client.get("/v1/jobs/regression-test-job")

    run_backend.init_schema()

    # After schema creation: the table exists, the row genuinely doesn't, so
    # this must now be a clean 404 - proof the real request path is talking
    # to a database with real tables, not just that a file exists on disk.
    post_response = client.get("/v1/jobs/regression-test-job")

    print(json.dumps({
        "pre_status": pre_response.status_code,
        "post_status": post_response.status_code,
    }))
    """
)


def _run_fresh_install(db_path: Path) -> dict:
    """Run `_SUBPROCESS_SCRIPT` in a fresh process pointed at `db_path`.

    Returns the parsed JSON the script printed. Raises via `check=True` if
    the subprocess itself errored (e.g. an uncaught exception outside the
    two probed requests), so a broken schema-init path fails loudly instead
    of producing a confusing assertion failure downstream.
    """
    env = {
        "PATH": __import__("os").environ.get("PATH", ""),
        "AURA_DATA_DIR": str(db_path.parent),
        "DATABASE_URL": f"sqlite:///{db_path}",
    }
    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT, str(DESKTOP_DIR)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_fresh_install_gets_real_schema_and_serves_requests(tmp_path: Path) -> None:
    """A brand-new, nonexistent DB file ends up with real tables after
    `run_backend.init_schema()`, and the real app can then serve a request
    against it without the pre-fix "no such table" 500.
    """
    db_path = tmp_path / "aura.db"
    assert not db_path.exists(), "test setup bug: db_path must start out nonexistent"

    outcome = _run_fresh_install(db_path)

    # The exact bug this guards against: before the fix, this route 500s on
    # a fresh install because no tables exist yet.
    assert outcome["pre_status"] == 500
    # After `init_schema()` runs, the same route resolves cleanly to "not
    # found" - the table exists, only the row doesn't.
    assert outcome["post_status"] == 404

    assert db_path.exists(), "init_schema() should have created the sqlite file"

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    finally:
        connection.close()
    table_names = {row[0] for row in rows}

    assert EXPECTED_TABLES <= table_names, (
        f"missing tables after init_schema(): {EXPECTED_TABLES - table_names}"
    )
