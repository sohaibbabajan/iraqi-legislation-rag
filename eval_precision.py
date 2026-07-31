"""
eval_precision.py — hard / precision-oriented retrieval eval (embeds only).

Complements eval_recall.py (title substring + any article in top-k). This
harness requires the *correct* law_book_id (and article on that law) within
a tight rank window, and catches amendment / wrong-law / near-miss traps
that saturated recall@6 cannot see.

    $env:IRAQI_RAG_DB_DIR = "C:\\iraqi-law-rag\\lancedb"
    python eval_precision.py
    python eval_precision.py --no-cards
    python eval_precision.py --k 6 --law-at 3

No answer LLM. Question embeds only (≪$0.01 for the full suite).
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import lancedb
import requests

from common import (
    DB_DIR,
    TABLE_NAME,
    OPENROUTER_URL,
    OPENROUTER_EMBED_MODEL,
    normalize_ar,
    parse_article_query,
    extract_article_numbers,
    load_dotenv,
    set_use_law_cards,
)
from ask import retrieve

load_dotenv()

# Canonical in-force base codes (Masadir full store / corpus-2026-07-31).
LID_PENAL = 25860          # قانون العقوبات رقم ١١١ لسنة ١٩٦٩
LID_LABOR = 32566          # قانون العمل رقم ٣٧ لسنة ٢٠١٥
LID_CIVIL = 27297          # القانون المدني العراقي رقم (٤٠) لسنة ١٩٥١
LID_PERSONAL = 12294       # قانون الاحوال الشخصية (1959)
LID_HI_PRIV_EDU = 35340    # قانون التعليم العالي الاهلي رقم ٢٥ لسنة ٢٠١٦
LID_SYS_PRIV_EDU = 30735   # نظام التعليم الاهلي و الاجنبي رقم ٥ لسنة ٢٠١٣
LID_SYS_PRIV_EDU_1968 = 23716  # نظام التعليم الاهلي والاجنبي رقم (٥) لسنة ١٩٦٨
LID_CRIM_PROC = 8766       # قانون اصول المحاكمات الجزائية رقم (٢٣) لسنة ١٩٧١
LID_CIVIL_PROC = 25832     # قانون المرافعات المدنية رقم ٨٣ لسنة ١٩٦٩
LID_COMPANIES = 22025      # قانون الشركات رقم (٢١) لسنة ١٩٩٧
LID_RENT = 16586           # قانون ايجار العقار رقم (٨٧) لسنة ١٩٧٩
LID_CIVIL_SERVICE = 3589   # قانون الخدمة المدنية رقم (٢٤) لسنة ١٩٦٠
LID_LABOR_BAYAN_1936 = 3555  # بيان - الموضوع قانون العمل رقم ٧٢ لسنة ١٩٣٦

# Hard cases: pass = correct law in top-`law_at` AND (if listed) correct
# article on that law in top-`article_at`, plus optional outrank forbids.
HARD_CASES: list[dict[str, Any]] = [
    # --- Colloquial alias → correct statute ---------------------------------
    {
        "id": "alias_edu_colloquial",
        "q": "ما هو قانون التعليم الاهلي؟",
        # 2016 عالي / 2013 نظام / 1968 نظام all count; تعديل الجامعات must not win.
        "must_law_ids": [LID_HI_PRIV_EDU, LID_SYS_PRIV_EDU, LID_SYS_PRIV_EDU_1968],
        "must_articles_any": [],
        "law_at": 3,
        "forbid_amendment_outrank": True,
        "note": "Colloquial «تعليم اهلي» → عالي/نظام; تعديل الجامعات must not outrank.",
    },
    {
        "id": "alias_edu_uni_land",
        "q": "هل يجوز بناء جامعة اهلية على ارض زراعية حسب قانون التعليم الاهلي؟",
        "must_law_ids": [LID_HI_PRIV_EDU],
        "must_articles_any": ["6", "51"],
        "law_at": 3,
        "note": "Named colloquial + ارض must not drown in اصلاح زراعي; عالي أهلي only.",
    },
    {
        "id": "alias_civil_service",
        "q": "ما أحكام التعيين في قانون الخدمة المدنية؟",
        "must_law_ids": [LID_CIVIL_SERVICE],
        "must_articles_any": [],
        "law_at": 3,
        "note": "Bare الخدمة المدنية → 24/1960, not تعليمات / ذيول.",
    },
    {
        "id": "alias_criminal_procedure",
        "q": "ما إجراءات التوقيف في اصول المحاكمات الجزائية؟",
        "must_law_ids": [LID_CRIM_PROC],
        "must_articles_any": [],
        "law_at": 3,
        "note": "اصول المحاكمات الجزائية → 23/1971 base.",
    },
    # --- Amendment traps (base must beat تعديل when query is not about تعديل)
    {
        "id": "amend_trap_penal",
        "q": "ما هي عقوبة السرقة في قانون العقوبات؟",
        "must_law_ids": [LID_PENAL],
        "must_articles_any": ["438", "439", "440", "441", "442", "443", "444", "445"],
        "law_at": 3,
        "forbid_amendment_outrank": True,
        "note": "تعديل قانون العقوبات must not outrank base 111/1969.",
    },
    {
        "id": "amend_trap_personal",
        "q": "ما أحكام الطلاق في قانون الأحوال الشخصية؟",
        "must_law_ids": [LID_PERSONAL],
        "must_articles_any": [],
        "law_at": 3,
        "forbid_amendment_outrank": True,
        "note": "Personal-status base 1959 before its تعديلات.",
    },
    {
        "id": "amend_trap_labor_bayan",
        "q": "كم مدة الإجازة السنوية في قانون العمل؟",
        "must_law_ids": [LID_LABOR],
        "must_articles_any": ["74", "75", "76", "77", "78"],
        "law_at": 3,
        "forbid_law_ids_outrank": [LID_LABOR_BAYAN_1936],
        "forbid_amendment_outrank": True,
        "note": "2015 labour code; 1936 bayan must not hijack bare «قانون العمل».",
    },
    # --- Exact article + wrong-law trap ------------------------------------
    {
        "id": "exact_labor_75_not_foreign",
        "q": "المادة 75 قانون العمل",
        "must_law_ids": [LID_LABOR],
        "must_articles_any": ["75"],
        "law_at": 3,
        "article_at": 3,
        "note": "Art 75 must be on قانون العمل 37/2015, not another statute's 75.",
    },
    {
        "id": "exact_civil_741_scoped",
        "q": "المادة 741 القانون المدني",
        "must_law_ids": [LID_CIVIL],
        "must_articles_any": ["741"],
        "law_at": 3,
        "article_at": 3,
        "note": "Civil-code lease art; wrong-law 741 is a fail.",
    },
    {
        "id": "wrong_law_art_trap",
        "q": "المادة 75 القانون المدني",
        "must_law_ids": [LID_CIVIL],
        "must_articles_any": ["75"],
        "law_at": 6,
        "article_at": 6,
        "forbid_law_ids_outrank": [LID_LABOR],
        "note": "Civil art 75 — labour-law art 75 must not win the slot.",
    },
    # --- Near-miss article numbers -----------------------------------------
    {
        "id": "near_miss_penal_438",
        "q": "المادة 438 قانون العقوبات",
        "must_law_ids": [LID_PENAL],
        "must_articles_any": ["438"],
        "law_at": 3,
        "article_at": 3,
        "note": "Must hit 438 on العقوبات, not only neighbour 439/440.",
    },
    {
        "id": "near_miss_labor_74",
        "q": "المادة 74 قانون العمل",
        "must_law_ids": [LID_LABOR],
        "must_articles_any": ["74"],
        "law_at": 3,
        "article_at": 3,
        "note": "Must hit 74, not only the more-retrieved 75 leave article.",
    },
    # --- Card-routing footguns ---------------------------------------------
    {
        "id": "card_footgun_personal_not_procedure",
        "q": "ما هو قانون الأحوال الشخصية؟",
        "must_law_ids": [LID_PERSONAL],
        "must_articles_any": [],
        "law_at": 3,
        "forbid_law_ids_outrank": [LID_CIVIL_PROC],
        "forbid_amendment_outrank": True,
        "note": "SPEND_REVIEW: مرافعات amendment must not claim الأحوال الشخصية.",
    },
    {
        "id": "card_footgun_named_penal",
        "q": "ما عقوبة القتل العمد في قانون العقوبات؟",
        "must_law_ids": [LID_PENAL],
        "must_articles_any": ["405", "406", "407", "408"],
        "law_at": 3,
        "forbid_amendment_outrank": True,
        "note": "Named instrument; inheritance/قتل bars must not beat العقوبات.",
    },
    # --- Topical precision (law_id, not loose title) -----------------------
    {
        "id": "companies_personality",
        "q": "متى تكتسب الشركة الشخصية المعنوية؟",
        "must_law_ids": [LID_COMPANIES],
        # 21/1997: art 5 (general) + art 22 (from certificate date). Art 8 is
        # private-company formation, not personality — do not require it.
        "must_articles_any": ["5", "22"],
        "law_at": 3,
        "note": "Companies 21/1997 art 5/22 — not شركات عامة / art 8 formation.",
    },
    {
        "id": "rent_named_statute",
        "q": "ما أحكام قانون إيجار العقار؟",
        "must_law_ids": [LID_RENT],
        "must_articles_any": [],
        "law_at": 3,
        "note": "Named إيجار العقار → 87/1979, not only civil-code lease chapter.",
    },
    # --- Amendment linkage meta (sidecar may exist; retrieve need not decorate)
    {
        "id": "amend_link_penal_meta",
        "q": "ما هي عقوبة السرقة في قانون العقوبات؟",
        "must_law_ids": [LID_PENAL],
        "must_articles_any": [],
        "law_at": 3,
        "expect_amendment_meta": True,
        "note": "Base 111/1969 in top-3 AND amendment_links sidecar lists amenders.",
    },
]


def _embed(session: requests.Session, q: str) -> list[float]:
    import time

    last_err: Exception | None = None
    for attempt in range(6):
        r = session.post(
            OPENROUTER_URL,
            json={"model": OPENROUTER_EMBED_MODEL, "input": [q]},
            timeout=60,
        )
        if r.status_code == 429:
            wait = min(2 ** attempt, 30)
            print(f"  (embed 429 — sleep {wait}s)", flush=True)
            time.sleep(wait)
            last_err = requests.HTTPError("429 after retries", response=r)
            continue
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]
    assert last_err is not None
    raise last_err


def _row_articles(r: dict) -> str:
    return r.get("article_nums") or extract_article_numbers(r.get("text") or "")


def _lid(r: dict) -> int:
    try:
        return int(r.get("law_book_id") or 0)
    except (TypeError, ValueError):
        return 0


def _is_amendment_row(r: dict) -> bool:
    flag = (r.get("law_flag") or "").strip()
    if flag == "تعديل":
        return True
    title = r.get("title") or ""
    tn = normalize_ar(title)
    # «معدل» alone is the base-as-amended, not an amending act.
    markers = (
        "تعديل قانون", "قانون تعديل", "التعديل الاول", "التعديل الثاني",
        "التعديل الثالث", "قانون التعديل", "تعديل اول", "بيان تصحيح",
        "بيان -", "قانون ذيل", "ذيل قانون",
    )
    return any(normalize_ar(m) in tn or m in title for m in markers)


def _best_rank(rows: list[dict], law_ids: list[int]) -> int | None:
    allow = set(int(x) for x in law_ids)
    for i, r in enumerate(rows):
        if _lid(r) in allow:
            return i
    return None


def _article_on_law(
    rows: list[dict], law_ids: list[int], articles: list[str], limit: int
) -> bool:
    if not articles:
        return True
    allow = set(int(x) for x in law_ids)
    for r in rows[:limit]:
        if _lid(r) not in allow:
            continue
        nums = _row_articles(r) or ""
        for a in articles:
            if f",{a}," in nums:
                return True
    return False


def _forbid_outrank(
    rows: list[dict],
    must_rank: int | None,
    forbid_ids: list[int],
    forbid_amendment: bool,
    must_ids: list[int],
) -> str | None:
    if must_rank is None:
        return None
    must_set = set(int(x) for x in must_ids)
    forbid_set = set(int(x) for x in forbid_ids)
    for i, r in enumerate(rows):
        if i >= must_rank:
            break
        lid = _lid(r)
        if lid in must_set:
            continue
        if lid in forbid_set:
            return f"forbid_law {lid} outranks must @{must_rank}"
        if forbid_amendment and _is_amendment_row(r):
            return (
                f"amendment outranks base: "
                f"#{i+1} lid={lid} before must @{must_rank+1}"
            )
    return None


def _has_amendment_meta(rows: list[dict], must_ids: list[int]) -> bool:
    """True if must-law rows expose link fields, or the sidecar knows amenders."""
    allow = set(int(x) for x in must_ids)
    keys = ("amended_by", "amends", "amendment_links", "linked_amendments")
    for r in rows:
        if _lid(r) not in allow:
            continue
        for k in keys:
            v = r.get(k)
            if v:
                return True
    # Sidecar may exist before retrieve decorates rows (peer amendment work).
    try:
        from amendment_links import get_amendment_index
        idx = get_amendment_index()
        for lid in allow:
            if idx.amended_by(lid):
                return True
    except Exception:
        pass
    return False


def evaluate_case(
    case: dict[str, Any],
    rows: list[dict],
    *,
    default_law_at: int,
    default_article_at: int,
) -> dict[str, Any]:
    must_ids = list(case.get("must_law_ids") or [])
    arts = list(case.get("must_articles_any") or [])
    law_at = int(case.get("law_at") or default_law_at)
    art_at = int(case.get("article_at") or default_article_at)
    xfail = case.get("xfail_until")

    reasons: list[str] = []
    window = rows[: max(law_at, art_at, 6)]
    must_rank = _best_rank(window, must_ids)
    law_ok = must_rank is not None and must_rank < law_at
    if not law_ok:
        reasons.append(f"law miss in top-{law_at} (want ids={must_ids})")

    art_ok = _article_on_law(rows, must_ids, arts, art_at)
    if arts and not art_ok:
        reasons.append(
            f"article {arts} miss on must-law in top-{art_at}"
        )

    outrank = _forbid_outrank(
        rows,
        must_rank if law_ok else None,
        list(case.get("forbid_law_ids_outrank") or []),
        bool(case.get("forbid_amendment_outrank")),
        must_ids,
    )
    if outrank:
        reasons.append(outrank)

    meta_ok = True
    if case.get("expect_amendment_meta"):
        meta_ok = _has_amendment_meta(rows, must_ids)
        if not meta_ok:
            reasons.append("amendment meta missing (amended_by/…)")

    hard_ok = law_ok and art_ok and outrank is None
    if case.get("expect_amendment_meta"):
        ok = hard_ok and meta_ok
    else:
        ok = hard_ok

    status = "PASS"
    if not ok:
        # Future amendment-meta cases: XFAIL when only meta is missing (or
        # when hard also fails — still reserved until linkage ships).
        if xfail and case.get("expect_amendment_meta") and not meta_ok:
            status = "XFAIL"
        else:
            status = "FAIL"

    return {
        "status": status,
        "ok": ok,
        "hard_ok": hard_ok,
        "reasons": reasons,
        "must_rank": must_rank,
        "law_at": law_at,
        "art_at": art_at,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--k", type=int, default=6)
    ap.add_argument("--law-at", type=int, default=3,
                    help="default rank window for must_law_ids (case may override)")
    ap.add_argument("--article-at", type=int, default=6,
                    help="default rank window for must articles on that law")
    ap.add_argument("--vector-only", action="store_true")
    ap.add_argument("--all", action="store_true",
                    help="include repealed laws")
    ap.add_argument("--no-cards", action="store_true")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 on any FAIL (default: report scores, exit 0 — suite is "
             "expected to stay partially red while routing gaps remain)",
    )
    ap.add_argument(
        "--include-xfail",
        action="store_true",
        help="with --strict, also fail the process on XFAIL",
    )
    ap.add_argument(
        "--ids",
        nargs="+",
        default=None,
        help="run only these case ids (smoke / debug)",
    )
    args = ap.parse_args()

    if args.no_cards:
        set_use_law_cards(False)

    if not DB_DIR.exists():
        sys.exit(
            f"No store at {DB_DIR}. Point at Masadir store:\n"
            '  $env:IRAQI_RAG_DB_DIR = "C:\\iraqi-law-rag\\lancedb"'
        )
    or_key = os.environ.get("OPENROUTER_API_KEY")
    if not or_key:
        sys.exit("Set OPENROUTER_API_KEY first (env or .env).")

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {or_key}",
        "Content-Type": "application/json",
    })
    db = lancedb.connect(str(DB_DIR))
    table = db.open_table(TABLE_NAME)
    n_chunks = table.count_rows()

    cases = HARD_CASES
    if args.ids:
        want = set(args.ids)
        cases = [c for c in HARD_CASES if c["id"] in want]
        missing = want - {c["id"] for c in cases}
        if missing:
            sys.exit(f"unknown case ids: {sorted(missing)}")

    print(
        f"chunks={n_chunks}  k={args.k}  law_at={args.law_at}  "
        f"mode={'vector' if args.vector_only else 'hybrid'}  "
        f"cards={'off' if args.no_cards else 'on'}  "
        f"cases={len(cases)}  db={DB_DIR}"
    )
    print("-" * 78)

    n_pass = n_fail = n_xfail = 0
    law3_hits = 0
    for case in cases:
        q = case["q"]
        qvec = _embed(session, q)
        rows = retrieve(table, qvec, q, args.k, args.all, args.vector_only)
        result = evaluate_case(
            case, rows,
            default_law_at=args.law_at,
            default_article_at=args.article_at,
        )
        status = result["status"]
        if status == "PASS":
            n_pass += 1
        elif status == "XFAIL":
            n_xfail += 1
        else:
            n_fail += 1

        if result["must_rank"] is not None and result["must_rank"] < 3:
            law3_hits += 1

        top = (rows[0].get("title") or "")[:52] if rows else "(none)"
        top_lid = _lid(rows[0]) if rows else 0
        parsed = parse_article_query(q)
        extra = f"  [exact art={parsed}]" if parsed else ""
        rank_s = (
            f"must@{result['must_rank']+1}"
            if result["must_rank"] is not None
            else "must@—"
        )
        print(
            f"{status:5}  {case['id']:34}  {rank_s:7}  "
            f"top_lid={top_lid}  top={top}{extra}"
        )
        if result["reasons"]:
            print(f"       ({'; '.join(result['reasons'])})")
            for i, r in enumerate(rows[:4], 1):
                arts = (_row_articles(r) or "").strip(",")[:36]
                flag = r.get("law_flag") or ""
                print(
                    f"       {i}. lid={_lid(r)} [{flag}] "
                    f"{(r.get('title') or '')[:58]}  arts={arts}"
                )

    total = len(cases)
    n_core = sum(1 for c in cases if not c.get("xfail_until"))
    print("-" * 78)
    print(
        f"precision suite: PASS {n_pass}/{total}  FAIL {n_fail}  "
        f"XFAIL {n_xfail}  "
        f"correct_law@3 {law3_hits}/{total} "
        f"({100.0 * law3_hits / total:.0f}%)"
    )
    print(
        f"core (excl. xfail-until cases): "
        f"{n_pass}/{n_core}  (use --strict to exit non-zero on FAIL)"
    )

    # Default exit 0: hard suite is a measurement, not a green gate yet.
    if args.strict and n_fail:
        sys.exit(1)
    if args.strict and args.include_xfail and n_xfail:
        sys.exit(1)


if __name__ == "__main__":
    main()
