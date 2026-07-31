"""
eval_recall.py — cheap recall@k harness for retrieval (no LLM calls).

Measures whether the right law / article shows up in the top-k retrieved
chunks. Embedding the question still needs OPENROUTER_API_KEY (same path as
ask.py). Run after hybrid FTS is built:

    python ingest.py --backfill-articles --build-fts
    python eval_recall.py
    python eval_recall.py --k 6 --vector-only   # compare old vs hybrid

Gold cases are the domains already spot-checked in AGENTS.md. Expand the
list as you manually validate more queries.
"""

from __future__ import annotations
import argparse
import os
import sys

import lancedb
import requests

from common import (
    DB_DIR, TABLE_NAME,
    OPENROUTER_URL, OPENROUTER_EMBED_MODEL,
    normalize_ar, parse_article_query, extract_article_numbers,
    load_dotenv, set_use_law_cards,
)
from ask import retrieve

load_dotenv()

# Gold for the tiny committed sample_laws.jsonl fixture (CI / cold-start smoke).
# Full EVAL_CASES below need a real corpus release.
SAMPLE_EVAL_CASES = [
    {
        "id": "theft",
        "q": "ما هي عقوبة السرقة؟",
        "title_any": ["قانون العقوبات"],
        "articles_any": ["438", "439"],
    },
    {
        "id": "labor_leave",
        "q": "كم مدة الإجازة السنوية في قانون العمل؟",
        "title_any": ["قانون العمل"],
        "articles_any": ["74", "75"],
    },
    {
        "id": "rent",
        "q": "ما أحكام إيجار العقار في القانون المدني؟",
        "title_any": ["القانون المدني", "المدني"],
        "articles_any": [],
    },
    {
        "id": "divorce",
        "q": "ما أحكام الطلاق في قانون الأحوال الشخصية؟",
        "title_any": ["احوال شخصيه", "الأحوال الشخصية", "الاحوال الشخصية"],
        "articles_any": [],
    },
    {
        "id": "article_exact_labor",
        "q": "المادة 75 قانون العمل",
        "title_any": ["قانون العمل"],
        "articles_any": ["75"],
    },
    {
        "id": "companies",
        "q": "متى تكتسب الشركة الشخصية المعنوية؟",
        "title_any": ["شركات", "الشركات"],
        "articles_any": ["8"],
    },
    {
        "id": "civil_lease_articles",
        "q": "المادة 741 القانون المدني",
        "title_any": ["القانون المدني", "المدني"],
        "articles_any": ["741"],
    },
]

# title_any: at least one of these substrings (Arabic-normalized) must appear
# in a retrieved title. articles_any: at least one of these ASCII article
# numbers must appear in article_nums or chunk text. Both are checked when
# provided; a case passes if title matches AND (articles match OR no articles
# listed).
EVAL_CASES = [
    {
        "id": "theft",
        "q": "ما هي عقوبة السرقة؟",
        "title_any": ["قانون العقوبات"],
        "articles_any": ["438", "439", "440", "441", "442", "443", "444", "445"],
    },
    {
        "id": "labor_leave",
        "q": "كم مدة الإجازة السنوية في قانون العمل؟",
        "title_any": ["قانون العمل"],
        "articles_any": ["74", "75", "76", "77", "78"],
    },
    {
        "id": "rent",
        "q": "ما أحكام إيجار العقار في القانون المدني؟",
        # Iraqi rental questions often hit قانون إيجار العقار (1979) before
        # the civil-code lease chapter — both are valid retrievals.
        "title_any": ["القانون المدني", "المدني", "ايجار العقار", "إيجار العقار"],
        "articles_any": [],
    },
    {
        "id": "divorce",
        "q": "ما أحكام الطلاق في قانون الأحوال الشخصية؟",
        "title_any": ["احوال شخصيه", "الأحوال الشخصية", "الاحوال الشخصية",
                      "جعفري", "الجعفري"],
        "articles_any": [],
    },
    {
        "id": "article_exact_labor",
        "q": "المادة 75 قانون العمل",
        "title_any": ["قانون العمل"],
        "articles_any": ["75"],
    },
    {
        "id": "companies_unregistered",
        "q": "ما هي عقوبة افتتاح شركة بدون أي تبليغ رسمي؟",
        "title_any": ["شركات", "الشركات"],
        "articles_any": [],
    },
    {
        "id": "murder_penalty",
        "q": "ما عقوبة القتل العمد؟",
        "title_any": ["قانون العقوبات"],
        "articles_any": ["405", "406", "407", "408"],
    },
    {
        "id": "notarial_notice",
        "q": "ما هو الإنذار العدلي ومتى يُستخدم؟",
        # Retrieval often hits إنذار/مواصفات خدمات or إشراف عدلي before
        # a general المرافعات chunk — all are on-topic for the query.
        "title_any": [
            "كاتب العدل", "الكتاب العدول", "التنفيذ", "المرافعات",
            "الانذار", "الإنذار", "اشراف عدلي", "إشراف عدلي", "عدلي",
        ],
        "articles_any": [],
    },
    {
        "id": "civil_lease_articles",
        "q": "المادة 741 القانون المدني",
        "title_any": ["القانون المدني", "المدني"],
        "articles_any": ["741"],
    },
    {
        "id": "private_education",
        "q": "ما هو قانون التعليم الاهلي؟",
        # Title-boost should surface نظام التعليم الأهلي (schools) for this
        # phrasing; العالي الأهلي is also acceptable as a related hit.
        "title_any": [
            "التعليم الاهلي", "التعليم الأهلي",
            "التعليم العالي الاهلي", "التعليم العالي الأهلي",
        ],
        "articles_any": [],
    },
    {
        "id": "private_uni_ag_land",
        "q": "هل يجوز بناء جامعة اهلية على ارض زراعية حسب قانون التعليم الاهلي؟",
        # Named colloquial statute + topical "ارض" must NOT drown in
        # اصلاح زراعي / سكن neighbors — route to العالي الأهلي 2016.
        "title_any": [
            "التعليم العالي الاهلي", "التعليم العالي الأهلي",
            "التعليم الاهلي", "التعليم الأهلي",
        ],
        "articles_any": ["6", "51"],
    },
    {
        "id": "murder_not_inheritance",
        "q": "ما عقوبة القتل العمد في قانون العقوبات؟",
        # Wrong-neighbor stress: inheritance bars that mention قتل must not
        # beat the penal code when the instrument is named.
        "title_any": ["قانون العقوبات"],
        "articles_any": ["405", "406", "407", "408"],
    },
]


