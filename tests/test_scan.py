from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import text

from app.core.errors import APIError
from app.core.image_storage import _object_path
from app.db.session import engine
from app.services import scan_service
from tests.fakes import FakeGeminiClient


def make_jpeg(color: tuple[int, int, int] = (12, 80, 190)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (12, 12), color).save(buffer, format="JPEG")
    return buffer.getvalue()


def post_scan(client, headers, *, key: str = "scan-test", image: bytes | None = None):
    return client.post(
        "/api/v1/scan-jobs",
        headers={**headers, "Idempotency-Key": key},
        files={"image": ("ingredients.jpg", image or make_jpeg(), "image/jpeg")},
    )


def test_scan_job_success_idempotency_and_confirm(client, headers, monkeypatch):
    fake = FakeGeminiClient(
        scan_result={
            "image_quality": "ok",
            "ingredients": [{"name": "Cà chua", "confidence": None}],
        }
    )
    monkeypatch.setattr(scan_service, "create_gemini_client", lambda: fake)

    created = post_scan(client, headers, key="scan-confirm-1")
    assert created.status_code == 202
    job_id = created.json()["scan_job_id"]
    current = client.get(f"/api/v1/scan-jobs/{job_id}", headers=headers)
    assert current.status_code == 200
    assert current.json()["status"] == "succeeded"
    candidate = current.json()["candidates"][0]
    assert candidate["suggested_name"] == "Cà chua"
    assert candidate["confidence"] is None

    replay = post_scan(client, headers, key="scan-confirm-1")
    assert replay.status_code == 202
    assert replay.json()["scan_job_id"] == job_id
    mismatch = post_scan(
        client, headers, key="scan-confirm-1", image=make_jpeg((250, 30, 40))
    )
    assert mismatch.status_code == 409

    payload = {
        "items": [
            {
                "candidate_id": candidate["id"],
                "ingredient_id": candidate["ingredient_id"],
                "name": candidate["suggested_name"],
                "quantity": 100,
                "unit_code": "g",
                "expiry_date": None,
            }
        ]
    }
    confirmation = client.post(
        f"/api/v1/scan-jobs/{job_id}/confirm", headers=headers, json=payload
    )
    assert confirmation.status_code == 200
    assert confirmation.json()["status"] == "confirmed"
    repeated = client.post(
        f"/api/v1/scan-jobs/{job_id}/confirm", headers=headers, json=payload
    )
    assert repeated.status_code == 200
    assert repeated.json() == confirmation.json()
    changed = client.post(
        f"/api/v1/scan-jobs/{job_id}/confirm",
        headers=headers,
        json={
            "items": [
                {
                    **payload["items"][0],
                    "quantity": 200,
                }
            ]
        },
    )
    assert changed.status_code == 409
    inventory = client.get("/api/v1/inventory-items", headers=headers)
    assert inventory.json()["total"] == 1
    assert inventory.json()["items"][0]["quantity"] == 100


@pytest.mark.parametrize(
    ("fake", "expected_code"),
    [
        (
            FakeGeminiClient(scan_result={"image_quality": "ok", "ingredients": "bad"}),
            "AI_OUTPUT_INVALID",
        ),
        (FakeGeminiClient(failure="rate_limited"), "AI_RATE_LIMITED"),
        (FakeGeminiClient(failure="provider_unavailable"), "AI_PROVIDER_UNAVAILABLE"),
        (
            FakeGeminiClient(
                scan_result={"image_quality": "blurry", "ingredients": []}
            ),
            "IMAGE_BLURRY",
        ),
    ],
)
def test_scan_worker_maps_ai_failures(client, headers, monkeypatch, fake, expected_code):
    monkeypatch.setattr(scan_service, "create_gemini_client", lambda: fake)
    created = post_scan(client, headers, key=f"failure-{expected_code}")
    assert created.status_code == 202

    result = client.get(
        f"/api/v1/scan-jobs/{created.json()['scan_job_id']}", headers=headers
    )
    assert result.status_code == 200
    assert result.json()["status"] == "failed"
    assert result.json()["error"]["code"] == expected_code


