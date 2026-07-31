"""Unit tests for amendment linkage matching (no LanceDB, no API)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = ROOT / "sources" / "sample_laws.jsonl"

from amendment_links import (  # noqa: E402
    AmendmentIndex,
    _brief,
    build_amendment_links,
    core_contained_in,
    core_title,
    match_amender_to_base,
    parse_citations,
    save_amendment_links,
)
from common import normalize_ar  # noqa: E402


def _rec(**kwargs) -> dict:
    base = {
        "lawBookID": 1,
        "lawTitle": "",
        "lawFlag": "",
        "lawCode": "",
        "lawYear": "",
        "classification": "",
        "lawIndex": "",
        "category": "قانون",
        "lawNotes": "",
        "status_label": "ساري",
    }
    base.update(kwargs)
    return base


def test_parse_citations_arabic_digits():
    assert parse_citations("رقم ١١١ لسنة ١٩٦٩") == [("111", "1969")]
    assert parse_citations("رقم (٢٤) لسنة ١٩٦٠") == [("24", "1960")]


def test_core_title_strips_num_year_and_parens():
    assert "عقوبات" in core_title("قانون العقوبات (عينة) رقم 111 لسنة 1969")
    assert "111" not in core_title("قانون العقوبات رقم 111 لسنة 1969")


def test_sample_links_penal_to_amendment():
    """sample_laws: 90001 معدل ← 90011 تعديل via notes/title."""
    assert SAMPLE_PATH.is_file()
    rows = build_amendment_links(SAMPLE_PATH)
    by_id = {r.base_law_book_id: r for r in rows}
    assert 90001 in by_id
    amender_ids = {a.law_book_id for a in by_id[90001].amended_by}
    assert 90011 in amender_ids


def test_false_friend_bare_num_year_refused_without_disambiguation():
    """
    18/1935 collides: مرسوم الادارة العرفية vs نظام المقابر.
    An amendment that only cites رقم ١٨ لسنة ١٩٣٥ with no further cue
    must NOT pick either (naive match forbidden).
    """
    muadal = [
        _brief(_rec(
            lawBookID=100,
            lawTitle="مرسوم الادارة العرفية رقم ١٨ لسنة ١٩٣٥",
            lawFlag="معدل",
            lawCode="18",
            lawYear="1935",
            classification="امن",
            category="مرسوم",
        )),
        _brief(_rec(
            lawBookID=101,
            lawTitle="نظام المقابر رقم ١٨ لسنة ١٩٣٥",
            lawFlag="معدل",
            lawCode="18",
            lawYear="1935",
            classification="بلديات",
            category="نظام",
        )),
    ]
    assert all(m is not None for m in muadal)
    by_ny = {("18", "1935"): list(muadal)}
    amender = _brief(_rec(
        lawBookID=200,
        lawTitle="قانون تعديل رقم ١٨ لسنة ١٩٣٥ رقم ٢١ لسنة ١٩٣٦",
        lawFlag="تعديل",
        lawCode="21",
        lawYear="1936",
        classification="",
    ))
    assert amender is not None
    base, method, conf = match_amender_to_base(amender, by_ny, list(muadal))
    assert base is None
    assert method == ""


def test_false_friend_disambiguated_by_instrument_kind():
    """نظام تعديل نظام المقابر رقم ١٨ لسنة ١٩٣٥ → مقابر, not ادارة عرفية."""
    muadal = [
        _brief(_rec(
            lawBookID=100,
            lawTitle="مرسوم الادارة العرفية رقم ١٨ لسنة ١٩٣٥",
            lawFlag="معدل",
            lawCode="18",
            lawYear="1935",
            classification="امن",
            category="مرسوم",
        )),
        _brief(_rec(
            lawBookID=101,
            lawTitle="نظام المقابر رقم ١٨ لسنة ١٩٣٥",
            lawFlag="معدل",
            lawCode="18",
            lawYear="1935",
            classification="بلديات",
            category="نظام",
        )),
    ]
    by_ny = {("18", "1935"): list(muadal)}
    amender = _brief(_rec(
        lawBookID=201,
        lawTitle="نظام تعديل نظام المقابر رقم ١٨ لسنة ١٩٣٥",
        lawFlag="تعديل",
        lawCode="22",
        lawYear="1936",
        classification="بلديات",
        category="نظام",
    ))
    assert amender is not None
    base, method, conf = match_amender_to_base(amender, by_ny, list(muadal))
    assert base is not None
    assert base.law_book_id == 101
    assert method in ("num_year", "classification", "title_contain")


def test_core_containment_rejects_amal_inside_umla():
    """
    قانون العمل must NOT match داخل «قانون تعديل قانون العملة الاجنبية».
    Regression for unbounded substring false friends (GPT review High #1).
    """
    labor_core = core_title("قانون العمل رقم ٣٧ لسنة ٢٠١٥")
    assert labor_core
    assert "عمل" in labor_core
    foreign_currency = normalize_ar(
        "قانون تعديل قانون العملة الاجنبية رقم ٤ لسنة ١٩٨١"
    )
    assert labor_core in foreign_currency  # naive substring WOULD fire
    assert not core_contained_in(labor_core, foreign_currency)

    # Honest amendment of labour code still matches at a token boundary.
    honest = normalize_ar(
        "قانون تعديل قانون العمل رقم ٧١ لسنة ١٩٨٧"
    )
    assert core_contained_in(labor_core, honest)

    labor = _brief(_rec(
        lawBookID=32566,
        lawTitle="قانون العمل رقم ٣٧ لسنة ٢٠١٥",
        lawFlag="معدل",
        lawCode="37",
        lawYear="2015",
        classification="عمل",
    ))
    currency_amender = _brief(_rec(
        lawBookID=27527,
        lawTitle="قانون تعديل قانون العملة الاجنبية رقم ٤ لسنة ١٩٨١",
        lawFlag="تعديل",
        lawCode="4",
        lawYear="1981",
        classification="عملة",
    ))
    assert labor is not None and currency_amender is not None
    base, method, conf = match_amender_to_base(
        currency_amender,
        muadal_by_ny={},
        muadal_list=[labor],
    )
    assert base is None, (base, method, conf)
    assert method == ""


def test_self_cite_not_treated_as_base():
    """Amendment's own رقم/لسنة must not link to itself as معدل."""
    muadal = [
        _brief(_rec(
            lawBookID=50,
            lawTitle="قانون العقوبات رقم ١١١ لسنة ١٩٦٩",
            lawFlag="معدل",
            lawCode="111",
            lawYear="1969",
            classification="عقوبات",
        )),
    ]
    by_ny = {("111", "1969"): list(muadal), ("25", "2001"): []}
    # Only cites its own identity 25/2001 — no base cite
    amender = _brief(_rec(
        lawBookID=25,
        lawTitle="قانون رقم ٢٥ لسنة ٢٠٠١",
        lawFlag="تعديل",
        lawCode="25",
        lawYear="2001",
    ))
    assert amender is not None
    base, method, _ = match_amender_to_base(amender, by_ny, list(muadal))
    assert base is None


def test_title_containment_unique_major_code():
    muadal = [
        _brief(_rec(
            lawBookID=25860,
            lawTitle="قانون العقوبات رقم ١١١ لسنة ١٩٦٩",
            lawFlag="معدل",
            lawCode="111",
            lawYear="1969",
            classification="عقوبات",
        )),
        _brief(_rec(
            lawBookID=999,
            lawTitle="قانون الجمارك رقم ١ لسنة ١٩٦٠",
            lawFlag="معدل",
            lawCode="1",
            lawYear="1960",
        )),
    ]
    by_ny = {
        ("111", "1969"): [muadal[0]],
        ("1", "1960"): [muadal[1]],
    }
    amender = _brief(_rec(
        lawBookID=300,
        lawTitle="قانون التعديل الاول لقانون العقوبات رقم ١١١ لسنة ١٩٦٩",
        lawFlag="تعديل",
        lawCode="1",
        lawYear="1970",
        classification="عقوبات",
    ))
    assert amender is not None
    base, method, conf = match_amender_to_base(amender, by_ny, list(muadal))
    assert base is not None
    assert base.law_book_id == 25860
    assert conf in ("high", "medium")


def test_year_only_not_a_citation():
    """Bare لسنة ١٩٢٣ without رقم must not parse as a citation key."""
    assert parse_citations("قانون تعديل قانون الباسبورات لسنة ١٩٢٣") == []


def test_warning_entries_list_amended_by(tmp_path):
    rows = build_amendment_links(SAMPLE_PATH)
    path = tmp_path / "links.jsonl"
    save_amendment_links(rows, path)
    idx = AmendmentIndex.load(path)
    retrieved = [
        {
            "law_book_id": 90001,
            "title": "قانون العقوبات (عينة)",
            "law_flag": "معدل",
        }
    ]
    entries = idx.warning_entries(retrieved)
    assert len(entries) == 1
    assert entries[0]["amended_by"]
    assert any(a["law_book_id"] == 90011 for a in entries[0]["amended_by"])
    lines = idx.format_warning_lines(entries)
    assert any("معدّل بـ" in ln for ln in lines)


def test_save_roundtrip(tmp_path):
    rows = build_amendment_links(SAMPLE_PATH)
    out = tmp_path / "amendment_links.jsonl"
    save_amendment_links(rows, out)
    loaded = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l]
    assert loaded
    assert all("base_law_book_id" in r and "amended_by" in r for r in loaded)
