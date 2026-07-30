"""
ingest.py — build (or extend) the local vector store from a source .jsonl.

OpenRouter-first (recommended):

    python ingest.py --api                    # sample_laws or laws_master
    python ingest.py --api --source sources/laws_master.jsonl
    python ingest.py --api --limit 50         # quick smoke
    python setup_store.py                     # ingest → FTS → law routes

It is RESUMABLE and idempotent: chunk_ids already in the store are skipped,
so you can Ctrl-C and re-run, or run it again after dropping in a new source
file to add only the new material.

Local GPU embedding (no --api) is optional and needs torch / sentence-transformers.
"""

from __future__ import annotations
import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import lancedb
import pyarrow as pa
from tqdm import tqdm

from common import (
    SOURCES_DIR, DB_DIR, TABLE_NAME, EMBED_MODEL, EMBED_DIM,
    MAX_SEQ_LEN, USE_FP16_ON_CUDA,
    OPENROUTER_URL, OPENROUTER_EMBED_MODEL, OPENROUTER_BATCH, OPENROUTER_WORKERS,
    iter_records, record_to_chunks, is_priority, extract_article_numbers,
    default_corpus_path, load_dotenv,
)

load_dotenv()

# 16 is tuned for the 4GB GTX 1650 together with MAX_SEQ_LEN=1024 and fp16.
# If nvidia-smi shows you sitting well under ~3GB during a run, raise to 32.
# If you get CUDA out-of-memory, drop to 8. (The old value of 128 assumed
# far more VRAM than this card has and will OOM immediately.)
BATCH = 16


def _schema() -> pa.Schema:
    return pa.schema([
        pa.field("chunk_id", pa.string()),
        pa.field("law_book_id", pa.int64()),
        pa.field("title", pa.string()),
        pa.field("category", pa.string()),
        pa.field("law_index", pa.string()),
        pa.field("status_label", pa.string()),
        pa.field("law_valid", pa.string()),
        pa.field("law_flag", pa.string()),
        pa.field("year", pa.string()),
        pa.field("date_iso", pa.string()),
        pa.field("source_url", pa.string()),
        pa.field("pdf_url", pa.string()),
        pa.field("text", pa.string()),
        pa.field("article_nums", pa.string()),  # ',31,57,' — exact article lookup
        pa.field("vector", pa.list_(pa.float32(), EMBED_DIM)),
    ])


def _existing_chunk_ids(table) -> set[str]:
    """Load chunk_ids already in the store (for resumable ingest)."""
    # Prefer column projection via to_arrow — avoids the optional pylance
    # dependency that table.to_lance() needs. Vectors come along for the ride
    # on older lancedb APIs; fine at this corpus size (~100k rows).
    try:
        return set(table.to_arrow().column("chunk_id").to_pylist())
    except Exception:
        existing: set[str] = set()
        for batch in table.to_lance().to_batches(columns=["chunk_id"]):
            existing.update(batch.column("chunk_id").to_pylist())
        return existing


def _ensure_article_nums_column(table) -> None:
    """Add article_nums to an older store that predates the column."""
    names = {f.name for f in table.schema}
    if "article_nums" not in names:
        table.add_columns({"article_nums": "cast('' as string)"})
        print("Added missing article_nums column.")


def backfill_article_nums(table) -> int:
    """
    Fill article_nums from each chunk's text without re-embedding.
    Recreates the table in place (same vectors) so we don't do 16k+ row updates.
    Drops any FTS index — caller should rebuild with build_fts_index().
    """
    _ensure_article_nums_column(table)
    arrow = table.to_arrow()
    texts = arrow.column("text").to_pylist()
    nums = [extract_article_numbers(t or "") for t in texts]
    # Replace the column
    idx = arrow.schema.get_field_index("article_nums")
    arrow = arrow.set_column(idx, "article_nums", pa.array(nums, type=pa.string()))
    # Drop + recreate keeps vectors; merge_insert with partial cols would
    # null out fields we don't send.
    db = lancedb.connect(str(DB_DIR))
    db.drop_table(TABLE_NAME)
    db.create_table(TABLE_NAME, arrow)
    filled = sum(1 for n in nums if n)
    print(f"Backfilled article_nums on {len(nums)} chunks "
          f"({filled} had at least one article number).")
    print("Note: table recreate dropped FTS — run --build-fts (or pass both flags).")
    return len(nums)


