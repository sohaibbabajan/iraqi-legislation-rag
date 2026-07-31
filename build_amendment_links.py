#!/usr/bin/env python3
"""
Build cache/amendment_links.jsonl from a laws JSONL (deterministic, $0).

    python build_amendment_links.py
    python build_amendment_links.py --source sources/sample_laws.jsonl
    python build_amendment_links.py --source C:\\iraqi-law-rag\\sources\\laws_master.jsonl
    python scripts/build_amendment_links.py   # same entrypoint

No OpenRouter calls. Safe to re-run anytime. Output is gitignored under cache/.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if _ROOT.name == "scripts":
    _ROOT = _ROOT.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from amendment_links import (  # noqa: E402
    AMENDMENT_LINKS_FILE,
    build_amendment_links,
    resolve_amendment_source,
    save_amendment_links,
    summarize_build,
)
from common import load_dotenv  # noqa: E402

load_dotenv()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Deterministic معدل ← تعديل amendment link map ($0)."
    )
    ap.add_argument(
        "--source",
        default=None,
        help="JSONL corpus (default: local/sibling master, else sample)",
    )
    ap.add_argument(
        "--out",
        default=str(AMENDMENT_LINKS_FILE),
        help="Output JSONL (default: cache/amendment_links.jsonl)",
    )
    ap.add_argument("--limit", type=int, default=0, help="First N records only")
    ap.add_argument(
        "--write-sample-fixture",
        action="store_true",
        help="Also write docs/examples/sample_amendment_links.jsonl from sample",
    )
    args = ap.parse_args()

    source = resolve_amendment_source(
        Path(args.source) if args.source else None
    )
    if not source.exists():
        sys.exit(f"Source not found: {source}")

    rows = build_amendment_links(source, limit=args.limit)
    out = save_amendment_links(rows, Path(args.out))
    stats = summarize_build(rows)
    print(
        f"Wrote {stats['bases_with_links']} bases / "
        f"{stats['amendment_edges']} amendment edges "
        f"(+{stats['replace_edges']} replace) -> {out}",
        flush=True,
    )
    print(f"  methods: {stats['methods']}", flush=True)
    for label, info in stats["majors"].items():
        print(
            f"  major {label}: id={info['base_law_book_id']} "
            f"amended_by={info['amended_by']}",
            flush=True,
        )

    if args.write_sample_fixture:
        from common import SAMPLE_LAWS
        fixture_rows = build_amendment_links(SAMPLE_LAWS)
        fixture = _ROOT / "docs" / "examples" / "sample_amendment_links.jsonl"
        fixture.parent.mkdir(parents=True, exist_ok=True)
        save_amendment_links(fixture_rows, fixture)
        print(f"Sample fixture -> {fixture} ({len(fixture_rows)} bases)", flush=True)


if __name__ == "__main__":
    main()
