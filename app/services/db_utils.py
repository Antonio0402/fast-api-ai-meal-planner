from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def fetch_one(
    db: Session, statement: str, parameters: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    row = db.execute(text(statement), parameters or {}).mappings().first()
    return dict(row) if row is not None else None


def fetch_all(
    db: Session, statement: str, parameters: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    rows = db.execute(text(statement), parameters or {}).mappings().all()
    return [dict(row) for row in rows]