def build_fts_index(table) -> None:
    """
    Native LanceDB FTS on `text`. Arabic: no English stemming/stopwords —
    those hurt Arabic recall. `simple` tokenizer splits on whitespace/punct.
    """
    from lancedb.index import FTS
    print("Building FTS index on `text` (Arabic-safe tokenizer) ...")
    table.create_index(
        "text",
        replace=True,
        config=FTS(
            language="Arabic",
            stem=False,
            remove_stop_words=False,
            ascii_folding=False,
            lower_case=True,
            base_tokenizer="simple",
        ),
    )
    print("FTS index ready — ask.py hybrid search can use it.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(default_corpus_path()),
                    help="path to a .jsonl corpus file "
                         "(defaults to laws_master.jsonl, else sample_laws.jsonl)")
    ap.add_argument("--limit", type=int, default=0,
                    help="only process the first N records (for a quick test)")
    ap.add_argument("--priority", action="store_true",
                    help="only ingest the major codes + recent laws first "
                         "(see PRIORITY_TITLE_PATTERNS in common.py). Run again "
                         "without this flag later to add everything else.")
    ap.add_argument("--dry-run", action="store_true",
                    help="chunk and count only — no model load, no embedding, "
                         "no writes. Use this to check --priority selection.")
    ap.add_argument("--api", action="store_true",
                    help="embed via OpenRouter instead of the local GPU. "
                         "No torch/CUDA needed. Costs ~$0.01/M tokens — the "
                         "full corpus is under $1. Requires OPENROUTER_API_KEY. "
                         "Don't mix with an existing local-GPU store; wipe "
                         "lancedb/ first if you've already run without --api.")
    ap.add_argument("--backfill-articles", action="store_true",
                    help="extract article numbers into the article_nums column "
                         "for an existing store (no re-embedding). Then exit.")
    ap.add_argument("--build-fts", action="store_true",
                    help="create/replace the full-text search index on `text` "
                         "(needed for hybrid BM25+vector retrieval). Then exit.")
    args = ap.parse_args()

    source_path = Path(args.source)
    if not source_path.exists() and not (args.backfill_articles or args.build_fts):
        sys.exit(f"Source file not found: {source_path}")

    # --- metadata / index maintenance (no embedding) ---------------------
    if args.backfill_articles or args.build_fts:
        if not DB_DIR.exists():
            sys.exit(f"No vector store at {DB_DIR}. Run ingest first.")
        db = lancedb.connect(str(DB_DIR))
        try:
            listed = db.list_tables()
            names = set(getattr(listed, "tables", None) or listed)
        except Exception:
            names = set(db.table_names())
        if TABLE_NAME not in names:
            sys.exit(f"No table '{TABLE_NAME}' in {DB_DIR}.")
        table = db.open_table(TABLE_NAME)
        if args.backfill_articles:
            backfill_article_nums(table)
            table = db.open_table(TABLE_NAME)  # reopen after recreate
            # Recreate always drops indexes — build FTS unless caller only
            # wanted the column and will rebuild later themselves.
            if not args.build_fts:
                print("Auto-rebuilding FTS after backfill ...")
                build_fts_index(table)
        if args.build_fts:
            build_fts_index(table)
        print(f"Store ready. chunks={table.count_rows()}")
        return

    # --- dry run: count what WOULD be ingested, then exit -----------------
    if args.dry_run:
        n_records = n_selected = n_chunks = 0
        titles = []
        for rec in iter_records(source_path):
            n_records += 1
            if args.limit and n_records > args.limit:
                break
            if args.priority and not is_priority(rec):
                continue
            n_selected += 1
            c = len(record_to_chunks(rec))
            n_chunks += c
            if c and len(titles) < 25:
                titles.append(f"  - {(rec.get('lawTitle') or '')[:70]} "
                              f"({rec.get('lawYear','')}) -> {c} chunks")
        print(f"Records read:     {n_records}")
        print(f"Records selected: {n_selected}")
        print(f"Chunks to embed:  {n_chunks}")
        print("\nSample of selected laws:")
        print("\n".join(titles))
        return

    if args.api:
        # --- OpenRouter path: no torch, no CUDA, no local model at all ---
        import time
        import requests

        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            sys.exit(
                "Set OPENROUTER_API_KEY first (env or .env).\n"
                '  PowerShell:  $env:OPENROUTER_API_KEY = "sk-or-v1-..."\n'
                '  bash:        export OPENROUTER_API_KEY="sk-or-v1-..."'
            )
        print(f"Embedding via OpenRouter ({OPENROUTER_EMBED_MODEL}) — no GPU needed.")
        print(f"batch={OPENROUTER_BATCH}  workers={OPENROUTER_WORKERS}")

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
                    # Rate limit — wait and retry the SAME batch. Splitting
                    # here multiplies requests and makes 429s worse.
                    wait = min(60, 2 ** attempt)
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait = max(wait, int(float(retry_after)))
                        except ValueError:
                            pass
                    if attempt >= 8:
                        resp.raise_for_status()
                    print(f"\n  429 rate limit — sleeping {wait}s "
                          f"(attempt {attempt + 1})")
                    time.sleep(wait)
                    return call_api(texts, attempt + 1)
                resp.raise_for_status()
                data = resp.json()
                # sort by index — batch responses aren't guaranteed in order
                items = sorted(data["data"], key=lambda d: d["index"])
                return [it["embedding"] for it in items]
            except requests.exceptions.HTTPError:
                raise
            except Exception:
                if attempt >= 3:
                    raise
                if len(texts) > 1:
                    # split and retry — narrows down a bad item and halves
                    # the blast radius of a transient failure
                    mid = len(texts) // 2
                    return call_api(texts[:mid], attempt + 1) + \
                           call_api(texts[mid:], attempt + 1)
                raise

        def encode_batch_api(batches: list[list]):
            """Run several API batches concurrently, return in-order results."""
            with ThreadPoolExecutor(max_workers=OPENROUTER_WORKERS) as ex:
                results = list(ex.map(
                    lambda b: call_api([c.text for c in b]), batches
                ))
            return results

        model = None
        device = "api"
    else:
        # --- load embedding model (GPU if available) ---
        from sentence_transformers import SentenceTransformer
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cpu":
            print("WARNING: CUDA not found — embedding on CPU will be slow.")
            print("         On the GTX 1650 laptop, install the CUDA build of torch (see README).")
            print("         Or use --api to embed via OpenRouter instead (no GPU needed).")
        print(f"Loading embedding model {EMBED_MODEL} on {device} ...")
        model = SentenceTransformer(EMBED_MODEL, device=device)

        # --- fit the model into 4GB of VRAM ---
        # bge-m3 defaults to an 8192-token window; our chunks never exceed ~900
        # tokens, so this truncates nothing and saves a lot of memory.
        model.max_seq_length = MAX_SEQ_LEN
        fp16 = (device == "cuda") and USE_FP16_ON_CUDA
        if fp16:
            model.half()
        print(f"max_seq_length={model.max_seq_length}  fp16={fp16}  batch={BATCH}")
        if device == "cuda":
            print("Watch VRAM in another terminal with:  nvidia-smi -l 2")

    # --- open/create the store ---
    db = lancedb.connect(str(DB_DIR))
    if TABLE_NAME in db.table_names():
        table = db.open_table(TABLE_NAME)
        _ensure_article_nums_column(table)
        table = db.open_table(TABLE_NAME)
        existing = _existing_chunk_ids(table)
        print(f"Existing store has {len(existing)} chunks; will skip those.")
    else:
        table = db.create_table(TABLE_NAME, schema=_schema())
        existing = set()
        print("Created new store.")

    # --- build chunk list (skipping already-ingested) ---
    print("Reading source and chunking ...")
    pending: list = []
    n_records = 0
    n_skipped_priority = 0
    for rec in iter_records(source_path):
        n_records += 1
        if args.limit and n_records > args.limit:
            break
        if args.priority and not is_priority(rec):
            n_skipped_priority += 1
            continue
        for ch in record_to_chunks(rec):
            if ch.chunk_id in existing:
                continue
            pending.append(ch)
    if args.priority:
        print(f"--priority: skipped {n_skipped_priority} non-priority records "
              f"(re-run without --priority later to add them).")
    print(f"Records read: {n_records} | new chunks to embed: {len(pending)}")
    if not pending:
        print("Nothing new to ingest. Done.")
        return

    # --- embed and write ---
    def write_rows(batch: list, vecs) -> None:
        rows = []
        for c, v in zip(batch, vecs):
            rows.append({
                "chunk_id": c.chunk_id,
                "law_book_id": int(c.law_book_id) if c.law_book_id is not None else -1,
                "title": c.title,
                "category": c.category,
                "law_index": c.law_index,
                "status_label": c.status_label,
                "law_valid": c.law_valid,
                "law_flag": c.law_flag,
                "year": c.year,
                "date_iso": c.date_iso,
                "source_url": c.source_url,
                "pdf_url": c.pdf_url,
                "text": c.text,
                "article_nums": c.article_nums or "",
                # local model.half() returns float16; API returns plain
                # python floats. Either way, cast to float32 for the schema.
                "vector": [float(x) for x in v],
            })
        table.add(rows)

    if args.api:
        # Chop pending into OPENROUTER_BATCH-sized groups, submit several
        # groups concurrently, write each group's results as they arrive.
        groups = [pending[i:i + OPENROUTER_BATCH]
                  for i in range(0, len(pending), OPENROUTER_BATCH)]
        with tqdm(total=len(pending), unit="chunk") as bar:
            with ThreadPoolExecutor(max_workers=OPENROUTER_WORKERS) as ex:
                futures = {ex.submit(call_api, [c.text for c in g]): g for g in groups}
                from concurrent.futures import as_completed
                for fut in as_completed(futures):
                    g = futures[fut]
                    vecs = fut.result()
                    write_rows(g, vecs)
                    bar.update(len(g))
    else:
        def encode_safe(texts: list[str]):
            """
            Encode, and if CUDA runs out of memory, split the batch and retry
            instead of killing a run that's 6 hours in at 3am.
            """
            try:
                return model.encode(
                    texts, normalize_embeddings=True, show_progress_bar=False,
                    batch_size=len(texts),
                )
            except (RuntimeError, torch.cuda.OutOfMemoryError) as e:
                if "out of memory" not in str(e).lower() or len(texts) == 1:
                    raise
                torch.cuda.empty_cache()
                mid = len(texts) // 2
                print(f"\n  OOM at batch of {len(texts)} — retrying in halves. "
                      f"Consider lowering BATCH in ingest.py.")
                import numpy as np
                return np.concatenate([encode_safe(texts[:mid]),
                                       encode_safe(texts[mid:])])

        with tqdm(total=len(pending), unit="chunk") as bar:
            for i in range(0, len(pending), BATCH):
                batch = pending[i:i + BATCH]
                texts = [c.text for c in batch]
                vecs = encode_safe(texts)
                write_rows(batch, vecs)
                bar.update(len(batch))

    print(f"\nDone. Store now at: {DB_DIR}")
    print(f"Total chunks in table: {table.count_rows()}")
    print("If you added chunks, refresh FTS:  python ingest.py --build-fts")


if __name__ == "__main__":
    main()
