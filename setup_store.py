#!/usr/bin/env python3
"""
One-command store setup: ingest → FTS → law routes.

    python setup_store.py                  # sample_laws.jsonl (or laws_master)
    python setup_store.py --source sources/laws_master.jsonl
    python setup_store.py --limit 50       # cheap smoke
    python setup_store.py --skip-routes    # ingest + FTS only

Requires OPENROUTER_API_KEY (env or .env). Does not run the answer LLM.
"""

from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path

from common import ROOT, default_corpus_path, load_dotenv

load_dotenv()


def _run(argv: list[str]) -> None:
    print("+", " ".join(argv), flush=True)
    r = subprocess.run([sys.executable, *argv], cwd=ROOT)
    if r.returncode != 0:
        sys.exit(r.returncode)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Embed corpus, build FTS, then law routing index."
    )
    ap.add_argument(
        "--source",
        default=str(default_corpus_path()),
        help="JSONL corpus path",
    )
    ap.add_argument("--limit", type=int, default=0,
                    help="ingest only the first N records (smoke)")
    ap.add_argument("--priority", action="store_true",
                    help="pass --priority to ingest")
    ap.add_argument("--skip-routes", action="store_true",
                    help="skip build_law_registry.py")
    ap.add_argument("--skip-ingest", action="store_true",
                    help="only FTS + routes (store already exists)")
    args = ap.parse_args()

    source = Path(args.source)
    if not source.exists() and not args.skip_ingest:
        sys.exit(f"Source not found: {source}")

    if not args.skip_ingest:
        ingest_cmd = ["ingest.py", "--api", "--source", str(source)]
        if args.limit:
            ingest_cmd += ["--limit", str(args.limit)]
        if args.priority:
            ingest_cmd.append("--priority")
        _run(ingest_cmd)

    _run(["ingest.py", "--build-fts"])

    if not args.skip_routes:
        route_cmd = ["build_law_registry.py", "--rebuild-json",
                     "--source", str(source)]
        if args.limit:
            route_cmd += ["--limit", str(args.limit)]
        _run(route_cmd)

    print("Store ready. Try:  python ask.py \"ما هي عقوبة السرقة؟\" --no-verify")


if __name__ == "__main__":
    main()
