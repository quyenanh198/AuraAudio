from sqlalchemy import text

from aura_api.db import get_engine


def test_get_engine_connects_against_sqlite_url(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    engine = get_engine()
    with engine.connect() as conn:
        assert conn.execute(text("select 1")).scalar() == 1
