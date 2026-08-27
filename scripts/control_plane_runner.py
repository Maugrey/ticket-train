#!/usr/bin/env python3
"""Deterministic wake packets and orchestration-budget control for ticket trains.

This runner deliberately does not perform technical reasoning. It reduces the
canonical manifest to a bounded decision packet, suppresses unchanged waits,
and requests a controlled orchestrator handoff before one conversation grows
into an expensive long-lived scheduler.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import run_registry
import train_controller
import orchestration_metrics


RUNNER_VERSION = "1.0"
PACKET_FORMAT = "ticket-train-decision-v1"
DEFAULT_PACKET_MAX_BYTES = 16_384
DEFAULT_SOFT_TOKEN_LIMIT = 10_000_000
DEFAULT_HARD_TOKEN_LIMIT = 25_000_000
DEFAULT_MODEL_WAKE_LIMIT = 50
DEFAULT_TOOL_CALL_LIMIT = 500
DEFAULT_CONTEXT_COMPACTION_LIMIT = 1

WAIT_ACTIONS = {"WAIT_FOR_PHASE_TRANSITION", "WAIT_FOR_UNITY_SLOT", "AWAIT_HUMAN_GATE"}


class RunnerError(ValueError):
    """Raised when runner state or inputs are invalid."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RunnerError(message)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def ensure_control_plane(state: dict[str, Any]) -> dict[str, Any]:
    value = state.get("control_plane")
    if not isinstance(value, dict):
        value = {
            "schema_version": 1,
            "runner_version": RUNNER_VERSION,
            "packet_sequence": 0,
            "last_semantic_hash": None,
            "last_packet_reference": None,
            "suppressed_unchanged_observations": 0,
            "thresholds": {
                "soft_total_tokens": DEFAULT_SOFT_TOKEN_LIMIT,
                "hard_total_tokens": DEFAULT_HARD_TOKEN_LIMIT,
                "max_model_wakes": DEFAULT_MODEL_WAKE_LIMIT,
                "max_tool_calls": DEFAULT_TOOL_CALL_LIMIT,
                "max_context_compactions": DEFAULT_CONTEXT_COMPACTION_LIMIT,
                "packet_max_bytes": DEFAULT_PACKET_MAX_BYTES,
            },
            "segments": [],
            "rotation": {"status": "CLEAR", "reasons": []},
        }
        state["control_plane"] = value
    value.setdefault("schema_version", 1)
    value["runner_version"] = RUNNER_VERSION
    value.setdefault("packet_sequence", 0)
    value.setdefault("last_semantic_hash", None)
    value.setdefault("last_packet_reference", None)
    value.setdefault("suppressed_unchanged_observations", 0)
    value.setdefault("segments", [])
    value.setdefault("rotation", {"status": "CLEAR", "reasons": []})
    thresholds = value.setdefault("thresholds", {})
    thresholds.setdefault("soft_total_tokens", DEFAULT_SOFT_TOKEN_LIMIT)
    thresholds.setdefault("hard_total_tokens", DEFAULT_HARD_TOKEN_LIMIT)
    thresholds.setdefault("max_model_wakes", DEFAULT_MODEL_WAKE_LIMIT)
    thresholds.setdefault("max_tool_calls", DEFAULT_TOOL_CALL_LIMIT)
    thresholds.setdefault("max_context_compactions", DEFAULT_CONTEXT_COMPACTION_LIMIT)
    thresholds.setdefault("packet_max_bytes", DEFAULT_PACKET_MAX_BYTES)
    policy = state.get("orchestrator_rotation_policy")
    if isinstance(policy, dict):
        mapping = {
            "soft_total_tokens": "soft_total_tokens",
            "hard_total_tokens": "hard_total_tokens",
            "max_model_wakes": "max_model_wakes",
            "max_tool_calls": "max_tool_calls",
            "max_context_compactions": "max_context_compactions",
            "decision_packet_max_bytes": "packet_max_bytes",
        }
        for source, destination in mapping.items():
            if policy.get(source) is not None:
                thresholds[destination] = int(policy[source])
    return value


def current_owner(state: dict[str, Any]) -> str | None:
    lease = state.get("orchestrator_lease")
    return str(lease.get("owner_thread_id")) if isinstance(lease, dict) and lease.get("owner_thread_id") else None


