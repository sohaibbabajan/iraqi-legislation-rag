"""Normalize catalog + detail payloads into law_record schema fields."""

from __future__ import annotations

import re
from typing import Any

from scraper.config import ScraperConfig
from scraper.identity import DEFAULT_SOURCE_TYPE

_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

_DATE_DMY = re.compile(r"^\s*(\d{1,2})\D+(\d{1,2})\D+(\d{4})\s*$")
_DATE_ISO = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})\s*$")


def arabic_digits_to_ascii(text: str | None) -> str:
    if text is None:
        return ""
    return str(text).translate(_ARABIC_INDIC)


def parse_date_iso(law_date: str | None, fallback: str | None = None) -> str:
    """Best-effort ISO date from iraqld lawDate (often DD-MM-YYYY Arabic digits)."""
    for candidate in (law_date, fallback):
        if not candidate:
            continue
        s = arabic_digits_to_ascii(str(candidate)).strip()
        m = _DATE_ISO.match(s)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        m = _DATE_DMY.match(s.replace("/", "-").replace(".", "-"))
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{mo:02d}-{d:02d}"
    return ""


def status_from_valid(law_valid: str | None, law_flag: str | None = None) -> str:
    """Map source validity to schema status_label enum."""
    v = (law_valid or "").strip().upper()
    if v == "N" or (law_flag or "").strip() == "ملغى":
        return "ملغى"
    return "ساري"


def _str_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def normalize_record(
    catalog: dict[str, Any],
    *,
    full_text: str = "",
    config: ScraperConfig | None = None,
) -> dict[str, Any]:
    """
    Build one law_record-compatible dict.

    ``catalog`` is typically an item from SearchLegislations ``lawCodes``.
    """
    cfg = config or ScraperConfig()
    law_id = int(catalog["lawBookID"])
    law_valid = catalog.get("lawValid")
    law_flag = catalog.get("lawFlag") or ""
    law_image = catalog.get("lawImage")
    law_date = catalog.get("lawDate") or ""
    tacks_date = catalog.get("tacksNewsDate") or ""

    full_text = full_text or ""
    return {
        "articles": catalog.get("articles"),
        "category": _str_or_empty(catalog.get("category")),
        "classification": _str_or_empty(catalog.get("classification")),
        "corpus_id": f"{DEFAULT_SOURCE_TYPE}:{law_id}",
        "country": _str_or_empty(catalog.get("country")),
        "date_iso": parse_date_iso(law_date, tacks_date),
        "full_text": full_text,
        "full_text_len": len(full_text),
        "groupsNewsDate": _str_or_empty(catalog.get("groupsNewsDate")),
        "groupsNewsNum": _str_or_empty(catalog.get("groupsNewsNum")),
        "groupsNewsPage": _str_or_empty(catalog.get("groupsNewsPage")),
        "lawBookID": law_id,
        "lawCode": arabic_digits_to_ascii(_str_or_empty(catalog.get("lawCode"))),
        "lawDate": _str_or_empty(law_date),
        "lawDoc": _str_or_empty(catalog.get("lawDoc")),
        "lawFlag": _str_or_empty(law_flag),
        "lawImage": law_image,
        "lawIndex": _str_or_empty(catalog.get("lawIndex")),
        "lawNotes": _str_or_empty(catalog.get("lawNotes")),
        "lawTitle": _str_or_empty(catalog.get("lawTitle")),
        "lawValid": law_valid if law_valid in ("Y", "N", None) else _str_or_empty(law_valid),
        "lawYear": arabic_digits_to_ascii(_str_or_empty(catalog.get("lawYear"))),
        "pdf_url": cfg.pdf_url(law_image),
        "source_type": DEFAULT_SOURCE_TYPE,
        "source_url": cfg.detail_url(law_id),
        "status_label": status_from_valid(
            _str_or_empty(law_valid) if law_valid is not None else None,
            _str_or_empty(law_flag),
        ),
        "tacksNewsDate": _str_or_empty(tacks_date),
        "tacksNewsNum": _str_or_empty(catalog.get("tacksNewsNum")),
        "tacksNewsPage": _str_or_empty(catalog.get("tacksNewsPage")),
        "tacksPageCount": _str_or_empty(catalog.get("tacksPageCount")),
        "tacksPartNum": _str_or_empty(catalog.get("tacksPartNum")),
    }
