#!/usr/bin/env python3
"""Create, discover, and claim canonical ticket-train runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


ACTIVE_RUN_STATES = {"ACTIVE", "AWAITING_USER", "BLOCKED", "CHECKPOINT"}
DEFAULT_LEASE_MINUTES = 30


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now().isoformat()


def default_root() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home) / "ticket-train" / "runs"
    return Path.home() / ".codex" / "ticket-train" / "runs"


def default_legacy_root() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home) / "ticket-trains"
    return Path.home() / ".codex" / "ticket-trains"


def canonical_text(value: str) -> str:
    return value.strip().replace("\\", "/").rstrip("/").casefold()


def ticket_ids(value: str) -> list[str]:
    tickets = sorted({item.strip() for item in value.split(",") if item.strip()})
    if not tickets:
        raise ValueError("At least one ticket ID is required")
    return tickets


def run_fingerprint(repository: str, train_branch: str, source: str, tickets: list[str]) -> str:
    payload = {
        "repository": canonical_text(repository),
        "train_branch": canonical_text(train_branch),
        "source": canonical_text(source),
        "tickets": [canonical_text(ticket) for ticket in sorted(tickets)],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def slug(value: str) -> str:
    result = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-._").lower()
    return result[:48] or "train"


def load_json(path: Path) -> dict[str, Any]:
    with path.expanduser().resolve().open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def save_json(path: Path, value: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_suffix(resolved.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, resolved)


@contextmanager
def directory_lock(root: Path, timeout_seconds: float = 10.0) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".registry.lock"
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f"{os.getpid()} {now_iso()}\n".encode("utf-8"))
            os.close(descriptor)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ValueError(f"Run registry is locked: {lock}")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def manifest_paths(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.glob("*/manifest.json"))


def matching_runs(root: Path, fingerprint: str) -> list[tuple[Path, dict[str, Any]]]:
    matches: list[tuple[Path, dict[str, Any]]] = []
    for path in manifest_paths(root):
        try:
            state = load_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        identity = state.get("run_identity")
        if isinstance(identity, dict) and identity.get("fingerprint") == fingerprint:
            matches.append((path, state))
    return matches


def legacy_ticket_ids(state: dict[str, Any]) -> list[str]:
    raw = state.get("ticket_selection") or state.get("requested_tickets") or state.get("tickets")
    if not isinstance(raw, list):
        return []
    values: list[str] = []
    for item in raw:
        if isinstance(item, str):
            values.append(item.strip())
        elif isinstance(item, dict) and item.get("id"):
            values.append(str(item["id"]).strip())
    return sorted(value for value in values if value)


def legacy_source(state: dict[str, Any]) -> str:
    value = state.get("ticket_source") or state.get("source")
    if isinstance(value, dict):
        value = value.get("path") or value.get("locator") or value.get("url")
    return canonical_text(str(value or ""))


def legacy_train_branch(state: dict[str, Any]) -> str:
    value = state.get("train_branch")
    if not value:
        train = state.get("train")
        if isinstance(train, dict):
            value = train.get("branch") or train.get("name")
    if not value:
        candidate = state.get("base_branch")
        if isinstance(candidate, str) and "train" in candidate.casefold():
            value = candidate
    return canonical_text(str(value or ""))


def matching_legacy_runs(
    root: Path, train_branch: str, source: str, tickets: list[str]
) -> list[Path]:
    if not root.exists():
        return []
    expected_tickets = [canonical_text(ticket) for ticket in sorted(tickets)]
    expected_source = canonical_text(source)
    expected_branch = canonical_text(train_branch)
    candidates = set(root.rglob("manifest.json")) | set(root.rglob("run-manifest.json"))
    matches: list[Path] = []
    for path in sorted(candidates):
        try:
            state = load_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        found_tickets = [canonical_text(ticket) for ticket in legacy_ticket_ids(state)]
        if found_tickets != expected_tickets:
            continue
        if legacy_source(state) != expected_source:
            continue
        if legacy_train_branch(state) != expected_branch:
            continue
        matches.append(path.resolve())
    return matches


def lease_expired(lease: Any) -> bool:
    if not isinstance(lease, dict) or not lease.get("expires_at"):
        return True
    try:
        expires = datetime.fromisoformat(str(lease["expires_at"]).replace("Z", "+00:00"))
    except ValueError:
        return True
    return expires <= now()


def lease(owner_thread_id: str, lease_minutes: int) -> dict[str, Any]:
    timestamp = now()
    return {
        "owner_thread_id": owner_thread_id,
        "claimed_at": timestamp.isoformat(),
        "heartbeat_at": timestamp.isoformat(),
        "expires_at": (timestamp + timedelta(minutes=lease_minutes)).isoformat(),
    }


def init_run(args: argparse.Namespace) -> int:
    root = args.root.expanduser().resolve()
    tickets = ticket_ids(args.tickets)
    fingerprint = run_fingerprint(args.repository, args.train_branch, args.source, tickets)
    with directory_lock(root):
        matches = matching_runs(root, fingerprint)
        active = [
            (path, state)
            for path, state in matches
            if state.get("run_status") in ACTIVE_RUN_STATES
        ]
        if active:
            path, state = active[-1]
            document = {
                "status": "existing-active-run",
                "manifest": str(path),
                "run_id": state.get("run_id"),
                "run_status": state.get("run_status"),
                "owner_thread_id": (state.get("orchestrator_lease") or {}).get("owner_thread_id"),
                "lease_expired": lease_expired(state.get("orchestrator_lease")),
                "required_action": "adopt-or-explicit-takeover; do not repeat completed phases",
            }
            sys.stdout.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
            return 3

        legacy = matching_legacy_runs(
            args.legacy_root.expanduser().resolve(), args.train_branch, args.source, tickets
        )
        if legacy and not args.adopt_legacy:
            sys.stdout.write(
                json.dumps(
                    {
                        "status": "legacy-run-found",
                        "legacy_manifests": [str(path) for path in legacy],
                        "required_action": "adopt and reconcile legacy manifests; do not repeat completed phases",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            )
            return 6

        stamp = now().strftime("%Y%m%dT%H%M%SZ")
        run_id = args.run_id or f"{stamp}-{slug(args.train_branch)}-{fingerprint[:8]}"
        path = root / run_id / "manifest.json"
        if path.exists():
            raise ValueError(f"Run manifest already exists: {path}")
        timestamp = now_iso()
        state = {
            "schema_version": 2,
            "run_id": run_id,
            "run_status": "ACTIVE",
            "execution_mode": args.execution_mode,
            "created_at": timestamp,
            "manifest_updated_at": timestamp,
            "run_identity": {
                "fingerprint": fingerprint,
                "repository": args.repository,
                "train_branch": args.train_branch,
                "source": args.source,
                "tickets": tickets,
            },
            "orchestrator_lease": lease(args.orchestrator_thread, args.lease_minutes),
            "supervision": {
                "mode": "UNRESOLVED",
                "status": "INACTIVE",
                "watcher_id": None,
                "last_check_at": None,
                "max_internal_poll_seconds": 300,
                "max_user_silence_seconds": 900,
            },
            "pending_human_action": None,
            "handoff_history": [],
            "analysis_artifacts": {},
            "legacy_manifest_inventory": [str(path) for path in legacy],
            "control": {
                "manifest_updated_at": timestamp,
                "manifest_reconciled": True,
                "terminal_reason": None,
                "next_automatic_action": (
                    "reconcile_legacy_manifests" if legacy else "await_orchestrator_confirmation"
                ),
                "launch_unknown_phase_keys": [],
                "pending_human_gates": [],
                "blocking_conditions": [],
                "requested_ticket_states": {ticket: "DISCOVERED" for ticket in tickets},
                "phases": [],
                "proportionality_profile_revision": None,
                "train_size_budget": {
                    "material_files": 0,
                    "schema_or_data_transformations": 0,
                    "structural_domains": [],
                    "checkpoint_crossed": False,
                },
                "review_pass_budgets": {},
                "verification_gates": {},
                "duplicate_session_inventory": [str(path) for path in legacy],
                "unmeasured_phase_inventory": [],
                "cost_anomaly_status": "checkpoint-open" if legacy else "clear",
                "finalization": {},
            },
        }
        save_json(path, state)
    sys.stdout.write(
        json.dumps(
            {"status": "created", "manifest": str(path), "run_id": run_id, "fingerprint": fingerprint},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return 0


def discover(args: argparse.Namespace) -> int:
    tickets = ticket_ids(args.tickets)
    fingerprint = run_fingerprint(args.repository, args.train_branch, args.source, tickets)
    matches = matching_runs(args.root.expanduser().resolve(), fingerprint)
    document = [
        {
            "manifest": str(path),
            "run_id": state.get("run_id"),
            "run_status": state.get("run_status"),
            "owner_thread_id": (state.get("orchestrator_lease") or {}).get("owner_thread_id"),
            "lease_expired": lease_expired(state.get("orchestrator_lease")),
        }
        for path, state in matches
    ]
    sys.stdout.write(json.dumps({"fingerprint": fingerprint, "matches": document}, indent=2) + "\n")
    return 0 if matches else 4


def claim(args: argparse.Namespace) -> int:
    path = args.state.expanduser().resolve()
    root = path.parent.parent
    with directory_lock(root):
        state = load_json(path)
        current = state.get("orchestrator_lease")
        current_owner = current.get("owner_thread_id") if isinstance(current, dict) else None
        expired = lease_expired(current)
        if current_owner and current_owner != args.orchestrator_thread and not expired and not args.takeover:
            sys.stdout.write(
                json.dumps(
                    {
                        "status": "owned-by-another-orchestrator",
                        "owner_thread_id": current_owner,
                        "expires_at": current.get("expires_at"),
                        "required_action": "resume in the owner thread or request explicit takeover",
                    },
                    indent=2,
                )
                + "\n"
            )
            return 5
        if args.takeover and not args.user_authorized_takeover:
            raise ValueError("--takeover requires --user-authorized-takeover")
        history = state.setdefault("handoff_history", [])
        if not isinstance(history, list):
            raise ValueError("handoff_history must be a list")
        if current_owner and current_owner != args.orchestrator_thread:
            history.append(
                {
                    "from_thread_id": current_owner,
                    "to_thread_id": args.orchestrator_thread,
                    "at": now_iso(),
                    "reason": "explicit-takeover" if args.takeover else "expired-lease-adoption",
                }
            )
        state["orchestrator_lease"] = lease(args.orchestrator_thread, args.lease_minutes)
        state["manifest_updated_at"] = now_iso()
        save_json(path, state)
    sys.stdout.write(
        json.dumps(
            {
                "status": "claimed",
                "manifest": str(path),
                "owner_thread_id": args.orchestrator_thread,
                "takeover": bool(args.takeover),
            },
            indent=2,
        )
        + "\n"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    initialize = subparsers.add_parser("init")
    initialize.add_argument("--root", type=Path, default=default_root())
    initialize.add_argument("--legacy-root", type=Path, default=default_legacy_root())
    initialize.add_argument("--repository", required=True)
    initialize.add_argument("--train-branch", required=True)
    initialize.add_argument("--source", required=True)
    initialize.add_argument("--tickets", required=True)
    initialize.add_argument("--orchestrator-thread", required=True)
    initialize.add_argument("--execution-mode", choices=("dry-run", "live"), required=True)
    initialize.add_argument("--run-id")
    initialize.add_argument("--adopt-legacy", action="store_true")
    initialize.add_argument("--lease-minutes", type=int, default=DEFAULT_LEASE_MINUTES)
    initialize.set_defaults(handler=init_run)

    find = subparsers.add_parser("discover")
    find.add_argument("--root", type=Path, default=default_root())
    find.add_argument("--repository", required=True)
    find.add_argument("--train-branch", required=True)
    find.add_argument("--source", required=True)
    find.add_argument("--tickets", required=True)
    find.set_defaults(handler=discover)

    acquire = subparsers.add_parser("claim")
    acquire.add_argument("--state", type=Path, required=True)
    acquire.add_argument("--orchestrator-thread", required=True)
    acquire.add_argument("--lease-minutes", type=int, default=DEFAULT_LEASE_MINUTES)
    acquire.add_argument("--takeover", action="store_true")
    acquire.add_argument("--user-authorized-takeover", action="store_true")
    acquire.set_defaults(handler=claim)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "lease_minutes", DEFAULT_LEASE_MINUTES) <= 0:
        parser.error("--lease-minutes must be positive")
    try:
        return int(args.handler(args))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
