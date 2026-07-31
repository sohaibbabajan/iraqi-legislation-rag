"""CLI entry for the iraqld legislation scraper."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scraper import __version__
from scraper.config import (
    DEFAULT_OUTPUT,
    DEFAULT_REQUEST_DELAY_S,
    DEFAULT_STATE_DIR,
    ScraperConfig,
)
from scraper.merge import merge_jsonl, mirror_master_file, resolve_mirror_path
from scraper.scrape import probe_connectivity, run_scrape
from scraper.state import ScrapeState, load_existing_ids
from scraper.sync import run_sync


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m scraper",
        description=(
            "Fetch Iraqi legislation catalog records from iraqld and write "
            "law_record JSONL. Snapshot releases are the supported path for "
            "most users; this scraper is maintainer / attended tooling. "
            "See docs/CORPUS_SYNC.md for incremental sync + merge."
        ),
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--mode",
            choices=("http", "playwright"),
            default="http",
            help="http=stdlib requests (may hit CF); playwright=semi-attended browser",
        )
        sp.add_argument("--base-url", default=None, help="Override iraqld base URL")
        sp.add_argument(
            "--delay",
            type=float,
            default=DEFAULT_REQUEST_DELAY_S,
            help=f"Seconds between requests (default {DEFAULT_REQUEST_DELAY_S})",
        )
        sp.add_argument(
            "--state-dir",
            type=Path,
            default=DEFAULT_STATE_DIR,
            help="Resume / changelog directory",
        )
        sp.add_argument(
            "--headed",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Playwright: show browser (needed to clear CF). Default: on",
        )
        sp.add_argument(
            "--challenge-wait",
            type=float,
            default=120.0,
            help="Seconds to wait for operator to clear CF in Playwright",
        )

    scrape_p = sub.add_parser("scrape", help="Full / resume catalog walk → JSONL")
    add_common(scrape_p)
    scrape_p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output JSONL (default {DEFAULT_OUTPUT})",
    )
    scrape_p.add_argument("--page-size", type=int, default=50)
    scrape_p.add_argument("--limit", type=int, default=None, help="Stop after N new records")
    scrape_p.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore saved state last_page (still skips existing output IDs)",
    )
    scrape_p.add_argument(
        "--refresh",
        action="store_true",
        help="Re-fetch even if lawBookID already in output (appends — prefer merge)",
    )
    scrape_p.add_argument(
        "--metadata-only",
        action="store_true",
        help="Write catalog fields only (empty full_text) — faster smoke / inventory",
    )

    sync_p = sub.add_parser(
        "sync",
        help="Incremental: discover new laws → fetch → upsert into master (no dups)",
    )
    add_common(sync_p)
    sync_p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Master JSONL to upsert into (default {DEFAULT_OUTPUT})",
    )
    sync_p.add_argument("--page-size", type=int, default=50)
    sync_p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after N newly discovered records (smoke: --limit 5)",
    )
    sync_p.add_argument(
        "--from-date",
        default=None,
        help="Catalog fromDate filter (e.g. 2026-01-01) for date-window sync",
    )
    sync_p.add_argument(
        "--to-date",
        default=None,
        help="Catalog toDate filter",
    )
    sync_p.add_argument(
        "--delta",
        type=Path,
        default=None,
        help="Append only new/updated records to this JSONL (optional small delta)",
    )
    sync_p.add_argument(
        "--stop-after-known",
        type=int,
        default=None,
        help="Stop after this many consecutive already-known ids (default 2× page size)",
    )
    sync_p.add_argument(
        "--metadata-only",
        action="store_true",
        help="Skip detail pages (empty full_text)",
    )
    sync_p.add_argument(
        "--mirror",
        type=Path,
        default=None,
        help=(
            "Also copy the updated master here after sync "
            "(default: IRAQI_RAG_MASTER env if set — e.g. Masadir sources/laws_master.jsonl)"
        ),
    )

    merge_p = sub.add_parser(
        "merge",
        help="Upsert one or more JSONL files into a master without duplicate identities",
    )
    merge_p.add_argument(
        "--into",
        type=Path,
        required=True,
        help="Master JSONL path (created if missing)",
    )
    merge_p.add_argument(
        "incoming",
        nargs="+",
        type=Path,
        help="Incoming JSONL file(s) to merge",
    )
    merge_p.add_argument(
        "--delta",
        type=Path,
        default=None,
        help="Also append changed records to this delta JSONL",
    )
    merge_p.add_argument(
        "--mirror",
        type=Path,
        default=None,
        help=(
            "Also copy the updated master here after merge "
            "(default: IRAQI_RAG_MASTER env if set)"
        ),
    )

    probe_p = sub.add_parser(
        "probe",
        help="One search + one detail; print Cloudflare honesty assessment",
    )
    add_common(probe_p)

    status_p = sub.add_parser("status", help="Show resume state + output id count")
    status_p.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    status_p.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)

    return p


def _config_from_args(args: argparse.Namespace) -> ScraperConfig:
    cfg = ScraperConfig(
        mode=getattr(args, "mode", "http"),
        request_delay_s=float(getattr(args, "delay", DEFAULT_REQUEST_DELAY_S)),
        state_dir=Path(getattr(args, "state_dir", DEFAULT_STATE_DIR)),
        headed=bool(getattr(args, "headed", True)),
        challenge_wait_s=float(getattr(args, "challenge_wait", 120.0)),
    )
    if getattr(args, "base_url", None):
        cfg.base_url = str(args.base_url).rstrip("/")
    if hasattr(args, "output"):
        cfg.output = Path(args.output)
    if hasattr(args, "page_size"):
        cfg.page_size = int(args.page_size)
    if hasattr(args, "limit"):
        cfg.limit = args.limit
    if getattr(args, "no_resume", False):
        cfg.resume = False
    if getattr(args, "refresh", False):
        cfg.skip_existing = False
    if getattr(args, "metadata_only", False):
        cfg.metadata_only = True
    if getattr(args, "from_date", None):
        cfg.from_date = str(args.from_date)
    if getattr(args, "to_date", None):
        cfg.to_date = str(args.to_date)
    if getattr(args, "delta", None):
        cfg.delta_path = Path(args.delta)
    if getattr(args, "stop_after_known", None) is not None:
        cfg.sync_stop_after_known = int(args.stop_after_known)
    if hasattr(args, "mirror"):
        cfg.mirror_output = resolve_mirror_path(
            Path(args.mirror) if args.mirror else None
        )
    return cfg


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "status":
        state = ScrapeState.load(Path(args.state_dir) / "state.json")
        ids = load_existing_ids(Path(args.output))
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "output_record_ids": len(ids),
                    "state_last_page": state.last_page,
                    "state_total_count": state.total_count,
                    "state_fetched": len(state.fetched_ids),
                    "state_failed": len(state.failed_ids),
                    "state_mode": state.mode,
                    "watermark_lawBookID": state.watermark_lawBookID,
                    "watermark_date_iso": state.watermark_date_iso,
                    "catalog_total_count": state.catalog_total_count,
                    "last_sync_at": state.last_sync_at,
                    "state_notes": state.notes,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.cmd == "merge":
        for p in args.incoming:
            if not Path(p).is_file():
                print(f"[merge] missing incoming file: {p}", file=sys.stderr)
                return 2
        into = Path(args.into)
        stats = merge_jsonl(
            into,
            [Path(p) for p in args.incoming],
            delta_path=Path(args.delta) if args.delta else None,
        )
        print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2))
        mirrored = mirror_master_file(
            into, resolve_mirror_path(Path(args.mirror) if args.mirror else None)
        )
        if mirrored is not None:
            print(f"[merge] mirrored master → {mirrored}", file=sys.stderr)
        return 0

    cfg = _config_from_args(args)
    if args.cmd == "probe":
        return probe_connectivity(cfg)
    if args.cmd == "scrape":
        return run_scrape(cfg)
    if args.cmd == "sync":
        return run_sync(cfg)
    parser.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
