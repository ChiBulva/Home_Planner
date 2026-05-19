from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy import inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    ensure_schema()


def ensure_schema() -> None:
    """Small SQLite-friendly migrations until the schema is ready for Alembic."""
    inspector = inspect(engine)
    if "tasks" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("tasks")}
    statements: list[str] = []
    if "project_name" not in columns:
        statements.append("ALTER TABLE tasks ADD COLUMN project_name VARCHAR(120) DEFAULT ''")
    if "project_order" not in columns:
        statements.append("ALTER TABLE tasks ADD COLUMN project_order INTEGER DEFAULT 0")
    if "color" not in columns:
        statements.append("ALTER TABLE tasks ADD COLUMN color VARCHAR(20) DEFAULT '#147d74'")
    if "completed_date" not in columns:
        statements.append("ALTER TABLE tasks ADD COLUMN completed_date DATE")
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
        conn.execute(text("UPDATE tasks SET status = 'incomplete' WHERE status IS NULL OR status = ''"))
        conn.execute(text("UPDATE tasks SET color = '#147d74' WHERE color IS NULL OR color = ''"))
        conn.execute(text("UPDATE tasks SET type = 'chore' WHERE type IS NULL OR type = ''"))
        conn.execute(
            text(
                "UPDATE tasks SET reset_frequency = 'daily' "
                "WHERE reset_frequency IS NULL OR reset_frequency = ''"
            )
        )
        conn.execute(
            text(
                "UPDATE tasks SET completed_date = date(updated_at) "
                "WHERE status = 'complete' AND completed_date IS NULL"
            )
        )


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
