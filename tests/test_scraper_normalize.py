"""Unit tests for scraper normalize/parse (no live network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scraper.config import ScraperConfig
from scraper.normalize import normalize_record, parse_date_iso, status_from_valid
from scraper.parse import (
    articles_to_full_text,
    extract_js_array,
    full_text_from_detail_html,
    looks_like_cloudflare_challenge,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "law_record.schema.json"


@pytest.fixture(scope="module")
def law_schema() -> dict:
    with SCHEMA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def test_status_from_valid():
    assert status_from_valid("Y") == "ساري"
    assert status_from_valid("N") == "ملغى"
    assert status_from_valid("Y", "ملغى") == "ملغى"


def test_parse_date_iso_arabic_indic():
    assert parse_date_iso("٠٦-٠١-١٩٢٣") == "1923-01-06"
    assert parse_date_iso("", "2026-07-20") == "2026-07-20"


def test_extract_articles_and_full_text():
    page = (
        '<html><script>var articles = [{"articleID":1,"articleCodeTxt":"١",'
        '"displayOrder":1,"articleText":"<p>نص تجريبي</p>"}];</script></html>'
    )
    arts = extract_js_array(page)
    assert arts and arts[0]["articleCodeTxt"] == "١"
    ft = articles_to_full_text(arts)
    assert "١" in ft and "نص تجريبي" in ft
    assert full_text_from_detail_html(page) == ft


def test_cloudflare_challenge_detection():
    assert looks_like_cloudflare_challenge("Just a moment... cf-browser-verification")
    ok = 'var articles = []; <input id="lawbookid">'
    assert not looks_like_cloudflare_challenge(ok)


def test_normalize_matches_schema(law_schema):
    catalog = {
        "lawBookID": 13,
        "lawDoc": "z0001",
        "lawIndex": "احوال شخصية ومدنية",
        "category": "قانون",
        "country": "العراق - اتحادي",
        "lawCode": "",
        "lawDate": "٠٦-٠١-١٩٢٣",
        "lawYear": "1923",
        "lawTitle": "قانون تعديل قانون الباسبورات لسنة ١٩٢٣",
        "lawValid": "Y",
        "lawFlag": "تعديل",
        "tacksNewsNum": "15",
        "tacksNewsDate": "",
        "tacksNewsPage": "",
        "tacksPageCount": "",
        "tacksPartNum": "",
        "groupsNewsNum": "",
        "groupsNewsDate": "1923",
        "groupsNewsPage": "3",
        "lawImage": "/pdf/1923/z0001.pdf",
        "lawNotes": "",
        "classification": "جواز سفر",
        "articles": None,
    }
    rec = normalize_record(
        catalog,
        full_text="استناد\nنص",
        config=ScraperConfig(),
    )
    validator = Draft202012Validator(law_schema)
    errors = sorted(validator.iter_errors(rec), key=lambda e: list(e.path))
    assert not errors, errors[0].message
    assert rec["status_label"] == "ساري"
    assert rec["date_iso"] == "1923-01-06"
    assert rec["full_text_len"] == len(rec["full_text"])
    assert "showlegislation?lawbookid=13" in rec["source_url"]
    assert rec["pdf_url"].endswith("/pdf/1923/z0001.pdf")
