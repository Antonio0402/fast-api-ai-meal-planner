from pathlib import Path

from app.db.migration_runner import execute_sql_file
from app.db.session import engine
from app.core.config import settings


if __name__ == "__main__":
    if not settings.is_mocking:
        raise SystemExit("Seed dữ liệu mẫu chỉ chạy khi IS_MOCKING=true.")
    seed_file = Path(__file__).resolve().parents[1] / "seed" / "seed_data.sql"
    execute_sql_file(engine, seed_file, transactional=False)
    print("Seed data applied.")
