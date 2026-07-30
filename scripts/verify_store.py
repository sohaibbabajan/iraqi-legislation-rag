#!/usr/bin/env python3
"""
Verify a local store / cache is in a usable state (no API calls).

    python scripts/verify_store.py
    python scripts/verify_store.py --require-store   # fail if lancedb missing
    python scripts/verify_store.py --sample          # also sanity-check sample fixture index

Checks (where applicable):
  - cache/article_index.jsonl exists and has defines rows
  - cache/law_registry.jsonl exists (routes JSON half)
  - lancedb/ has `laws` table; FTS index present if Lance reports indexes
  - lancedb/ has `law_routes` when registry was built into the DB
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common import (  # noqa: E402
    ARTICLE_INDEX_FILE,
    DB_DIR,
    SAMPLE_LAWS,
    TABLE_NAME,
    load_dotenv,
)
from law_registry import REGISTRY_FILE, ROUTES_TABLE  # noqa: E402

load_dotenv()


def _ok(msg: str) -> None:
    print(f"OK  {msg}", flush=True)


def _fail(msg: str, errors: list[str]) -> None:
    print(f"FAIL {msg}", flush=True)
    errors.append(msg)


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


def _list_index_names(table) -> list[str]:
    names: list[str] = []
    try:
        idxs = table.list_indices()
    except Exception:
        try:
            idxs = table.list_indexes()
        except Exception:
            return names
    if idxs is None:
        return names
    for ix in idxs:
        name = getattr(ix, "name", None) or getattr(ix, "index_name", None)
        if name:
            names.append(str(name))
        elif isinstance(ix, dict) and ix.get("name"):
            names.append(str(ix["name"]))
        else:
            names.append(str(ix))
    return names


def check_article_index(errors: list[str], *, path: Path = ARTICLE_INDEX_FILE) -> None:
    if not path.exists():
        _fail(f"article_index missing: {path}", errors)
        return
    n_def = n_men = 0
    n = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            n += 1
            role = row.get("role")
            if role == "defines":
                n_def += 1
                for key in ("law_book_id", "article_label", "char_start", "char_end"):
                    if key not in row:
                        _fail(f"article_index row missing {key}", errors)
                        return
            elif role == "mentions":
                n_men += 1
            else:
                _fail(f"article_index bad role: {role!r}", errors)
                return
    if n_def < 1:
        _fail(f"article_index has no defines rows ({n} total)", errors)
        return
    _ok(f"article_index: {n} rows ({n_def} defines, {n_men} mentions) @ {path}")


def check_registry(errors: list[str]) -> None:
    if not REGISTRY_FILE.exists():
        _fail(f"law_registry missing: {REGISTRY_FILE}", errors)
        return
    n = sum(1 for line in REGISTRY_FILE.open(encoding="utf-8") if line.strip())
    if n < 1:
        _fail("law_registry is empty", errors)
        return
    _ok(f"law_registry: {n} rows @ {REGISTRY_FILE}")


def check_lancedb(errors: list[str], *, require_store: bool) -> None:
    if not DB_DIR.exists():
        msg = f"lancedb dir missing: {DB_DIR}"
        if require_store:
            _fail(msg, errors)
        else:
            print(f"SKIP {msg} (pass --require-store to fail)", flush=True)
        return

    import lancedb

    db = lancedb.connect(str(DB_DIR))
    names = _table_names(db)
    if TABLE_NAME not in names:
        _fail(f"table {TABLE_NAME!r} not in {names}", errors)
        return
    table = db.open_table(TABLE_NAME)
    try:
        nrows = table.count_rows()
    except Exception:
        nrows = len(table.to_arrow())
    if nrows < 1:
        _fail(f"{TABLE_NAME} has 0 rows", errors)
        return
    _ok(f"lancedb.{TABLE_NAME}: {nrows} rows")

    idx_names = _list_index_names(table)
    fts_like = [n for n in idx_names if "fts" in n.lower() or "text" in n.lower()]
    if fts_like:
        _ok(f"FTS-related indexes: {fts_like}")
    elif idx_names:
        # Index list exists but naming varies across LanceDB versions.
        _ok(f"indexes present (may include FTS): {idx_names}")
    else:
        # Missing FTS is a soft fail for sample smoke — hybrid falls back to vector.
        print(
            "WARN no indexes listed on laws table "
            "(run: python ingest.py --build-fts)",
            flush=True,
        )

    if ROUTES_TABLE in names:
        routes = db.open_table(ROUTES_TABLE)
        try:
            rn = routes.count_rows()
        except Exception:
            rn = len(routes.to_arrow())
        if rn < 1:
            _fail(f"{ROUTES_TABLE} has 0 rows", errors)
        else:
            _ok(f"lancedb.{ROUTES_TABLE}: {rn} rows")
    else:
        print(
            f"SKIP {ROUTES_TABLE} not in DB "
            "(run build_law_registry.py if you need routes)",
            flush=True,
        )


def check_sample_index_consistency(errors: list[str]) -> None:
    """Rebuild from sample fixture into memory and compare key defines."""
    if not SAMPLE_LAWS.exists():
        _fail(f"sample fixture missing: {SAMPLE_LAWS}", errors)
        return
    from article_index import build_article_index, lookup_defines

    rows = [r.to_json() for r in build_article_index(SAMPLE_LAWS)]
    theft = lookup_defines(rows, law_book_id=90001, article_label="438")
    if not theft:
        _fail("sample parse: expected defines 438 for lawBookID 90001", errors)
        return
    amend_mentions = [
        r for r in rows
        if r.get("role") == "mentions"
        and r.get("law_book_id") == 90011
        and r.get("article_label") == "438"
    ]
    if not amend_mentions:
        _fail("sample parse: expected mentions 438 in amendment 90011", errors)
        return
    _ok("sample_laws defines/mentions sanity (438 defines vs amendment mentions)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify LanceDB + article index presence.")
    ap.add_argument(
        "--require-store",
        action="store_true",
        help="fail if lancedb/ is missing",
    )
    ap.add_argument(
        "--sample",
        action="store_true",
        help="also re-parse sample_laws.jsonl for defines/mentions checks",
    )
    ap.add_argument(
        "--skip-registry",
        action="store_true",
        help="do not require cache/law_registry.jsonl",
    )
    args = ap.parse_args()

    errors: list[str] = []
    check_article_index(errors)
    if not args.skip_registry:
        check_registry(errors)
    check_lancedb(errors, require_store=args.require_store)
    if args.sample:
        check_sample_index_consistency(errors)

    if errors:
        print(f"\n{len(errors)} check(s) failed.", flush=True)
        sys.exit(1)
    print("\nAll applicable checks passed.", flush=True)


if __name__ == "__main__":
    main()
