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
from scraper.scrape import probe_connectivity, run_scrape
from scraper.state import ScrapeState, load_existing_ids


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m scraper",
        description=(
            "Fetch Iraqi legislation catalog records from iraqld and write "
            "law_record JSONL. Snapshot releases are the supported path for "
            "most users; this scraper is maintainer / attended tooling."
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

    scrape_p = sub.add_parser("scrape", help="List catalog + fetch details → JSONL")
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
        help="Re-fetch even if lawBookID already in output (appends duplicates — avoid)",
    )
    scrape_p.add_argument(
        "--metadata-only",
        action="store_true",
        help="Write catalog fields only (empty full_text) — faster smoke / inventory",
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
                    "state_notes": state.notes,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    cfg = _config_from_args(args)
    if args.cmd == "probe":
        return probe_connectivity(cfg)
    if args.cmd == "scrape":
        return run_scrape(cfg)
    parser.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
