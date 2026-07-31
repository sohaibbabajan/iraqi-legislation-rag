"""Unit tests for identity + merge (no live network)."""

from __future__ import annotations

import json
from pathlib import Path

from scraper.identity import (
    content_fingerprint,
    ensure_extension_fields,
    record_identity,
)
from scraper.merge import (
    load_jsonl_by_identity,
    merge_jsonl,
    merge_records,
    mirror_master_file,
    resolve_mirror_path,
)


def _rec(**kwargs):
    base = {
        "lawBookID": 100,
        "lawTitle": "قانون تجريبي",
        "status_label": "ساري",
        "full_text": "نص",
        "full_text_len": 3,
        "lawYear": "2020",
        "lawCode": "1",
        "lawFlag": "",
        "lawValid": "Y",
        "date_iso": "2020-01-01",
        "lawDate": "01-01-2020",
        "lawNotes": "",
    }
    base.update(kwargs)
    return base


def test_identity_prefers_law_book_id():
    assert record_identity(_rec(lawBookID=54944)) == "iraqld:lawBookID:54944"
    assert record_identity(_rec(lawBookID=13, source_type="iraqld")) == "iraqld:lawBookID:13"


def test_identity_corpus_id_for_future_sources():
    rec = {
        "source_type": "textbook",
        "corpus_id": "civil-vol-2-ch3",
        "lawTitle": "شرح القانون المدني",
        "full_text": "…",
        "status_label": "ساري",
    }
    assert record_identity(rec) == "textbook:corpus_id:civil-vol-2-ch3"


def test_identity_title_year_fallback_stable():
    a = record_identity(
        {
            "source_type": "other",
            "lawTitle": "  قانون  ما  ",
            "lawYear": "1969",
            "lawCode": "111",
            "full_text": "",
            "status_label": "ساري",
        }
    )
    b = record_identity(
        {
            "source_type": "other",
            "lawTitle": "قانون ما",
            "lawYear": "1969",
            "lawCode": "111",
            "full_text": "different body",
            "status_label": "ساري",
        }
    )
    assert a == b
    assert a.startswith("other:title_year:")


def test_fingerprint_changes_on_text_update():
    a = content_fingerprint(_rec(full_text="نص قديم", full_text_len=7))
    b = content_fingerprint(_rec(full_text="نص جديد", full_text_len=7))
    assert a != b


def test_fingerprint_stable_for_identical():
    assert content_fingerprint(_rec()) == content_fingerprint(_rec())


def test_merge_inserts_and_skips_dupes():
    master = {}
    incoming = [_rec(lawBookID=1), _rec(lawBookID=1), _rec(lawBookID=2)]
    out, changed, stats = merge_records(master, incoming)
    assert stats.inserted == 2
    assert stats.skipped == 1
    assert stats.updated == 0
    assert len(out) == 2
    assert len(changed) == 2


def test_merge_updates_changed_content():
    master = {record_identity(_rec(lawBookID=5)): _rec(lawBookID=5, full_text="قديم")}
    incoming = [_rec(lawBookID=5, full_text="جديد", full_text_len=4)]
    out, changed, stats = merge_records(master, incoming)
    assert stats.updated == 1
    assert stats.inserted == 0
    assert out[record_identity(_rec(lawBookID=5))]["full_text"] == "جديد"
    assert changed[0]["full_text"] == "جديد"


def test_merge_jsonl_atomic_no_duplicate_lines(tmp_path: Path):
    master = tmp_path / "master.jsonl"
    delta_a = tmp_path / "a.jsonl"
    delta_b = tmp_path / "b.jsonl"
    delta_out = tmp_path / "changed.jsonl"

    master.write_text(
        json.dumps(_rec(lawBookID=10), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    delta_a.write_text(
        json.dumps(_rec(lawBookID=10), ensure_ascii=False)
        + "\n"
        + json.dumps(_rec(lawBookID=11), ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    delta_b.write_text(
        json.dumps(_rec(lawBookID=11, full_text="محدث", full_text_len=4), ensure_ascii=False)
        + "\n"
        + json.dumps(_rec(lawBookID=12), ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    stats1 = merge_jsonl(master, [delta_a], delta_path=delta_out)
    assert stats1.inserted == 1  # 11 new; 10 skipped
    assert stats1.skipped == 1

    stats2 = merge_jsonl(master, [delta_b], delta_path=delta_out)
    assert stats2.inserted == 1  # 12
    assert stats2.updated == 1  # 11 text change

    index = load_jsonl_by_identity(master)
    assert len(index) == 3
    lines = [ln for ln in master.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 3
    assert index[record_identity(_rec(lawBookID=11))]["full_text"] == "محدث"


def test_ensure_extension_fields():
    out = ensure_extension_fields({"lawBookID": 99, "lawTitle": "x", "full_text": "", "status_label": "ساري"})
    assert out["source_type"] == "iraqld"
    assert out["corpus_id"] == "iraqld:99"


def test_mirror_master_copies_and_skips_same_path(tmp_path: Path, monkeypatch):
    src = tmp_path / "master.jsonl"
    dst = tmp_path / "masadir" / "laws_master.jsonl"
    src.write_text('{"lawBookID":1}\n', encoding="utf-8")
    assert mirror_master_file(src, None) is None
    assert mirror_master_file(src, src) is None
    out = mirror_master_file(src, dst)
    assert out == dst.resolve()
    assert dst.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")

    monkeypatch.setenv("IRAQI_RAG_MASTER", str(dst))
    assert resolve_mirror_path(None) == dst
    assert resolve_mirror_path(tmp_path / "explicit.jsonl") == tmp_path / "explicit.jsonl"