def current_segment(control_plane: dict[str, Any], owner: str) -> dict[str, Any]:
    segments = control_plane.setdefault("segments", [])
    require(isinstance(segments, list), "control_plane.segments must be a list")
    if segments and isinstance(segments[-1], dict) and segments[-1].get("thread_id") == owner and segments[-1].get("status") == "ACTIVE":
        return segments[-1]
    segment = {
        "thread_id": owner,
        "status": "ACTIVE",
        "started_at": now_iso(),
        "baseline_total_tokens": 0,
        "latest_total_tokens": 0,
        "token_delta": 0,
        "model_wakes": 0,
        "tool_calls": 0,
        "context_compactions": 0,
    }
    if segments and isinstance(segments[-1], dict) and segments[-1].get("status") == "ACTIVE":
        segments[-1]["status"] = "SUPERSEDED"
        segments[-1]["ended_at"] = now_iso()
    segments.append(segment)
    return segment


def rotation_reasons(segment: dict[str, Any], thresholds: dict[str, Any]) -> tuple[list[str], list[str]]:
    soft: list[str] = []
    hard: list[str] = []
    token_delta = int(segment.get("token_delta", 0))
    model_wakes = int(segment.get("model_wakes", 0))
    tool_calls = int(segment.get("tool_calls", 0))
    compactions = int(segment.get("context_compactions", 0))
    if token_delta >= int(thresholds["soft_total_tokens"]):
        soft.append("orchestrator token soft limit reached")
    if token_delta >= int(thresholds["hard_total_tokens"]):
        hard.append("orchestrator token hard limit reached")
    if model_wakes >= int(thresholds["max_model_wakes"]):
        hard.append("orchestrator model-wake limit reached")
    if tool_calls >= int(thresholds["max_tool_calls"]):
        hard.append("orchestrator tool-call limit reached")
    if compactions >= int(thresholds["max_context_compactions"]):
        hard.append("orchestrator context-compaction limit reached")
    return soft, hard


def refresh_rotation(control_plane: dict[str, Any], owner: str) -> dict[str, Any]:
    segment = current_segment(control_plane, owner)
    soft, hard = rotation_reasons(segment, control_plane["thresholds"])
    pending = control_plane.get("rotation") if isinstance(control_plane.get("rotation"), dict) else {}
    if pending.get("status") == "PREPARED":
        status = "PREPARED"
    elif hard:
        status = "REQUIRED"
    elif soft:
        status = "SOFT_WARNING"
    else:
        status = "CLEAR"
    rotation = {
        "status": status,
        "reasons": hard or soft,
        "evaluated_at": now_iso(),
        "thread_id": owner,
        "token_delta": int(segment.get("token_delta", 0)),
        "model_wakes": int(segment.get("model_wakes", 0)),
        "tool_calls": int(segment.get("tool_calls", 0)),
        "context_compactions": int(segment.get("context_compactions", 0)),
    }
    control_plane["rotation"] = rotation
    return rotation


def compact_gate(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        key: value.get(key)
        for key in ("gate_id", "kind", "ticket_id", "revision", "status")
        if value.get(key) is not None
    }


def compact_context_packet(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        key: value.get(key)
        for key in ("format", "reference", "sha256", "byte_count", "profile_revision", "exact_base", "exact_head")
        if value.get(key) is not None
    }


def compact_action(action: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "action", "phase_key", "phase_keys", "kind", "ticket_id", "tickets",
        "model", "reasoning_effort", "base", "branch", "thread_id",
        "head_commit", "collection_id", "deadline_at", "model_tokens",
        "required_visibility", "completion_callback", "target_thread_id",
        "watcher_id", "allowed_replacements",
    )
    result = {key: action.get(key) for key in allowed if action.get(key) is not None}
    result["executor_kind"] = orchestration_metrics.classify_action(str(action.get("action")))
    if "gate" in action:
        result["gate"] = compact_gate(action.get("gate"))
    if "context_packet" in action:
        result["context_packet"] = compact_context_packet(action.get("context_packet"))
    if "anomalies" in action and isinstance(action["anomalies"], list):
        result["anomalies"] = [
            {
                key: item.get(key)
                for key in ("anomaly_id", "phase_key", "ticket_id", "reason", "status")
                if isinstance(item, dict) and item.get(key) is not None
            }
            for item in action["anomalies"]
        ]
    return result


