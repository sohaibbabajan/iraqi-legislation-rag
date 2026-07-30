"""
Embed article_index *defines* rows into LanceDB table `articles`.

Resumable OpenRouter bge-m3 path (same batching as ingest.py). Prefer this
granularity at query time over fat chunks when the article index exists.

    # After article_index is built (setup_store does this):
    python embed_articles.py --api
    python embed_articles.py --api --limit 50          # cheap smoke
    python embed_articles.py --api --source sources/sample_laws.jsonl

Full corpus (when laws_master.jsonl is present):
    python build_article_index.py --source sources/laws_master.jsonl
    python embed_articles.py --api --source sources/laws_master.jsonl

Or symlink/copy from the private Masadir tree:
    # PowerShell
    New-Item -ItemType SymbolicLink -Path sources\\laws_master.jsonl `
      -Target C:\\iraqi-law-rag\\sources\\laws_master.jsonl

Cost note: full ~article defines embed is ~$1 (ARCHITECTURE est.); sample
fixture is a few cents. Do NOT run full-corpus embed in CI.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import lancedb
import pyarrow as pa
from tqdm import tqdm

from article_index import load_article_index, iter_defines
from common import (
    ROOT,
    DB_DIR,
    ARTICLES_TABLE_NAME,
    ARTICLE_INDEX_FILE,
    EMBED_DIM,
    OPENROUTER_URL,
    OPENROUTER_EMBED_MODEL,
    OPENROUTER_BATCH,
    OPENROUTER_WORKERS,
    default_corpus_path,
    iter_records,
    load_dotenv,
)

load_dotenv()
# Also try sibling Masadir .env if local key missing (never commit .env).
if not os.environ.get("OPENROUTER_API_KEY"):
    load_dotenv(Path(r"C:\iraqi-law-rag\.env"))


def article_row_id(law_book_id: int, article_label: str) -> str:
    return f"{int(law_book_id)}:{article_label}"


def _schema() -> pa.Schema:
    return pa.schema([
        pa.field("article_id", pa.string()),
        pa.field("law_book_id", pa.int64()),
        pa.field("article_label", pa.string()),
        pa.field("title", pa.string()),
        pa.field("status_label", pa.string()),
        pa.field("law_flag", pa.string()),
        pa.field("year", pa.string()),
        pa.field("source_url", pa.string()),
        pa.field("text", pa.string()),
        pa.field("role", pa.string()),  # always "defines" for this table
        pa.field("vector", pa.list_(pa.float32(), EMBED_DIM)),
    ])


def _meta_by_law(source: Path, limit: int = 0) -> dict[int, dict]:
    """law_book_id → title/status/year/url from corpus JSONL."""
    out: dict[int, dict] = {}
    n = 0
    for rec in iter_records(source):
        n += 1
        if limit and n > limit:
            break
        try:
            lid = int(rec.get("lawBookID"))
        except (TypeError, ValueError):
            continue
        out[lid] = {
            "title": rec.get("lawTitle") or "",
            "status_label": rec.get("status_label") or "",
            "law_flag": rec.get("lawFlag") or "",
            "year": str(rec.get("lawYear") or ""),
            "source_url": rec.get("source_url") or "",
        }
    return out


def _existing_ids(table) -> set[str]:
    try:
        return set(table.to_arrow().column("article_id").to_pylist())
    except Exception:
        existing: set[str] = set()
        for batch in table.to_lance().to_batches(columns=["article_id"]):
            existing.update(batch.column("article_id").to_pylist())
        return existing


def _table_names(db) -> list[str]:
    try:
        listed = db.list_tables()
        if hasattr(listed, "tables") and listed.tables is not None:
            return list(listed.tables)
        if isinstance(listed, (list, tuple)):
            return list(listed)
    except Exception:
        pass
    try:
        return list(db.table_names())
    except Exception:
        return []


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Embed article_index defines into LanceDB `articles`."
    )
    ap.add_argument(
        "--source",
        default=str(default_corpus_path()),
        help="JSONL for law metadata (title/status); defaults to corpus path",
    )
    ap.add_argument(
        "--index",
        default=str(ARTICLE_INDEX_FILE),
        help="article_index.jsonl path",
    )
    ap.add_argument("--limit", type=int, default=0,
                    help="embed at most N defines (smoke)")
    ap.add_argument("--api", action="store_true", default=True,
                    help="OpenRouter embed (default; only supported path)")
    ap.add_argument("--dry-run", action="store_true",
                    help="count pending defines, no API calls")
    args = ap.parse_args()

    index_path = Path(args.index)
    if not index_path.exists():
        sys.exit(
            f"article_index missing: {index_path}\n"
            "  Run: python build_article_index.py"
        )

    source = Path(args.source)
    if not source.exists():
        sys.exit(f"Source not found: {source}")

    defines = list(iter_defines(load_article_index(index_path)))
    if args.limit:
        defines = defines[: args.limit]
    meta = _meta_by_law(source)

    pending_rows: list[dict] = []
    for d in defines:
        try:
            lid = int(d["law_book_id"])
        except (KeyError, TypeError, ValueError):
            continue
        label = str(d.get("article_label") or "")
        text = (d.get("text") or "").strip()
        if not label or not text:
            continue
        m = meta.get(lid, {})
        pending_rows.append({
            "article_id": article_row_id(lid, label),
            "law_book_id": lid,
            "article_label": label,
            "title": m.get("title") or "",
            "status_label": m.get("status_label") or "",
            "law_flag": m.get("law_flag") or "",
            "year": m.get("year") or "",
            "source_url": m.get("source_url") or "",
            "text": text,
            "role": "defines",
        })

    print(f"Defines in index (selected): {len(pending_rows)}")

    db = lancedb.connect(str(DB_DIR))
    names = _table_names(db)
    if ARTICLES_TABLE_NAME in names:
        table = db.open_table(ARTICLES_TABLE_NAME)
        existing = _existing_ids(table)
        print(f"Existing articles table: {len(existing)} rows; will skip those.")
    else:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        table = db.create_table(ARTICLES_TABLE_NAME, schema=_schema())
        existing = set()
        print("Created new articles table.")

    pending = [r for r in pending_rows if r["article_id"] not in existing]
    print(f"New articles to embed: {len(pending)}")
    if args.dry_run:
        sample = pending[:5]
        for r in sample:
            print(f"  - {r['article_id']}  {r['title'][:50]}")
        return
    if not pending:
        print("Nothing new to embed. Done.")
        return

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit(
            "Set OPENROUTER_API_KEY (env or .env).\n"
            "  Copy from C:\\iraqi-law-rag\\.env if needed — never commit .env."
        )

    import requests

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })

    def call_api(texts: list[str], attempt: int = 0) -> list[list[float]]:
        try:
            resp = session.post(
                OPENROUTER_URL,
                json={"model": OPENROUTER_EMBED_MODEL, "input": texts},
                timeout=90,
            )
            if resp.status_code == 429:
                wait = min(60, 2 ** attempt)
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = max(wait, int(float(retry_after)))
                    except ValueError:
                        pass
                if attempt >= 8:
                    resp.raise_for_status()
                print(f"\n  429 — sleeping {wait}s (attempt {attempt + 1})")
                time.sleep(wait)
                return call_api(texts, attempt + 1)
            resp.raise_for_status()
            data = resp.json()
            items = sorted(data["data"], key=lambda d: d["index"])
            return [it["embedding"] for it in items]
        except requests.exceptions.HTTPError:
            raise
        except Exception:
            if attempt >= 3:
                raise
            if len(texts) > 1:
                mid = len(texts) // 2
                return call_api(texts[:mid], attempt + 1) + \
                       call_api(texts[mid:], attempt + 1)
            raise

    def write_rows(batch: list[dict], vecs: list[list[float]]) -> None:
        rows = []
        for r, v in zip(batch, vecs):
            rows.append({
                **r,
                "vector": [float(x) for x in v],
            })
        table.add(rows)

    print(f"Embedding via OpenRouter ({OPENROUTER_EMBED_MODEL})")
    print(f"batch={OPENROUTER_BATCH}  workers={OPENROUTER_WORKERS}")
    groups = [
        pending[i:i + OPENROUTER_BATCH]
        for i in range(0, len(pending), OPENROUTER_BATCH)
    ]
    with tqdm(total=len(pending), unit="art") as bar:
        with ThreadPoolExecutor(max_workers=OPENROUTER_WORKERS) as ex:
            futures = {
                ex.submit(call_api, [r["text"] for r in g]): g for g in groups
            }
            for fut in as_completed(futures):
                g = futures[fut]
                vecs = fut.result()
                write_rows(g, vecs)
                bar.update(len(g))

    print(f"\nDone. articles table at {DB_DIR} / {ARTICLES_TABLE_NAME}")
    print(f"Total article rows: {table.count_rows()}")


if __name__ == "__main__":
    main()
