from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Header, UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.auth import get_current_user
from app.core.errors import APIError
from app.core.image_storage import delete_scan_image
from app.db.session import get_db
from app.schemas.scan import ScanConfirmInput
from app.services.scan_service import (
    _ERROR_MESSAGES,
    _get_ai_output,
    _safe_scan_error_code,
    _validated_candidates,
    confirm_scan_job,
    create_scan_job,
    get_scan_job,
    image_request_hash,
    process_scan_job_background,
    read_and_validate_upload,
    reconcile_scan_jobs,
)

router = APIRouter()


@router.post("/detect")
async def detect_for_mobile(
    file: UploadFile = File(...),
    _: str = Depends(get_current_user),
) -> list[dict[str, str]]:
    image_bytes, mime_type = await read_and_validate_upload(file)
    try:
        raw = await run_in_threadpool(_get_ai_output, image_bytes, mime_type)
        quality, candidates = _validated_candidates(raw)
    except APIError:
        raise
    except Exception as exc:
        code = _safe_scan_error_code(exc)
        status_code = {
            "AI_RATE_LIMITED": 429,
            "AI_TIMEOUT": 504,
            "AI_OUTPUT_INVALID": 502,
            "INVALID_IMAGE_INPUT": 422,
        }.get(code, 503)
        raise APIError(status_code, code, _ERROR_MESSAGES[code]) from exc
    if quality == "blurry":
        raise APIError(422, "blurry", "Ảnh chưa đủ rõ.")
    return [{"id": str(uuid4()), "name": candidate["suggested_name"], "unit": ""}
            for candidate in candidates]


@router.post("", status_code=202)
async def create_scan(
    background_tasks: BackgroundTasks,
    image: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if idempotency_key is None:
        raise APIError(422, "VALIDATION_ERROR", "Idempotency-Key là bắt buộc.")
    image_bytes, mime_type = await read_and_validate_upload(image)
    result, created = create_scan_job(
        db=db,
        user_id=user_id,
        idempotency_key=idempotency_key,
        request_fingerprint=image_request_hash(image_bytes),
        image_bytes=image_bytes,
        mime_type=mime_type,
    )
    if created:
        background_tasks.add_task(
            process_scan_job_background, UUID(result["scan_job_id"])
        )
    return result


@router.get("/{scan_job_id}")
def read_scan(
    scan_job_id: UUID,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return get_scan_job(db, user_id, scan_job_id)


@router.post("/{scan_job_id}/confirm")
def confirm_scan(
    scan_job_id: UUID,
    payload: ScanConfirmInput,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    reconcile_scan_jobs()
    result, image_key = confirm_scan_job(db, user_id, scan_job_id, payload)
    if image_key:
        delete_scan_image(image_key)
    return result
