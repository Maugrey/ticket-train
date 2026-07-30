#!/usr/bin/env python3
"""Deterministically reconcile ticket-train thread, GitHub, and test state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ERROR_PATTERN = re.compile(
    r"(^|\b)(error|failed|failure|exception|fatal|panic|assertion)(\b|:)",
    re.IGNORECASE,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def control(state: dict[str, Any]) -> dict[str, Any]:
    value = state.get("control")
    if not isinstance(value, dict):
        raise ValueError("Manifest is missing control object")
    return value


def append_event(state: dict[str, Any], event: dict[str, Any]) -> None:
    events = state.setdefault("supervisor_events", [])
    if not isinstance(events, list):
        raise ValueError("supervisor_events must be a list")
    event = dict(event)
    event.setdefault("observed_at", now_iso())
    events.append(event)
    if len(events) > 500:
        del events[:-500]


def find_phase(state: dict[str, Any], phase_key: str) -> dict[str, Any]:
    phases = control(state).get("phases")
    if not isinstance(phases, list):
        raise ValueError("control.phases must be a list")
    matches = [phase for phase in phases if isinstance(phase, dict) and phase.get("phase_key") == phase_key]
    if len(matches) != 1:
        raise ValueError(f"Expected one phase for {phase_key!r}, found {len(matches)}")
    return matches[0]


def finalize_update(state: dict[str, Any], transition: str) -> None:
    timestamp = now_iso()
    ctl = control(state)
    ctl["manifest_updated_at"] = timestamp
    ctl["manifest_reconciled"] = True
    state["manifest_updated_at"] = timestamp
    state["last_state_transition"] = transition


def thread_event(args: argparse.Namespace) -> None:
    state = load_json(args.state)
    if args.event_json is not None:
        event = json.loads(args.event_json)
        if not isinstance(event, dict):
            raise ValueError("Thread event JSON must be an object")
    else:
        event = load_json(args.event)
    phase_key = event.get("phase_key")
    if not isinstance(phase_key, str) or not phase_key:
        raise ValueError("Thread event requires phase_key")
    phase = find_phase(state, phase_key)
    allowed = {
        "client_thread_id",
        "thread_id",
        "host_id",
        "wait_cursor",
        "launch_state",
        "last_observed_at",
        "final_report_captured",
        "usage_captured",
        "actual_model",
        "actual_reasoning_effort",
    }
    changed: list[str] = []
    for key in allowed:
        if key in event and phase.get(key) != event[key]:
            phase[key] = event[key]
            changed.append(key)
    if not changed:
        sys.stdout.write(json.dumps({"status": "unchanged", "changed_fields": []}) + "\n")
        return
    phase["last_observed_at"] = event.get("observed_at") or now_iso()
    append_event(
        state,
        {
            "type": "thread",
            "phase_key": phase_key,
            "changed_fields": changed,
            "launch_state": phase.get("launch_state"),
        },
    )
    finalize_update(state, f"thread:{phase_key}:{phase.get('launch_state')}")
    save_json(args.state, state)
    sys.stdout.write(json.dumps({"status": "updated", "changed_fields": changed}) + "\n")


def github_snapshot(args: argparse.Namespace) -> None:
    command = [
        "gh",
        "pr",
        "view",
        str(args.pr),
        "--repo",
        args.repo,
        "--json",
        "number,url,state,baseRefName,headRefName,headRefOid,mergeStateStatus,statusCheckRollup",
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise ValueError(f"gh pr view failed: {completed.stderr.strip()}")
    snapshot = json.loads(completed.stdout)
    if not isinstance(snapshot, dict):
        raise ValueError("GitHub snapshot was not an object")

    state = load_json(args.state)
    ctl = control(state)
    snapshots = ctl.setdefault("github_snapshots", {})
    if not isinstance(snapshots, dict):
        raise ValueError("control.github_snapshots must be an object")
    key = str(snapshot.get("number") or args.pr)
    previous = snapshots.get(key)
    previous_comparable = dict(previous) if isinstance(previous, dict) else previous
    if isinstance(previous_comparable, dict):
        previous_comparable.pop("observed_at", None)
    changed = previous_comparable != snapshot
    if not changed:
        sys.stdout.write(json.dumps({"status": "unchanged", "changed": False}) + "\n")
        return
    snapshot["observed_at"] = now_iso()
    snapshots[key] = snapshot
    append_event(
        state,
        {"type": "github", "pull_request": key, "changed": changed, "head": snapshot.get("headRefOid")},
    )
    finalize_update(state, f"github:pr-{key}:{'changed' if changed else 'unchanged'}")
    save_json(args.state, state)
    sys.stdout.write(json.dumps({"status": "updated", "changed": changed, "snapshot": snapshot}) + "\n")


def concise_errors(lines: list[str], limit: int = 200) -> list[str]:
    matches = [line.rstrip("\n") for line in lines if ERROR_PATTERN.search(line)]
    if matches:
        return matches[-limit:]
    return [line.rstrip("\n") for line in lines[-min(limit, 40):]]


def test_result(args: argparse.Namespace) -> None:
    log_path = args.log.expanduser().resolve()
    data = log_path.read_bytes()
    lines = data.decode("utf-8", errors="replace").splitlines()
    artifact = {
        "phase_key": args.phase_key,
        "command": args.command,
        "exit_code": args.exit_code,
        "head_commit": args.head,
        "duration_seconds": args.duration_seconds,
        "log_path": str(log_path),
        "log_sha256": hashlib.sha256(data).hexdigest(),
        "log_bytes": len(data),
        "observed_at": now_iso(),
        "status": "passed" if args.exit_code == 0 else "failed",
        "error_excerpt": concise_errors(lines),
    }
    state = load_json(args.state)
    ctl = control(state)
    artifacts = ctl.setdefault("log_artifacts", [])
    if not isinstance(artifacts, list):
        raise ValueError("control.log_artifacts must be a list")
    artifacts.append(artifact)
    append_event(
        state,
        {"type": "test", "phase_key": args.phase_key, "status": artifact["status"], "head": args.head},
    )
    finalize_update(state, f"test:{args.phase_key}:{artifact['status']}")
    save_json(args.state, state)
    sys.stdout.write(json.dumps(artifact, ensure_ascii=False) + "\n")


def verification_event(args: argparse.Namespace) -> None:
    state = load_json(args.state)
    event = json.loads(args.event_json)
    if not isinstance(event, dict):
        raise ValueError("Verification event JSON must be an object")
    ctl = control(state)
    gates = ctl.setdefault("verification_gates", {})
    if not isinstance(gates, dict):
        raise ValueError("control.verification_gates must be an object")
    gate = gates.setdefault(args.ticket, {})
    if not isinstance(gate, dict):
        raise ValueError(f"Verification gate must be an object: {args.ticket}")

    allowed = {
        "implementation_contract_revision",
        "verification_contract_revision",
        "execution_pair_base",
        "implementation_thread_id",
        "implementation_branch",
        "acceptance_test_thread_id",
        "acceptance_test_branch",
        "acceptance_test_commit",
        "acceptance_test_pull_request",
        "independent_test_authorship",
        "implementation_disclosed_before_test_commit",
        "acceptance_coverage_status",
        "baseline_red_status",
        "baseline_red_base",
        "baseline_red_not_applicable_reason",
        "acceptance_tests_integrated",
        "integrated_green_status",
        "integrated_green_head",
        "ticket_head",
        "environment_parity_status",
        "environment_fingerprint",
        "supabase_auth_applicable",
        "supabase_auth_verification_status",
        "privileged_credentials_setup_only",
        "automatable_manual_scenarios",
        "unresolved_validation_failures",
        "logs_captured",
    }
    unknown = sorted(set(event) - allowed)
    if unknown:
        raise ValueError("Unsupported verification fields: " + ", ".join(unknown))

    changed: list[str] = []
    for key, value in event.items():
        if gate.get(key) != value:
            gate[key] = value
            changed.append(key)
    if not changed:
        sys.stdout.write(json.dumps({"status": "unchanged", "changed_fields": []}) + "\n")
        return
    append_event(
        state,
        {"type": "verification", "ticket_id": args.ticket, "changed_fields": changed},
    )
    finalize_update(state, f"verification:{args.ticket}:updated")
    save_json(args.state, state)
    sys.stdout.write(json.dumps({"status": "updated", "changed_fields": changed}) + "\n")


def status(args: argparse.Namespace) -> None:
    state = load_json(args.state)
    ctl = control(state)
    phases = ctl.get("phases") if isinstance(ctl.get("phases"), list) else []
    active = [
        phase.get("phase_key")
        for phase in phases
        if isinstance(phase, dict)
        and phase.get("launch_state") in {"INTENT_RECORDED", "LAUNCH_REQUESTED", "LAUNCH_UNKNOWN", "QUEUED", "RUNNING"}
    ]
    document = {
        "manifest_updated_at": ctl.get("manifest_updated_at"),
        "active_phase_keys": active,
        "next_automatic_action": ctl.get("next_automatic_action"),
        "pending_human_gates": ctl.get("pending_human_gates", []),
        "blocking_conditions": ctl.get("blocking_conditions", []),
        "cost_anomaly_status": ctl.get("cost_anomaly_status"),
        "train_size_budget": ctl.get("train_size_budget"),
    }
    sys.stdout.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    thread = subparsers.add_parser("thread-event")
    thread.add_argument("--state", type=Path, required=True)
    event_source = thread.add_mutually_exclusive_group(required=True)
    event_source.add_argument("--event", type=Path)
    event_source.add_argument("--event-json")
    thread.set_defaults(handler=thread_event)

    github = subparsers.add_parser("github-snapshot")
    github.add_argument("--state", type=Path, required=True)
    github.add_argument("--repo", required=True)
    github.add_argument("--pr", required=True)
    github.set_defaults(handler=github_snapshot)

    test = subparsers.add_parser("test-result")
    test.add_argument("--state", type=Path, required=True)
    test.add_argument("--phase-key", required=True)
    test.add_argument("--command", required=True)
    test.add_argument("--exit-code", type=int, required=True)
    test.add_argument("--head", required=True)
    test.add_argument("--duration-seconds", type=float, required=True)
    test.add_argument("--log", type=Path, required=True)
    test.set_defaults(handler=test_result)

    verification = subparsers.add_parser("verification-event")
    verification.add_argument("--state", type=Path, required=True)
    verification.add_argument("--ticket", required=True)
    verification.add_argument("--event-json", required=True)
    verification.set_defaults(handler=verification_event)

    show = subparsers.add_parser("status")
    show.add_argument("--state", type=Path, required=True)
    show.set_defaults(handler=status)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.handler(args)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
