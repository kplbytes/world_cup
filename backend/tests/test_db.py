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

