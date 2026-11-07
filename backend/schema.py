"""Compatibility helpers for older database schemas."""

from sqlalchemy import text
from sqlalchemy.engine import Engine


def ensure_user_columns(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    with engine.begin() as connection:
        rows = connection.execute(text("PRAGMA table_info(users)"))
        user_columns = {str(row[1]) for row in rows}
        if not user_columns:
            return
        for column_name, column_def in {
            "musician": "BOOLEAN NOT NULL DEFAULT 0",
            "is_singer": "BOOLEAN NOT NULL DEFAULT 0",
            "mm_username": "VARCHAR(128)",
        }.items():
            if column_name not in user_columns:
                connection.execute(text(f"ALTER TABLE users ADD COLUMN {column_name} {column_def}"))
