"""Semi-attended Playwright session for Cloudflare-challenged scrapes."""

from __future__ import annotations

import sys
import time
from typing import Any

from scraper.config import ScraperConfig
from scraper.parse import looks_like_cloudflare_challenge


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "Playwright is not installed. For attended mode:\n"
            "  pip install playwright\n"
            "  playwright install chromium\n"
        ) from exc
    return sync_playwright


class PlaywrightSession:
    """
    Opens a real Chromium window. If Cloudflare challenges, the operator
    completes it in the browser; cookies then reuse for subsequent fetches.
    """

    def __init__(self, config: ScraperConfig) -> None:
        self.config = config
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    def __enter__(self) -> "PlaywrightSession":
        sync_playwright = _require_playwright()
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=not self.config.headed)
        self._context = self._browser.new_context(
            user_agent=self.config.user_agent,
            locale="ar-IQ",
            extra_http_headers=dict(self.config.extra_headers),
        )
        self._page = self._context.new_page()
        self._page.set_default_timeout(int(self.config.timeout_s * 1000))
        self.warmup()
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    def warmup(self) -> None:
        assert self._page is not None
        home = self.config.base_url.rstrip("/") + "/"
        print(
            f"[playwright] Opening {home}\n"
            "  If a Cloudflare / bot check appears, complete it in the browser window.\n"
            f"  Waiting up to {self.config.challenge_wait_s:.0f}s for a clear page…",
            file=sys.stderr,
        )
        self._page.goto(home, wait_until="domcontentloaded")
        deadline = time.monotonic() + self.config.challenge_wait_s
        while time.monotonic() < deadline:
            html = self._page.content()
            if not looks_like_cloudflare_challenge(html):
                # Soft check: title or search link present
                if "تشريع" in html or "legislation" in html.lower() or "iraqld" in html.lower():
                    print("[playwright] Site content visible; continuing.", file=sys.stderr)
                    return
            time.sleep(2.0)
            try:
                self._page.reload(wait_until="domcontentloaded")
            except Exception:
                pass
        # Do not hard-fail — operator may have cleared mid-wait; proceed and let
        # per-request CF detection raise if still blocked.
        print(
            "[playwright] Challenge wait elapsed — proceeding; requests may still fail.",
            file=sys.stderr,
        )

    def get_text(self, url: str) -> str:
        assert self._page is not None
        time.sleep(self.config.request_delay_s)
        self._page.goto(url, wait_until="domcontentloaded")
        html = self._page.content()
        if looks_like_cloudflare_challenge(html):
            print(
                "[playwright] Challenge detected mid-run. Complete it in the browser; "
                f"waiting up to {self.config.challenge_wait_s:.0f}s…",
                file=sys.stderr,
            )
            deadline = time.monotonic() + self.config.challenge_wait_s
            while time.monotonic() < deadline:
                time.sleep(2.0)
                html = self._page.content()
                if not looks_like_cloudflare_challenge(html):
                    break
        return html

    def get_json(self, url: str) -> Any:
        import json

        text = self.get_text(url)
        # Playwright navigation to JSON endpoints yields a <pre> or raw body.
        # Prefer page.evaluate fetch with cookies from the context.
        assert self._page is not None
        raw = self._page.evaluate(
            """async (url) => {
                const r = await fetch(url, { credentials: 'include' });
                return await r.text();
            }""",
            url,
        )
        if looks_like_cloudflare_challenge(raw):
            from scraper.parse import CloudflareChallengeError

            raise CloudflareChallengeError(f"Cloudflare challenge for {url}")
        return json.loads(raw)
