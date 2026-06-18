from __future__ import annotations

import re
import unicodedata


def normalize_ascii(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    normalized = normalized.encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", normalized)


def normalize_unicode(value: str) -> str:
    return re.sub(r"[^\w\d]", "", (value or "").lower(), flags=re.UNICODE)


__all__ = ["normalize_ascii", "normalize_unicode"]