import unicodedata


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).strip().lower()
    normalized = normalized.replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", normalized)
    without_marks = "".join(
        character
        for character in decomposed
        if unicodedata.category(character) != "Mn"
    )
    return " ".join(without_marks.split())
