"""Unit tests for law-record JSON Schema + sample fixture (no LanceDB, no API)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "law_record.schema.json"
CHUNK_SCHEMA_PATH = ROOT / "schemas" / "chunk_record.schema.json"
SAMPLE_PATH = ROOT / "sources" / "sample_laws.jsonl"
VALIDATE_SCRIPT = ROOT / "scripts" / "validate_laws_schema.py"


@pytest.fixture(scope="module")
def law_schema() -> dict:
    with SCHEMA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def sample_records() -> list[dict]:
    rows = []
    with SAMPLE_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def test_schema_files_exist():
    assert SCHEMA_PATH.is_file()
    assert CHUNK_SCHEMA_PATH.is_file()
    assert SAMPLE_PATH.is_file()


def test_sample_size_in_range(sample_records):
    assert 20 <= len(sample_records) <= 50


def test_all_sample_records_valid(law_schema, sample_records):
    validator = Draft202012Validator(law_schema)
    for i, rec in enumerate(sample_records, start=1):
        errors = sorted(validator.iter_errors(rec), key=lambda e: list(e.path))
        assert not errors, f"record {i} (lawBookID={rec.get('lawBookID')}): {errors[0].message}"


def test_required_fields_present(sample_records):
    for rec in sample_records:
        assert "lawBookID" in rec
        assert "lawTitle" in rec and rec["lawTitle"]
        assert "full_text" in rec
        assert rec["status_label"] in ("ساري", "ملغى")


def test_rejects_missing_required(law_schema):
    validator = Draft202012Validator(law_schema)
    bad = {"lawTitle": "x", "full_text": "y", "status_label": "ساري"}
    assert not validator.is_valid(bad)


def test_rejects_bad_status(law_schema):
    validator = Draft202012Validator(law_schema)
    bad = {
        "lawBookID": 1,
        "lawTitle": "x",
        "full_text": "y",
        "status_label": "unknown",
    }
    assert not validator.is_valid(bad)


def test_chunk_schema_accepts_minimal():
    with CHUNK_SCHEMA_PATH.open(encoding="utf-8") as f:
        schema = json.load(f)
    validator = Draft202012Validator(schema)
    ok = {
        "chunk_id": "90001#0",
        "law_book_id": 90001,
        "title": "sample",
        "status_label": "ساري",
        "text": "المادة 1\nنص.",
        "article_nums": ",1,",
    }
    assert validator.is_valid(ok)


def test_validate_script_exits_zero():
    proc = subprocess.run(
        [sys.executable, str(VALIDATE_SCRIPT), str(SAMPLE_PATH)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    assert "OK:" in proc.stdout