def test_scan_worker_maps_missing_ai_configuration(client, headers, monkeypatch):
    def no_configuration():
        raise APIError(503, "AI_CONFIG_ERROR", "test provider not configured")

    monkeypatch.setattr(scan_service, "create_gemini_client", no_configuration)
    created = post_scan(client, headers, key="failure-config")
    result = client.get(
        f"/api/v1/scan-jobs/{created.json()['scan_job_id']}", headers=headers
    )
    assert result.json()["error"]["code"] == "AI_CONFIG_ERROR"


def test_empty_scan_succeeds_with_no_candidates(client, headers, monkeypatch):
    fake = FakeGeminiClient(
        scan_result={"image_quality": "no_food", "ingredients": []}
    )
    monkeypatch.setattr(scan_service, "create_gemini_client", lambda: fake)
    created = post_scan(client, headers, key="empty-scan")
    result = client.get(
        f"/api/v1/scan-jobs/{created.json()['scan_job_id']}", headers=headers
    )
    assert result.json()["status"] == "succeeded"
    assert result.json()["candidates"] == []


def test_upload_size_media_type_and_decode_validation(client, headers):
    oversized = b"\xff\xd8\xff" + b"x" * (5 * 1024 * 1024)
    too_large = post_scan(client, headers, key="too-large", image=oversized)
    assert too_large.status_code == 413

    unsupported = client.post(
        "/api/v1/scan-jobs",
        headers={**headers, "Idempotency-Key": "unsupported"},
        files={"image": ("food.gif", b"GIF89a", "image/gif")},
    )
    assert unsupported.status_code == 415

    corrupted = post_scan(
        client, headers, key="corrupted", image=b"\xff\xd8\xffnot-a-jpeg"
    )
    assert corrupted.status_code == 422
    assert client.post(
        "/api/v1/scan-jobs",
        headers=headers,
        files={"image": ("food.jpg", make_jpeg(), "image/jpeg")},
    ).status_code == 422


def test_expired_job_returns_200_and_deletes_private_image(client, headers, monkeypatch):
    monkeypatch.setattr(
        scan_service,
        "create_gemini_client",
        lambda: FakeGeminiClient(),
    )
    created = post_scan(client, headers, key="expires")
    job_id = created.json()["scan_job_id"]
    with engine.begin() as connection:
        row = connection.execute(
            text(
                """
                SELECT image_object_key FROM scan_jobs
                WHERE id = :job_id
                """
            ),
            {"job_id": job_id},
        ).mappings().one()
        image_path = _object_path(row["image_object_key"])
        connection.execute(
            text(
                """
                UPDATE scan_jobs SET expires_at = now() - interval '1 second'
                WHERE id = :job_id
                """
            ),
            {"job_id": job_id},
        )
    expired = client.get(f"/api/v1/scan-jobs/{job_id}", headers=headers)
    assert expired.status_code == 200
    assert expired.json()["status"] == "expired"
    assert not image_path.exists()


def test_stuck_processing_job_is_marked_failed(client, auth_user, headers):
    client.get("/api/v1/users/me/profile", headers=headers)
    job_id = str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO scan_jobs (
                    id, user_id, status, idempotency_key, created_at,
                    updated_at, expires_at
                ) VALUES (
                    :job_id, :user_id, 'processing', :key,
                    now() - interval '10 minutes',
                    now() - interval '10 minutes',
                    now() + interval '20 minutes'
                )
                """
            ),
            {
                "job_id": job_id,
                "user_id": auth_user["id"],
                "key": f"stuck-{job_id}",
            },
        )
    scan_service.reconcile_scan_jobs()
    with engine.connect() as connection:
        status_row = connection.execute(
            text("SELECT status, error_code FROM scan_jobs WHERE id = :job_id"),
            {"job_id": job_id},
        ).one()
    assert status_row == ("failed", "AI_PROVIDER_UNAVAILABLE")


def test_scan_job_is_not_visible_to_another_user(client, headers, other_headers, monkeypatch):
    monkeypatch.setattr(
        scan_service,
        "create_gemini_client",
        lambda: FakeGeminiClient(),
    )
    created = post_scan(client, headers, key="private-job")
    result = client.get(
        f"/api/v1/scan-jobs/{created.json()['scan_job_id']}",
        headers=other_headers,
    )
    assert result.status_code == 404
