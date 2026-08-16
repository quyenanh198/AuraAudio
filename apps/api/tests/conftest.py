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


@pytest.fixture()
def db_session():
    engine = create_engine(os.environ["DATABASE_URL"], connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
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
