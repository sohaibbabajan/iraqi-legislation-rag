#!/usr/bin/env python3
"""
Build a corpus release manifest (+ optional sidecar checksum file) for a JSONL.

Does NOT upload anywhere — produces artifacts you attach to a GitHub Release
or Hugging Face dataset. Never commits the JSONL itself into git when huge.

Example:
  py -3 scripts/package_corpus_release.py sources/sample_laws.jsonl \\
      --corpus-version 0.1.0-sample --out-dir docs/examples
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def summarize_jsonl(path: Path) -> dict:
    n = 0
    min_id = None
    max_id = None
    with_text = 0
    empty_text = 0
    status_counts: dict[str, int] = {}
    years: set[str] = set()

    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON ({exc})") from exc
            n += 1
            lid = rec.get("lawBookID")
            if lid is not None:
                lid_i = int(lid)
                min_id = lid_i if min_id is None else min(min_id, lid_i)
                max_id = lid_i if max_id is None else max(max_id, lid_i)
            ft = rec.get("full_text") or ""
            if str(ft).strip():
                with_text += 1
            else:
                empty_text += 1
            st = rec.get("status_label") or ""
            status_counts[st] = status_counts.get(st, 0) + 1
            y = rec.get("lawYear")
            if y not in (None, ""):
                years.add(str(y))

    if n == 0:
        raise ValueError(f"{path}: no records found")

    return {
        "record_count": n,
        "records_with_text": with_text,
        "records_empty_text": empty_text,
        "lawBookID_min": min_id,
        "lawBookID_max": max_id,
        "status_label_counts": dict(sorted(status_counts.items())),
        "lawYear_distinct": len(years),
    }


def build_manifest(
    jsonl_path: Path,
    *,
    corpus_version: str,
    scrape_date: str | None,
    source_note: str | None,
    include_checksum_file: bool,
) -> tuple[dict, str]:
    jsonl_path = jsonl_path.resolve()
    digest = sha256_file(jsonl_path)
    summary = summarize_jsonl(jsonl_path)
    scrape = scrape_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    manifest = {
        "schema": "iraqi-legislation-rag.corpus_manifest/v1",
        "corpus_version": corpus_version,
        "scrape_date": scrape,
        "packaged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "file": {
            "name": jsonl_path.name,
            "path_hint": str(jsonl_path.name),
            "size_bytes": jsonl_path.stat().st_size,
            "sha256": digest,
        },
        "counts": summary,
        "source": {
            "site": "https://iraqld.e-sjc-services.iq/",
            "note": source_note
            or (
                "Normalized iraqld catalog snapshot. Not an official gazette. "
                "See DATA_NOTICE.md. Prefer this release over running the scraper."
            ),
        },
        "compatibility": {
            "law_record_schema": "schemas/law_record.schema.json",
            "ingest": "python ingest.py --api --source <this.jsonl>",
        },
    }
    checksum_body = f"{digest}  {jsonl_path.name}\n"
    if not include_checksum_file:
        checksum_body = ""
    return manifest, checksum_body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("jsonl", type=Path, help="Path to laws JSONL")
    ap.add_argument(
        "--corpus-version",
        required=True,
        help="Version string pinned by eval/cache (e.g. 2026-07-31 or 0.1.0)",
    )
    ap.add_argument(
        "--scrape-date",
        default=None,
        help="ISO date the corpus was scraped/exported (default: today UTC)",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Directory for manifest (+ checksum). Default: alongside the JSONL",
    )
    ap.add_argument(
        "--manifest-name",
        default=None,
        help="Manifest filename (default: <stem>.manifest.json)",
    )
    ap.add_argument(
        "--source-note",
        default=None,
        help="Override source.note in the manifest",
    )
    ap.add_argument(
        "--no-checksum-file",
        action="store_true",
        help="Do not write a separate .sha256 file (hash still in manifest)",
    )
    ap.add_argument(
        "--print",
        dest="print_manifest",
        action="store_true",
        help="Print manifest JSON to stdout as well",
    )
    args = ap.parse_args(argv)

    if not args.jsonl.is_file():
        print(f"JSONL not found: {args.jsonl}", file=sys.stderr)
        return 2

    out_dir = args.out_dir or args.jsonl.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.jsonl.stem
    manifest_name = args.manifest_name or f"{stem}.manifest.json"

    try:
        manifest, checksum_body = build_manifest(
            args.jsonl,
            corpus_version=args.corpus_version,
            scrape_date=args.scrape_date,
            source_note=args.source_note,
            include_checksum_file=not args.no_checksum_file,
        )
    except ValueError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    manifest_path = out_dir / manifest_name
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {manifest_path}")

    if not args.no_checksum_file:
        checksum_path = out_dir / f"{stem}.sha256"
        checksum_path.write_text(checksum_body, encoding="utf-8")
        print(f"Wrote {checksum_path}")

    if args.print_manifest:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))

    print(
        f"OK: corpus_version={manifest['corpus_version']} "
        f"records={manifest['counts']['record_count']} "
        f"sha256={manifest['file']['sha256'][:16]}…"
    )
    print(
        "Next: attach the JSONL + manifest (+ .sha256) to a GitHub Release "
        "or Hugging Face dataset. Do not git-commit large corpora."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