def wake_kind(
    actions: list[dict[str, Any]], rotation: dict[str, Any], pending_handoff: Any,
    supervision: dict[str, Any],
) -> str:
    if isinstance(pending_handoff, dict) and pending_handoff.get("status") == "PREPARED":
        return "COMPLETE_CONTROLLED_HANDOFF"
    names = {str(item.get("action")) for item in actions}
    if (not names or names.issubset(WAIT_ACTIONS)) and not supervision.get("orchestrator_status_required"):
        return "NO_MODEL_WAKE"
    if rotation.get("status") == "REQUIRED":
        return "ROTATE_ORCHESTRATOR"
    if supervision.get("orchestrator_status_required"):
        return "WAKE_ADAPTER"
    executor_kinds = {orchestration_metrics.classify_action(name) for name in names}
    if "technical-model" in executor_kinds:
        return "WAKE_TECHNICAL_DECISION"
    if "adapter" in executor_kinds:
        return "WAKE_ADAPTER"
    if executor_kinds == {"deterministic"}:
        return "RUN_DETERMINISTIC"
    raise RunnerError("action executor taxonomy did not produce a wake class")


def build_packet(state: dict[str, Any], control_plane: dict[str, Any]) -> dict[str, Any]:
    proc = train_controller.procedure(state)
    owner = current_owner(state)
    require(owner is not None, "orchestrator lease has no owner")
    actions = [compact_action(item) for item in train_controller.next_actions(state)]
    supervision = train_controller.supervision_projection(state, actions)
    rotation = refresh_rotation(control_plane, owner)
    pending_action = state.get("pending_human_action")
    if isinstance(pending_action, dict):
        pending_action = {
            key: pending_action.get(key)
            for key in (
                "gate_id", "gate_type", "ticket_id", "revision", "reason",
                "decision_summary", "blocked_scope", "continuing_scope",
                "accepted_replies", "notification_status",
            )
            if pending_action.get(key) is not None
        }
    packet = {
        "format": PACKET_FORMAT,
        "run_id": state.get("run_id"),
        "run_fingerprint": (state.get("run_identity") or {}).get("fingerprint"),
        "controller_revision": proc.get("revision"),
        "controller_updated_at": proc.get("updated_at"),
        "owner_thread_id": owner,
        "wake_kind": wake_kind(
            actions, rotation, state.get("pending_orchestrator_handoff"), supervision
        ),
        "ticket_states": {
            ticket_id: value.get("status")
            for ticket_id, value in proc.get("tickets", {}).items()
            if isinstance(value, dict)
        },
        "active_phases": train_controller.active_phase_inventory(state),
        "supervision_projection": supervision,
        "pending_human_action": pending_action,
        "finalization_status": (proc.get("finalization") or {}).get("status"),
        "cost_anomaly_count": len(train_controller.unresolved_cost_anomalies(proc)),
        "orchestrator_budget": {
            key: rotation.get(key)
            for key in (
                "status", "reasons", "thread_id", "token_delta", "model_wakes",
                "tool_calls", "context_compactions",
            )
        },
        "pending_orchestrator_handoff": state.get("pending_orchestrator_handoff"),
        "next_actions": actions,
    }
    return packet


