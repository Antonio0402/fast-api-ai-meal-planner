from __future__ import annotations

import asyncio
import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any
from uuid import UUID

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from app.ai.client import create_gemini_client
from app.ai.vendor.gemini_adapter import GeminiApiError, GeminiTimeoutError, GeminiTransportError
from app.ai.vendor.scan import MAX_CANDIDATES, ScanIngredientsError, scan_ingredients
from app.core.config import settings
from app.core.errors import APIError
from app.core.image_storage import delete_scan_image, read_scan_image, save_scan_image
from app.core.normalization import normalize_name
from app.db.session import SessionLocal
from app.schemas.inventory import InventoryItemInput
from app.schemas.scan import ScanConfirmInput
from app.services.db_utils import fetch_all, fetch_one
from app.services.inventory_service import upsert_inventory_item

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
_worker_semaphore: asyncio.Semaphore | None = None
_ERROR_MESSAGES = {
    "INVALID_IMAGE_INPUT": "Ảnh tải lên không hợp lệ.",
    "AI_OUTPUT_INVALID": "AI trả về kết quả không hợp lệ.",
    "AI_RATE_LIMITED": "Dịch vụ nhận diện đang quá tải. Vui lòng thử lại.",
    "AI_TIMEOUT": "Nhận diện ảnh quá lâu. Vui lòng thử lại.",
    "AI_PROVIDER_UNAVAILABLE": "Dịch vụ nhận diện tạm thời không khả dụng.",
    "AI_CONFIG_ERROR": "Dịch vụ nhận diện chưa được cấu hình.",
    "IMAGE_BLURRY": "Ảnh chưa đủ rõ để nhận diện.",
}


async def read_and_validate_upload(upload: UploadFile) -> tuple[bytes, str]:
    if upload.content_type not in SUPPORTED_IMAGE_TYPES:
        raise APIError(415, "UNSUPPORTED_MEDIA_TYPE", "Chỉ hỗ trợ JPEG, PNG hoặc WebP.")
    image_bytes = await upload.read(MAX_IMAGE_BYTES + 1)
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise APIError(413, "IMAGE_TOO_LARGE", "Ảnh không được vượt quá 5 MB.")
    if not image_bytes:
        raise APIError(422, "INVALID_IMAGE_INPUT", "Ảnh tải lên rỗng.")
    detected_type = _detect_image_type(image_bytes)
    if detected_type is None:
        raise APIError(415, "UNSUPPORTED_MEDIA_TYPE", "Định dạng ảnh không được hỗ trợ.")
    if detected_type != upload.content_type:
        raise APIError(415, "UNSUPPORTED_MEDIA_TYPE", "MIME type không khớp nội dung ảnh.")
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise APIError(415, "UNSUPPORTED_MEDIA_TYPE", "Định dạng ảnh không được hỗ trợ.")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise APIError(413, "IMAGE_TOO_LARGE", "Kích thước ảnh giải mã quá lớn.")
            image.verify()
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
    except APIError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError) as exc:
        raise APIError(422, "INVALID_IMAGE_INPUT", "Ảnh không giải mã được.") from exc
    return image_bytes, detected_type


def _detect_image_type(image_bytes: bytes) -> str | None:
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if (
        len(image_bytes) >= 12
        and image_bytes[:4] == b"RIFF"
        and image_bytes[8:12] == b"WEBP"
    ):
        return "image/webp"
    return None


def image_request_hash(image_bytes: bytes) -> str:
    return hashlib.sha256(image_bytes).hexdigest()


