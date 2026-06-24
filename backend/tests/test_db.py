from sqlalchemy import text

from app.db import create_database, session_scope


def test_database_uses_wal_and_commits(tmp_path):
    create_database(tmp_path / "test.sqlite3")

    with session_scope() as session:
        session.execute(text("CREATE TABLE probe (value INTEGER NOT NULL)"))
        session.execute(text("INSERT INTO probe VALUES (42)"))

    with session_scope() as session:
        assert session.scalar(text("SELECT value FROM probe")) == 42
        assert session.scalar(text("PRAGMA journal_mode")) == "wal"
        assert session.scalar(text("PRAGMA busy_timeout")) >= 30000


def test_create_database_disposes_previous_engine(tmp_path, monkeypatch):
    old_engine = create_database(tmp_path / "old.sqlite3")
    calls = []

    def dispose() -> None:
        calls.append("disposed")

    monkeypatch.setattr(old_engine, "dispose", dispose)

    new_engine = create_database(tmp_path / "new.sqlite3")

    assert new_engine is not old_engine
    assert calls == ["disposed"]
