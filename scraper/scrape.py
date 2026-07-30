"""Scrape orchestration: list catalog → fetch details → append JSONL."""

from __future__ import annotations

import sys
from typing import Any, Iterator, Protocol

from scraper.config import ScraperConfig
from scraper.http_client import HttpClient, search_legislations
from scraper.normalize import normalize_record
from scraper.parse import CloudflareChallengeError, full_text_from_detail_html
from scraper.state import (
    ScrapeState,
    append_changelog,
    append_jsonl,
    load_existing_ids,
)


class Transport(Protocol):
    def get_text(self, url: str) -> str: ...
    def get_json(self, url: str) -> Any: ...


def open_transport(config: ScraperConfig):
    if config.mode == "playwright":
        from scraper.playwright_session import PlaywrightSession

        return PlaywrightSession(config)
    return _HttpTransport(config)


class _HttpTransport:
    def __init__(self, config: ScraperConfig) -> None:
        self.config = config
        self.client = HttpClient(config)

    def __enter__(self) -> "_HttpTransport":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def get_text(self, url: str) -> str:
        return self.client.get_text(url)

    def get_json(self, url: str) -> Any:
        return self.client.get_json(url)


def iter_catalog_pages(
    transport: Transport,
    config: ScraperConfig,
    *,
    start_page: int = 1,
) -> Iterator[tuple[int, dict[str, Any]]]:
    page = max(1, int(start_page))
    # search_legislations expects HttpClient; for playwright wrap adapter
    while True:
        if isinstance(transport, _HttpTransport):
            data = search_legislations(
                transport.client, config, page_number=page, page_size=config.page_size
            )
        else:
            from scraper.http_client import search_legislations as _search
            # Build URL the same way via a tiny shim
            data = _search_via_transport(transport, config, page)
        yield page, data
        codes = data.get("lawCodes") or []
        total = int(data.get("totalCount") or 0)
        page_size = int(data.get("pageSize") or config.page_size)
        if not codes:
            break
        if page * page_size >= total:
            break
        page += 1


def _search_via_transport(
    transport: Transport, config: ScraperConfig, page: int
) -> dict[str, Any]:
    import urllib.parse

    params = {
        "legType": "",
        "legNumber": "",
        "fromDate": "",
        "toDate": "",
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
    return data


def fetch_full_text(transport: Transport, config: ScraperConfig, law_id: int) -> str:
    html = transport.get_text(config.detail_url(law_id))
    return full_text_from_detail_html(html)


def run_scrape(config: ScraperConfig) -> int:
    config.state_dir.mkdir(parents=True, exist_ok=True)
    state = ScrapeState.load(config.state_path) if config.resume else ScrapeState()
    state.mode = config.mode
    existing = load_existing_ids(config.output) if config.skip_existing else set()
    # Also treat state.fetched_ids as done for resume across metadata-only runs
    done = set(existing) | set(state.fetched_ids)

    written = 0
    start_page = state.last_page + 1 if (config.resume and state.last_page > 0) else 1

    print(
        f"[scrape] mode={config.mode} output={config.output} "
        f"delay={config.request_delay_s}s page_size={config.page_size} "
        f"existing_ids={len(done)} start_page={start_page}",
        file=sys.stderr,
    )

    try:
        with open_transport(config) as transport:
            for page, data in iter_catalog_pages(
                transport, config, start_page=start_page
            ):
                state.last_page = page
                state.total_count = int(data.get("totalCount") or 0)
                codes = data.get("lawCodes") or []
                print(
                    f"[scrape] page {page}: {len(codes)} rows "
                    f"(catalog total≈{state.total_count})",
                    file=sys.stderr,
                )
                for item in codes:
                    if config.limit is not None and written >= config.limit:
                        state.save(config.state_path)
                        print(f"[scrape] limit={config.limit} reached", file=sys.stderr)
                        return 0
                    try:
                        law_id = int(item["lawBookID"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if law_id in done:
                        continue
                    try:
                        full_text = ""
                        if not config.metadata_only:
                            full_text = fetch_full_text(transport, config, law_id)
                        rec = normalize_record(
                            item, full_text=full_text, config=config
                        )
                        append_jsonl(config.output, [rec])
                        append_changelog(
                            config.changelog_path,
                            {
                                "event": "upsert",
                                "lawBookID": law_id,
                                "lawTitle": rec.get("lawTitle"),
                                "full_text_len": rec.get("full_text_len"),
                            },
                        )
                        state.mark_ok(law_id)
                        done.add(law_id)
                        written += 1
                        if written % 10 == 0:
                            state.save(config.state_path)
                            print(
                                f"[scrape] wrote {written} new "
                                f"(last id={law_id})",
                                file=sys.stderr,
                            )
                    except CloudflareChallengeError as exc:
                        state.mark_fail(law_id)
                        state.notes = str(exc)
                        state.save(config.state_path)
                        print(f"[scrape] BLOCKED by Cloudflare: {exc}", file=sys.stderr)
                        print(
                            "[scrape] Tip: re-run with --mode playwright (attended), "
                            "or package an existing JSONL release instead.",
                            file=sys.stderr,
                        )
                        return 2
                    except Exception as exc:  # noqa: BLE001 — keep going on per-id errors
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
                            f"[scrape] error id={law_id}: {type(exc).__name__}: {exc}",
                            file=sys.stderr,
                        )
                state.save(config.state_path)
    except CloudflareChallengeError as exc:
        state.notes = str(exc)
        state.save(config.state_path)
        print(f"[scrape] BLOCKED by Cloudflare: {exc}", file=sys.stderr)
        return 2

    state.save(config.state_path)
    print(
        f"[scrape] done. newly_written={written} "
        f"output_ids≈{len(done)} failed={len(state.failed_ids)}",
        file=sys.stderr,
    )
    return 0


def probe_connectivity(config: ScraperConfig) -> int:
    """One catalog page + one detail fetch — report CF status honestly."""
    print(f"[probe] base={config.base_url} mode={config.mode}", file=sys.stderr)
    try:
        with open_transport(config) as transport:
            if isinstance(transport, _HttpTransport):
                data = search_legislations(
                    transport.client, config, page_number=1, page_size=1
                )
            else:
                data = _search_via_transport(transport, config, 1)
            total = data.get("totalCount")
            codes = data.get("lawCodes") or []
            print(f"[probe] SearchLegislations OK totalCount={total} sample={len(codes)}")
            if codes:
                lid = int(codes[0]["lawBookID"])
                html = transport.get_text(config.detail_url(lid))
                from scraper.parse import looks_like_cloudflare_challenge

                if looks_like_cloudflare_challenge(html):
                    print("[probe] detail page looks like Cloudflare challenge")
                    return 2
                ft = full_text_from_detail_html(html)
                print(
                    f"[probe] detail OK lawBookID={lid} full_text_len={len(ft)} "
                    f"title={codes[0].get('lawTitle')!r}"
                )
            print(
                "[probe] Cloudflare assessment: HTTP path succeeded from this network "
                "RIGHT NOW. This is NOT a guarantee for cron/unattended runs — "
                "challenge behavior varies by IP/ASN/time. Prefer published releases "
                "for public users; treat scraper as maintainer tooling."
            )
            return 0
    except CloudflareChallengeError as exc:
        print(f"[probe] Cloudflare challenge: {exc}")
        print(
            "[probe] Assessment: unattended HTTP scrape is blocked here. "
            "Use --mode playwright (semi-attended) or ship a snapshot release."
        )
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"[probe] error: {type(exc).__name__}: {exc}")
        return 1
