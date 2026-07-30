#!/usr/bin/env python3
"""Validate a laws JSONL file against schemas/law_record.schema.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ImportError:
    print(
        "Missing dependency: jsonschema. Install with:\n"
        "  pip install -r requirements.txt",
        file=sys.stderr,
    )
    sys.exit(2)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = ROOT / "schemas" / "law_record.schema.json"
DEFAULT_JSONL = ROOT / "sources" / "sample_laws.jsonl"


def load_schema(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def iter_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield lineno, json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON ({exc})") from exc


def validate_file(jsonl_path: Path, schema_path: Path) -> list[str]:
    schema = load_schema(schema_path)
    validator = Draft202012Validator(schema)
    errors: list[str] = []
    count = 0
    for lineno, record in iter_jsonl(jsonl_path):
        count += 1
        for err in sorted(validator.iter_errors(record), key=lambda e: list(e.path)):
            loc = ".".join(str(p) for p in err.path) or "(root)"
            errors.append(f"{jsonl_path}:{lineno}: {loc}: {err.message}")
    if count == 0:
        errors.append(f"{jsonl_path}: no records found")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "jsonl",
        nargs="?",
        type=Path,
        default=DEFAULT_JSONL,
        help=f"JSONL to validate (default: {DEFAULT_JSONL})",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA,
        help=f"JSON Schema path (default: {DEFAULT_SCHEMA})",
    )
    args = parser.parse_args(argv)

    if not args.schema.is_file():
        print(f"Schema not found: {args.schema}", file=sys.stderr)
        return 2
    if not args.jsonl.is_file():
        print(f"JSONL not found: {args.jsonl}", file=sys.stderr)
        return 2

    errors = validate_file(args.jsonl, args.schema)
    if errors:
        print(f"FAILED: {len(errors)} error(s)", file=sys.stderr)
        for msg in errors[:50]:
            print(msg, file=sys.stderr)
        if len(errors) > 50:
            print(f"... and {len(errors) - 50} more", file=sys.stderr)
        return 1

    n = sum(1 for _ in iter_jsonl(args.jsonl))
    print(f"OK: {n} record(s) validated against {args.schema.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
