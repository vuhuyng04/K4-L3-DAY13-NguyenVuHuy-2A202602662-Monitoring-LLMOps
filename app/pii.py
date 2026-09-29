from __future__ import annotations

import hashlib
import re
from typing import Any

# Thứ tự quan trọng: pattern dài/cụ thể chạy trước để số thẻ 16 chữ số không bị
# pattern CCCD hoặc số điện thoại che mất một phần rồi để lộ phần còn lại.
PII_PATTERNS: dict[str, str] = {
    "email": r"[\w\.-]+@[\w\.-]+\.\w+",
    "credit_card": r"(?<!\d)\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}(?!\d)",
    "cccd": r"(?<!\d)\d{12}(?!\d)",
    "phone_vn": r"(?<!\d)(?:\+84|0)(?:[ .-]?\d){9}(?!\d)",
    # Hộ chiếu Việt Nam: 1 chữ cái in hoa + 7 chữ số, ví dụ C1234567.
    "passport_vn": r"\b[A-Z]\d{7}\b",
}

_COMPILED_PATTERNS = [
    (re.compile(pattern), f"[REDACTED_{name.upper()}]") for name, pattern in PII_PATTERNS.items()
]


def scrub_text(text: str) -> str:
    safe = text
    for pattern, replacement in _COMPILED_PATTERNS:
        safe = pattern.sub(replacement, safe)
    return safe


def scrub_value(value: Any) -> Any:
    """Scrub đệ quy mọi chuỗi trong dict/list/tuple; giữ nguyên kiểu dữ liệu khác."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {k: scrub_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(scrub_value(v) for v in value)
    return value


def summarize_text(text: str, max_len: int = 80) -> str:
    safe = scrub_text(text).strip().replace("\n", " ")
    return safe[:max_len] + ("..." if len(safe) > max_len else "")


def hash_user_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:12]
