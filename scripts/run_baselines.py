"""One-shot baseline runner: hybrid/vector × cards on/off. Embeds only."""
from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ["IRAQI_RAG_DB_DIR"] = os.environ.get(
    "IRAQI_RAG_DB_DIR", r"C:\iraqi-law-rag\lancedb"
)

import lancedb
import requests
from common import (
    DB_DIR, TABLE_NAME, OPENROUTER_URL, OPENROUTER_EMBED_MODEL,
    load_dotenv, set_use_law_cards,
)
from eval_recall import EVAL_CASES, _embed, _title_hit, _article_hit, _row_articles
from ask import retrieve

load_dotenv()


def run_suite(*, vector_only: bool, cards: bool, k: int = 6) -> tuple[int, int]:
    set_use_law_cards(cards)
    or_key = os.environ.get("OPENROUTER_API_KEY")
    if not or_key:
        sys.exit("OPENROUTER_API_KEY missing")
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {or_key}",
        "Content-Type": "application/json",
    })
    db = lancedb.connect(str(DB_DIR))
    table = db.open_table(TABLE_NAME)
    n = table.count_rows()
    label = f"{'vector' if vector_only else 'hybrid'} cards={'on' if cards else 'off'}"
    print(f"\n=== {label}  chunks={n} db={DB_DIR} ===", flush=True)
    n_pass = 0
    for case in EVAL_CASES:
        qvec = _embed(session, case["q"])
        rows = retrieve(table, qvec, case["q"], k, False, vector_only)
        ok = _title_hit(rows, case["title_any"]) and _article_hit(
            rows, case["articles_any"]
        )
        n_pass += int(ok)
        mark = "PASS" if ok else "FAIL"
        top = (rows[0].get("title") or "")[:50] if rows else "(none)"
        print(f"{mark}  {case['id']:22}  top={top}", flush=True)
    total = len(EVAL_CASES)
    print(f"RESULT {label}: {n_pass}/{total}", flush=True)
    return n_pass, total


def main():
    # Only the missing cell if argv says so; else all four.
    mode = (sys.argv[1] if len(sys.argv) > 1 else "all").strip()
    out_path = None
    if len(sys.argv) > 2:
        out_path = sys.argv[2]
        # tee prints into a utf-8 file ourselves (avoid PowerShell UTF-16 redirect)
        class _Tee:
            def __init__(self, path):
                self._f = open(path, "w", encoding="utf-8")
                self._out = sys.__stdout__
            def write(self, s):
                self._out.write(s)
                self._f.write(s)
            def flush(self):
                self._out.flush()
                self._f.flush()
            def close(self):
                self._f.close()
        tee = _Tee(out_path)
        sys.stdout = tee  # type: ignore
    results = []
    specs = [
        ("hybrid_cards", False, True),
        ("hybrid_nocards", False, False),
        ("vector_cards", True, True),
        ("vector_nocards", True, False),
    ]
    if mode != "all":
        specs = [s for s in specs if s[0] == mode]
        if not specs:
            sys.exit(f"unknown mode {mode}")
    try:
        for name, vo, cards in specs:
            n_pass, total = run_suite(vector_only=vo, cards=cards)
            results.append((name, n_pass, total))
        print("\n--- SUMMARY ---", flush=True)
        for name, n_pass, total in results:
            print(f"{name}: {n_pass}/{total}", flush=True)
    finally:
        if out_path:
            sys.stdout.flush()
            getattr(sys.stdout, "close", lambda: None)()
            sys.stdout = sys.__stdout__


if __name__ == "__main__":
    main()
