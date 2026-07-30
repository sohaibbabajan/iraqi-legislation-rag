"""Unit tests for deterministic article index (no LanceDB, no API)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = ROOT / "sources" / "sample_laws.jsonl"

from article_index import (  # noqa: E402
    build_article_index,
    find_define_headers,
    lookup_defines,
    parse_full_text,
    save_article_index,
)


@pytest.fixture(scope="module")
def sample_rows():
    assert SAMPLE_PATH.is_file()
    return build_article_index(SAMPLE_PATH)


def test_sample_has_defines_and_mentions(sample_rows):
    defines = [r for r in sample_rows if r.role == "defines"]
    mentions = [r for r in sample_rows if r.role == "mentions"]
    assert len(defines) >= 10
    assert any(r.article_label == "438" and r.law_book_id == 90001 for r in defines)
    # Amendment cites المادة 438 inside defining art. 1
    assert any(
        r.article_label == "438" and r.law_book_id == 90011 and r.in_article == "1"
        for r in mentions
    )


def test_defines_not_conflated_with_mentions(sample_rows):
    """438 is defined by the penal sample, only mentioned by the amendment."""
    as_json = [r.to_json() for r in sample_rows]
    defined = lookup_defines(as_json, article_label="438")
    law_ids = {int(r["law_book_id"]) for r in defined}
    assert 90001 in law_ids
    assert 90011 not in law_ids


def test_char_spans_slice_full_text():
    text = (
        "المادة 1\n"
        "يسمى هذا القانون.\n\n"
        "المادة 438\n"
        "يعاقب بالحبس من ارتكب سرقة.\n"
    )
    rows = parse_full_text(1, text)
    defines = [r for r in rows if r.role == "defines"]
    assert [r.article_label for r in defines] == ["1", "438"]
    for r in defines:
        assert text[r.char_start:r.char_end] == r.text
        assert r.text.startswith("المادة")


def test_mention_inside_body_has_span():
    text = "المادة 1\nيلغى نص المادة 438 من القانون.\n"
    rows = parse_full_text(99, text)
    mentions = [r for r in rows if r.role == "mentions"]
    assert len(mentions) == 1
    m = mentions[0]
    assert m.article_label == "438"
    assert m.in_article == "1"
    assert text[m.char_start:m.char_end] in ("438", "٤٣٨")


def test_lettered_and_mukarrar_labels():
    text = "المادة 126 أ\nنص.\n\nالمادة 43 مكرر\nنص آخر.\n"
    headers = find_define_headers(text)
    labels = [h[2] for h in headers]
    assert "126أ" in labels or any(x.startswith("126") for x in labels)
    assert any("مكرر" in x for x in labels)


def test_bare_digit_header():
    text = "١\nنص المادة الاولى.\n\n٢\nنص المادة الثانية.\n"
    rows = parse_full_text(7, text)
    defines = [r for r in rows if r.role == "defines"]
    assert [r.article_label for r in defines] == ["1", "2"]


def test_save_roundtrip(tmp_path, sample_rows):
    out = tmp_path / "article_index.jsonl"
    save_article_index(sample_rows, out)
    lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l]
    assert len(lines) == len(sample_rows)
    assert all("role" in r and "article_label" in r for r in lines)


def test_empty_full_text():
    assert parse_full_text(1, "") == []
    assert parse_full_text(1, "   ") == []
