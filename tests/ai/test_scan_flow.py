"""Kiểm tra luồng scan bằng ảnh và transport giả; không mở database hoặc gọi Gemini."""

from io import BytesIO
from urllib.error import URLError

import pytest
from fastapi import FastAPI
from PIL import Image
from starlette.testclient import TestClient

from app.ai.vendor.gemini_adapter import (
    GeminiAdapter, GeminiApiError, GeminiTimeoutError, GeminiTransportError,
)
from app.ai.vendor.key_routing import ApiKeyEntry, ApiKeyRouter
from app.ai.vendor.scan import MAX_CANDIDATES, SCAN_RESPONSE_SCHEMA, ScanIngredientsError, scan_ingredients


@pytest.fixture
def scan_client(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test@localhost/ai_unit_test")
    from app.api.routes import scan_jobs
    from app.core.auth import get_current_user
    from app.core.errors import install_error_handlers
    from app.services import scan_service

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(scan_jobs.router, prefix="/api/v1/scan-jobs")
    app.dependency_overrides[get_current_user] = lambda: "fixture-user"
    with TestClient(app) as client:
        yield client, scan_service


def upload(client):
    buffer = BytesIO()
    Image.new("RGB", (12, 12)).save(buffer, format="JPEG")
    return client.post("/api/v1/scan-jobs/detect", files={
        "file": ("photo.jpg", buffer.getvalue(), "image/jpeg"),
    })


@pytest.mark.parametrize("error,status,code", [
    (GeminiApiError(429), 429, "AI_RATE_LIMITED"),
    (GeminiTimeoutError("private timeout"), 504, "AI_TIMEOUT"),
    (GeminiApiError(504), 504, "AI_TIMEOUT"),
    (GeminiApiError(403), 503, "AI_CONFIG_ERROR"),
    (GeminiTransportError("private transport"), 503, "AI_PROVIDER_UNAVAILABLE"),
    (ScanIngredientsError("OUTPUT_SCHEMA_INVALID", "private JSON"), 502, "AI_OUTPUT_INVALID"),
])
def test_detect_preserves_safe_error_codes(scan_client, monkeypatch, error, status, code):
    client, service = scan_client

    class Model:
        def scan_ingredients(self, **kwargs):
            raise error

    monkeypatch.setattr(service, "create_gemini_client", Model)
    response = upload(client)
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert "private" not in response.text


@pytest.mark.parametrize("quality,ingredients,status", [
    ("ok", [{"name": "Cà chua", "confidence": None}], 200),
    ("no_food", [], 200),
    ("blurry", [], 422),
    ("no_food", [{"name": "Trứng"}], 502),
    ("ok", "invalid", 502),
])
def test_detect_result_contract(scan_client, monkeypatch, quality, ingredients, status):
    client, service = scan_client

    class Model:
        def scan_ingredients(self, **kwargs):
            return {"image_quality": quality, "ingredients": ingredients}

    monkeypatch.setattr(service, "create_gemini_client", Model)
    response = upload(client)
    assert response.status_code == status
    if quality == "ok" and status == 200:
        item = response.json()[0]
        assert item["name"] == "Cà chua"
        assert item["unit"] == ""
        assert "quantity" not in item
    if quality == "no_food" and status == 200:
        assert response.json() == []
    if quality == "blurry":
        assert response.json()["detail"]["code"] == "blurry"


def adapter():
    return GeminiAdapter(key_router=ApiKeyRouter([
        ApiKeyEntry(name="fixture", api_key="fixture-key"),
    ]), model="gemini-2.5-flash")


def test_scan_request_contains_bounded_schema(monkeypatch):
    payloads = []

    def request(self, payload):
        payloads.append(payload)
        return {"candidates": [{"content": {"parts": [{
            "text": '{"image_quality":"no_food","ingredients":[]}',
        }]}}]}

    monkeypatch.setattr(GeminiAdapter, "_request", request)
    scan_ingredients(image_bytes=b"fixture", mime_type="image/jpeg", model_client=adapter())
    config = payloads[0]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"] == SCAN_RESPONSE_SCHEMA
    assert config["responseJsonSchema"]["properties"]["ingredients"]["maxItems"] == MAX_CANDIDATES == 20
    assert "at most 20" in payloads[0]["contents"][0]["parts"][0]["text"]


@pytest.mark.parametrize("failure", [TimeoutError("private"), URLError(TimeoutError("private"))])
def test_transport_preserves_timeout(monkeypatch, failure):
    def urlopen(*args, **kwargs):
        raise failure

    monkeypatch.setattr("app.ai.vendor.gemini_adapter.urlopen", urlopen)
    with pytest.raises(GeminiTimeoutError):
        adapter().scan_ingredients(image_bytes=b"fixture", mime_type="image/jpeg", prompt="fixture")


def test_scan_still_rejects_provider_over_limit():
    class Model:
        def scan_ingredients(self, **kwargs):
            return {"image_quality": "ok", "ingredients": [{"name": "Trứng"}] * 21}

    with pytest.raises(ScanIngredientsError):
        scan_ingredients(image_bytes=b"fixture", mime_type="image/jpeg", model_client=Model())
