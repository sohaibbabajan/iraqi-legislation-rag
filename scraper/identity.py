"""Stable record identity + content fingerprint for corpus dedupe."""

from __future__ import annotations

import hashlib
import re
from typing import Any

DEFAULT_SOURCE_TYPE = "iraqld"

_WS = re.compile(r"\s+")


def normalize_title(title: str | None) -> str:
    if not title:
        return ""
    return _WS.sub(" ", str(title).strip().casefold())


def source_type_of(rec: dict[str, Any]) -> str:
    st = rec.get("source_type")
    if st is None or str(st).strip() == "":
        return DEFAULT_SOURCE_TYPE
    return str(st).strip()


def record_identity(rec: dict[str, Any]) -> str:
    """
    Stable upsert key for a corpus row.

    Prefer site ``lawBookID`` (iraqld), then explicit ``corpus_id``, then a
    title/year/number fallback for future non-legislation sources.
    """
    st = source_type_of(rec)
    lid = rec.get("lawBookID")
    if lid is not None and str(lid).strip() != "":
        return f"{st}:lawBookID:{int(lid)}"

    corpus_id = rec.get("corpus_id")
    if corpus_id is not None and str(corpus_id).strip() != "":
        return f"{st}:corpus_id:{str(corpus_id).strip()}"

    num = str(rec.get("lawCode") or rec.get("law_number") or "").strip()
    year = str(rec.get("lawYear") or "").strip()
    title = normalize_title(rec.get("lawTitle") or rec.get("title"))
    raw = f"{num}|{year}|{title}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{st}:title_year:{digest}"


def content_fingerprint(rec: dict[str, Any]) -> str:
    """Hash of fields that matter for 'same id, changed content' detection."""
    ft = rec.get("full_text") or ""
    ft_hash = hashlib.sha256(str(ft).encode("utf-8")).hexdigest()
    parts = [
        source_type_of(rec),
        str(rec.get("lawTitle") or ""),
        str(rec.get("status_label") or ""),
        str(rec.get("lawFlag") or ""),
        str(rec.get("lawValid") or ""),
        str(rec.get("date_iso") or ""),
        str(rec.get("lawDate") or ""),
        str(rec.get("lawYear") or ""),
        str(rec.get("lawCode") or ""),
        str(rec.get("lawNotes") or ""),
        str(rec.get("full_text_len") if rec.get("full_text_len") is not None else len(str(ft))),
        ft_hash,
    ]
    blob = "\n".join(parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def ensure_extension_fields(rec: dict[str, Any]) -> dict[str, Any]:
    """Attach source_type / corpus_id when missing (non-destructive copy)."""
    out = dict(rec)
    if not out.get("source_type"):
        out["source_type"] = DEFAULT_SOURCE_TYPE
    if out.get("corpus_id") in (None, "") and out.get("lawBookID") is not None:
        out["corpus_id"] = f"{DEFAULT_SOURCE_TYPE}:{int(out['lawBookID'])}"
    return out
