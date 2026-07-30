"""
One-command store setup: ingest → FTS → law routes → article index →
article embeddings → verify.

    python setup_store.py                  # sample_laws.jsonl (or laws_master)
    python setup_store.py --source sources/laws_master.jsonl
    python setup_store.py --limit 50       # cheap smoke
    python setup_store.py --skip-routes    # ingest + FTS only
    python setup_store.py --skip-article-index
    python setup_store.py --skip-article-embed
    python setup_store.py --skip-verify

Requires OPENROUTER_API_KEY (env or .env) for ingest/routes/article embed.
Article index is deterministic ($0, no API). Article embed uses bge-m3.
"""

from __future__ import annotations
import argparse
import os
import subprocess
import sys
from pathlib import Path

from common import ROOT, default_corpus_path, load_dotenv

load_dotenv()
# Sibling Masadir key if local .env missing
if not os.environ.get("OPENROUTER_API_KEY"):
    load_dotenv(Path(r"C:\iraqi-law-rag\.env"))


def _run(argv: list[str]) -> None:
    print("+", " ".join(argv), flush=True)
    r = subprocess.run([sys.executable, *argv], cwd=ROOT)
    if r.returncode != 0:
        sys.exit(r.returncode)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Embed corpus, build FTS, routes, article index + vectors."
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
                    help="only FTS + routes + article index/embed (store exists)")
    # --- article index / embed / verify ---------------------------------
    ap.add_argument("--skip-article-index", action="store_true",
                    help="skip deterministic cache/article_index.jsonl build")
    ap.add_argument("--skip-article-embed", action="store_true",
                    help="skip OpenRouter embed of defines → lancedb/articles")
    ap.add_argument("--skip-verify", action="store_true",
                    help="skip scripts/verify_store.py at the end")
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

    if not args.skip_article_index:
        art_cmd = ["build_article_index.py", "--source", str(source)]
        if args.limit:
            art_cmd += ["--limit", str(args.limit)]
        _run(art_cmd)

    if not args.skip_article_embed and not args.skip_article_index:
        emb_cmd = ["embed_articles.py", "--api", "--source", str(source)]
        if args.limit:
            emb_cmd += ["--limit", str(args.limit)]
        _run(emb_cmd)
    elif args.skip_article_index and not args.skip_article_embed:
        print(
            "SKIP article embed (requires article index; "
            "omit --skip-article-index)",
            flush=True,
        )

    if not args.skip_verify:
        verify_cmd = ["scripts/verify_store.py", "--sample"]
        if args.skip_routes:
            verify_cmd.append("--skip-registry")
        if args.skip_article_embed:
            verify_cmd.append("--skip-articles-table")
        _run(verify_cmd)

    print("Store ready. Try:  python ask.py \"ما هي عقوبة السرقة؟\" --no-verify")


if __name__ == "__main__":
    main()