def create_scan_job(
    *,
    db: Any,
    user_id: str,
    idempotency_key: str,
    request_fingerprint: str,
    image_bytes: bytes,
    mime_type: str,
) -> tuple[dict[str, Any], bool]:
    if not idempotency_key.strip() or len(idempotency_key) > 200:
        raise APIError(422, "VALIDATION_ERROR", "Idempotency-Key không hợp lệ.")
    object_key: str | None = None
    try:
        with db.begin():
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                {"lock_key": f"scan:{user_id}:{idempotency_key}"},
            )
            existing = fetch_one(
                db,
                """
                SELECT id, status, created_at, expires_at, request_hash
                FROM scan_jobs
                WHERE user_id = :user_id AND idempotency_key = :idempotency_key
                """,
                {"user_id": user_id, "idempotency_key": idempotency_key},
            )
            if existing:
                if existing["request_hash"] != request_fingerprint:
                    raise APIError(
                        409,
                        "IDEMPOTENCY_CONFLICT",
                        "Idempotency-Key đã dùng với ảnh khác.",
                    )
                return _job_created_response(existing), False
            object_key = save_scan_image(image_bytes, mime_type)
            now = datetime.now(UTC)
            row = fetch_one(
                db,
                """
                INSERT INTO scan_jobs (
                    user_id, status, image_object_key, idempotency_key,
                    request_hash, created_at, updated_at, expires_at
                ) VALUES (
                    :user_id, 'queued', :image_object_key, :idempotency_key,
                    :request_hash, :created_at, :created_at, :expires_at
                )
                RETURNING id, status, created_at, expires_at
                """,
                {
                    "user_id": user_id,
                    "image_object_key": object_key,
                    "idempotency_key": idempotency_key,
                    "request_hash": request_fingerprint,
                    "created_at": now,
                    "expires_at": now
                    + timedelta(minutes=settings.scan_job_ttl_minutes),
                },
            )
        return _job_created_response(row), True
    except Exception:
        if object_key:
            delete_scan_image(object_key)
        raise


def _job_created_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "scan_job_id": str(row["id"]),
        "status": row["status"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
    }


def reconcile_scan_jobs() -> None:
    expired_keys: list[str] = []
    with SessionLocal() as db:
        with db.begin():
            expired = fetch_all(
                db,
                """
                WITH expired_jobs AS (
                    SELECT id, image_object_key
                    FROM scan_jobs
                    WHERE expires_at <= now() AND status <> 'expired'
                    FOR UPDATE
                ),
                updated_jobs AS (
                    UPDATE scan_jobs AS job
                    SET status = 'expired', error_code = NULL, updated_at = now(),
                        image_object_key = NULL
                    FROM expired_jobs AS expired
                    WHERE job.id = expired.id
                    RETURNING expired.image_object_key
                )
                SELECT image_object_key FROM updated_jobs
                """,
            )
            expired_keys.extend(
                row["image_object_key"] for row in expired if row["image_object_key"]
            )
            fetch_all(
                db,
                """
                UPDATE scan_jobs
                SET status = 'failed', error_code = 'AI_PROVIDER_UNAVAILABLE',
                    updated_at = now()
                WHERE status = 'processing'
                  AND expires_at > now()
                  AND updated_at < now() - (:timeout_seconds * interval '1 second')
                RETURNING id
                """,
                {"timeout_seconds": settings.scan_stuck_timeout_seconds},
            )
    for object_key in expired_keys:
        delete_scan_image(object_key)


def get_scan_job(db: Any, user_id: str, job_id: UUID) -> dict[str, Any]:
    reconcile_scan_jobs()
    job = fetch_one(
        db,
        """
        SELECT id, status, error_code
        FROM scan_jobs
        WHERE id = :job_id AND user_id = :user_id
        """,
        {"job_id": job_id, "user_id": user_id},
    )
    if job is None:
        raise APIError(404, "NOT_FOUND", "Không tìm thấy yêu cầu nhận diện.")
    candidates = fetch_all(
        db,
        """
        SELECT id, suggested_name, ingredient_id, confidence, position
        FROM scan_candidates
        WHERE scan_job_id = :job_id
        ORDER BY position
        """,
        {"job_id": job_id},
    )
    result: dict[str, Any] = {
        "scan_job_id": str(job["id"]),
        "status": job["status"],
        "candidates": [
            {
                "id": str(row["id"]),
                "suggested_name": row["suggested_name"],
                "ingredient_id": (
                    str(row["ingredient_id"])
                    if row["ingredient_id"] is not None
                    else None
                ),
                "confidence": (
                    float(row["confidence"]) if row["confidence"] is not None else None
                ),
                "position": row["position"],
            }
            for row in candidates
        ],
    }
    if job["status"] == "failed" and job["error_code"]:
        code = job["error_code"]
        result["error"] = {
            "code": code,
            "message": _ERROR_MESSAGES.get(code, "Không thể xử lý ảnh."),
        }
    return result


