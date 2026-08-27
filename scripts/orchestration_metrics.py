#!/usr/bin/env python3
"""Record and aggregate ticket-train orchestration execution metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import run_registry


SCHEMA_VERSION = 1
EXECUTOR_KINDS = ("deterministic", "adapter", "technical-model")
WAIT_ACTIONS = {"AWAIT_HUMAN_GATE", "WAIT_FOR_PHASE_TRANSITION", "WAIT_FOR_UNITY_SLOT"}

DETERMINISTIC_ACTIONS = {
    "ACQUIRE_UNITY_SLOT_DETERMINISTICALLY",
    "AWAIT_HUMAN_GATE",
    "COMPLETE_RUN",
    "CREATE_FINAL_TRAIN_PR",
    "CREATE_OR_UPDATE_TICKET_PR_TARGETING_TRAIN",
    "INITIALIZE_UNITY_SLOTS_DETERMINISTICALLY",
    "INTEGRATE_EXECUTION_PAIR_DETERMINISTICALLY",
    "MARK_FINAL_TRAIN_PR_READY",
    "MARK_TICKET_PR_READY",
    "MERGE_FINAL_REMEDIATION_PR_INTO_TRAIN",
    "MERGE_TICKET_PR_INTO_TRAIN",
    "POLL_FINAL_FEEDBACK_DETERMINISTICALLY",
    "RELEASE_UNITY_SLOT_DETERMINISTICALLY",
    "RUN_DETERMINISTIC_TICKET_VERIFICATION",
    "RUN_FINAL_EXACT_HEAD_VERIFICATION_DETERMINISTICALLY",
    "START_FINAL_GITHUB_FEEDBACK_COLLECTION",
    "START_FINALIZATION",
    "UPDATE_FINAL_TRAIN_PR_HEAD",
    "WAIT_FOR_PHASE_TRANSITION",
    "WAIT_FOR_UNITY_SLOT",
}

ADAPTER_ACTIONS = {
    "ANNOUNCE_HUMAN_GATE",
    "BLOCKED_OR_INCONSISTENT_STATE",
    "COLLECT_FINAL_TOKENS_AND_REPORTS",
    "COMPLETE_CONTROLLED_ORCHESTRATOR_HANDOFF",
    "CONFIGURE_SUPERVISION_BEFORE_DISPATCH",
    "DISPATCH_EXECUTION_PAIR_ATOMICALLY",
    "DISPATCH_EXHAUSTIVE_INITIAL_REVIEW",
    "DISPATCH_FOCUSED_FOLLOWUP_REVIEW",
    "DISPATCH_FRESH_BATCHED_REMEDIATION",
    "DISPATCH_VISIBLE_PHASE",
    "RECONCILE_AMBIGUOUS_LAUNCH",
    "RECONFIGURE_EVENT_CALLBACKS_FOR_CURRENT_OWNER",
    "RECORD_ANALYSIS_DISPATCH_INTENT",
    "RECORD_ANALYSIS_RESULT",
    "RECORD_BATCH_TRIAGE_DISPATCH_INTENT",
    "RECORD_DRY_RUN_REPORT_AND_USAGE_EVIDENCE",
    "RECORD_FINAL_REMEDIATION_DISPATCH_INTENT",
    "RECORD_FINAL_REMEDIATION_PR",
    "RECORD_FINAL_REVIEW_DISPATCH_INTENT",
    "RECORD_FINAL_REVIEW_RESULT",
    "RECORD_TRIAGE_RESULTS",
    "REPLACE_MODEL_WAKING_WATCHER",
    "REQUEST_ORCHESTRATOR_CONFIRMATION",
    "RESUME_VISIBLE_PHASE_WITH_INPUT",
}

TECHNICAL_MODEL_ACTIONS = {
    "CLASSIFY_FINAL_VERIFICATION_FAILURE",
    "CLASSIFY_VERIFICATION_FAILURE",
    "CONSOLIDATE_DEPENDENCIES",
    "RECONCILE_CODEX_CI_COPILOT_FINDINGS",
    "RECONCILE_FINAL_CODEX_CI_COPILOT_FINDINGS",
    "RESOLVE_COST_ANOMALY_CHECKPOINT",
    "ROOT_CAUSE_CHECKPOINT_REQUIRED",
}

JUSTIFIED_WAKE_REASONS = {
    "callback",
    "dispatch",
    "failure",
    "blocker",
    "gate-announcement",
    "report",
    "technical-decision",
    "transition",
    "user-message",
}
UNJUSTIFIED_WAKE_REASONS = {"unchanged-poll", "liveness-only", "repeated-gate"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def classify_action(action_name: str) -> str:
    memberships = [
        kind
        for kind, actions in (
            ("deterministic", DETERMINISTIC_ACTIONS),
            ("adapter", ADAPTER_ACTIONS),
            ("technical-model", TECHNICAL_MODEL_ACTIONS),
        )
        if action_name in actions
    ]
    if len(memberships) != 1:
        raise ValueError(f"Action must have exactly one executor classification: {action_name}")
    return memberships[0]


def validate_taxonomy(action_names: set[str]) -> dict[str, list[str]]:
    classified = DETERMINISTIC_ACTIONS | ADAPTER_ACTIONS | TECHNICAL_MODEL_ACTIONS
    duplicates = sorted(
        action
        for action in classified
        if sum(action in group for group in (DETERMINISTIC_ACTIONS, ADAPTER_ACTIONS, TECHNICAL_MODEL_ACTIONS)) != 1
    )
    return {
        "unclassified": sorted(action_names - classified),
        "stale_classifications": sorted(classified - action_names),
        "duplicate_classifications": duplicates,
    }


def ensure_metrics(state: dict[str, Any]) -> dict[str, Any]:
    metrics = state.get("orchestration_metrics")
    if not isinstance(metrics, dict):
        metrics = {
            "schema_version": SCHEMA_VERSION,
            "actions": {},
            "wakes": {},
            "decision_packets_by_wake_kind": {},
            "created_at": now_iso(),
        }
        state["orchestration_metrics"] = metrics
    if metrics.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported orchestration metrics schema")
    metrics.setdefault("actions", {})
    metrics.setdefault("wakes", {})
    metrics.setdefault("decision_packets_by_wake_kind", {})
    return metrics


def write_json(value: dict[str, Any], output: Path | None = None) -> None:
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is None:
        sys.stdout.write(rendered)
        return
    destination = output.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(destination)


def start_action(args: argparse.Namespace) -> None:
    expected = classify_action(args.action_name)
    path = args.state.expanduser().resolve()
    with run_registry.directory_lock(path.parent):
        state = run_registry.load_json(path)
        metrics = ensure_metrics(state)
        actions = metrics["actions"]
        if args.action_id in actions:
            existing = actions[args.action_id]
            if existing.get("action_name") != args.action_name:
                raise ValueError("action_id already belongs to another action")
            write_json({"status": "duplicate-idempotent", "action": existing})
            return
        record = {
            "action_id": args.action_id,
            "action_name": args.action_name,
            "expected_executor_kind": expected,
            "ticket_id": args.ticket_id,
            "phase_key": args.phase_key,
            "controller_revision": args.controller_revision,
            "started_at": args.started_at or now_iso(),
            "baseline_total_tokens": args.baseline_total_tokens,
            "status": "RUNNING",
        }
        actions[args.action_id] = record
        metrics["updated_at"] = now_iso()
        run_registry.save_json(path, state)
    write_json({"status": "started", "action": record})


def finish_action(args: argparse.Namespace) -> None:
    path = args.state.expanduser().resolve()
    with run_registry.directory_lock(path.parent):
        state = run_registry.load_json(path)
        metrics = ensure_metrics(state)
        record = metrics["actions"].get(args.action_id)
        if not isinstance(record, dict):
            raise ValueError(f"unknown action_id: {args.action_id}")
        if record.get("status") != "RUNNING":
            write_json({"status": "duplicate-idempotent", "action": record})
            return
        actual = args.actual_executor_kind or record["expected_executor_kind"]
        if actual not in EXECUTOR_KINDS:
            raise ValueError(f"invalid actual executor kind: {actual}")
        ended_at = args.ended_at or now_iso()
        duration = (parse_iso(ended_at) - parse_iso(record["started_at"])).total_seconds()
        if duration < 0:
            raise ValueError("action duration cannot be negative")
        baseline = record.get("baseline_total_tokens")
        token_delta: int | None = None
        if args.final_total_tokens is not None:
            if baseline is None:
                raise ValueError("final token counter requires a baseline")
            token_delta = args.final_total_tokens - int(baseline)
            if token_delta < 0:
                raise ValueError("final token counter is lower than baseline")
        wake_issue = None
        if args.model_wake and record["action_name"] in WAIT_ACTIONS:
            wake_issue = "model-wake-for-wait"
        elif args.model_wake and record["expected_executor_kind"] == "deterministic":
            wake_issue = "model-wake-for-deterministic-action"
        record.update({
            "actual_executor_kind": actual,
            "ended_at": ended_at,
            "duration_seconds": duration,
            "final_total_tokens": args.final_total_tokens,
            "token_delta": token_delta,
            "model_wake": args.model_wake,
            "wake_issue": wake_issue,
            "outcome": args.outcome,
            "status": "COMPLETED",
        })
        metrics["updated_at"] = now_iso()
        run_registry.save_json(path, state)
    write_json({"status": "finished", "action": record})


def record_wake(args: argparse.Namespace) -> None:
    if args.reason not in JUSTIFIED_WAKE_REASONS | UNJUSTIFIED_WAKE_REASONS:
        raise ValueError(f"unsupported wake reason: {args.reason}")
    path = args.state.expanduser().resolve()
    with run_registry.directory_lock(path.parent):
        state = run_registry.load_json(path)
        metrics = ensure_metrics(state)
        wakes = metrics["wakes"]
        if args.wake_id in wakes:
            write_json({"status": "duplicate-idempotent", "wake": wakes[args.wake_id]})
            return
        record = {
            "wake_id": args.wake_id,
            "reason": args.reason,
            "model_woken": args.model_woken,
            "justification": (
                "unjustified" if args.reason in UNJUSTIFIED_WAKE_REASONS
                else "justified"
            ),
            "controller_revision": args.controller_revision,
            "total_tokens": args.total_tokens,
            "recorded_at": args.recorded_at or now_iso(),
        }
        wakes[args.wake_id] = record
        metrics["updated_at"] = now_iso()
        run_registry.save_json(path, state)
    write_json({"status": "recorded", "wake": record})


def empty_bucket() -> dict[str, Any]:
    return {
        "action_count": 0,
        "duration_seconds": 0.0,
        "token_total": 0,
        "token_measurement_count": 0,
        "model_wake_count": 0,
    }


def percentage(value: float, total: float) -> float | None:
    return round((value / total) * 100, 2) if total else None


def build_report(state: dict[str, Any]) -> dict[str, Any]:
    metrics = ensure_metrics(state)
    completed = [
        item for item in metrics["actions"].values()
        if isinstance(item, dict) and item.get("status") == "COMPLETED"
    ]
    running = [
        item.get("action_id") for item in metrics["actions"].values()
        if isinstance(item, dict) and item.get("status") == "RUNNING"
    ]
    by_expected = {kind: empty_bucket() for kind in EXECUTOR_KINDS}
    by_actual = {kind: empty_bucket() for kind in EXECUTOR_KINDS}
    missing_tokens: list[str] = []
    wake_issues: list[dict[str, Any]] = []
    for item in completed:
        for group, kind in (
            (by_expected, item["expected_executor_kind"]),
            (by_actual, item.get("actual_executor_kind") or item["expected_executor_kind"]),
        ):
            bucket = group[kind]
            bucket["action_count"] += 1
            bucket["duration_seconds"] += float(item.get("duration_seconds") or 0)
            bucket["model_wake_count"] += int(item.get("model_wake") is True)
            if item.get("token_delta") is not None:
                bucket["token_total"] += int(item["token_delta"])
                bucket["token_measurement_count"] += 1
        if item.get("token_delta") is None:
            missing_tokens.append(str(item.get("action_id")))
        if item.get("wake_issue"):
            wake_issues.append({
                "action_id": item.get("action_id"),
                "action_name": item.get("action_name"),
                "issue": item.get("wake_issue"),
            })
    action_total = len(completed)
    duration_total = sum(float(item.get("duration_seconds") or 0) for item in completed)
    token_total = sum(int(item.get("token_delta") or 0) for item in completed)
    for buckets in (by_expected, by_actual):
        for bucket in buckets.values():
            bucket["duration_seconds"] = round(bucket["duration_seconds"], 3)
            bucket["action_share_percent"] = percentage(bucket["action_count"], action_total)
            bucket["duration_share_percent"] = percentage(bucket["duration_seconds"], duration_total)
            bucket["token_share_percent"] = percentage(bucket["token_total"], token_total)

    wakes = [item for item in metrics["wakes"].values() if isinstance(item, dict)]
    recorded_model_wakes = sum(item.get("model_woken") is True for item in wakes)
    unjustified = [
        item for item in wakes
        if item.get("model_woken") is True and item.get("justification") == "unjustified"
    ]
    explicit_avoided = sum(item.get("model_woken") is False for item in wakes)
    control_plane = state.get("control_plane") if isinstance(state.get("control_plane"), dict) else {}
    suppressed = int(control_plane.get("suppressed_unchanged_observations", 0))
    segments = control_plane.get("segments") if isinstance(control_plane.get("segments"), list) else []
    segment_model_wakes = sum(
        int(item.get("model_wakes", 0)) for item in segments if isinstance(item, dict)
    )
    status = "complete"
    if running or missing_tokens or not completed:
        status = "partial"
    if not completed and not wakes and suppressed == 0:
        status = "unavailable"
    deterministic_actual = by_actual["deterministic"]
    adapter_actual = by_actual["adapter"]
    technical_actual = by_actual["technical-model"]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "orchestration-metrics-report",
        "generated_at": now_iso(),
        "status": status,
        "totals": {
            "action_count": action_total,
            "duration_seconds": round(duration_total, 3),
            "measured_action_tokens": token_total,
        },
        "by_expected_executor": by_expected,
        "by_actual_executor": by_actual,
        "execution_share": {
            "scripted_action_percent": deterministic_actual["action_share_percent"],
            "ai_action_percent": percentage(
                adapter_actual["action_count"] + technical_actual["action_count"], action_total
            ),
            "scripted_duration_percent": deterministic_actual["duration_share_percent"],
            "ai_duration_percent": percentage(
                adapter_actual["duration_seconds"] + technical_actual["duration_seconds"], duration_total
            ),
            "scripted_measured_token_percent": deterministic_actual["token_share_percent"],
            "ai_measured_token_percent": percentage(
                adapter_actual["token_total"] + technical_actual["token_total"], token_total
            ),
            "adapter_action_percent": adapter_actual["action_share_percent"],
            "technical_model_action_percent": technical_actual["action_share_percent"],
        },
        "wake_analysis": {
            "recorded_model_wakes": recorded_model_wakes,
            "justified_model_wakes": recorded_model_wakes - len(unjustified),
            "confirmed_unjustified_model_wakes": len(unjustified),
            "minimum_unjustified_model_wakes": max(len(unjustified), len(wake_issues)),
            "unjustified_wake_records": unjustified,
            "action_wake_issues": wake_issues,
            "explicit_avoided_wakes": explicit_avoided,
            "suppressed_unchanged_observations": suppressed,
            "minimum_total_avoided_wakes": explicit_avoided + suppressed,
            "segment_model_wakes": segment_model_wakes,
            "unattributed_model_wakes": max(0, segment_model_wakes - recorded_model_wakes),
        },
        "coverage": {
            "running_action_ids": running,
            "actions_missing_token_measurement": missing_tokens,
            "completed_action_count": action_total,
            "recorded_wake_count": len(wakes),
        },
        "interpretation": {
            "deterministic_share": "scripted or structured zero-model execution",
            "adapter_share": "Codex/application bridge without technical judgment",
            "technical_model_share": "reasoning required for a technical decision",
            "token_caveat": "Only non-overlapping recorded action deltas are allocated; session totals remain authoritative.",
        },
    }


def report(args: argparse.Namespace) -> None:
    path = args.state.expanduser().resolve()
    with run_registry.directory_lock(path.parent):
        state = run_registry.load_json(path)
        document = build_report(state)
        if args.output:
            destination = args.output.expanduser().resolve()
            write_json(document, destination)
            descriptor = {
                "reference": str(destination),
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                "status": document["status"],
                "generated_at": document["generated_at"],
            }
            metrics = ensure_metrics(state)
            metrics["last_report"] = descriptor
            metrics["updated_at"] = now_iso()
            run_registry.save_json(path, state)
            write_json({"status": "written", **descriptor})
            return
    write_json(document)


def taxonomy(args: argparse.Namespace) -> None:
    names = set(args.action_name or [])
    result = validate_taxonomy(names) if names else {
        "unclassified": [],
        "stale_classifications": [],
        "duplicate_classifications": [],
    }
    result["classification_count"] = len(
        DETERMINISTIC_ACTIONS | ADAPTER_ACTIONS | TECHNICAL_MODEL_ACTIONS
    )
    result["status"] = "pass" if not any(result[key] for key in (
        "unclassified", "stale_classifications", "duplicate_classifications"
    )) else "fail"
    write_json(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser("start-action")
    start.add_argument("--state", type=Path, required=True)
    start.add_argument("--action-id", required=True)
    start.add_argument("--action-name", required=True)
    start.add_argument("--ticket-id")
    start.add_argument("--phase-key")
    start.add_argument("--controller-revision", type=int)
    start.add_argument("--started-at")
    start.add_argument("--baseline-total-tokens", type=int)
    start.set_defaults(handler=start_action)

    finish = commands.add_parser("finish-action")
    finish.add_argument("--state", type=Path, required=True)
    finish.add_argument("--action-id", required=True)
    finish.add_argument("--actual-executor-kind", choices=EXECUTOR_KINDS)
    finish.add_argument("--ended-at")
    finish.add_argument("--final-total-tokens", type=int)
    finish.add_argument("--model-wake", action=argparse.BooleanOptionalAction, required=True)
    finish.add_argument("--outcome", choices=("completed", "failed", "blocked", "cancelled"), required=True)
    finish.set_defaults(handler=finish_action)

    wake = commands.add_parser("record-wake")
    wake.add_argument("--state", type=Path, required=True)
    wake.add_argument("--wake-id", required=True)
    wake.add_argument("--reason", required=True)
    wake.add_argument("--model-woken", action=argparse.BooleanOptionalAction, required=True)
    wake.add_argument("--controller-revision", type=int)
    wake.add_argument("--total-tokens", type=int)
    wake.add_argument("--recorded-at")
    wake.set_defaults(handler=record_wake)

    render = commands.add_parser("report")
    render.add_argument("--state", type=Path, required=True)
    render.add_argument("--output", type=Path)
    render.set_defaults(handler=report)

    check = commands.add_parser("taxonomy")
    check.add_argument("--action-name", action="append")
    check.set_defaults(handler=taxonomy)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.handler(args)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        sys.stderr.write(json.dumps({"status": "rejected", "error": str(error)}) + "\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
