import pytest
from sqlalchemy.orm import Session

from app.db import create_database


@pytest.fixture
def db_session(tmp_path) -> Session:
    engine = create_database(tmp_path / "test.sqlite3")
    with Session(engine) as session:
        yield session

