import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

import psycopg
from psycopg.rows import dict_row


def inspect_database() -> dict:
    database_url = os.environ["SCHEMA_DATABASE_URL"]
    with psycopg.connect(database_url, connect_timeout=15, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SET LOCAL statement_timeout = 15000")
            cursor.execute("SET LOCAL search_path = public, pg_catalog")
            cursor.execute("SELECT current_database() AS name")
            database = cursor.fetchone()["name"]
            cursor.execute("""
                SELECT c.relname AS name, c.relkind AS kind,
                       c.relrowsecurity AS rls, c.relforcerowsecurity AS force_rls
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m')
                ORDER BY c.relname
            """)
            tables = cursor.fetchall()
            cursor.execute("""
                SELECT c.relname AS table_name, a.attname AS name,
                       format_type(a.atttypid, a.atttypmod) AS type,
                       a.attnotnull AS not_null, a.attidentity AS identity,
                       a.attgenerated AS generated,
                       pg_get_expr(d.adbin, d.adrelid) AS default_expression
                FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m')
                  AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY c.relname, a.attnum
            """)
            columns = cursor.fetchall()
            cursor.execute("""
                SELECT c.relname AS table_name, con.conname AS name,
                       con.convalidated AS validated,
                       pg_get_constraintdef(con.oid, true) AS definition
                FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' ORDER BY c.relname, con.conname
            """)
            constraints = cursor.fetchall()
            cursor.execute("SELECT tablename AS table_name, indexname AS name, indexdef AS definition FROM pg_indexes WHERE schemaname = 'public' ORDER BY tablename, indexname")
            indexes = cursor.fetchall()
            cursor.execute("SELECT tablename AS table_name, policyname AS name, permissive, roles, cmd, qual, with_check FROM pg_policies WHERE schemaname = 'public' ORDER BY tablename, policyname")
            policies = cursor.fetchall()
            cursor.execute("SELECT table_name, grantee, privilege_type, is_grantable FROM information_schema.table_privileges WHERE table_schema = 'public' ORDER BY table_name, grantee, privilege_type")
            grants = cursor.fetchall()
    migrations = Path(__file__).resolve().parents[1] / "sql" / "migrations"
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(migrations.glob("*.sql"))}
    return {"database": database, "tables": tables, "columns": columns,
            "constraints": constraints, "indexes": indexes, "policies": policies,
            "grants": grants, "migration_sources": hashes}


def export_api() -> dict:
    # Cấu hình riêng cho tiến trình CLI: không sửa .env hoặc khởi động lifespan.
    os.environ.update({
        "DATABASE_URL": "sqlite+pysqlite:///:memory:", "SUPABASE_URL": "",
        "SUPABASE_JWT_SECRET": "", "GEMINI_API_KEY": "",
        "IS_MOCKING": "false", "WEB_ORIGINS": "",
    })
    from app.main import app

    return app.openapi()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["api", "database"])
    parser.add_argument("--output", type=Path, help="Ghi JSON vào file thay vì stdout.")
    args = parser.parse_args()
    document = export_api() if args.mode == "api" else inspect_database()
    content = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(content, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(content)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # DSN và lỗi driver có thể chứa thông tin đăng nhập; không in exception gốc.
        message = str(error).lower()
        reason = next((label for token, label in [
            ('password authentication failed', 'DB_PASSWORD_REJECTED'),
            ('tenant or user not found', 'DB_TENANT_REJECTED'),
            ('timeout', 'DB_TIMEOUT'), ('could not translate', 'DB_DNS_ERROR'),
            ('connection refused', 'DB_CONNECTION_REFUSED'),
            ('permission denied', 'DB_PERMISSION_DENIED'),
            ('ssl', 'DB_SSL_ERROR'),
        ] if token in message), type(error).__name__)
        print(f"Không đọc được metadata database: {reason}, SQLSTATE={getattr(error, 'sqlstate', None)}.", file=sys.stderr)
        sys.exit(1)
