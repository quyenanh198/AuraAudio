from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config


def run_migrations() -> None:
    """Bring the database up to head.

    Called at API startup so a fresh install works with no manual step —
    before this existed, nothing created the schema outside the test
    conftest, so a clean data directory served /healthz and accepted an
    upload (filesystem only) and then failed the first database write with
    "no such table: projects".

    script_location must be absolute. A packaged app's working directory is
    whatever the OS gave it, so alembic.ini's relative path does not
    resolve; this is also why the alembic directory lives inside the
    package rather than beside it, since the wheel is built from
    src/aura_api only.
    """
    url = os.environ["DATABASE_URL"]
    if url.startswith("sqlite:///"):
        db_file = url[len("sqlite:///") :]
        if db_file != ":memory:":
            Path(db_file).parent.mkdir(parents=True, exist_ok=True)

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
