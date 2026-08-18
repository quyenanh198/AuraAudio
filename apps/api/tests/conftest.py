import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_TEST_DB_PATH = Path(tempfile.gettempdir()) / "aura_api_test.db"
# Unconditional (not setdefault): `.envrc` exports DATABASE_URL/AURA_DATA_DIR
# pointing at the real app's ./data before `make test` even starts pytest,
# so a plain setdefault would be a no-op and the suite would run against —
# and truncate — the real application database and blob directory.
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"
os.environ["AURA_DATA_DIR"] = tempfile.gettempdir()

from aura_api.db import Base  # noqa: E402
from aura_api.migrations import run_migrations  # noqa: E402

# Bootstrap the test database the same way the real app does — via Alembic,
# not Base.metadata.create_all. The two disagree: create_all leaves no
# alembic_version row, so the startup migration then tries to create tables
# that already exist and dies with "table projects already exists". Any test
# that enters a TestClient as a context manager (which runs the lifespan
# handler, and so the startup migration) hit that; tests using TestClient
# without `with` never ran startup and silently did not.
#
# The stale file from a previous run is removed first: it may predate this
# and carry create_all-made tables with no stamp.
_TEST_DB_PATH.unlink(missing_ok=True)


@pytest.fixture()
def db_session():
    engine = create_engine(os.environ["DATABASE_URL"], connect_args={"check_same_thread": False})
    run_migrations()
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.rollback()
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
        session.close()
