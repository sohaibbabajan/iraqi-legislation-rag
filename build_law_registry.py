"""
build_law_registry.py — one-time (resumable) law routing index.

Builds cache/law_registry.jsonl and LanceDB table `law_routes` with bge-m3
embeddings of each in-force law's title+aliases. ~38k short strings ≈ cents
on OpenRouter — then query-time routing reuses the question embedding.

    python build_law_registry.py           # build jsonl + embed routes
    python build_law_registry.py --json-only
    python build_law_registry.py --limit 500
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
import requests

from common import (
    DB_DIR, load_dotenv, default_corpus_path,
    OPENROUTER_URL, OPENROUTER_EMBED_MODEL, OPENROUTER_BATCH, OPENROUTER_WORKERS,
)
from law_registry import (
    ROUTES_TABLE, REGISTRY_FILE,
    build_registry_rows, save_registry, load_registry,
)

load_dotenv()


def _log(msg: str) -> None:
    print(msg, flush=True)


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


def _embed_batch(session: requests.Session, texts: list[str]) -> list[list[float]]:
    last_err = None
    for attempt in range(6):
        try:
            r = session.post(
                OPENROUTER_URL,
                json={"model": OPENROUTER_EMBED_MODEL, "input": texts},
                timeout=120,
            )
            if r.status_code == 429:
                time.sleep(min(2 ** attempt, 30))
                continue
            r.raise_for_status()
            data = r.json()["data"]
            data.sort(key=lambda x: x["index"])
            return [d["embedding"] for d in data]
        except Exception as e:
            last_err = e
            time.sleep(min(2 ** attempt, 20))
    raise RuntimeError(f"embed batch failed: {last_err}")


def _existing_route_ids(db) -> set[int]:
    if ROUTES_TABLE not in _table_names(db):
        return set()
    existing: set[int] = set()
    try:
        old = db.open_table(ROUTES_TABLE)
        try:
            for batch in old.to_lance().to_batches(columns=["law_book_id"], batch_size=8192):
                col = batch.column("law_book_id")
                for i in range(len(col)):
                    existing.add(int(col[i].as_py()))
        except Exception:
            # Avoid pandas dependency — Arrow table is enough.
            arrow = old.to_arrow()
            for v in arrow.column("law_book_id").to_pylist():
                existing.add(int(v))
    except Exception as e:
        _log(f"(could not read existing routes: {e}; will recreate)")
        try:
            db.drop_table(ROUTES_TABLE)
        except Exception:
            pass
        return set()
    return existing


def embed_routes(rows: list[dict], *, limit: int | None = None) -> None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("Set OPENROUTER_API_KEY first.")

    if limit:
        rows = rows[:limit]

    db = lancedb.connect(str(DB_DIR))
    _log("Scanning existing law_routes ids …")
    existing_ids = _existing_route_ids(db)
    todo = [r for r in rows if int(r["law_book_id"]) not in existing_ids]
    _log(
        f"Registry laws: {len(rows)}  already embedded: {len(existing_ids)}  "
        f"to embed: {len(todo)}"
    )
    if not todo:
        _log("Nothing to embed.")
        return

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    def work(batch_rows: list[dict]) -> list[dict]:
        # One Session per task — requests.Session is not fully thread-safe.
        session = requests.Session()
        session.headers.update(headers)
        texts = [r["route_text"] for r in batch_rows]
        vecs = _embed_batch(session, texts)
        out = []
        for r, v in zip(batch_rows, vecs):
            out.append({
                "law_book_id": int(r["law_book_id"]),
                "title": r["title"],
                "year": r.get("year") or "",
                "status_label": r.get("status_label") or "",
                "law_flag": r.get("law_flag") or "",
                "aliases_joined": r.get("aliases_joined") or "",
                "route_text": r["route_text"],
                "vector": v,
            })
        return out

    batches = [todo[i:i + OPENROUTER_BATCH] for i in range(0, len(todo), OPENROUTER_BATCH)]
    # Submit in waves so a hung worker can't leave thousands of futures queued.
    wave = max(OPENROUTER_WORKERS * 4, 8)
    done = 0
    pending_write: list[dict] = []
    table = None
    if ROUTES_TABLE in _table_names(db) and existing_ids:
        table = db.open_table(ROUTES_TABLE)

    t0 = time.time()
    for start in range(0, len(batches), wave):
        chunk = batches[start:start + wave]
        with ThreadPoolExecutor(max_workers=OPENROUTER_WORKERS) as ex:
            futs = [ex.submit(work, b) for b in chunk]
            for fut in as_completed(futs):
                part = fut.result()
                pending_write.extend(part)
                done += len(part)
                if done % 512 < OPENROUTER_BATCH or done == len(todo):
                    rate = done / max(time.time() - t0, 0.1)
                    _log(f"  embedded {done}/{len(todo)}  ({rate:.0f}/s)")

        if len(pending_write) >= 1024 or start + wave >= len(batches):
            if table is None:
                if ROUTES_TABLE in _table_names(db):
                    db.drop_table(ROUTES_TABLE)
                table = db.create_table(ROUTES_TABLE, pending_write)
            else:
                table.add(pending_write)
            _log(f"  flushed {len(pending_write)} rows → {ROUTES_TABLE}")
            pending_write = []

    _log(f"Done. {done} new rows in {DB_DIR} / {ROUTES_TABLE} "
         f"({time.time() - t0:.1f}s)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json-only", action="store_true",
                    help="only write law_registry.jsonl, no embeddings")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rebuild-json", action="store_true",
                    help="rebuild jsonl even if it exists")
    ap.add_argument("--source", default=None,
                    help="corpus JSONL (defaults to laws_master or sample_laws)")
    args = ap.parse_args()

    source = Path(args.source) if args.source else default_corpus_path()
    if not source.exists():
        sys.exit(f"Corpus not found: {source}")

    if args.rebuild_json or not REGISTRY_FILE.exists():
        _log(f"Building registry from {source} …")
        rows = build_registry_rows(source=source)
        save_registry(rows)
        _log(f"Wrote {len(rows)} laws → {REGISTRY_FILE}")
    else:
        rows = load_registry()
        _log(f"Loaded {len(rows)} laws from {REGISTRY_FILE}")

    if args.json_only:
        return
    if not DB_DIR.exists():
        sys.exit(f"No vector store at {DB_DIR}. Run ingest first.")
    embed_routes(rows, limit=args.limit)


if __name__ == "__main__":
    main()
