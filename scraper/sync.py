"""Incremental sync: discover new catalog rows → fetch → merge into master."""

from __future__ import annotations

import sys
import time
from typing import Any

from scraper.config import ScraperConfig
from scraper.identity import ensure_extension_fields, record_identity
from scraper.merge import (
    MergeStats,
    append_jsonl_records,
    load_jsonl_by_identity,
    merge_records,
    mirror_master_file,
    write_jsonl_atomic,
)
from scraper.normalize import normalize_record
from scraper.parse import CloudflareChallengeError, full_text_from_detail_html
from scraper.scrape import (
    _HttpTransport,
    fetch_full_text,
    open_transport,
)
from scraper.state import ScrapeState, append_changelog


def _catalog_filters(config: ScraperConfig) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if config.from_date:
        filters["from_date"] = config.from_date
    if config.to_date:
        filters["to_date"] = config.to_date
    return filters


def iter_catalog_pages_filtered(
    transport: Any,
    config: ScraperConfig,
    *,
    start_page: int = 1,
):
    """Like iter_catalog_pages but passes optional from/to date filters."""
    from scraper.http_client import search_legislations

    page = max(1, int(start_page))
    filters = _catalog_filters(config)
    while True:
        if isinstance(transport, _HttpTransport):
            data = search_legislations(
                transport.client,
                config,
                page_number=page,
                page_size=config.page_size,
                **filters,
            )
        else:
            # Playwright path: build URL with filters
            import urllib.parse

            params = {
                "legType": "",
                "legNumber": "",
                "fromDate": filters.get("from_date") or "",
                "toDate": filters.get("to_date") or "",
                "legistlator": "",
                "legTitle": "",
                "legValidity": "",
                "tacksNewsNum": "",
                "codeIndexId": "",
                "structuredCodeIndex": "",
                "searchTerm": "",
                "usesSynonym": "false",
                "legId": "",
                "legFlag": "",
                "pageNumber": str(page),
                "pageSize": str(config.page_size),
            }
            url = config.search_url() + "?" + urllib.parse.urlencode(params)
            data = transport.get_json(url)
            if not isinstance(data, dict):
                raise RuntimeError(f"Unexpected search payload: {type(data)}")
        yield page, data
        codes = data.get("lawCodes") or []
        total = int(data.get("totalCount") or 0)
        page_size = int(data.get("pageSize") or config.page_size)
        if not codes:
            break
        if page * page_size >= total:
            break
        page += 1


def _update_watermarks(state: ScrapeState, rec: dict[str, Any]) -> None:
    lid = rec.get("lawBookID")
    if lid is not None:
        try:
            lid_i = int(lid)
            if state.watermark_lawBookID is None or lid_i > state.watermark_lawBookID:
                state.watermark_lawBookID = lid_i
        except (TypeError, ValueError):
            pass
    date_iso = (rec.get("date_iso") or "").strip()
    if date_iso and (
        not state.watermark_date_iso or date_iso > state.watermark_date_iso
    ):
        state.watermark_date_iso = date_iso


