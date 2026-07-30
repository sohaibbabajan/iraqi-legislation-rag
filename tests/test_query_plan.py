"""Unit tests for QueryPlan classification + quota fusion (no LanceDB / API)."""
from __future__ import annotations

from query_plan import (
    Shape,
    plan_query,
    fuse_legs,
    quotas_dict,
    parse_citation_key,
    route_vector_margin,
)


def test_exact_article_shape():
    plan = plan_query("المادة 75 قانون العمل")
    assert plan.shape == Shape.EXACT_ARTICLE
    assert plan.article_label == "75"
    assert "article_exact" in quotas_dict(plan)


def test_article_analytical_shape():
    plan = plan_query("ما هي احكام المادة 75 من قانون العمل؟")
    assert plan.shape == Shape.ARTICLE_ANALYTICAL
    assert plan.article_label == "75"
    q = quotas_dict(plan)
    assert q.get("article_exact", 0) >= 1
    assert q.get("hybrid", 0) >= 1


def test_topical_theft_shape():
    plan = plan_query("ما هي عقوبة السرقة؟")
    assert plan.shape == Shape.TOPICAL
    q = quotas_dict(plan)
    assert q.get("hybrid", 0) >= q.get("law_scoped", 0)


def test_named_instrument_from_phrase():
    plan = plan_query(
        "ما احكام الطلاق في قانون الاحوال الشخصية؟",
        phrase_ids=[188],
    )
    assert plan.shape in (Shape.NAMED_INSTRUMENT, Shape.DEFINITIONAL)
    assert quotas_dict(plan).get("law_scoped", 0) >= 2


def test_definitional_overview():
    plan = plan_query(
        "ما هو قانون العمل؟",
        phrase_ids=[71],
    )
    assert plan.shape == Shape.DEFINITIONAL
    assert "defining_articles" in quotas_dict(plan)


def test_citation_key_parse_and_shape():
    assert parse_citation_key("قانون العقوبات رقم 111 لسنة 1969") == ("111", "1969")
    plan = plan_query(
        "ما نص قانون العقوبات رقم 111 لسنة 1969؟",
        citation_law_ids=[111],
    )
    assert plan.shape == Shape.CITATION_KEY
    assert plan.scope_doc_ids == [111]


def test_multi_instrument_comparative():
    plan = plan_query("ما الفرق بين قانون العمل ونظام الخدمة المدنية؟")
    assert plan.shape == Shape.MULTI_INSTRUMENT


def test_route_margin_prefers_gap_not_absolute():
    # Absolute distance alone used to drive ROUTE_DIST_HIGH; margin is scale-free.
    tight = route_vector_margin([(0.20, 1), (0.45, 2)])
    loose = route_vector_margin([(0.20, 1), (0.22, 2)])
    assert tight > loose
    assert tight >= 0.08


def test_named_via_route_margin():
    plan = plan_query(
        "ما هي عقوبة السرقة؟",
        vector_ranked=[(0.18, 111), (0.40, 222)],
    )
    assert plan.shape == Shape.NAMED_INSTRUMENT


def test_fuse_respects_leg_quotas():
    plan = plan_query("ما هي عقوبة السرقة؟")  # TOPICAL: hybrid 4, …
    hybrid = [
        {"chunk_id": f"h{i}", "law_book_id": 100 + i, "article_nums": f",{i},"}
        for i in range(8)
    ]
    routed = [
        {"chunk_id": f"r{i}", "law_book_id": 200 + i, "article_nums": f",{i},"}
        for i in range(8)
    ]
    out = fuse_legs(
        {"hybrid": hybrid, "law_scoped": routed, "title_like": [], "card_route": []},
        plan,
        k=6,
    )
    assert len(out) <= 6
    n_hybrid = sum(1 for r in out if str(r.get("_leg")) == "hybrid")
    n_scoped = sum(1 for r in out if str(r.get("_leg")) == "law_scoped")
    # Hybrid quota dominates topical; law_scoped cannot crowd out all hybrid slots
    assert n_hybrid >= n_scoped


def test_fuse_per_law_diversity_cap_topical():
    plan = plan_query("ما هي عقوبة السرقة؟")
    assert plan.max_per_law == 2
    # One verbose statute trying to monopolize context
    monopolist = [
        {
            "chunk_id": f"m{i}",
            "law_book_id": 111,
            "article_nums": f",{400 + i},",
            "article_label": str(400 + i),
        }
        for i in range(10)
    ]
    other = [
        {
            "chunk_id": "o1",
            "law_book_id": 222,
            "article_nums": ",1,",
            "article_label": "1",
        }
    ]
    out = fuse_legs(
        {"hybrid": monopolist + other, "law_scoped": [], "title_like": [], "card_route": []},
        plan,
        k=6,
    )
    from collections import Counter
    counts = Counter(int(r["law_book_id"]) for r in out)
    assert counts.get(111, 0) <= 2
    assert 222 in counts  # diversity lets the other law in


def test_fuse_dedupes_article_and_chunk():
    plan = plan_query("المادة 438 قانون العقوبات")
    art = {
        "chunk_id": "art:111:438",
        "law_book_id": 111,
        "article_label": "438",
        "article_nums": ",438,",
        "text": "defines",
    }
    chunk = {
        "chunk_id": "111#3",
        "law_book_id": 111,
        "article_nums": ",438,439,",
        "article_label": "438",
        "text": "fat chunk",
    }
    out = fuse_legs(
        {"article_exact": [art], "hybrid": [chunk]},
        plan,
        k=3,
    )
    # Same (law, article) collapses to one slot
    keys = {(int(r["law_book_id"]), str(r.get("article_label") or "")) for r in out}
    assert (111, "438") in keys
    assert len([r for r in out if int(r["law_book_id"]) == 111 and str(r.get("article_label")) == "438"]) == 1


def test_multi_guarantees_per_instrument_slot():
    plan = plan_query("ما الفرق بين قانون العمل ونظام الخدمة؟")
    assert plan.shape == Shape.MULTI_INSTRUMENT
    law_a = [{"chunk_id": "a1", "law_book_id": 71, "article_nums": ",1,"}]
    law_b = [{"chunk_id": "b1", "law_book_id": 24, "article_nums": ",1,"}]
    # Many hybrid from unrelated law would otherwise fill k
    hybrid = [
        {"chunk_id": f"h{i}", "law_book_id": 999, "article_nums": f",{i},"}
        for i in range(10)
    ]
    out = fuse_legs(
        {"law_scoped": law_a + law_b, "hybrid": hybrid},
        plan,
        k=6,
        guaranteed_law_ids=[71, 24],
    )
    lids = {int(r["law_book_id"]) for r in out}
    assert 71 in lids and 24 in lids
