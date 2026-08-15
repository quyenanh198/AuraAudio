import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://aura:aura@localhost:5432/aura")

from aura_api.db import Base  # noqa: E402
from aura_api.models import MediaAsset, Project, TranscriptionJob  # noqa: E402


@pytest.fixture()
def db_session():
    engine = create_engine(os.environ["DATABASE_URL"])
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


@pytest.fixture()
def sample_job(db_session):
    project = Project(owner_id="anonymous", title="X", instrument="guitar")
    db_session.add(project)
    db_session.flush()
    asset = MediaAsset(project_id=project.id, kind="source", object_key="uploads/a/riff.wav")
    db_session.add(asset)
    db_session.flush()
    job = TranscriptionJob(project_id=project.id, media_asset_id=asset.id, input_hash="h1", status="queued")
    db_session.add(job)
    db_session.commit()
    return job


@pytest.fixture()
def workdir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)
