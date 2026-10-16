"""Chuẩn hóa dị ứng người dùng và đối chiếu với nguyên liệu/công thức.

Quy tắc so khớp:
- Từ khóa người dùng nhập có dấu được so với văn bản giữ dấu (tránh nhầm "cá" với "cà").
- Từ khóa không dấu được so với văn bản đã bỏ dấu.
- Luôn so theo ranh giới từ để "mì" không khớp một phần của từ khác.
"""

import re
import unicodedata
from typing import Annotated

from pydantic import Field, StringConstraints

from app.core.normalization import normalize_name

MAX_ALLERGIES = 20
MAX_ALLERGY_LENGTH = 50

AllergyList = Annotated[
    list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_ALLERGY_LENGTH)]],
    Field(max_length=MAX_ALLERGIES),
]

# Nhóm dị ứng phổ biến -> các tên nguyên liệu (giữ dấu) thường chứa dị nguyên đó.
# Khóa là dạng bỏ dấu để người dùng nhập "hai san" hay "hải sản" đều được mở rộng.
ALLERGY_ALIASES: dict[str, tuple[str, ...]] = {
    "hai san": ("hải sản", "tôm", "tép", "cua", "ghẹ", "mực", "bạch tuộc", "nghêu",
                "ngao", "sò", "ốc", "hến", "hàu", "trai", "tôm khô", "ruốc", "mắm tôm"),
    "tom": ("tôm", "tép", "tôm khô", "ruốc", "mắm tôm"),
    "ca": ("cá", "nước mắm", "mắm"),
    "trung": ("trứng", "lòng đỏ", "lòng trắng", "mayonnaise", "sốt mayonnaise"),
    "sua": ("sữa", "sữa chua", "phô mai", "pho mát", "bơ sữa", "kem tươi", "whipping cream"),
    "gluten": ("gluten", "bột mì", "lúa mì", "bánh mì", "mì", "mì ý", "nui", "bia",
               "lúa mạch", "yến mạch", "bột chiên giòn", "bột chiên xù"),
    "dau nanh": ("đậu nành", "đậu phụ", "đậu hũ", "tàu hũ", "nước tương", "xì dầu",
                 "sữa đậu nành", "tương đen", "miso"),
    "dau phong": ("đậu phộng", "lạc", "bơ đậu phộng"),
    "lac": ("đậu phộng", "lạc", "bơ đậu phộng"),
    "hat": ("hạt điều", "hạnh nhân", "óc chó", "hạt dẻ", "hồ đào", "mắc ca"),
    "me": ("mè", "vừng", "dầu mè", "dầu vừng"),
    "vung": ("mè", "vừng", "dầu mè", "dầu vừng"),
}


def _marked(value: str) -> str:
    """Chữ thường, NFC, gộp khoảng trắng nhưng giữ dấu tiếng Việt."""
    return " ".join(unicodedata.normalize("NFC", value).lower().split())


def _has_marks(value: str) -> bool:
    return _marked(value) != normalize_name(value)


def normalize_allergies(allergies: list[str]) -> list[str]:
    """Bỏ rỗng/trùng, giữ thứ tự người dùng nhập; dùng cho cache hash và prompt."""
    seen: set[str] = set()
    result: list[str] = []
    for raw in allergies:
        value = _marked(raw)[:MAX_ALLERGY_LENGTH]
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result[:MAX_ALLERGIES]


def expand_allergy_terms(allergies: list[str]) -> list[str]:
    """Mở rộng nhóm dị ứng thành danh sách từ khóa cần tránh (giữ dấu nếu có)."""
    terms: list[str] = []
    for allergy in normalize_allergies(allergies):
        terms.append(allergy)
        terms.extend(ALLERGY_ALIASES.get(normalize_name(allergy), ()))
    return list(dict.fromkeys(terms))


def _contains(text: str, term: str) -> bool:
    haystack = _marked(text) if _has_marks(term) else normalize_name(text)
    needle = _marked(term) if _has_marks(term) else normalize_name(term)
    return bool(needle) and re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


def find_allergy_term(text: str, terms: list[str]) -> str | None:
    """Trả về từ khóa dị ứng đầu tiên xuất hiện trong văn bản, hoặc None."""
    return next((term for term in terms if _contains(text, term)), None)