def _embed(session: requests.Session, q: str) -> list[float]:
    r = session.post(
        OPENROUTER_URL,
        json={"model": OPENROUTER_EMBED_MODEL, "input": [q]},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


def _row_articles(r: dict) -> str:
    return r.get("article_nums") or extract_article_numbers(r.get("text") or "")


def _title_hit(rows: list[dict], needles: list[str]) -> bool:
    if not needles:
        return True
    norms = [normalize_ar(n) for n in needles]
    for r in rows:
        title = normalize_ar(r.get("title") or "")
        if any(n in title for n in norms):
            return True
    return False


def _article_hit(rows: list[dict], articles: list[str]) -> bool:
    if not articles:
        return True
    for r in rows:
        nums = _row_articles(r)
        for a in articles:
            if f",{a}," in (nums or ""):
                return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=6)
    ap.add_argument("--vector-only", action="store_true")
    ap.add_argument("--all", action="store_true",
                    help="include repealed laws (default: ساري only)")
    ap.add_argument(
        "--sample",
        action="store_true",
        help="use SAMPLE_EVAL_CASES (for sample_laws.jsonl stores)",
    )
    ap.add_argument(
        "--full",
        action="store_true",
        help="force full EVAL_CASES even on a small store",
    )
    ap.add_argument(
        "--no-cards",
        action="store_true",
        help="disable law_cards/alias_lexicon routing (A/B). "
             "Same as IRAQI_RAG_NO_CARDS=1.",
    )
    args = ap.parse_args()

    if args.no_cards:
        set_use_law_cards(False)

    if not DB_DIR.exists():
        sys.exit(
            f"No store at {DB_DIR}. Run ingest.py / setup_store.py first.\n"
            "Or point at an existing store: "
            '$env:IRAQI_RAG_DB_DIR = "C:\\path\\to\\lancedb"'
        )
    or_key = os.environ.get("OPENROUTER_API_KEY")
    if not or_key:
        sys.exit(
            "Set OPENROUTER_API_KEY first (env or .env).\n"
            '  PowerShell:  $env:OPENROUTER_API_KEY = "sk-or-v1-..."\n'
            '  bash:        export OPENROUTER_API_KEY="sk-or-v1-..."'
        )

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {or_key}",
        "Content-Type": "application/json",
    })
    db = lancedb.connect(str(DB_DIR))
    table = db.open_table(TABLE_NAME)
    n_chunks = table.count_rows()

    use_sample = args.sample or (not args.full and n_chunks < 200)
    cases = SAMPLE_EVAL_CASES if use_sample else EVAL_CASES

    print(f"chunks={n_chunks}  k={args.k}  "
          f"mode={'vector' if args.vector_only else 'hybrid'}  "
          f"suite={'sample' if use_sample else 'full'}  "
          f"cards={'off' if args.no_cards else 'on'}  "
          f"db={DB_DIR}")
    print("-" * 72)

    n_pass = 0
    for case in cases:
        q = case["q"]
        qvec = _embed(session, q)
        rows = retrieve(table, qvec, q, args.k, args.all, args.vector_only)
        title_ok = _title_hit(rows, case["title_any"])
        art_ok = _article_hit(rows, case["articles_any"])
        ok = title_ok and art_ok
        n_pass += int(ok)
        mark = "PASS" if ok else "FAIL"
        top = (rows[0].get("title") or "")[:55] if rows else "(none)"
        detail = []
        if not title_ok:
            detail.append("title miss")
        if not art_ok:
            detail.append(f"articles {case['articles_any'][:4]}… miss")
        parsed = parse_article_query(q)
        extra = f"  [exact art={parsed}]" if parsed else ""
        print(f"{mark:4}  {case['id']:22}  top={top}{extra}")
        if detail:
            print(f"      ({', '.join(detail)})")
            for i, r in enumerate(rows[:3], 1):
                arts = (_row_articles(r) or "").strip(",")[:40]
                print(f"      {i}. {(r.get('title') or '')[:60]}  arts={arts}")

    total = len(cases)
    print("-" * 72)
    print(f"recall@{args.k}: {n_pass}/{total} cases "
          f"({100.0 * n_pass / total:.0f}%)")
    if n_pass < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
