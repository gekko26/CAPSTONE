"""Shared pytest fixtures for the AlcoDetect backend test suite.

The `db` fixture lives here so every test module gets the same throwaway schema.
Engine selection is deliberately SQLite-in-tmp_path: the suite must never touch the
live MariaDB instance.
"""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from database.db import Base


@pytest.fixture()
def db(tmp_path):
    """Real ORM rows against a throwaway SQLite file in tmp_path.

    SQLite ignores FOREIGN KEY constraints unless you switch them on, and MariaDB
    (production) always enforces them — so the PRAGMA below is what makes this
    fixture behave like the real database. Without it a test can happily store a
    subject_id that has no subjects row, which MariaDB rejects with error 1452.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}",
                           connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()