def run_sync(config: ScraperConfig) -> int:
    """
    Incremental sync aimed at newly published instruments.

    Walks catalog from page 1 (observed newest-first), fetches unknown
    ``lawBookID``s, merges into the master JSONL without duplicates, and stops
    after a streak of already-known ids (or ``--limit``).
    """
    config.state_dir.mkdir(parents=True, exist_ok=True)
    state = ScrapeState.load(config.state_path)
    state.mode = config.mode

    master_index = load_jsonl_by_identity(config.output)
    known_ids = set()
    for rec in master_index.values():
        lid = rec.get("lawBookID")
        if lid is not None:
            try:
                known_ids.add(int(lid))
            except (TypeError, ValueError):
                pass

    stop_after_known = config.sync_stop_after_known
    if stop_after_known is None:
        stop_after_known = max(config.page_size * 2, 20)

    print(
        f"[sync] mode={config.mode} output={config.output} "
        f"known_ids={len(known_ids)} stop_after_known={stop_after_known} "
        f"from_date={config.from_date!r} to_date={config.to_date!r} "
        f"watermark_id={state.watermark_lawBookID}",
        file=sys.stderr,
    )

    pending: list[dict[str, Any]] = []
    known_streak = 0
    pages_seen = 0
    discovered = 0

    try:
        with open_transport(config) as transport:
            for page, data in iter_catalog_pages_filtered(transport, config, start_page=1):
                pages_seen += 1
                state.total_count = int(data.get("totalCount") or 0)
                state.catalog_total_count = state.total_count
                codes = data.get("lawCodes") or []
                print(
                    f"[sync] page {page}: {len(codes)} rows "
                    f"(catalog total≈{state.total_count})",
                    file=sys.stderr,
                )
                if not codes:
                    break

                for item in codes:
                    if config.limit is not None and discovered >= config.limit:
                        break
                    try:
                        law_id = int(item["lawBookID"])
                    except (KeyError, TypeError, ValueError):
                        continue

                    if law_id in known_ids:
                        known_streak += 1
                        continue

                    known_streak = 0
                    try:
                        full_text = ""
                        if not config.metadata_only:
                            full_text = fetch_full_text(transport, config, law_id)
                        rec = ensure_extension_fields(
                            normalize_record(item, full_text=full_text, config=config)
                        )
                        pending.append(rec)
                        known_ids.add(law_id)
                        discovered += 1
                        state.mark_ok(law_id)
                        _update_watermarks(state, rec)
                        append_changelog(
                            config.changelog_path,
                            {
                                "event": "sync_new",
                                "lawBookID": law_id,
                                "lawTitle": rec.get("lawTitle"),
                                "date_iso": rec.get("date_iso"),
                            },
                        )
                        print(
                            f"[sync] new id={law_id} title={rec.get('lawTitle')!r}",
                            file=sys.stderr,
                        )
                    except CloudflareChallengeError as exc:
                        state.mark_fail(law_id)
                        state.notes = str(exc)
                        state.save(config.state_path)
                        print(f"[sync] BLOCKED by Cloudflare: {exc}", file=sys.stderr)
                        print(
                            "[sync] Tip: re-run with --mode playwright, "
                            "or ship a corpus release instead.",
                            file=sys.stderr,
                        )
                        return 2
                    except Exception as exc:  # noqa: BLE001
                        state.mark_fail(law_id)
                        append_changelog(
                            config.changelog_path,
                            {
                                "event": "error",
                                "lawBookID": law_id,
                                "error": f"{type(exc).__name__}: {exc}",
                            },
                        )
                        print(
                            f"[sync] error id={law_id}: {type(exc).__name__}: {exc}",
                            file=sys.stderr,
                        )

                state.save(config.state_path)

                if config.limit is not None and discovered >= config.limit:
                    print(f"[sync] limit={config.limit} reached", file=sys.stderr)
                    break
                if known_streak >= stop_after_known:
                    print(
                        f"[sync] stop: {known_streak} consecutive known ids "
                        f"(threshold={stop_after_known})",
                        file=sys.stderr,
                    )
                    break
    except CloudflareChallengeError as exc:
        state.notes = str(exc)
        state.save(config.state_path)
        print(f"[sync] BLOCKED by Cloudflare: {exc}", file=sys.stderr)
        return 2

    stats = MergeStats()
    if pending:
        master_index, changed, stats = merge_records(master_index, pending)
        records = list(master_index.values())

        def _sort_key(r: dict[str, Any]) -> tuple[int, str]:
            lid = r.get("lawBookID")
            try:
                return (0, f"{int(lid):08d}") if lid is not None else (1, record_identity(r))
            except (TypeError, ValueError):
                return (1, record_identity(r))

        records.sort(key=_sort_key)
        write_jsonl_atomic(config.output, records)
        if config.delta_path is not None and changed:
            append_jsonl_records(config.delta_path, changed)
            stats.delta_written = len(changed)

    # Always attempt mirror after a successful sync pass (even if no new
    # rows) so Masadir / a second checkout stays byte-aligned with -o.
    mirrored = mirror_master_file(config.output, config.mirror_output)
    if mirrored is not None:
        print(f"[sync] mirrored master → {mirrored}", file=sys.stderr)

    state.last_sync_at = time.time()
    state.save(config.state_path)
    print(
        f"[sync] done. pages={pages_seen} discovered={discovered} "
        f"merge={stats.as_dict()} failed={len(state.failed_ids)}",
        file=sys.stderr,
    )
    return 0
