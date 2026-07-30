#!/usr/bin/env python3
"""
Build cache/article_index.jsonl from a laws JSONL (deterministic, $0).

    python build_article_index.py
    python build_article_index.py --source sources/sample_laws.jsonl
    python build_article_index.py --limit 50
    python scripts/build_article_index.py   # same entrypoint

No OpenRouter calls. Safe to re-run anytime.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python scripts/build_article_index.py` from any cwd.
_ROOT = Path(__file__).resolve().parent
if _ROOT.name == "scripts":
    _ROOT = _ROOT.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from article_index import (  # noqa: E402
    ARTICLE_INDEX_FILE,
    build_article_index,
    save_article_index,
)
from common import default_corpus_path, load_dotenv  # noqa: E402

load_dotenv()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Deterministic article index (defines vs mentions)."
    )
    ap.add_argument(
        "--source",
        default=str(default_corpus_path()),
        help="JSONL corpus path",
    )
    ap.add_argument(
        "--out",
        default=str(ARTICLE_INDEX_FILE),
        help="Output JSONL path (default: cache/article_index.jsonl)",
    )
    ap.add_argument("--limit", type=int, default=0, help="First N records only")
    args = ap.parse_args()

    source = Path(args.source)
    if not source.exists():
        sys.exit(f"Source not found: {source}")

    rows = build_article_index(source, limit=args.limit)
    out = save_article_index(rows, Path(args.out))
    n_def = sum(1 for r in rows if r.role == "defines")
    n_men = sum(1 for r in rows if r.role == "mentions")
    print(
        f"Wrote {len(rows)} rows ({n_def} defines, {n_men} mentions) -> {out}",
        flush=True,
    )


if __name__ == "__main__":
    main()
