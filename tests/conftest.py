import base64
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import text
from sqlalchemy.engine import make_url
from starlette.testclient import TestClient


test_database_url = os.environ.get("TEST_DATABASE_URL")
if not test_database_url:
    raise RuntimeError(
        "Set TEST_DATABASE_URL to a dedicated localhost PostgreSQL test database."
    )
parsed_test_url = make_url(test_database_url)
if parsed_test_url.host not in {"localhost", "127.0.0.1", "::1"}:
    raise RuntimeError("Tests refuse to connect to a non-local PostgreSQL host.")
if not parsed_test_url.database or parsed_test_url.database.lower() == "postgres":
    raise RuntimeError("Tests require a dedicated database, not the postgres database.")

os.environ["DATABASE_URL"] = test_database_url
os.environ["SUPABASE_URL"] = "https://freshmeal-test.invalid"
os.environ["SUPABASE_JWT_SECRET"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["SCAN_STORAGE_DIR"] = "var/test-scans"

from app.core.auth import get_jwks
from app.db.migration_runner import execute_sql_file, run_migrations
from app.db.session import engine
from app.main import app

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _base64url_integer(value: int) -> str:
    size = (value.bit_length() + 7) // 8
    encoded = base64.urlsafe_b64encode(value.to_bytes(size, "big")).decode("ascii")
    return encoded.rstrip("=")


public_numbers = _private_key.public_key().public_numbers()
jwks = {
    "keys": [
        {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": "freshmeal-test-key",
            "n": _base64url_integer(public_numbers.n),
            "e": _base64url_integer(public_numbers.e),
        }
    ]
}


def token_for(user_id: str, *, expires_at: datetime | None = None) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user_id,
            "iss": "https://freshmeal-test.invalid/auth/v1",
            "aud": "authenticated",
            "iat": now,
            "exp": expires_at or now + timedelta(minutes=10),
        },
        _private_key,
        algorithm="RS256",
        headers={"kid": "freshmeal-test-key"},
    )


@pytest.fixture(scope="session")
def initialized_database():
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS auth"))
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS auth.users (
                    id UUID PRIMARY KEY
                )
                """
            )
        )
    run_migrations(engine)
    seed_file = Path(__file__).resolve().parents[1] / "seed" / "seed_data.sql"
    execute_sql_file(engine, seed_file, transactional=False)
    yield
    scan_storage = Path(__file__).resolve().parents[1] / "var" / "test-scans"
    if scan_storage.exists():
        for image_path in scan_storage.iterdir():
            if image_path.is_file():
                image_path.unlink()


@pytest.fixture
def client(initialized_database):
    app.dependency_overrides[get_jwks] = lambda: jwks
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def auth_user(initialized_database):
    user_id = str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO auth.users (id) VALUES (:user_id)"),
            {"user_id": user_id},
        )
    try:
        yield {"id": user_id, "token": token_for(user_id)}
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM auth.users WHERE id = :user_id"),
                {"user_id": user_id},
            )


@pytest.fixture
def second_auth_user(initialized_database):
    user_id = str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO auth.users (id) VALUES (:user_id)"),
            {"user_id": user_id},
        )
    try:
        yield {"id": user_id, "token": token_for(user_id)}
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM auth.users WHERE id = :user_id"),
                {"user_id": user_id},
            )


@pytest.fixture
def headers(auth_user):
    return {"Authorization": f"Bearer {auth_user['token']}"}


@pytest.fixture
def expired_token(auth_user):
    return token_for(
        auth_user["id"],
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )


@pytest.fixture
def other_headers(second_auth_user):
    return {"Authorization": f"Bearer {second_auth_user['token']}"}
