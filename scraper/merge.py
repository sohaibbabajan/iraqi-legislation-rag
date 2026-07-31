"""Merge incoming law_record JSONL into a master file without duplicate ids."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from scraper.identity import content_fingerprint, ensure_extension_fields, record_identity

# Sibling Masadir (or any second checkout) can set this so sync/merge
# keeps a second master copy current without a manual copy step.
MIRROR_ENV = "IRAQI_RAG_MASTER"


def resolve_mirror_path(explicit: Path | None = None) -> Path | None:
    """CLI ``--mirror`` wins; else ``IRAQI_RAG_MASTER`` if set."""
    if explicit is not None:
        return Path(explicit)
    raw = (os.environ.get(MIRROR_ENV) or "").strip()
    if not raw:
        return None
    return Path(raw)


def mirror_master_file(source: Path, dest: Path | None) -> Path | None:
    """
    Copy ``source`` → ``dest`` after a successful sync/merge rewrite.

    No-op when dest is unset or resolves to the same path as source.
    Returns the dest path when a copy ran, else None.
    """
    if dest is None:
        return None
    src = Path(source).resolve()
    dst = Path(dest).resolve()
    if src == dst:
        return None
    if not src.is_file():
        raise FileNotFoundError(f"mirror source missing: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


@dataclass
class MergeStats:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    total_in: int = 0
    total_out: int = 0
    delta_written: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped": self.skipped,
            "total_in": self.total_in,
            "total_out": self.total_out,
            "delta_written": self.delta_written,
        }


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON ({exc})") from exc
            if not isinstance(rec, dict):
                raise ValueError(f"{path}:{lineno}: expected object, got {type(rec)}")
            yield rec


def load_jsonl_by_identity(path: Path) -> dict[str, dict[str, Any]]:
    """Last-wins if the file already contains duplicate identities."""
    index: dict[str, dict[str, Any]] = {}
    for rec in iter_jsonl(path):
        index[record_identity(rec)] = rec
    return index


def write_jsonl_atomic(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    n = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
                n += 1
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return n


def append_jsonl_records(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
            n += 1
    return n


def merge_records(
    master: dict[str, dict[str, Any]],
    incoming: Iterable[dict[str, Any]],
    *,
    attach_extension_fields: bool = True,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], MergeStats]:
    """
    Upsert ``incoming`` into ``master`` (mutates a copy of master).

    Returns (new_index, changed_records, stats).
    """
    out = dict(master)
    changed: list[dict[str, Any]] = []
    stats = MergeStats()
    for raw in incoming:
        stats.total_in += 1
        rec = ensure_extension_fields(raw) if attach_extension_fields else dict(raw)
        key = record_identity(rec)
        prev = out.get(key)
        if prev is None:
            out[key] = rec
            changed.append(rec)
            stats.inserted += 1
            continue
        if content_fingerprint(prev) == content_fingerprint(rec):
            stats.skipped += 1
            continue
        out[key] = rec
        changed.append(rec)
        stats.updated += 1
    stats.total_out = len(out)
    return out, changed, stats


def merge_jsonl(
    master_path: Path,
    incoming_paths: list[Path],
    *,
    delta_path: Path | None = None,
    sort_by_id: bool = True,
) -> MergeStats:
    index = load_jsonl_by_identity(master_path)

    def _incoming() -> Iterator[dict[str, Any]]:
        for p in incoming_paths:
            yield from iter_jsonl(p)

    index, changed, stats = merge_records(index, _incoming())

    records = list(index.values())
    if sort_by_id:

        def _sort_key(r: dict[str, Any]) -> tuple[int, str]:
            lid = r.get("lawBookID")
            try:
                return (0, f"{int(lid):08d}") if lid is not None else (1, record_identity(r))
            except (TypeError, ValueError):
                return (1, record_identity(r))

        records.sort(key=_sort_key)

    write_jsonl_atomic(master_path, records)
    if delta_path is not None and changed:
        append_jsonl_records(delta_path, changed)
        stats.delta_written = len(changed)
    return stats
