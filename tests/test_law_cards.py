"""Unit tests for law-card parsing / schema / lexicon (no OpenRouter)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from law_cards import (
    LAW_CARD_JSON_SCHEMA,
    cards_to_lexicon_rows,
    laws_matching_lexicon_aliases,
    parse_card_payload,
    strongest_lexicon_alias_len,
    validate_card,
)


SAMPLE_PAYLOAD = {
    "scope_summary": "ينظم جرائم السرقة والعقوبات المقررة لها.",
    "subject_tags": ["عقوبات", "سرقة", "جنائي"],
    "colloquial_aliases": ["قانون العقوبات", "العقوبات", "الجزائي"],
    "likely_questions": [
        "ما هي عقوبة السرقة؟",
        "متى تكون السرقة مشددة؟",
    ],
    "title_en": "Penal Code",
}


def test_parse_card_payload_dict():
    card = parse_card_payload(SAMPLE_PAYLOAD, law_book_id=90001, title="قانون العقوبات")
    assert card["law_book_id"] == 90001
    assert card["title"] == "قانون العقوبات"
    assert "سرقة" in card["subject_tags"]
    assert card["title_en"] == "Penal Code"
    validate_card(card)


def test_parse_card_payload_json_string():
    raw = json.dumps(SAMPLE_PAYLOAD, ensure_ascii=False)
    card = parse_card_payload(raw, law_book_id=42, title="x")
    assert card["law_book_id"] == 42
    assert len(card["likely_questions"]) == 2


def test_parse_card_payload_markdown_fence():
    raw = "```json\n" + json.dumps(SAMPLE_PAYLOAD, ensure_ascii=False) + "\n```"
    card = parse_card_payload(raw, law_book_id=7, title="t")
    assert card["law_book_id"] == 7


def test_parse_rejects_empty_scope():
    bad = dict(SAMPLE_PAYLOAD, scope_summary="  ")
    with pytest.raises(ValueError, match="scope_summary"):
        parse_card_payload(bad, law_book_id=1, title="t")


def test_parse_rejects_empty_tags():
    bad = dict(SAMPLE_PAYLOAD, subject_tags=[])
    with pytest.raises(ValueError, match="subject_tags"):
        parse_card_payload(bad, law_book_id=1, title="t")


def test_schema_required_keys():
    req = set(LAW_CARD_JSON_SCHEMA["required"])
    assert "scope_summary" in req
    assert "colloquial_aliases" in req
    assert "likely_questions" in req
    assert "subject_tags" in req


def test_lexicon_rows_and_match(tmp_path: Path):
    card = parse_card_payload(SAMPLE_PAYLOAD, law_book_id=90001, title="قانون العقوبات")
    rows = cards_to_lexicon_rows([card])
    assert rows
    assert all(r["law_book_id"] == 90001 for r in rows)
    assert any("عقوبات" in r["alias"] for r in rows)

    ids = laws_matching_lexicon_aliases("ما عقوبة السرقة في قانون العقوبات؟", rows)
    assert 90001 in ids
    assert strongest_lexicon_alias_len("قانون العقوبات", rows) >= 10


def test_sample_example_file_if_present():
    """Shipped example cards must validate without API."""
    path = Path(__file__).resolve().parents[1] / "docs" / "examples" / "sample_law_cards.jsonl"
    if not path.exists():
        pytest.skip("sample_law_cards.jsonl not shipped yet")
    n = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            card = json.loads(line)
            validate_card(card)
            n += 1
    assert n >= 1
