#!/usr/bin/env python3
"""Build a bounded, hash-addressed phase context packet."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


MAX_BYTES = 65_536
FORBIDDEN_KEY_FRAGMENTS = ("transcript", "private_reasoning", "full_prompt", "conversation_history", "raw_log")


def load_payload(path: Path) -> dict[str, Any]:
    value = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Context payload must be a JSON object")
    return value


def reject_forbidden_keys(value: Any, path: str = "payload") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower()
            if any(fragment in normalized for fragment in FORBIDDEN_KEY_FRAGMENTS):
                raise ValueError(f"Context packet contains forbidden key: {path}.{key}")
            reject_forbidden_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_forbidden_keys(child, f"{path}[{index}]")


def build_packet(
    payload_path: Path,
    output_path: Path,
    profile_revision: str,
    exact_base: str,
    exact_head: str,
) -> dict[str, Any]:
    payload = load_payload(payload_path)
    reject_forbidden_keys(payload)
    packet = {
        "schema_version": 1,
        "format": "fresh-compact-v1",
        "history_turns_included": 0,
        "profile_revision": profile_revision,
        "exact_base": exact_base,
        "exact_head": exact_head,
        "payload": payload,
    }
    rendered = json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    encoded = rendered.encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise ValueError(f"Context packet exceeds {MAX_BYTES} bytes")
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(encoded)
    return {
        "format": "fresh-compact-v1",
        "reference": str(output_path),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "byte_count": len(encoded),
        "history_turns_included": 0,
        "profile_revision": profile_revision,
        "exact_base": exact_base,
        "exact_head": exact_head,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile-revision", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        descriptor = build_packet(
            args.input, args.output, args.profile_revision, args.base, args.head
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        sys.stderr.write(json.dumps({"status": "rejected", "error": str(error)}) + "\n")
        return 2
    sys.stdout.write(json.dumps({"status": "created", "context_packet": descriptor}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
