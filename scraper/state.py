"""Idempotent scrape state + JSONL id index."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass
class ScrapeState:
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_page: int = 0
    total_count: int | None = None
    fetched_ids: list[int] = field(default_factory=list)
    failed_ids: list[int] = field(default_factory=list)
    mode: str = "http"
    notes: str = ""

    def mark_ok(self, law_id: int) -> None:
        if law_id not in self.fetched_ids:
            self.fetched_ids.append(law_id)
        if law_id in self.failed_ids:
            self.failed_ids.remove(law_id)
        self.updated_at = time.time()

    def mark_fail(self, law_id: int) -> None:
        if law_id not in self.failed_ids:
            self.failed_ids.append(law_id)
        self.updated_at = time.time()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "ScrapeState":
        if not path.is_file():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            started_at=float(data.get("started_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            last_page=int(data.get("last_page") or 0),
            total_count=data.get("total_count"),
            fetched_ids=[int(x) for x in data.get("fetched_ids") or []],
            failed_ids=[int(x) for x in data.get("failed_ids") or []],
            mode=str(data.get("mode") or "http"),
            notes=str(data.get("notes") or ""),
        )


def load_existing_ids(jsonl_path: Path) -> set[int]:
    ids: set[int] = set()
    if not jsonl_path.is_file():
        return ids
    with jsonl_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            lid = rec.get("lawBookID")
            if lid is not None:
                ids.add(int(lid))
    return ids


def append_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
            n += 1
    return n


def append_changelog(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {**event, "ts": time.time()}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
