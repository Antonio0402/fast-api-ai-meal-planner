from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.core.errors import APIError


def _storage_root() -> Path:
    backend_root = Path(__file__).resolve().parents[2]
    configured = Path(settings.scan_storage_dir)
    root = (backend_root / configured).resolve() if not configured.is_absolute() else configured.resolve()
    if not root.is_relative_to(backend_root):
        raise APIError(500, "INTERNAL_ERROR", "Thư mục lưu ảnh nằm ngoài backend.")
    return root


def save_scan_image(image_bytes: bytes, mime_type: str) -> str:
    extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[
        mime_type
    ]
    root = _storage_root()
    root.mkdir(parents=True, exist_ok=True)
    object_key = f"{uuid4()}{extension}"
    target = root / object_key
    temporary = root / f"{object_key}.tmp"
    temporary.write_bytes(image_bytes)
    os.replace(temporary, target)
    return object_key


def read_scan_image(object_key: str) -> bytes:
    path = _object_path(object_key)
    return path.read_bytes()


def delete_scan_image(object_key: str | None) -> None:
    if not object_key:
        return
    _object_path(object_key).unlink(missing_ok=True)


def _object_path(object_key: str) -> Path:
    root = _storage_root()
    path = (root / object_key).resolve()
    if not path.is_relative_to(root) or Path(object_key).name != object_key:
        raise APIError(500, "INTERNAL_ERROR", "Khóa lưu ảnh không hợp lệ.")
    return path