def write_packet(state_path: Path, control_plane: dict[str, Any], packet: dict[str, Any], output_dir: Path | None) -> dict[str, Any]:
    semantic_hash = sha256_json(packet)
    if semantic_hash == control_plane.get("last_semantic_hash"):
        control_plane["suppressed_unchanged_observations"] = int(control_plane.get("suppressed_unchanged_observations", 0)) + 1
        return {
            "status": "unchanged-suppressed",
            "wake_kind": "NO_MODEL_WAKE",
            "semantic_hash": semantic_hash,
            "packet_reference": control_plane.get("last_packet_reference"),
        }
    sequence = int(control_plane.get("packet_sequence", 0)) + 1
    stamped = {**packet, "packet_sequence": sequence, "generated_at": now_iso(), "semantic_hash": semantic_hash}
    encoded = json.dumps(stamped, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    maximum = int(control_plane["thresholds"]["packet_max_bytes"])
    require(len(encoded) <= maximum, f"decision packet exceeds {maximum} bytes")
    root = output_dir.expanduser().resolve() if output_dir else state_path.parent / "control-plane" / "wake-packets"
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"{sequence:06d}-{semantic_hash[:12]}.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_bytes(encoded)
    temporary.replace(destination)
    control_plane["packet_sequence"] = sequence
    control_plane["last_semantic_hash"] = semantic_hash
    control_plane["last_packet_reference"] = str(destination)
    control_plane["last_packet_bytes"] = len(encoded)
    control_plane["last_packet_created_at"] = stamped["generated_at"]
    return {
        "status": "packet-written",
        "wake_kind": packet["wake_kind"],
        "semantic_hash": semantic_hash,
        "packet_reference": str(destination),
        "packet_bytes": len(encoded),
        "controller_revision": packet["controller_revision"],
    }


def step(args: argparse.Namespace) -> int:
    path = args.state.expanduser().resolve()
    with run_registry.directory_lock(path.parent):
        state = run_registry.load_json(path)
        train_controller.migrate_procedure(state)
        control_plane = ensure_control_plane(state)
        packet = build_packet(state, control_plane)
        result = write_packet(path, control_plane, packet, args.output_dir)
        metrics = orchestration_metrics.ensure_metrics(state)
        if result["status"] == "packet-written":
            wake_counts = metrics["decision_packets_by_wake_kind"]
            wake_kind = str(result["wake_kind"])
            wake_counts[wake_kind] = int(wake_counts.get(wake_kind, 0)) + 1
            metrics["updated_at"] = now_iso()
        state["manifest_updated_at"] = now_iso()
        run_registry.save_json(path, state)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0


def record_activity(args: argparse.Namespace) -> int:
    path = args.state.expanduser().resolve()
    with run_registry.directory_lock(path.parent):
        state = run_registry.load_json(path)
        owner = current_owner(state)
        require(owner == args.thread_id, "activity thread must own the orchestrator lease")
        control_plane = ensure_control_plane(state)
        segment = current_segment(control_plane, args.thread_id)
        baseline = int(args.baseline_total_tokens)
        latest = int(args.latest_total_tokens)
        require(latest >= baseline >= 0, "token counters must satisfy latest >= baseline >= 0")
        prior_baseline = int(segment.get("baseline_total_tokens", 0))
        if segment.get("measured_at") is not None:
            require(baseline == prior_baseline, "orchestrator segment baseline cannot change")
        for label, value in (
            ("model_wakes", args.model_wakes),
            ("tool_calls", args.tool_calls),
            ("context_compactions", args.context_compactions),
        ):
            require(int(value) >= int(segment.get(label, 0)), f"{label} cannot decrease")
        segment.update({
            "baseline_total_tokens": baseline,
            "latest_total_tokens": latest,
            "token_delta": latest - baseline,
            "model_wakes": int(args.model_wakes),
            "tool_calls": int(args.tool_calls),
            "context_compactions": int(args.context_compactions),
            "measured_at": now_iso(),
        })
        rotation = refresh_rotation(control_plane, args.thread_id)
        state["manifest_updated_at"] = now_iso()
        run_registry.save_json(path, state)
    output = {"status": "recorded", "thread_id": args.thread_id, "segment": segment, "rotation": rotation}
    sys.stdout.write(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    run_step = commands.add_parser("step")
    run_step.add_argument("--state", type=Path, required=True)
    run_step.add_argument("--output-dir", type=Path)
    run_step.set_defaults(handler=step)

    activity = commands.add_parser("record-activity")
    activity.add_argument("--state", type=Path, required=True)
    activity.add_argument("--thread-id", required=True)
    activity.add_argument("--baseline-total-tokens", type=int, required=True)
    activity.add_argument("--latest-total-tokens", type=int, required=True)
    activity.add_argument("--model-wakes", type=int, required=True)
    activity.add_argument("--tool-calls", type=int, required=True)
    activity.add_argument("--context-compactions", type=int, required=True)
    activity.set_defaults(handler=record_activity)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.handler(args))
    except (OSError, json.JSONDecodeError, RunnerError, ValueError) as error:
        sys.stderr.write(json.dumps({"status": "rejected", "error": str(error)}, ensure_ascii=False) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
