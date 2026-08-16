import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_TEST_DB_PATH = Path(tempfile.gettempdir()) / "aura_api_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEST_DB_PATH}")
# Temporary: Task 2 will make these optional. For now, set dummy values for config to load.
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("S3_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("S3_ACCESS_KEY", "dummy")
os.environ.setdefault("S3_SECRET_KEY", "dummy")
os.environ.setdefault("S3_BUCKET", "dummy")
os.environ.setdefault("S3_REGION", "us-east-1")

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
