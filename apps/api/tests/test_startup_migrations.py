import sqlite3
from pathlib import Path

from aura_api.migrations import run_migrations


def test_run_migrations_creates_schema_in_an_empty_database(tmp_path: Path, monkeypatch):
    db = tmp_path / "fresh.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")

    run_migrations()

    tables = {
        row[0]
        for row in sqlite3.connect(db).execute(
            "select name from sqlite_master where type='table'"
        )
    }
    assert "projects" in tables
    assert "transcription_jobs" in tables


def test_run_migrations_is_idempotent(tmp_path: Path, monkeypatch):
    db = tmp_path / "twice.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")

    run_migrations()
    run_migrations()  # must not raise on an already-migrated database


def test_run_migrations_creates_the_parent_directory(tmp_path: Path, monkeypatch):
    # A packaged app's data directory does not exist on first launch.
    db = tmp_path / "does" / "not" / "exist" / "aura.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")

    run_migrations()

    assert db.exists()
