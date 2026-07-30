"""Tests for scripts/package_corpus_release.py (offline)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "package_corpus_release.py"
SAMPLE = ROOT / "sources" / "sample_laws.jsonl"


def test_package_sample_manifest(tmp_path: Path):
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(SAMPLE),
            "--corpus-version",
            "0.1.0-sample",
            "--scrape-date",
            "2026-07-31",
            "--out-dir",
            str(tmp_path),
            "--source-note",
            "Synthetic CI fixture — not real statutes.",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    manifest_path = tmp_path / "sample_laws.manifest.json"
    assert manifest_path.is_file()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["corpus_version"] == "0.1.0-sample"
    assert data["scrape_date"] == "2026-07-31"
    assert data["counts"]["record_count"] >= 20
    assert len(data["file"]["sha256"]) == 64
    checksum = tmp_path / "sample_laws.sha256"
    assert checksum.is_file()
    assert data["file"]["sha256"] in checksum.read_text(encoding="utf-8")
