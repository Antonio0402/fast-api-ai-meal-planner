from app.core.normalization import normalize_name
from app.services.unit_aliases import from_base, to_base


def test_normalize_name_removes_vietnamese_marks_and_collapses_spaces():
    assert normalize_name("  CÀ   CHUA  ") == "ca chua"
    assert normalize_name("Đậu   phụ") == "dau phu"
    assert normalize_name("Ca\u0300 chua") == "ca chua"


def test_unit_conversion_keeps_dimensions_separate():
    assert to_base(1.5, "kg").quantity == 1500
    assert to_base(2, "lít").dimension == "ml"
    assert to_base(2, "quả").dimension == "quả"
    assert from_base(1100, "g") == (1.1, "kg")
    assert from_base(450, "ml") == (450, "ml")
