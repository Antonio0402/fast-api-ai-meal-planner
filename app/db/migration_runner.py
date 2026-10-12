from pathlib import Path

from sqlalchemy.engine import Engine

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "sql" / "migrations"


def execute_sql_file(
    engine: Engine, sql_file: Path, *, transactional: bool = True
) -> None:
    script = sql_file.read_text(encoding="utf-8")
    if transactional:
        with engine.begin() as connection:
            raw_connection = connection.connection.driver_connection
            with raw_connection.cursor() as cursor:
                cursor.execute(script, prepare=False)
        return
    raw_connection = engine.raw_connection()
    try:
        with raw_connection.cursor() as cursor:
            cursor.execute(script, prepare=False)
        raw_connection.commit()
    except Exception:
        raw_connection.rollback()
        raise
    finally:
        raw_connection.close()


def run_migrations(engine: Engine) -> list[str]:
    applied: list[str] = []
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        execute_sql_file(engine, migration)
        applied.append(migration.name)
    return applied
