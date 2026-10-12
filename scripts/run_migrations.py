from app.db.migration_runner import run_migrations
from app.db.session import engine


if __name__ == "__main__":
    for migration_name in run_migrations(engine):
        print(f"Applied idempotent migration: {migration_name}")
