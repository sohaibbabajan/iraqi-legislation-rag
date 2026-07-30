"""Unit tests for confidence-gated routing helpers (no LanceDB / API)."""
from __future__ import annotations

from ask import (
    _routing_confidence,
    _merge_rows,
    content_tokens,
    _chunk_topic_bonus,
    ROUTE_DIST_HIGH,
)
from law_registry import (
    extract_instrument_phrases,
    strongest_seed_alias_len,
    laws_matching_instrument_phrases,
    laws_matching_seed_aliases,
)


FAKE_REG = [
    {
        "law_book_id": 35340,
        "title": "قانون التعليم العالي الاهلي رقم ٢٥ لسنة ٢٠١٦",
        "year": "2016",
        "aliases": [
            "قانون التعليم العالي الاهلي",
            "التعليم العالي الاهلي",
            "قانون التعليم الاهلي",
        ],
    },
    {
        "law_book_id": 30735,
        "title": "نظام التعليم الاهلي و الاجنبي رقم ٥ لسنة ٢٠١٣",
        "year": "2013",
        "aliases": ["نظام التعليم الاهلي و الاجنبي", "التعليم الاهلي"],
    },
    {
        "law_book_id": 111,
        "title": "قانون العقوبات رقم ١١١ لسنة ١٩٦٩",
        "year": "1969",
        "aliases": ["قانون العقوبات", "العقوبات"],
    },
    {
        "law_book_id": 999,
        "title": "قانون الاصلاح الزراعي",
        "year": "1970",
        "aliases": ["الاصلاح الزراعي"],
    },
]


def test_instrument_phrases():
    q = "هل يجوز بناء جامعة اهلية على ارض زراعية حسب قانون التعليم الاهلي؟"
    phrases = extract_instrument_phrases(q)
    assert any("تعليم" in p and "اهلي" in p for p in phrases), phrases


def test_phrase_match_ranks_2016():
    q = "حسب قانون التعليم الاهلي"
    ids = laws_matching_instrument_phrases(q, FAKE_REG)
    assert ids, "expected phrase hits"
    assert ids[0] == 35340, ids


def test_seed_ranks_2016_for_uni():
    q = "جامعة اهلية على ارض زراعية حسب قانون التعليم الاهلي"
    ids = laws_matching_seed_aliases(q, FAKE_REG)
    assert ids and ids[0] == 35340, ids


def test_confidence_high_named_law():
    q = "ما احكام الطلاق في قانون الاحوال الشخصية؟"
    assert _routing_confidence(q, phrase_ids=[1], seed_ids=[], vector_ranked=[]) == "high"
    assert extract_instrument_phrases(q)
    assert _routing_confidence(q, [], [], []) == "high"


def test_confidence_low_topical_theft():
    q = "ما هي عقوبة السرقة؟"
    assert not extract_instrument_phrases(q)
    assert strongest_seed_alias_len(q) < 10
    assert _routing_confidence(q, [], [], [(0.45, 111)]) == "low"


def test_confidence_high_tight_route_distance():
    q = "ما هي عقوبة السرقة؟"
    assert _routing_confidence(
        q, [], [], [(ROUTE_DIST_HIGH - 0.01, 111)]
    ) == "high"


def test_content_tokens_general():
    toks = content_tokens("هل يجوز بناء جامعة اهلية على ارض زراعية؟")
    # normalize_ar folds ة→ه, ى→ي
    assert "جامعه" in toks or "اهليه" in toks, toks
    assert "ارض" in toks or "زراعيه" in toks, toks
    assert "هل" not in toks
    assert "قانون" not in toks
    assert "علي" not in toks  # على after normalize
    bonus = _chunk_topic_bonus(
        "جامعة على ارض زراعية",
        "يشترط مساحة الارض والبناية للجامعة الاهلية",
    )
    assert bonus > 0, bonus


def test_merge_order_high_prefers_routed():
    """Named-law merge: routed before hybrid."""
    routed = [{"chunk_id": "r1", "title": "routed"}]
    hybrid = [{"chunk_id": "h1", "title": "hybrid"}]
    title = [{"chunk_id": "t1", "title": "title"}]
    out = _merge_rows([], routed, title, hybrid, limit=3)
    assert [r["chunk_id"] for r in out] == ["r1", "t1", "h1"]


def test_merge_order_low_prefers_hybrid():
    """Topical merge: hybrid before routed."""
    routed = [{"chunk_id": "r1", "title": "routed"}]
    hybrid = [{"chunk_id": "h1", "title": "hybrid"}]
    title = [{"chunk_id": "t1", "title": "title"}]
    out = _merge_rows([], hybrid, title, routed, limit=3)
    assert [r["chunk_id"] for r in out] == ["h1", "t1", "r1"]


def test_agrarian_not_preferred_over_ahli_phrase():
    q = "حسب قانون التعليم الاهلي"
    ids = laws_matching_instrument_phrases(q, FAKE_REG)
    assert 999 not in ids[:1]
    assert 35340 in ids


if __name__ == "__main__":
    test_instrument_phrases()
    test_phrase_match_ranks_2016()
    test_seed_ranks_2016_for_uni()
    test_confidence_high_named_law()
    test_confidence_low_topical_theft()
    test_confidence_high_tight_route_distance()
    test_content_tokens_general()
    test_merge_order_high_prefers_routed()
    test_merge_order_low_prefers_hybrid()
    test_agrarian_not_preferred_over_ahli_phrase()
    print("all routing unit tests passed")
