"""HTTP transport with retries, rate limit, and CF challenge detection."""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from scraper.config import ScraperConfig
from scraper.parse import CloudflareChallengeError, looks_like_cloudflare_challenge


class RateLimiter:
    def __init__(self, delay_s: float) -> None:
        self.delay_s = max(0.0, float(delay_s))
        self._last = 0.0

    def wait(self) -> None:
        if self.delay_s <= 0:
            return
        now = time.monotonic()
        gap = self._last + self.delay_s - now
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()


class HttpClient:
    def __init__(self, config: ScraperConfig) -> None:
        self.config = config
        self.limiter = RateLimiter(config.request_delay_s)
        self._ctx = ssl.create_default_context()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self._ctx)
        )

    def _headers(self, accept: str) -> dict[str, str]:
        h = {
            "User-Agent": self.config.user_agent,
            "Accept": accept,
            "Accept-Language": "ar,en;q=0.8",
            "Referer": f"{self.config.base_url.rstrip('/')}/legislations/search-legislation",
        }
        h.update(self.config.extra_headers)
        return h

    def get_text(self, url: str, *, accept: str = "text/html,*/*") -> str:
        return self._request(url, accept=accept)

    def get_json(self, url: str) -> Any:
        body = self._request(url, accept="application/json, text/javascript, */*;q=0.01")
        if looks_like_cloudflare_challenge(body):
            raise CloudflareChallengeError(
                "Cloudflare challenge on JSON endpoint. "
                "Switch to --mode playwright or use a corpus release."
            )
        return json.loads(body)

    def _request(self, url: str, *, accept: str) -> str:
        last_err: Exception | None = None
        for attempt in range(1, self.config.max_retries + 1):
            self.limiter.wait()
            req = urllib.request.Request(url, headers=self._headers(accept))
            try:
                with self._opener.open(req, timeout=self.config.timeout_s) as resp:
                    raw = resp.read()
                    text = raw.decode("utf-8", errors="replace")
                    if looks_like_cloudflare_challenge(text):
                        raise CloudflareChallengeError(
                            f"Cloudflare challenge for {url}"
                        )
                    return text
            except CloudflareChallengeError:
                raise
            except urllib.error.HTTPError as exc:
                last_err = exc
                body = exc.read().decode("utf-8", errors="replace")
                if looks_like_cloudflare_challenge(body):
                    raise CloudflareChallengeError(
                        f"Cloudflare challenge (HTTP {exc.code}) for {url}"
                    ) from exc
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.config.max_retries:
                    time.sleep(min(30.0, 2.0**attempt))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_err = exc
                if attempt < self.config.max_retries:
                    time.sleep(min(30.0, 2.0**attempt))
                    continue
                raise
        assert last_err is not None
        raise last_err


def search_legislations(
    client: HttpClient,
    config: ScraperConfig,
    *,
    page_number: int,
    page_size: int | None = None,
    **filters: Any,
) -> dict[str, Any]:
    """GET /Legislations/SearchLegislations (same params as the site JS)."""
    params = {
        "legType": filters.get("leg_type"),
        "legNumber": filters.get("leg_number"),
        "fromDate": filters.get("from_date"),
        "toDate": filters.get("to_date"),
        "legistlator": filters.get("legislator"),
        "legTitle": filters.get("leg_title"),
        "legValidity": filters.get("leg_validity"),
        "tacksNewsNum": filters.get("tacks_news_num"),
        "codeIndexId": filters.get("code_index_id"),
        "structuredCodeIndex": filters.get("structured_code_index"),
        "searchTerm": filters.get("search_term"),
        "usesSynonym": filters.get("uses_synonym", False),
        "legId": filters.get("leg_id"),
        "legFlag": filters.get("leg_flag"),
        "pageNumber": int(page_number),
        "pageSize": int(page_size or config.page_size),
    }
    # Drop Nones so the server gets empty defaults like the browser.
    clean = {k: ("" if v is None else v) for k, v in params.items()}
    url = config.search_url() + "?" + urllib.parse.urlencode(clean)
    data = client.get_json(url)
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected search payload type: {type(data)}")
    return data


Fetcher = Callable[[str], str]