def _validated_candidates(output: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
    if set(output) != {"ingredients", "image_quality"}:
        raise ScanIngredientsError("OUTPUT_SCHEMA_INVALID", "Output fields mismatch.")
    quality = output["image_quality"]
    ingredients = output["ingredients"]
    if quality not in {None, "ok", "blurry", "no_food"}:
        raise ScanIngredientsError("OUTPUT_SCHEMA_INVALID", "Invalid image quality.")
    if not isinstance(ingredients, list) or len(ingredients) > MAX_CANDIDATES:
        raise ScanIngredientsError("OUTPUT_SCHEMA_INVALID", "Invalid candidate count.")
    if quality == "no_food" and ingredients:
        raise ScanIngredientsError("OUTPUT_SCHEMA_INVALID", "Invalid no-food output.")
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in ingredients:
        if not isinstance(item, dict) or set(item) - {"name", "confidence"}:
            raise ScanIngredientsError("OUTPUT_SCHEMA_INVALID", "Invalid candidate.")
        name = item.get("name")
        confidence = item.get("confidence")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise ScanIngredientsError("OUTPUT_SCHEMA_INVALID", "Invalid candidate name.")
        if confidence is not None and (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise ScanIngredientsError("OUTPUT_SCHEMA_INVALID", "Invalid confidence.")
        display_name = name.strip()
        normalized_name = normalize_name(display_name)
        if normalized_name not in seen:
            seen.add(normalized_name)
            candidates.append(
                {
                    "suggested_name": display_name,
                    "normalized_name": normalized_name,
                    "confidence": confidence,
                }
            )
    return quality, candidates


def _get_ai_output(image_bytes: bytes, mime_type: str) -> dict[str, Any]:
    return scan_ingredients(
        image_bytes=image_bytes,
        mime_type=mime_type,
        model_client=create_gemini_client(),
    )


def process_scan_job(job_id: UUID) -> None:
    object_key: str | None = None
    mime_type: str | None = None
    with SessionLocal() as db:
        reconcile_scan_jobs()
        with db.begin():
            job = fetch_one(
                db,
                """
                SELECT id, status, image_object_key, expires_at
                FROM scan_jobs
                WHERE id = :job_id
                FOR UPDATE
                """,
                {"job_id": job_id},
            )
            if job is None or job["status"] != "queued":
                return
            if job["expires_at"] <= datetime.now(UTC):
                object_key = job["image_object_key"]
                db.execute(
                    text(
                        """
                        UPDATE scan_jobs
                        SET status = 'expired', image_object_key = NULL, updated_at = now()
                        WHERE id = :job_id
                        """
                    ),
                    {"job_id": job_id},
                )
                expired = True
            else:
                object_key = job["image_object_key"]
                db.execute(
                    text(
                        """
                        UPDATE scan_jobs
                        SET status = 'processing', updated_at = now()
                        WHERE id = :job_id AND status = 'queued'
                        """
                    ),
                    {"job_id": job_id},
                )
                expired = False
        if expired:
            delete_scan_image(object_key)
            return

    try:
        if not object_key:
            raise GeminiTransportError("Scan image is not available.")
        image_bytes = read_scan_image(object_key)
        mime_type = _mime_type_for_key(object_key)
        output = _get_ai_output(image_bytes, mime_type)
        quality, candidates = _validated_candidates(output)
        if quality == "blurry":
            raise ScanIngredientsError("IMAGE_BLURRY", "Image is blurry.")
        with SessionLocal() as db:
            with db.begin():
                job = fetch_one(
                    db,
                    """
                    SELECT status, expires_at
                    FROM scan_jobs
                    WHERE id = :job_id
                    FOR UPDATE
                    """,
                    {"job_id": job_id},
                )
                if job is None or job["status"] != "processing":
                    return
                if job["expires_at"] <= datetime.now(UTC):
                    db.execute(
                        text(
                            """
                            UPDATE scan_jobs
                            SET status = 'expired', image_object_key = NULL, updated_at = now()
                            WHERE id = :job_id
                            """
                        ),
                        {"job_id": job_id},
                    )
                    should_delete = True
                else:
                    should_delete = False
                    for position, candidate in enumerate(candidates):
                        ingredient = fetch_one(
                            db,
                            """
                            SELECT id FROM ingredients
                            WHERE normalized_name = :normalized_name
                            """,
                            {"normalized_name": candidate["normalized_name"]},
                        )
                        db.execute(
                            text(
                                """
                                INSERT INTO scan_candidates (
                                    scan_job_id, suggested_name, ingredient_id,
                                    confidence, position
                                ) VALUES (
                                    :job_id, :suggested_name, :ingredient_id,
                                    :confidence, :position
                                )
                                """
                            ),
                            {
                                "job_id": job_id,
                                "suggested_name": candidate["suggested_name"],
                                "ingredient_id": ingredient["id"] if ingredient else None,
                                "confidence": candidate["confidence"],
                                "position": position,
                            },
                        )
                    db.execute(
                        text(
                            """
                            UPDATE scan_jobs
                            SET status = 'succeeded', error_code = NULL, updated_at = now()
                            WHERE id = :job_id AND status = 'processing'
                            """
                        ),
                        {"job_id": job_id},
                    )
            if should_delete:
                delete_scan_image(object_key)
    except Exception as exc:
        failure_code = _safe_scan_error_code(exc)
        with SessionLocal() as db:
            with db.begin():
                db.execute(
                    text(
                        """
                        UPDATE scan_jobs
                        SET status = 'failed', error_code = :error_code, updated_at = now()
                        WHERE id = :job_id AND status = 'processing'
                        """
                    ),
                    {"job_id": job_id, "error_code": failure_code},
                )


def _safe_scan_error_code(error: Exception) -> str:
    if isinstance(error, ScanIngredientsError):
        return (
            "IMAGE_BLURRY"
            if error.code == "IMAGE_BLURRY"
            else "INVALID_IMAGE_INPUT"
            if error.code == "INVALID_INPUT"
            else "AI_OUTPUT_INVALID"
        )
    if isinstance(error, APIError) and error.code == "AI_CONFIG_ERROR":
        return "AI_CONFIG_ERROR"
    if isinstance(error, GeminiTimeoutError):
        return "AI_TIMEOUT"
    if isinstance(error, GeminiApiError):
        if error.status_code == 429:
            return "AI_RATE_LIMITED"
        if error.status_code in {401, 403}:
            return "AI_CONFIG_ERROR"
        if error.status_code == 504:
            return "AI_TIMEOUT"
    return "AI_PROVIDER_UNAVAILABLE"


def _mime_type_for_key(object_key: str) -> str:
    extension = object_key.rsplit(".", 1)[-1].lower()
    types = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
    mime_type = types.get(extension)
    if mime_type is None:
        raise GeminiTransportError("Stored scan image type is invalid.")
    return mime_type


def confirm_scan_job(
    db: Any, user_id: str, job_id: UUID, payload: ScanConfirmInput
) -> tuple[dict[str, Any], str | None]:
    canonical = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    image_key: str | None = None
    with db.begin():
        job = fetch_one(
            db,
            """
            SELECT id, status, confirm_hash, confirm_response, image_object_key
            FROM scan_jobs
            WHERE id = :job_id AND user_id = :user_id
            FOR UPDATE
            """,
            {"job_id": job_id, "user_id": user_id},
        )
        if job is None:
            raise APIError(404, "NOT_FOUND", "Không tìm thấy yêu cầu nhận diện.")
        if job["status"] == "confirmed":
            if job["confirm_hash"] != fingerprint:
                raise APIError(
                    409, "CONFIRM_CONFLICT", "Yêu cầu xác nhận khác với lần trước."
                )
            return job["confirm_response"], None
        if job["status"] != "succeeded":
            raise APIError(
                409, "SCAN_NOT_READY", "Chỉ có thể xác nhận job đã nhận diện thành công."
            )
        candidate_rows = fetch_all(
            db,
            "SELECT id, ingredient_id FROM scan_candidates WHERE scan_job_id = :job_id",
            {"job_id": job_id},
        )
        candidate_by_id = {str(row["id"]): row for row in candidate_rows}
        response_items = []
        for item in payload.items:
            ingredient_id = str(item.ingredient_id) if item.ingredient_id else None
            if item.candidate_id:
                candidate = candidate_by_id.get(str(item.candidate_id))
                if candidate is None:
                    raise APIError(
                        422,
                        "INVALID_CANDIDATE",
                        "Ứng viên không thuộc yêu cầu nhận diện này.",
                    )
                if ingredient_id is None and normalize_name(item.name):
                    ingredient_id = (
                        str(candidate["ingredient_id"])
                        if candidate["ingredient_id"] is not None
                        and normalize_name(item.name)
                        == _candidate_normalized_name(db, item.candidate_id)
                        else None
                    )
            if ingredient_id is not None:
                ingredient = fetch_one(
                    db,
                    "SELECT id, normalized_name FROM ingredients WHERE id = :ingredient_id",
                    {"ingredient_id": ingredient_id},
                )
                if ingredient is None or ingredient["normalized_name"] != normalize_name(item.name):
                    raise APIError(
                        422,
                        "INVALID_INGREDIENT",
                        "Nguyên liệu không khớp với catalog.",
                    )
            request_item = InventoryItemInput(
                name=item.name,
                quantity=item.quantity,
                unit_code=item.unit_code,
                expiry_date=item.expiry_date,
            )
            response_items.append(
                upsert_inventory_item(db, user_id, request_item, ingredient_id)
            )
        result = {
            "scan_job_id": str(job_id),
            "status": "confirmed",
            "items": response_items,
        }
        db.execute(
            text(
                """
                UPDATE scan_jobs
                SET status = 'confirmed', confirm_hash = :confirm_hash,
                    confirm_response = CAST(:confirm_response AS JSONB),
                    image_object_key = NULL, updated_at = now()
                WHERE id = :job_id AND user_id = :user_id
                """
            ),
            {
                "confirm_hash": fingerprint,
                "confirm_response": json.dumps(result, ensure_ascii=False, default=str),
                "job_id": job_id,
                "user_id": user_id,
            },
        )
        image_key = job["image_object_key"]
    return result, image_key


def _candidate_normalized_name(db: Any, candidate_id: UUID) -> str:
    row = fetch_one(
        db,
        """
        SELECT i.normalized_name
        FROM scan_candidates c
        LEFT JOIN ingredients i ON i.id = c.ingredient_id
        WHERE c.id = :candidate_id
        """,
        {"candidate_id": candidate_id},
    )
    return row["normalized_name"] if row and row["normalized_name"] else ""


async def process_scan_job_background(job_id: UUID) -> None:
    global _worker_semaphore
    if _worker_semaphore is None:
        _worker_semaphore = asyncio.Semaphore(settings.scan_worker_concurrency)
    async with _worker_semaphore:
        await run_in_threadpool(process_scan_job, job_id)
