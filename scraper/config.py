"""Scraper configuration and defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_BASE_URL = "https://iraqld.e-sjc-services.iq"
DEFAULT_OUTPUT = ROOT / "sources" / "laws_master.jsonl"
DEFAULT_STATE_DIR = ROOT / "cache" / "scraper"
DEFAULT_USER_AGENT = (
    "iraqi-legislation-rag-scraper/0.1 "
    "(+https://github.com/sohaibbabajan/iraqi-legislation-rag; research/archival)"
)

# Polite defaults — raise only if you know the site tolerates it.
DEFAULT_REQUEST_DELAY_S = 1.0
DEFAULT_PAGE_SIZE = 50
DEFAULT_MAX_RETRIES = 4
DEFAULT_TIMEOUT_S = 45.0


@dataclass
class ScraperConfig:
    base_url: str = DEFAULT_BASE_URL
    output: Path = DEFAULT_OUTPUT
    state_dir: Path = DEFAULT_STATE_DIR
    mode: str = "http"  # http | playwright
    request_delay_s: float = DEFAULT_REQUEST_DELAY_S
    page_size: int = DEFAULT_PAGE_SIZE
    max_retries: int = DEFAULT_MAX_RETRIES
    timeout_s: float = DEFAULT_TIMEOUT_S
    user_agent: str = DEFAULT_USER_AGENT
    limit: int | None = None
    resume: bool = True
    skip_existing: bool = True
    metadata_only: bool = False
    headed: bool = True  # Playwright: visible browser for CF challenge
    challenge_wait_s: float = 120.0
    extra_headers: dict[str, str] = field(default_factory=dict)
    # Incremental sync
    from_date: str | None = None
    to_date: str | None = None
    delta_path: Path | None = None
    sync_stop_after_known: int | None = None

    @property
    def state_path(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def changelog_path(self) -> Path:
        return self.state_dir / "changelog.jsonl"

    def search_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/Legislations/SearchLegislations"

    def detail_url(self, law_book_id: int) -> str:
        return (
            f"{self.base_url.rstrip('/')}/legislations/showlegislation"
            f"?lawbookid={int(law_book_id)}"
        )

    def pdf_url(self, law_image: str | None) -> str | None:
        if not law_image:
            return None
        if law_image.startswith("http"):
            return law_image
        return f"{self.base_url.rstrip('/')}{law_image}"
