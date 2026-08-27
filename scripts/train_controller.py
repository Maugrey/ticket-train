#!/usr/bin/env python3
"""Deterministic state machine for the ticket-train control plane.

The controller owns sequencing and gate enforcement. Codex threads remain
responsible for technical judgment and return structured evidence as events.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import run_registry


CRITICALITIES = ("LOW", "NORMAL", "HIGH", "CRITICAL")
COMPLEXITIES = ("LOW", "MEDIUM", "HIGH", "MAXIMUM")
ACTIVE_PHASE_STATES = {"INTENT_RECORDED", "QUEUED", "RUNNING", "LAUNCH_UNKNOWN"}
INPUT_PHASE_STATES = {"NEEDS_INPUT", "INPUT_READY"}
TERMINAL_TICKET_STATES = {"ANALYSIS_REPORTED", "MERGED_INTO_TRAIN", "BLOCKED", "FAILED", "CANCELLED"}
MAX_COMPACT_CONTEXT_BYTES = 65_536
SHA256_HEX_LENGTH = 64
AUTOMATIC_REMEDIATION_CYCLE_LIMIT = 2
EXCEPTIONAL_REMEDIATION_CYCLE_INCREMENT = 1
ENVIRONMENT_PROFILES = ("generic", "unity-mcp-local")
UNITY_REQUIREMENTS = ("none", "editor-read", "editor-write", "playmode-ui", "build")
UNITY_REQUIREMENT_ORDER = {value: index for index, value in enumerate(UNITY_REQUIREMENTS)}
DEFAULT_MAX_UNITY_EDITORS = 3
MAX_CONFIGURABLE_UNITY_EDITORS = 16
ROUTING_POLICY_VERSION = "2026-08-27-v2"
MECHANICAL_FAST_PATH_FIELDS = (
    "fully_specified",
    "direct_deterministic_oracle",
    "no_shared_contract",
    "no_critical_invariant",
    "no_migration",
    "no_concurrency",
    "no_access_boundary",
    "reversible",
    "immediately_detectable",
)

ANALYSIS_MATRIX = {
    "LOW": ("Terra/M", "Terra/H", "Sol/H", "Sol/XH"),
    "NORMAL": ("Terra/H", "Sol/M", "Sol/H", "Sol/XH"),
    "HIGH": ("Sol/H", "Sol/H", "Sol/XH", "Sol/XH"),
    "CRITICAL": ("Sol/XH", "Sol/XH", "Sol/XH", "Sol/Max"),
}
IMPLEMENTATION_MATRIX = {
    "LOW": ("Terra/M", "Terra/M", "Sol/H", "Sol/XH"),
    "NORMAL": ("Terra/M", "Terra/H", "Sol/H", "Sol/XH"),
    "HIGH": ("Sol/M", "Sol/H", "Sol/XH", "Sol/XH"),
    "CRITICAL": ("Sol/H", "Sol/XH", "Sol/XH", "Sol/Max"),
}
ACCEPTANCE_MATRIX = {
    "LOW": ("Terra/M", "Terra/H", "Sol/H", "Sol/XH"),
    "NORMAL": ("Terra/H", "Terra/H", "Sol/H", "Sol/XH"),
    "HIGH": ("Sol/H", "Sol/H", "Sol/XH", "Sol/XH"),
    "CRITICAL": ("Sol/H", "Sol/XH", "Sol/XH", "Sol/Max"),
}
INITIAL_REVIEW_MATRIX = {
    "LOW": ("Terra/M", "Terra/H", "Sol/H", "Sol/XH"),
    "NORMAL": ("Terra/H", "Sol/H", "Sol/H", "Sol/XH"),
    "HIGH": ("Sol/H", "Sol/XH", "Sol/XH", "Sol/XH"),
    "CRITICAL": ("Sol/XH", "Sol/XH", "Sol/XH", "Sol/Max"),
}
FOLLOWUP_REVIEW_MATRIX = {
    "LOW": ("Terra/M", "Terra/H", "Sol/H", "Sol/XH"),
    "NORMAL": ("Terra/H", "Sol/H", "Sol/H", "Sol/XH"),
    "HIGH": ("Sol/H", "Sol/H", "Sol/XH", "Sol/XH"),
    "CRITICAL": ("Sol/H", "Sol/XH", "Sol/XH", "Sol/Max"),
}
REMEDIATION_MATRIX = IMPLEMENTATION_MATRIX
FINAL_REVIEW_MATRIX = INITIAL_REVIEW_MATRIX
SETTING_NAMES = {
    "Luna/M": ("gpt-5.6-luna", "medium"),
    "Terra/M": ("gpt-5.6-terra", "medium"),
    "Terra/H": ("gpt-5.6-terra", "high"),
    "Sol/M": ("gpt-5.6-sol", "medium"),
    "Sol/H": ("gpt-5.6-sol", "high"),
    "Sol/XH": ("gpt-5.6-sol", "xhigh"),
    "Sol/Max": ("gpt-5.6-sol", "max"),
}

# Capability is phase-specific. These tables intentionally avoid claiming a
# global order between Luna, Terra/High, and Sol/Medium.
ANALYSIS_ROUTE_COVERAGE = {
    "Terra/M": {"Terra/M"},
    "Terra/H": {"Terra/M", "Terra/H"},
    "Sol/M": {"Terra/M", "Sol/M"},
    "Sol/H": {"Terra/M", "Terra/H", "Sol/M", "Sol/H"},
    "Sol/XH": {"Terra/M", "Terra/H", "Sol/M", "Sol/H", "Sol/XH"},
    "Sol/Max": {"Terra/M", "Terra/H", "Sol/M", "Sol/H", "Sol/XH", "Sol/Max"},
}
FOLLOWUP_CEILING_COMPATIBILITY = {
    "Terra/M": {"Terra/M"},
    "Terra/H": {"Terra/M", "Terra/H"},
    "Sol/H": {"Terra/M", "Terra/H", "Sol/H"},
    "Sol/XH": {"Terra/M", "Terra/H", "Sol/H", "Sol/XH"},
    "Sol/Max": {"Terra/M", "Terra/H", "Sol/H", "Sol/XH", "Sol/Max"},
}


class ControllerError(ValueError):
    """Raised when an event violates the procedure."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ControllerError(f"invalid {label}: {value}") from error
    require(parsed.tzinfo is not None, f"{label} must include a timezone")
    return parsed


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ControllerError(message)


def require_fields(value: dict[str, Any], fields: tuple[str, ...], label: str) -> None:
    missing = [field for field in fields if value.get(field) in (None, "", [])]
    require(not missing, f"{label} is missing: {', '.join(missing)}")


def procedure(state: dict[str, Any]) -> dict[str, Any]:
    value = state.get("procedure")
    require(isinstance(value, dict), "manifest has not been bootstrapped by train_controller.py")
    return value


def ticket(proc: dict[str, Any], ticket_id: str) -> dict[str, Any]:
    tickets = proc.get("tickets")
    require(isinstance(tickets, dict) and ticket_id in tickets, f"unknown ticket: {ticket_id}")
    value = tickets[ticket_id]
    require(isinstance(value, dict), f"invalid ticket state: {ticket_id}")
    return value


def setting_from_matrix(
    matrix: dict[str, tuple[str, str, str, str]], criticality: str, complexity: str
) -> tuple[str, str]:
    require(criticality in CRITICALITIES, f"invalid criticality: {criticality}")
    require(complexity in COMPLEXITIES, f"invalid complexity: {complexity}")
    return SETTING_NAMES[matrix[criticality][COMPLEXITIES.index(complexity)]]


def setting_name(setting: tuple[str, str]) -> str:
    matches = [name for name, value in SETTING_NAMES.items() if value == setting]
    require(len(matches) == 1, f"unsupported or ambiguous setting: {setting}")
    return matches[0]


def routed_setting(
    matrix: dict[str, tuple[str, str, str, str]],
    criticality: str,
    complexity: str,
    reasoning_authorized: bool,
) -> tuple[str, str, str]:
    model, effort = setting_from_matrix(matrix, criticality, complexity)
    if effort == "max" and not reasoning_authorized:
        return "gpt-5.6-sol", "xhigh", "documented-fallback"
    return model, effort, "conformant"


def mechanical_fast_path(
    event: dict[str, Any], *, criticality: str, complexity: str, field: str
) -> bool:
    evidence = event.get(field)
    if evidence is None:
        return False
    require(criticality == "LOW" and complexity == "LOW", f"{field} requires LOW/LOW")
    require(isinstance(evidence, dict), f"{field} must be an object")
    require(
        all(evidence.get(key) is True for key in MECHANICAL_FAST_PATH_FIELDS),
        f"{field} is missing a required mechanical fast-path proof",
    )
    return True


def routed_phase_setting(
    matrix: dict[str, tuple[str, str, str, str]],
    criticality: str,
    complexity: str,
    reasoning_authorized: bool,
    *,
    event: dict[str, Any] | None = None,
    fast_path_field: str | None = None,
) -> tuple[str, str, str]:
    if event is not None and fast_path_field and mechanical_fast_path(
        event, criticality=criticality, complexity=complexity, field=fast_path_field
    ):
        return "gpt-5.6-luna", "medium", "conformant"
    return routed_setting(matrix, criticality, complexity, reasoning_authorized)


def triage_setting(event: dict[str, Any]) -> tuple[str, str, str]:
    profile = event.get("triage_profile", "standard")
    require(profile in {"mechanical", "standard", "sensitive"}, "invalid triage profile")
    if profile == "mechanical":
        evidence = event.get("mechanical_fast_path")
        require(isinstance(evidence, dict), "mechanical triage requires fast-path evidence")
        require(
            all(evidence.get(key) is True for key in MECHANICAL_FAST_PATH_FIELDS),
            "mechanical triage fast-path evidence is incomplete",
        )
        return "gpt-5.6-luna", "medium", "conformant"
    if profile == "sensitive":
        reasons = event.get("triage_escalation_reasons")
        require(isinstance(reasons, list) and reasons, "sensitive triage requires escalation reasons")
        return "gpt-5.6-terra", "high", "conformant"
    require(not event.get("triage_escalation_reasons"), "standard triage cannot carry escalation reasons")
    return "gpt-5.6-terra", "medium", "conformant"


def plan_contract_setting(event: dict[str, Any]) -> tuple[str, str, str]:
    profile = event.get("contract_validation_profile", "standard")
    require(profile in {"standard", "sensitive"}, "invalid contract-validation profile")
    if profile == "sensitive":
        reasons = event.get("contract_validation_escalation_reasons")
        require(isinstance(reasons, list) and reasons, "sensitive contract validation requires reasons")
        return "gpt-5.6-terra", "high", "conformant"
    require(
        not event.get("contract_validation_escalation_reasons"),
        "standard contract validation cannot carry escalation reasons",
    )
    return "gpt-5.6-terra", "medium", "conformant"


def validate_reasoning_authorization(
    proc: dict[str, Any], event: dict[str, Any], expected: tuple[str, str, str],
    *, stage: str, ticket_id: str | None, head: str | None,
    authorization_field: str = "reasoning_authorization_id",
) -> None:
    if expected[1] != "max":
        require(not event.get(authorization_field), "reasoning authorization supplied for a non-Max route")
        return
    authorization_id = event.get(authorization_field)
    require(bool(authorization_id), "Sol/Max requires a scoped reasoning authorization")
    authorizations = proc.get("reasoning_authorizations", {})
    authorization = authorizations.get(authorization_id) if isinstance(authorizations, dict) else None
    require(isinstance(authorization, dict), "unknown reasoning authorization")
    require(authorization.get("status") == "ACTIVE", "reasoning authorization is not active")
    require(authorization.get("stage") == stage, "reasoning authorization stage mismatch")
    require(authorization.get("ticket_id") in {ticket_id, "*"}, "reasoning authorization ticket mismatch")
    require(authorization.get("head") in {None, "*", head}, "reasoning authorization head mismatch")


def route_is_covered(
    coverage: dict[str, set[str]], actual: tuple[str, str], required: tuple[str, str]
) -> bool:
    actual_name = setting_name(actual)
    required_name = setting_name(required)
    return required_name in coverage.get(actual_name, set())


def latest_trustworthy_full_review(item: dict[str, Any]) -> dict[str, Any] | None:
    for review in reversed(item.get("reviews", [])):
        if (
            review.get("review_kind") == "full"
            and review.get("routing_status", "conformant") in {"conformant", "documented-fallback"}
            and not review.get("stale_due_to_head_drift")
        ):
            return review
    return None


def strongest_review_setting(settings: list[tuple[str, str]]) -> tuple[str, str] | None:
    strongest: tuple[str, str] | None = None
    for candidate in settings:
        candidate_name = setting_name(candidate)
        require(candidate_name in FOLLOWUP_CEILING_COMPATIBILITY, "setting is not valid for review comparison")
        if strongest is None:
            strongest = candidate
            continue
        strongest_name = setting_name(strongest)
        if candidate_name not in FOLLOWUP_CEILING_COMPATIBILITY[strongest_name]:
            require(
                strongest_name in FOLLOWUP_CEILING_COMPATIBILITY[candidate_name],
                "review settings are incomparable",
            )
            strongest = candidate
    return strongest


def higher_classification(original: str, candidate: str, levels: tuple[str, ...]) -> str:
    require(original in levels and candidate in levels, "invalid classification value")
    return candidate if levels.index(candidate) >= levels.index(original) else original


def finalize_analysis_gate(proc: dict[str, Any], item: dict[str, Any]) -> None:
    analysis = item["analysis"]
    required = analysis_human_gate(analysis["criticality"], analysis["complexity"], proc["approval_mode"])
    item["analysis_gate_required"] = required
    if required:
        gate_id = f"{analysis['ticket_id']}:analysis:{analysis['analysis_revision']}"
        create_gate(
            proc,
            gate_id=gate_id,
            kind="analysis",
            ticket_id=analysis["ticket_id"],
            revision=analysis["analysis_revision"],
        )
        item["analysis_gate_id"] = gate_id
        item["status"] = "AWAITING_ANALYSIS_APPROVAL"
    else:
        item["status"] = "ANALYZED"


def analysis_human_gate(criticality: str, complexity: str, approval_mode: str) -> bool:
    if approval_mode in {"auto-analysis", "full-auto"}:
        return False
    return criticality == "CRITICAL" or (criticality == "HIGH" and complexity == "MAXIMUM")


def merge_human_gate(criticality: str, complexity: str, approval_mode: str) -> bool:
    if approval_mode in {"auto-merge", "full-auto"}:
        return False
    return (
        criticality in {"HIGH", "CRITICAL"}
        or complexity == "MAXIMUM"
        or (criticality == "NORMAL" and complexity == "HIGH")
    )


def validate_routing(
    event: dict[str, Any], expected: tuple[str, str, str], prefix: str = ""
) -> None:
    model_field = f"{prefix}model"
    effort_field = f"{prefix}reasoning_effort"
    conformance_field = f"{prefix}routing_conformance"
    expected_model, expected_effort, expected_conformance = expected
    require(event.get(model_field) == expected_model, f"{model_field} must be {expected_model}")
    require(event.get(effort_field) == expected_effort, f"{effort_field} must be {expected_effort}")
    require(
        event.get(conformance_field, expected_conformance) == expected_conformance,
        f"{conformance_field} must be {expected_conformance}",
    )


def require_sha256(value: Any, label: str) -> None:
    require(
        isinstance(value, str)
        and len(value) == SHA256_HEX_LENGTH
        and all(character in "0123456789abcdef" for character in value.lower()),
        f"{label} must be a SHA-256 hex digest",
    )


def validate_compact_context(event: dict[str, Any], label: str) -> dict[str, Any]:
    packet = event.get("context_packet")
    require(isinstance(packet, dict), f"{label} requires a fresh compact context packet")
    require_fields(
        packet,
        (
            "format", "reference", "sha256", "byte_count", "profile_revision",
            "exact_base", "exact_head",
        ),
        f"{label} context packet",
    )
    require(packet["format"] == "fresh-compact-v1", f"{label} context packet format is invalid")
    require_sha256(packet["sha256"], f"{label} context packet sha256")
    require(
        isinstance(packet["byte_count"], int)
        and not isinstance(packet["byte_count"], bool)
        and 0 < packet["byte_count"] <= MAX_COMPACT_CONTEXT_BYTES,
        f"{label} context packet exceeds the {MAX_COMPACT_CONTEXT_BYTES}-byte budget",
    )
    require(packet.get("history_turns_included", 0) == 0, f"{label} must not inherit conversation history")
    return dict(packet)


def validate_deterministic_verification(event: dict[str, Any], label: str) -> None:
    require(event.get("execution_mode") == "deterministic", f"{label} must use the deterministic runner")
    require(event.get("model_tokens") == 0, f"{label} must consume zero model tokens")
    require_fields(
        event,
        ("runner_version", "runner_result_reference", "runner_result_sha256", "command_results"),
        label,
    )
    require_sha256(event["runner_result_sha256"], f"{label} runner result sha256")
    commands = event["command_results"]
    require(isinstance(commands, list) and commands, f"{label} command_results must be non-empty")
    for command in commands:
        require(isinstance(command, dict), f"{label} command result must be an object")
        require_fields(command, ("command_id", "status", "exit_code", "duration_seconds", "log_reference"), label)
        require(command["status"] in {"passed", "failed", "timed_out"}, f"{label} command status is invalid")


def open_cost_anomaly(proc: dict[str, Any], *, phase_item: dict[str, Any], reason: str) -> None:
    control = proc.setdefault("cost_control", {})
    anomalies = control.setdefault("anomalies", [])
    anomaly_id = f"cost:{phase_item['phase_key']}:{len(anomalies) + 1}"
    anomalies.append(
        {
            "anomaly_id": anomaly_id,
            "phase_key": phase_item["phase_key"],
            "ticket_id": phase_item.get("ticket_id"),
            "reason": reason,
            "status": "OPEN",
            "detected_at": now_iso(),
        }
    )
    control["status"] = "CHECKPOINT_REQUIRED"


def unresolved_cost_anomalies(proc: dict[str, Any]) -> list[dict[str, Any]]:
    control = proc.get("cost_control") if isinstance(proc.get("cost_control"), dict) else {}
    return [item for item in control.get("anomalies", []) if item.get("status") == "OPEN"]


def usable_remediation_limit_exception(item: dict[str, Any]) -> dict[str, Any] | None:
    exception = item.get("remediation_limit_exception")
    if not isinstance(exception, dict):
        return None
    if (
        exception.get("status") != "GRANTED"
        or exception.get("additional_cycles") != EXCEPTIONAL_REMEDIATION_CYCLE_INCREMENT
    ):
        return None
    return exception


def phase(proc: dict[str, Any], phase_key: str) -> dict[str, Any]:
    phases = proc.get("phases")
    require(isinstance(phases, dict) and phase_key in phases, f"unknown phase: {phase_key}")
    value = phases[phase_key]
    require(isinstance(value, dict), f"invalid phase: {phase_key}")
    return value


def environment(proc: dict[str, Any]) -> dict[str, Any]:
    value = proc.get("environment")
    if value is None:
        return {"profile": "generic", "status": "NOT_APPLICABLE", "mcp_mode": None, "slots": {}}
    require(isinstance(value, dict), "procedure.environment must be an object")
    return value


def uses_unity_mcp(proc: dict[str, Any]) -> bool:
    return environment(proc).get("profile") == "unity-mcp-local"


def validate_unity_requirement(value: Any, label: str) -> str:
    require(value in UNITY_REQUIREMENTS, f"{label} must be one of: {', '.join(UNITY_REQUIREMENTS)}")
    return str(value)


def event_unity_requirement(
    proc: dict[str, Any], event: dict[str, Any], field: str = "unity_requirement"
) -> str:
    if not uses_unity_mcp(proc):
        requirement = validate_unity_requirement(event.get(field, "none"), field)
        require(requirement == "none", f"{field} requires the unity-mcp-local environment profile")
        return requirement
    require(field in event, f"{field} is required by the unity-mcp-local environment profile")
    return validate_unity_requirement(event[field], field)


def unity_slots(proc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    slots = environment(proc).get("slots", {})
    require(isinstance(slots, dict), "Unity environment slots must be an object")
    return slots


def unity_lease_for_owner(proc: dict[str, Any], owner_key: str) -> tuple[str, dict[str, Any]] | None:
    if not uses_unity_mcp(proc):
        return None
    for slot_id, slot in unity_slots(proc).items():
        lease = slot.get("lease") if isinstance(slot, dict) else None
        if isinstance(lease, dict) and lease.get("owner_key") == owner_key:
            return slot_id, lease
    return None


def active_unity_leases(proc: dict[str, Any]) -> int:
    if not uses_unity_mcp(proc):
        return 0
    return sum(
        1
        for slot in unity_slots(proc).values()
        if isinstance(slot, dict) and isinstance(slot.get("lease"), dict)
    )


def strongest_unity_requirement(values: list[str]) -> str:
    normalized = [validate_unity_requirement(value, "Unity requirement") for value in values]
    return max(normalized or ["none"], key=lambda value: UNITY_REQUIREMENT_ORDER[value])


def expected_unity_operation(proc: dict[str, Any], owner_key: str) -> tuple[str, str | None, str]:
    if owner_key.startswith("ticket:") and owner_key.endswith(":verification"):
        ticket_id = owner_key[len("ticket:") : -len(":verification")]
        item = ticket(proc, ticket_id)
        require(
            item.get("status") in {"AWAITING_VERIFICATION", "AWAITING_REMEDIATION_VERIFICATION"},
            "ticket verification is not ready for a Unity slot",
        )
        execution = item.get("execution") or {}
        requirement = validate_unity_requirement(
            execution.get("verification_unity_requirement", "none"),
            "ticket verification Unity requirement",
        )
        head = None
        branch = execution.get("implementation_branch")
        if item.get("status") == "AWAITING_REMEDIATION_VERIFICATION":
            remediations = [
                value for value in proc.get("phases", {}).values()
                if value.get("ticket_id") == ticket_id
                and value.get("kind") == "remediation"
                and value.get("launch_state") == "COMPLETED"
            ]
            if remediations:
                latest = remediations[-1]
                head = ((latest.get("completion_envelope") or {}).get("artifacts") or {}).get("commit")
                branch = latest.get("branch")
        if not head and item.get("status") == "AWAITING_VERIFICATION":
            head = execution.get("integrated_head")
        if not head:
            implementation = phase(proc, execution["implementation_phase_key"])
            envelope = implementation.get("completion_envelope") or {}
            head = (envelope.get("artifacts") or {}).get("commit")
        require(bool(head), "ticket verification Unity slot requires an exact expected head")
        return requirement, branch, str(head)
    if owner_key == "run:final-verification":
        final = proc.get("finalization", {})
        pull_request = final.get("pull_request") or {}
        require(bool(pull_request.get("head_commit")), "final verification Unity slot requires a final PR head")
        requirement = final.get("verification_unity_requirement")
        if requirement is None:
            requirement = strongest_unity_requirement([
                (item.get("execution") or {}).get("verification_unity_requirement", "none")
                for item in proc.get("tickets", {}).values()
                if isinstance(item, dict) and item.get("status") == "MERGED_INTO_TRAIN"
            ])
        return validate_unity_requirement(requirement, "final verification Unity requirement"), None, str(pull_request["head_commit"])
    raise ControllerError(f"unknown Unity slot owner: {owner_key}")


def add_phase(
    proc: dict[str, Any], *, key: str, ticket_id: str | None, kind: str,
    model: str, effort: str, branch: str | None, base: str, scope: str | None = None,
    context_packet: dict[str, Any] | None = None, unity_requirement: str = "none",
) -> None:
    phases = proc.setdefault("phases", {})
    require(isinstance(phases, dict), "procedure.phases must be an object")
    require(key not in phases, f"phase already exists: {key}")
    phases[key] = {
        "phase_key": key,
        "ticket_id": ticket_id,
        "kind": kind,
        "scope": scope,
        "launch_state": "INTENT_RECORDED",
        "requested_model": model,
        "requested_reasoning_effort": effort,
        "branch": branch,
        "base": base,
        "thread_id": None,
        "client_thread_id": None,
        "visibility_verified": False,
        "execution_visibility": None,
        "context_packet": context_packet,
        "unity_requirement": validate_unity_requirement(unity_requirement, "phase Unity requirement"),
        "unity_slot_lease": None,
        "completion_envelope": None,
        "created_at": now_iso(),
    }


def create_gate(
    proc: dict[str, Any], *, gate_id: str, kind: str, ticket_id: str, revision: str,
    phase_key: str | None = None, prior_ticket_status: str | None = None,
) -> dict[str, Any]:
    gates = proc.setdefault("human_gates", {})
    require(isinstance(gates, dict), "procedure.human_gates must be an object")
    require(gate_id not in gates, f"gate already exists: {gate_id}")
    gates[gate_id] = {
        "gate_id": gate_id,
        "kind": kind,
        "ticket_id": ticket_id,
        "revision": revision,
        "phase_key": phase_key,
        "prior_ticket_status": prior_ticket_status,
        "status": "PENDING_UNANNOUNCED",
    }
    return gates[gate_id]


def active_execution_pairs(proc: dict[str, Any]) -> int:
    return sum(
        1
        for value in proc.get("tickets", {}).values()
        if isinstance(value, dict) and value.get("status") == "EXECUTION_PAIR_RUNNING"
    )


def dependencies_satisfied(proc: dict[str, Any], item: dict[str, Any]) -> bool:
    for dependency in item.get("hard_dependencies", []):
        if ticket(proc, dependency).get("status") != "MERGED_INTO_TRAIN":
            return False
    return True


def complete_phase(event: dict[str, Any], proc: dict[str, Any]) -> dict[str, Any]:
    item = phase(proc, str(event.get("phase_key") or ""))
    require(item.get("launch_state") == "RUNNING", "only a verified running phase may complete")
    envelope = event.get("envelope")
    require(isinstance(envelope, dict), "PHASE_COMPLETED requires envelope")
    require_fields(
        envelope,
        (
            "phase_key", "phase_status", "actual_model", "actual_reasoning_effort",
            "result_summary", "artifacts", "tests_and_checks", "residual_risks",
            "requested_or_recommended_next_action", "files_modified", "usage",
        ),
        "completion envelope",
    )
    require(envelope["phase_key"] == item["phase_key"], "completion envelope phase_key mismatch")
    require(envelope["phase_status"] == "completed", "use PHASE_TERMINATED for a non-completed phase")
    require(envelope["actual_model"] == item["requested_model"], "actual model differs from routed model")
    require(
        envelope["actual_reasoning_effort"] == item["requested_reasoning_effort"],
        "actual reasoning effort differs from routed effort",
    )
    usage = envelope.get("usage")
    require(isinstance(usage, dict), "completion envelope usage must be an object")
    require(usage.get("measurement") in {"complete", "partial", "unavailable"}, "invalid phase usage measurement")
    if usage.get("measurement") == "complete":
        require(
            isinstance(usage.get("total_tokens"), int)
            and not isinstance(usage.get("total_tokens"), bool)
            and usage["total_tokens"] >= 0,
            "complete phase usage requires a non-negative total_tokens value",
        )
        token_limit = int(proc.get("cost_control", {}).get("phase_token_limit", 50_000_000))
        if usage["total_tokens"] > token_limit:
            open_cost_anomaly(
                proc,
                phase_item=item,
                reason=f"phase tokens {usage['total_tokens']} exceed limit {token_limit}",
            )
    compactions = usage.get("context_compactions", 0)
    require(isinstance(compactions, int) and compactions >= 0, "context_compactions must be non-negative")
    compaction_limit = int(proc.get("cost_control", {}).get("context_compaction_limit", 1))
    if compactions > compaction_limit:
        open_cost_anomaly(
            proc,
            phase_item=item,
            reason=f"context compactions {compactions} exceed limit {compaction_limit}",
        )
    if usage.get("measurement") == "complete" and item.get("scope") == "followup":
        baselines = [
            candidate
            for candidate in proc.get("phases", {}).values()
            if candidate.get("kind") == item.get("kind")
            and candidate.get("ticket_id") == item.get("ticket_id")
            and candidate.get("scope") == "initial"
            and isinstance(candidate.get("completion_envelope", {}).get("usage", {}).get("total_tokens"), int)
        ]
        if baselines:
            baseline_tokens = baselines[0]["completion_envelope"]["usage"]["total_tokens"]
            ratio_limit = float(proc.get("cost_control", {}).get("followup_to_initial_ratio_limit", 2.0))
            if baseline_tokens > 0 and usage["total_tokens"] > baseline_tokens * ratio_limit:
                open_cost_anomaly(
                    proc,
                    phase_item=item,
                    reason=(
                        f"follow-up review tokens {usage['total_tokens']} exceed "
                        f"{ratio_limit}x initial review tokens {baseline_tokens}"
                    ),
                )
    item["launch_state"] = "COMPLETED"
    item["completion_envelope"] = envelope
    item["usage_captured"] = True
    item["completed_at"] = now_iso()
    artifacts = envelope.get("artifacts") if isinstance(envelope.get("artifacts"), dict) else {}
    item["resume_head"] = artifacts.get("commit") or item.get("base")
    return item


def terminate_phase(event: dict[str, Any], proc: dict[str, Any]) -> dict[str, Any]:
    item = phase(proc, str(event.get("phase_key") or ""))
    require(item.get("launch_state") == "RUNNING", "only a verified running phase may terminate")
    envelope = event.get("envelope")
    require(isinstance(envelope, dict), "PHASE_TERMINATED requires envelope")
    require_fields(
        envelope,
        (
            "phase_key", "phase_status", "actual_model", "actual_reasoning_effort",
            "result_summary", "artifacts", "tests_and_checks", "residual_risks",
            "requested_or_recommended_next_action", "files_modified", "usage",
        ),
        "termination envelope",
    )
    require(envelope["phase_key"] == item["phase_key"], "termination envelope phase_key mismatch")
    outcome = envelope["phase_status"]
    require(outcome in {"needs_input", "blocked", "failed", "cancelled"}, "invalid terminated phase status")
    require(envelope["actual_model"] == item["requested_model"], "actual model differs from routed model")
    require(
        envelope["actual_reasoning_effort"] == item["requested_reasoning_effort"],
        "actual reasoning effort differs from routed effort",
    )
    usage = envelope.get("usage")
    require(isinstance(usage, dict), "termination envelope usage must be an object")
    require(usage.get("measurement") in {"complete", "partial", "unavailable"}, "invalid phase usage measurement")
    if usage.get("measurement") == "complete":
        require(
            isinstance(usage.get("total_tokens"), int)
            and not isinstance(usage.get("total_tokens"), bool)
            and usage["total_tokens"] >= 0,
            "complete phase usage requires a non-negative total_tokens value",
        )
    item["completion_envelope"] = envelope
    item["usage_captured"] = True
    item["completed_at"] = now_iso()
    artifacts = envelope.get("artifacts") if isinstance(envelope.get("artifacts"), dict) else {}
    item["resume_head"] = artifacts.get("commit") or item.get("base")
    ticket_id = item.get("ticket_id")
    if outcome == "needs_input":
        if uses_unity_mcp(proc) and item.get("unity_requirement") != "none" and item.get("branch"):
            require(bool(artifacts.get("commit")), "editor-backed write phase must persist a clean resume commit before requesting input")
        require(ticket_id, "a run-level phase cannot request ticket input")
        request = envelope.get("input_request")
        require(isinstance(request, dict), "needs_input requires input_request")
        require_fields(
            request,
            ("gate_id", "revision", "question", "reason", "blocked_scope", "continuing_scope", "accepted_replies"),
            "input request",
        )
        ticket_item = ticket(proc, str(ticket_id))
        prior_status = ticket_item.get("status")
        gate = create_gate(
            proc, gate_id=request["gate_id"], kind="input", ticket_id=str(ticket_id),
            revision=request["revision"], phase_key=item["phase_key"],
            prior_ticket_status=prior_status,
        )
        gate.update({key: request[key] for key in (
            "question", "reason", "blocked_scope", "continuing_scope", "accepted_replies"
        )})
        item["launch_state"] = "NEEDS_INPUT"
        ticket_item["status"] = "AWAITING_REQUIRED_INPUT"
    else:
        item["launch_state"] = outcome.upper()
        if ticket_id:
            ticket(proc, str(ticket_id))["status"] = outcome.upper()
    return item


def handle_event(state: dict[str, Any], event: dict[str, Any]) -> None:
    proc = procedure(state)
    event_type = str(event.get("type") or "")

    if unresolved_cost_anomalies(proc) and event_type != "COST_ANOMALY_RESOLVED":
        raise ControllerError("resolve the open cost anomaly checkpoint before another transition")

    if event_type == "REASONING_OVERRIDE_AUTHORIZED":
        require_fields(
            event,
            ("authorization_id", "stage", "ticket_id", "user_decision_reference", "authorized_at"),
            "reasoning authorization",
        )
        require(
            event["stage"] in {
                "analysis", "analysis-route-validation", "implementation", "acceptance",
                "initial-review", "followup-review", "remediation", "final-review",
                "final-remediation",
            },
            "invalid reasoning authorization stage",
        )
        parse_iso(str(event["authorized_at"]), "reasoning authorization time")
        authorizations = proc.setdefault("reasoning_authorizations", {})
        require(event["authorization_id"] not in authorizations, "reasoning authorization already exists")
        authorizations[event["authorization_id"]] = {
            "authorization_id": event["authorization_id"],
            "stage": event["stage"],
            "ticket_id": event["ticket_id"],
            "head": event.get("head"),
            "user_decision_reference": event["user_decision_reference"],
            "authorized_at": event["authorized_at"],
            "status": "ACTIVE",
        }
        return

    if event_type == "ORCHESTRATOR_CONFIRMED":
        require(proc.get("orchestrator_confirmed") is False, "orchestrator already confirmed")
        require_fields(
            event,
            (
                "orchestration_profile", "recommended_model", "recommended_reasoning_effort",
                "current_model", "current_reasoning_effort", "confirmation_reference",
            ),
            "orchestrator confirmation",
        )
        profile = event["orchestration_profile"]
        require(profile in {"normal", "complex-recovery", "technical-arbitration"}, "invalid orchestration profile")
        recommended = {
            "normal": ("gpt-5.6-terra", "medium"),
            "complex-recovery": ("gpt-5.6-terra", "high"),
            "technical-arbitration": ("gpt-5.6-sol", "high"),
        }[profile]
        require(
            (event["recommended_model"], event["recommended_reasoning_effort"]) == recommended,
            "orchestrator recommendation does not match its profile",
        )
        proc["orchestrator_preflight"] = {
            "profile": profile,
            "recommended_model": recommended[0],
            "recommended_reasoning_effort": recommended[1],
            "current_model": event["current_model"],
            "current_reasoning_effort": event["current_reasoning_effort"],
            "confirmation_reference": event["confirmation_reference"],
        }
        proc["orchestrator_confirmed"] = True
        return

    if event_type == "SUPERVISION_CONFIGURED":
        require(proc.get("orchestrator_confirmed") is True, "confirm orchestrator first")
        mode = event.get("mode")
        require(
            mode in {"FOREGROUND_WAIT", "EVENT_CALLBACK", "BACKGROUND_WATCHER"},
            "invalid supervision mode",
        )
        if mode == "BACKGROUND_WATCHER":
            require(bool(event.get("watcher_id")), "background supervision requires watcher_id")
            require(
                event.get("watcher_consumes_model_tokens") is False,
                "background watcher must be a verified zero-model process",
            )
        if mode == "EVENT_CALLBACK":
            owner_thread_id = (state.get("orchestrator_lease") or {}).get("owner_thread_id")
            require(event.get("callback_verified") is True, "event callback must be verified")
            require(
                event.get("callback_target_thread_id") == owner_thread_id,
                "event callback must target the current orchestrator owner",
            )
        proc["supervision"] = {
            "status": "ACTIVE", "mode": mode, "watcher_id": event.get("watcher_id"),
            "last_check_at": event.get("last_check_at") or now_iso(),
            "next_check_at": event.get("next_check_at"),
            "callback_verified": event.get("callback_verified") is True,
            "callback_target_thread_id": event.get("callback_target_thread_id"),
            "watcher_consumes_model_tokens": event.get("watcher_consumes_model_tokens"),
        }
        state["supervision"] = dict(proc["supervision"])
        return

    if event_type == "SUPERVISION_PAUSED_FOR_HUMAN_GATE":
        supervision = proc.get("supervision", {})
        require(supervision.get("status") == "ACTIVE", "supervision is not active")
        pending_action = state.get("pending_human_action")
        require(
            isinstance(pending_action, dict)
            and pending_action.get("notification_status") == "ANNOUNCED",
            "human action must be announced before supervision can pause",
        )
        active = [
            value for value in proc.get("phases", {}).values()
            if isinstance(value, dict) and value.get("launch_state") in ACTIVE_PHASE_STATES
        ]
        require(not active, "cannot pause supervision while a technical phase is active")
        current_actions = next_actions(state)
        require(
            current_actions
            and all(action.get("action") == "AWAIT_HUMAN_GATE" for action in current_actions),
            "automatic or technical work remains while the human gate is pending",
        )
        prior_watcher_id = supervision.get("watcher_id")
        if supervision.get("mode") == "BACKGROUND_WATCHER":
            require(
                event.get("watcher_id") == prior_watcher_id,
                "paused watcher ID differs from configured supervision",
            )
        proc["supervision"] = {
            "status": "PAUSED_HUMAN_GATE",
            "mode": None,
            "watcher_id": None,
            "previous_watcher_id": prior_watcher_id,
            "paused_at": event.get("paused_at") or now_iso(),
            "pause_reason": "human-gate-only",
        }
        state["supervision"] = dict(proc["supervision"])
        return

    if event_type == "UNITY_ENVIRONMENT_CONFIGURED":
        unity = environment(proc)
        require(unity.get("profile") == "unity-mcp-local", "run does not use the unity-mcp-local profile")
        require(unity.get("status") in {"UNINITIALIZED", "CONFIGURING"}, "Unity environment is already configured")
        require_fields(
            event,
            (
                "registry_reference", "repository", "slot_root", "max_editors",
                "cli_package", "plugin_package", "slots",
            ),
            "Unity environment configuration",
        )
        require(event.get("mcp_mode") == "local", "ticket-train supports only local Unity MCP mode")
        require(event["repository"] == unity.get("repository"), "Unity slot registry belongs to another local repository")
        require(
            event["max_editors"] == proc["limits"]["max_unity_editors"],
            "Unity slot registry editor limit differs from the run limit",
        )
        require(isinstance(event["slots"], list), "Unity slots must be a list")
        require(len(event["slots"]) >= event["max_editors"], "Unity slot registry has insufficient slots")
        slots: dict[str, dict[str, Any]] = {}
        for raw_slot in event["slots"]:
            require(isinstance(raw_slot, dict), "Unity slot must be an object")
            require_fields(raw_slot, ("slot_id", "path", "status", "config_profile_sha256"), "Unity slot")
            require(raw_slot["slot_id"] not in slots, "duplicate Unity slot ID")
            require(raw_slot["status"] in {"IDLE", "READY"}, "new Unity slot must be idle or ready")
            require_sha256(raw_slot["config_profile_sha256"], "Unity slot config profile")
            slots[raw_slot["slot_id"]] = {
                "slot_id": raw_slot["slot_id"],
                "path": raw_slot["path"],
                "status": raw_slot["status"],
                "config_profile_sha256": raw_slot["config_profile_sha256"],
                "lease": None,
            }
        unity.update(
            {
                "status": "READY",
                "mcp_mode": "local",
                "registry_reference": event["registry_reference"],
                "repository": event["repository"],
                "slot_root": event["slot_root"],
                "cli_package": event["cli_package"],
                "plugin_package": event["plugin_package"],
                "slots": slots,
                "configured_at": now_iso(),
            }
        )
        return

    if event_type == "UNITY_SLOT_ACQUIRED":
        unity = environment(proc)
        require(unity.get("profile") == "unity-mcp-local" and unity.get("status") == "READY", "Unity environment is not ready")
        require_fields(
            event,
            (
                "owner_key", "slot_id", "lease_id", "requirement", "path",
                "expected_head", "observed_head", "readiness_evidence_reference",
            ),
            "Unity slot acquisition",
        )
        owner_key = str(event["owner_key"])
        requirement = validate_unity_requirement(event["requirement"], "Unity slot requirement")
        require(requirement != "none", "a no-MCP operation cannot acquire a Unity slot")
        if owner_key in proc["phases"]:
            owner_phase = phase(proc, owner_key)
            require(owner_phase.get("launch_state") in {"INTENT_RECORDED", "INPUT_READY"}, "Unity phase must acquire its slot before launch or resume")
            require(owner_phase.get("unity_requirement") == requirement, "Unity slot requirement differs from phase requirement")
            expected_branch = owner_phase.get("branch")
            expected_head = (
                owner_phase.get("resume_head")
                if owner_phase.get("launch_state") == "INPUT_READY"
                else owner_phase.get("base")
            )
        else:
            expected_requirement, expected_branch, expected_head = expected_unity_operation(proc, owner_key)
            require(expected_requirement == requirement, "Unity slot requirement differs from operation requirement")
        require(event["expected_head"] == expected_head, "Unity slot expected head differs from owner head")
        require(event["observed_head"] == expected_head, "Unity slot observed head differs from owner head")
        require(event.get("branch") == expected_branch, "Unity slot branch differs from owner branch")
        require(active_unity_leases(proc) < int(proc["limits"]["max_unity_editors"]), "Unity editor concurrency limit reached")
        slots = unity_slots(proc)
        slot = slots.get(str(event["slot_id"]))
        require(isinstance(slot, dict), "unknown Unity slot")
        require(slot.get("path") == event["path"], "Unity slot path differs from configured path")
        require(not slot.get("lease"), "Unity slot is already leased")
        lease = {
            "lease_id": event["lease_id"],
            "owner_key": owner_key,
            "requirement": requirement,
            "expected_head": expected_head,
            "branch": expected_branch,
            "readiness_evidence_reference": event["readiness_evidence_reference"],
            "acquired_at": event.get("acquired_at") or now_iso(),
        }
        slot["lease"] = lease
        slot["status"] = "LEASED"
        if owner_key in proc["phases"]:
            phase(proc, owner_key)["unity_slot_lease"] = {"slot_id": event["slot_id"], **lease}
        return

    if event_type == "UNITY_SLOT_RELEASED":
        require_fields(event, ("owner_key", "slot_id", "lease_id", "release_evidence_reference"), "Unity slot release")
        slots = unity_slots(proc)
        slot = slots.get(str(event["slot_id"]))
        require(isinstance(slot, dict) and isinstance(slot.get("lease"), dict), "Unity slot is not leased")
        lease = slot["lease"]
        require(lease.get("owner_key") == event["owner_key"], "Unity slot is owned by another operation")
        require(lease.get("lease_id") == event["lease_id"], "Unity slot lease ID mismatch")
        if event["owner_key"] in proc["phases"]:
            owner_phase = phase(proc, event["owner_key"])
            require(owner_phase.get("launch_state") not in ACTIVE_PHASE_STATES, "cannot release Unity slot while phase is active")
            owner_phase["unity_slot_released_at"] = event.get("released_at") or now_iso()
        slot["lease"] = None
        slot["status"] = event.get("slot_status", "READY")
        require(slot["status"] in {"READY", "IDLE"}, "released Unity slot status must be ready or idle")
        slot["release_evidence_reference"] = event["release_evidence_reference"]
        return

    if event_type == "PHASE_DISPATCHED":
        kind = event.get("kind")
        require(
            kind in {
                "triage", "analysis", "analysis_route_validation",
                "analysis_reconciliation", "plan_contract_validation",
            },
            "unsupported generic phase kind",
        )
        require(proc.get("supervision", {}).get("status") == "ACTIVE", "configure supervision first")
        require_fields(event, ("phase_key", "base_commit", "model", "reasoning_effort"), "phase dispatch")
        context_packet = validate_compact_context(event, f"{kind} dispatch")
        ticket_id = event.get("ticket_id")
        if kind == "triage":
            require(ticket_id in (None, "run"), "triage is a run-level batch phase")
            expected = triage_setting(event)
            validate_routing(event, expected)
            require(not any(p.get("kind") == "triage" for p in proc["phases"].values()), "batch triage already dispatched")
            ticket_id = None
        elif kind == "analysis":
            item = ticket(proc, str(ticket_id or ""))
            require(item.get("status") == "TRIAGED", "analysis dispatch requires triage")
            expected = routed_setting(
                ANALYSIS_MATRIX, item["triage"]["criticality"], item["triage"]["complexity"],
                bool(event.get("reasoning_authorized")),
            )
            validate_routing(event, expected)
            validate_reasoning_authorization(
                proc, event, expected, stage="analysis", ticket_id=str(ticket_id), head=event["base_commit"]
            )
            active_analyses = sum(
                1 for p in proc["phases"].values()
                if p.get("kind") == "analysis" and p.get("launch_state") in ACTIVE_PHASE_STATES
            )
            require(active_analyses < int(proc["limits"]["max_active_analyses"]), "analysis concurrency limit reached")
        elif kind == "analysis_route_validation":
            require(ticket_id, "analysis route validation requires ticket_id")
            item = ticket(proc, str(ticket_id))
            require(item.get("status") == "ANALYSIS_ROUTE_VALIDATION_REQUIRED", "analysis does not require route validation")
            required = item.get("analysis_route_validation_required")
            require(isinstance(required, dict), "analysis route validation requirement is missing")
            expected = routed_setting(
                ANALYSIS_MATRIX, required["criticality"], required["complexity"],
                bool(event.get("reasoning_authorized")),
            )
            validate_routing(event, expected)
            validate_reasoning_authorization(
                proc, event, expected, stage="analysis-route-validation",
                ticket_id=str(ticket_id), head=event["base_commit"],
            )
        elif kind == "plan_contract_validation":
            require(ticket_id, "plan contract validation requires ticket_id")
            item = ticket(proc, str(ticket_id))
            require(item.get("status") == "READY_FOR_IMPLEMENTATION", "ticket is not ready for contract validation")
            require(item.get("analysis", {}).get("complexity") in {"HIGH", "MAXIMUM"}, "contract validation is required only for HIGH or MAXIMUM complexity")
            expected = plan_contract_setting(event)
            validate_routing(event, expected)
        else:
            require(ticket_id, f"{kind} requires ticket_id")
            ticket(proc, str(ticket_id))
        unity_requirement = event_unity_requirement(
            proc,
            event,
        ) if kind != "triage" else validate_unity_requirement(event.get("unity_requirement", "none"), "triage Unity requirement")
        require(kind != "triage" or unity_requirement == "none", "batch triage cannot reserve a Unity editor")
        add_phase(
            proc, key=event["phase_key"], ticket_id=ticket_id, kind=kind,
            model=event["model"], effort=event["reasoning_effort"], branch=None,
            base=event["base_commit"], scope=event.get("scope"), context_packet=context_packet,
            unity_requirement=unity_requirement,
        )
        phase(proc, event["phase_key"])["triage_profile"] = event.get("triage_profile") if kind == "triage" else None
        phase(proc, event["phase_key"])["contract_validation_profile"] = (
            event.get("contract_validation_profile") if kind == "plan_contract_validation" else None
        )
        return

    if event_type == "TICKET_TRIAGED":
        require(proc.get("supervision", {}).get("status") == "ACTIVE", "configure supervision first")
        item = ticket(proc, str(event.get("ticket_id") or ""))
        require(item.get("triage") is None, "ticket already triaged")
        source_phase = phase(proc, str(event.get("phase_key") or ""))
        require(source_phase.get("kind") == "triage" and source_phase.get("launch_state") == "COMPLETED", "triage result requires a completed visible batch-triage phase")
        criticality, complexity = event.get("criticality"), event.get("complexity")
        require(event.get("confidence") in {"low", "medium", "high"}, "triage confidence is required")
        expected = routed_setting(ANALYSIS_MATRIX, criticality, complexity, bool(event.get("reasoning_authorized")))
        validate_routing(event, expected, "analysis_")
        validate_reasoning_authorization(
            proc, event, expected, stage="analysis", ticket_id=event["ticket_id"], head=source_phase.get("base")
        )
        require(
            (event.get("triage_model"), event.get("triage_reasoning_effort"))
            == (source_phase.get("requested_model"), source_phase.get("requested_reasoning_effort")),
            "triage result routing differs from its completed phase",
        )
        if source_phase.get("triage_profile") == "mechanical":
            require((criticality, complexity) == ("LOW", "LOW"), "Luna triage may return only LOW/LOW")
            require(event["confidence"] == "high", "Luna triage requires high confidence")
            require(not event.get("declared_or_suspected_dependencies"), "Luna triage cannot report dependencies")
            require(not event.get("suspected_collision_domains"), "Luna triage cannot report collision domains")
        if uses_unity_mcp(proc):
            event_unity_requirement(proc, event, "analysis_unity_requirement")
        item["triage"] = dict(event)
        item["status"] = "TRIAGED"
        return

    if event_type == "ANALYSIS_RECORDED":
        item = ticket(proc, str(event.get("ticket_id") or ""))
        require(item.get("status") == "TRIAGED", "analysis requires completed triage")
        source_phase = phase(proc, str(event.get("phase_key") or ""))
        require(
            source_phase.get("kind") == "analysis"
            and source_phase.get("ticket_id") == event.get("ticket_id")
            and source_phase.get("launch_state") == "COMPLETED",
            "analysis result requires its completed visible analysis phase",
        )
        require_fields(
            event,
            (
                "analysis_revision", "analysis_base_commit", "source_revision", "profile_revision",
                "criticality", "complexity", "criticality_evidence", "complexity_evidence",
                "structural_digest", "implementation_contract_revision",
                "verification_contract_revision", "report_thread_id",
                "residual_implementation_complexity", "verification_complexity",
                "complexity_reduction_evidence",
            ),
            "analysis",
        )
        require(event["residual_implementation_complexity"] in COMPLEXITIES, "invalid residual implementation complexity")
        require(event["verification_complexity"] in COMPLEXITIES, "invalid verification complexity")
        require("unresolved_implementation_difficulty" in event, "analysis is missing unresolved implementation difficulty")
        require(
            isinstance(event["unresolved_implementation_difficulty"], list),
            "unresolved implementation difficulty must be a list",
        )
        require(
            (event.get("model"), event.get("reasoning_effort"))
            == (source_phase.get("requested_model"), source_phase.get("requested_reasoning_effort")),
            "analysis result routing differs from its completed phase",
        )
        item["analysis"] = dict(event)
        require(event["report_thread_id"] == source_phase.get("thread_id"), "analysis report thread does not match the visible phase")
        confirmed_expected = routed_setting(
            ANALYSIS_MATRIX, event["criticality"], event["complexity"], bool(event.get("reasoning_authorized"))
        )
        if route_is_covered(
            ANALYSIS_ROUTE_COVERAGE,
            (source_phase["requested_model"], source_phase["requested_reasoning_effort"]),
            confirmed_expected[:2],
        ):
            item["analysis"]["routing_validation_status"] = "not-required"
            finalize_analysis_gate(proc, item)
        else:
            item["analysis"]["routing_validation_status"] = "required"
            item["analysis_route_validation_required"] = {
                "criticality": event["criticality"],
                "complexity": event["complexity"],
                "required_model": confirmed_expected[0],
                "required_reasoning_effort": confirmed_expected[1],
                "routing_conformance": confirmed_expected[2],
                "analysis_revision": event["analysis_revision"],
            }
            item["status"] = "ANALYSIS_ROUTE_VALIDATION_REQUIRED"
        return

    if event_type == "ANALYSIS_ROUTE_VALIDATION_RECORDED":
        item = ticket(proc, str(event.get("ticket_id") or ""))
        require(item.get("status") == "ANALYSIS_ROUTE_VALIDATION_REQUIRED", "analysis route validation is not required")
        source_phase = phase(proc, str(event.get("phase_key") or ""))
        require(
            source_phase.get("kind") == "analysis_route_validation"
            and source_phase.get("ticket_id") == event.get("ticket_id")
            and source_phase.get("launch_state") == "COMPLETED",
            "analysis route validation requires its completed visible phase",
        )
        require_fields(
            event,
            ("analysis_revision", "status", "validated_sections", "report_reference"),
            "analysis route validation",
        )
        require(event["analysis_revision"] == item["analysis"]["analysis_revision"], "analysis revision mismatch")
        require(event["status"] in {"passed", "failed"}, "invalid analysis route validation status")
        require(isinstance(event["validated_sections"], list) and event["validated_sections"], "validated sections are required")
        item["analysis"]["routing_validation"] = dict(event)
        item["analysis"]["routing_validation_status"] = event["status"]
        if event["status"] == "failed":
            item["status"] = "ANALYSIS_RECONCILIATION_REQUIRED"
        else:
            finalize_analysis_gate(proc, item)
        return

    if event_type == "DEPENDENCIES_CONSOLIDATED":
        require(not proc.get("dependencies_consolidated"), "dependencies already consolidated")
        require(
            all(
                value.get("analysis")
                and value.get("status") not in {
                    "TRIAGED", "ANALYSIS_ROUTE_VALIDATION_REQUIRED", "ANALYSIS_RECONCILIATION_REQUIRED",
                }
                for value in proc["tickets"].values()
            ),
            "all analyses and targeted route validations must finish first",
        )
        graph = event.get("graph")
        require(isinstance(graph, dict), "dependency graph must be an object")
        schedule = event.get("schedule")
        budget = event.get("train_size_budget")
        require(isinstance(schedule, dict), "dependency consolidation requires an implementation schedule")
        require(isinstance(budget, dict), "dependency consolidation requires a train size budget")
        require_fields(
            budget,
            ("material_file_count", "schema_or_data_transformation_count", "structural_domain_count", "checkpoint"),
            "train size budget",
        )
        require(budget["checkpoint"] in {"clear", "freeze-train", "decompose-epic"}, "invalid train size checkpoint")
        if (
            budget["material_file_count"] > 60
            or budget["schema_or_data_transformation_count"] > 2
            or budget["structural_domain_count"] > 4
        ):
            require(budget["checkpoint"] != "clear", "train size threshold crossed without a checkpoint")
        group_members: dict[str, list[str]] = {}
        for ticket_id, value in proc["tickets"].items():
            entry = graph.get(ticket_id)
            require(isinstance(entry, dict), f"dependency graph missing ticket: {ticket_id}")
            required_inventory_fields = (
                "collision_domains", "planned_material_files", "structural_domains",
                "schema_or_data_transformations",
            )
            require(
                all(field in entry for field in required_inventory_fields),
                f"dependency evidence is incomplete for {ticket_id}",
            )
            hard = entry.get("hard_dependencies", [])
            require(isinstance(hard, list), f"hard_dependencies must be a list: {ticket_id}")
            require(all(dep in proc["tickets"] and dep != ticket_id for dep in hard), f"invalid dependency: {ticket_id}")
            for field in required_inventory_fields:
                require(isinstance(entry[field], list), f"{field} must be a list: {ticket_id}")
            schedule_entry = schedule.get(ticket_id)
            require(isinstance(schedule_entry, dict), f"implementation schedule missing ticket: {ticket_id}")
            require_fields(schedule_entry, ("mode", "reason"), f"implementation schedule for {ticket_id}")
            require(schedule_entry["mode"] in {"parallel-safe", "sequential", "blocked"}, f"invalid schedule mode: {ticket_id}")
            if schedule_entry["mode"] == "parallel-safe":
                require(bool(schedule_entry.get("parallel_group")), f"parallel-safe ticket lacks a parallel group: {ticket_id}")
                group_members.setdefault(schedule_entry["parallel_group"], []).append(ticket_id)
            value["hard_dependencies"] = hard
            value["collision_domains"] = entry.get("collision_domains", [])
            value["scope_inventory"] = {
                "planned_material_files": entry["planned_material_files"],
                "structural_domains": entry["structural_domains"],
                "schema_or_data_transformations": entry["schema_or_data_transformations"],
            }
            value["schedule"] = dict(schedule_entry)
            if value["status"] == "ANALYZED":
                value["status"] = "READY_FOR_IMPLEMENTATION"
        for group, members in group_members.items():
            for index, left_id in enumerate(members):
                for right_id in members[index + 1:]:
                    left = graph[left_id]
                    right = graph[right_id]
                    shared_domains = set(left["collision_domains"]) & set(right["collision_domains"])
                    shared_files = set(left["planned_material_files"]) & set(right["planned_material_files"])
                    require(
                        not shared_domains and not shared_files,
                        f"parallel group {group} has an unproven collision between {left_id} and {right_id}",
                    )
        proc["dependencies_consolidated"] = True
        proc["dependency_revision"] = event.get("dependency_revision") or f"dependencies-r{proc['revision'] + 1}"
        proc["train_size_budget"] = dict(budget)
        return

    if event_type == "HUMAN_INPUT_REQUESTED":
        item = ticket(proc, str(event.get("ticket_id") or ""))
        require(item.get("status") not in TERMINAL_TICKET_STATES, "terminal ticket cannot request input")
        require_fields(
            event,
            (
                "gate_id", "revision", "question", "reason", "blocked_scope",
                "continuing_scope", "accepted_replies",
            ),
            "human input request",
        )
        gate = create_gate(
            proc, gate_id=event["gate_id"], kind="input", ticket_id=event["ticket_id"],
            revision=event["revision"], phase_key=event.get("phase_key"),
            prior_ticket_status=item.get("status"),
        )
        gate.update({key: event[key] for key in (
            "question", "reason", "blocked_scope", "continuing_scope", "accepted_replies"
        )})
        item["status"] = "AWAITING_REQUIRED_INPUT"
        return

    if event_type == "GATE_ANNOUNCED":
        gates = proc.get("human_gates", {})
        gate = gates.get(event.get("gate_id")) if isinstance(gates, dict) else None
        require(isinstance(gate, dict), "unknown gate")
        require(gate.get("status") == "PENDING_UNANNOUNCED", "gate is not awaiting announcement")
        require_fields(
            event,
            ("revision", "decision_summary", "evidence_summary", "blocked_scope", "continuing_scope", "accepted_replies"),
            "gate announcement",
        )
        require(event["revision"] == gate["revision"], "gate revision mismatch")
        gate.update({key: event[key] for key in (
            "decision_summary", "evidence_summary", "blocked_scope", "continuing_scope", "accepted_replies"
        )})
        gate["status"] = "PENDING_ANNOUNCED"
        gate["announced_at"] = event.get("announced_at") or now_iso()
        require(state.get("pending_human_action") in (None, {}), "another human action is already announced")
        state["pending_human_action"] = {
            "gate_id": gate["gate_id"],
            "gate_type": gate["kind"],
            "ticket_id": gate["ticket_id"],
            "revision": gate["revision"],
            "reason": gate.get("reason") or event["decision_summary"],
            "question": gate.get("question") or event["decision_summary"],
            "decision_summary": event["decision_summary"],
            "evidence_summary": event["evidence_summary"],
            "blocked_scope": event["blocked_scope"],
            "continuing_scope": event["continuing_scope"],
            "accepted_replies": event["accepted_replies"],
            "notification_status": "ANNOUNCED",
            "announced_at": gate["announced_at"],
        }
        return

    if event_type == "GATE_RESOLVED":
        gates = proc.get("human_gates", {})
        gate = gates.get(event.get("gate_id")) if isinstance(gates, dict) else None
        require(isinstance(gate, dict), "unknown gate")
        require(gate.get("status") == "PENDING_ANNOUNCED", "gate must be announced before resolution")
        require(event.get("revision") == gate.get("revision"), "gate revision mismatch")
        require(gate.get("kind") != "input" or event.get("decision") == "rejected", "use INPUT_PROVIDED for supplied information")
        decision = event.get("decision")
        require(decision in {"approved", "rejected"}, "gate decision must be approved or rejected")
        gate["status"] = decision.upper()
        gate["resolved_at"] = now_iso()
        pending_action = state.get("pending_human_action")
        if isinstance(pending_action, dict) and pending_action.get("gate_id") == gate["gate_id"]:
            state["pending_human_action"] = None
        item = ticket(proc, gate["ticket_id"])
        if decision == "rejected":
            item["status"] = "BLOCKED"
        elif gate["kind"] == "analysis":
            item["status"] = "READY_FOR_IMPLEMENTATION" if proc.get("dependencies_consolidated") else "ANALYZED"
        elif gate["kind"] == "pre_merge":
            item["status"] = "READY_TO_MERGE"
        return

    if event_type == "INPUT_PROVIDED":
        gates = proc.get("human_gates", {})
        gate = gates.get(event.get("gate_id")) if isinstance(gates, dict) else None
        require(isinstance(gate, dict) and gate.get("kind") == "input", "unknown input gate")
        require(gate.get("status") == "PENDING_ANNOUNCED", "input request must be announced first")
        require(event.get("revision") == gate.get("revision"), "input revision mismatch")
        require_fields(event, ("response_summary", "response_artifact"), "provided input")
        gate["status"] = "PROVIDED"
        gate["response_summary"] = event["response_summary"]
        gate["response_artifact"] = event["response_artifact"]
        gate["resolved_at"] = now_iso()
        pending_action = state.get("pending_human_action")
        if isinstance(pending_action, dict) and pending_action.get("gate_id") == gate["gate_id"]:
            state["pending_human_action"] = None
        item = ticket(proc, gate["ticket_id"])
        if gate.get("phase_key"):
            interrupted = phase(proc, gate["phase_key"])
            require(interrupted.get("launch_state") == "NEEDS_INPUT", "input phase is not waiting")
            interrupted["launch_state"] = "INPUT_READY"
            interrupted["provided_input"] = {
                "summary": event["response_summary"],
                "artifact": event["response_artifact"],
            }
        else:
            item["status"] = gate.get("prior_ticket_status") or "READY_FOR_IMPLEMENTATION"
        return

    if event_type == "PHASE_RESUMED":
        value = phase(proc, str(event.get("phase_key") or ""))
        require(value.get("launch_state") == "INPUT_READY", "phase has no provided input to resume")
        if value.get("unity_requirement") != "none":
            require(unity_lease_for_owner(proc, value["phase_key"]) is not None, "Unity phase must reacquire a slot before resume")
        require(event.get("thread_id") == value.get("thread_id"), "resume must continue the same visible phase thread")
        require(event.get("visibility_verified") is True, "resumed phase visibility must be verified")
        value["launch_state"] = "RUNNING"
        value["last_observed_at"] = now_iso()
        gate = next(
            (gate for gate in proc.get("human_gates", {}).values() if gate.get("phase_key") == value["phase_key"]),
            None,
        )
        require(isinstance(gate, dict) and gate.get("status") == "PROVIDED", "phase input gate is not resolved")
        ticket(proc, gate["ticket_id"])["status"] = gate.get("prior_ticket_status") or "EXECUTION_PAIR_RUNNING"
        return

    if event_type == "PLAN_CONTRACT_VALIDATION_RECORDED":
        item = ticket(proc, str(event.get("ticket_id") or ""))
        require(item.get("status") == "READY_FOR_IMPLEMENTATION", "ticket is not ready for contract validation")
        completed = phase(proc, str(event.get("phase_key") or ""))
        require(
            completed.get("kind") == "plan_contract_validation"
            and completed.get("ticket_id") == event.get("ticket_id")
            and completed.get("launch_state") == "COMPLETED",
            "plan contract validation requires its completed visible phase",
        )
        require_fields(
            event,
            (
                "status", "analysis_complexity", "residual_implementation_complexity",
                "verification_complexity", "complexity_reduction_evidence",
                "validation_reference",
            ),
            "plan contract validation",
        )
        require("unresolved_implementation_difficulty" in event, "contract validation is missing unresolved implementation difficulty")
        require(event["status"] in {"passed", "failed"}, "invalid plan contract validation status")
        analysis = item["analysis"]
        require(event["analysis_complexity"] == analysis["complexity"], "analysis complexity changed during contract validation")
        require(event["residual_implementation_complexity"] in COMPLEXITIES, "invalid residual implementation complexity")
        require(event["verification_complexity"] in COMPLEXITIES, "invalid verification complexity")
        require(isinstance(event["unresolved_implementation_difficulty"], list), "unresolved implementation difficulty must be a list")
        item["plan_contract_validation"] = dict(event)
        if event["status"] == "passed":
            analysis["residual_implementation_complexity"] = event["residual_implementation_complexity"]
            analysis["verification_complexity"] = event["verification_complexity"]
            analysis["complexity_reduction_evidence"] = event["complexity_reduction_evidence"]
            analysis["unresolved_implementation_difficulty"] = event["unresolved_implementation_difficulty"]
        else:
            item["status"] = "NEEDS_CONTRACT_AMENDMENT"
        return

    if event_type == "EXECUTION_PAIR_DISPATCHED":
        require(state.get("execution_mode") == "live", "implementation is forbidden in dry-run mode")
        item = ticket(proc, str(event.get("ticket_id") or ""))
        require(item.get("status") == "READY_FOR_IMPLEMENTATION", "ticket is not ready for implementation")
        require(dependencies_satisfied(proc, item), "hard dependencies are not merged into the train")
        require(active_execution_pairs(proc) < int(proc["limits"]["max_active_execution_pairs"]), "execution-pair concurrency limit reached")
        require_fields(
            event,
            (
                "base_commit", "implementation_phase_key", "acceptance_phase_key",
                "implementation_branch", "acceptance_branch", "verification_complexity",
                "implementation_model", "implementation_reasoning_effort",
                "acceptance_model", "acceptance_reasoning_effort",
            ),
            "execution pair",
        )
        require(event["implementation_phase_key"] != event["acceptance_phase_key"], "execution phases must be distinct")
        require(event["implementation_branch"] != event["acceptance_branch"], "implementation and tests require distinct branches")
        implementation_context = validate_compact_context(
            {"context_packet": event.get("implementation_context_packet")}, "implementation dispatch"
        )
        acceptance_context = validate_compact_context(
            {"context_packet": event.get("acceptance_context_packet")}, "acceptance-test dispatch"
        )
        analysis = item["analysis"]
        if analysis["complexity"] in {"HIGH", "MAXIMUM"}:
            require(
                item.get("plan_contract_validation", {}).get("status") == "passed",
                "HIGH or MAXIMUM implementation requires a passed plan contract validation",
            )
        implementation_complexity = analysis["residual_implementation_complexity"]
        require(
            event["verification_complexity"] == analysis["verification_complexity"],
            "execution verification complexity differs from the validated contract",
        )
        implementation_expected = routed_phase_setting(
            IMPLEMENTATION_MATRIX, analysis["criticality"], implementation_complexity,
            bool(event.get("implementation_reasoning_authorized", event.get("reasoning_authorized"))), event=event,
            fast_path_field="implementation_mechanical_fast_path",
        )
        acceptance_expected = routed_phase_setting(
            ACCEPTANCE_MATRIX, analysis["criticality"], event["verification_complexity"],
            bool(event.get("acceptance_reasoning_authorized", event.get("reasoning_authorized"))), event=event,
            fast_path_field="acceptance_mechanical_fast_path",
        )
        validate_routing(event, implementation_expected, "implementation_")
        validate_routing(event, acceptance_expected, "acceptance_")
        validate_reasoning_authorization(
            proc, event, implementation_expected, stage="implementation",
            ticket_id=event["ticket_id"], head=event["base_commit"],
            authorization_field="implementation_reasoning_authorization_id",
        )
        validate_reasoning_authorization(
            proc, event, acceptance_expected, stage="acceptance",
            ticket_id=event["ticket_id"], head=event["base_commit"],
            authorization_field="acceptance_reasoning_authorization_id",
        )
        implementation_unity = event_unity_requirement(proc, event, "implementation_unity_requirement")
        acceptance_unity = event_unity_requirement(proc, event, "acceptance_unity_requirement")
        verification_unity = event_unity_requirement(proc, event, "verification_unity_requirement")
        add_phase(
            proc, key=event["implementation_phase_key"], ticket_id=event["ticket_id"], kind="implementation",
            model=event["implementation_model"], effort=event["implementation_reasoning_effort"],
            branch=event["implementation_branch"], base=event["base_commit"],
            context_packet=implementation_context,
            unity_requirement=implementation_unity,
        )
        add_phase(
            proc, key=event["acceptance_phase_key"], ticket_id=event["ticket_id"], kind="acceptance_tests",
            model=event["acceptance_model"], effort=event["acceptance_reasoning_effort"],
            branch=event["acceptance_branch"], base=event["base_commit"],
            context_packet=acceptance_context,
            unity_requirement=acceptance_unity,
        )
        item["execution"] = {
            "base_commit": event["base_commit"],
            "implementation_phase_key": event["implementation_phase_key"],
            "acceptance_phase_key": event["acceptance_phase_key"],
            "implementation_branch": event["implementation_branch"],
            "acceptance_branch": event["acceptance_branch"],
            "residual_implementation_complexity": implementation_complexity,
            "verification_complexity": event["verification_complexity"],
            "verification_unity_requirement": verification_unity,
        }
        item["status"] = "EXECUTION_PAIR_RUNNING"
        return

    if event_type == "HIDDEN_FALLBACK_AUTHORIZED":
        require_fields(
            event,
            ("authorization_id", "phase_key", "user_decision_reference", "reason", "authorized_at"),
            "hidden fallback authorization",
        )
        phase(proc, event["phase_key"])
        authorizations = proc.setdefault("hidden_fallback_authorizations", {})
        require(event["authorization_id"] not in authorizations, "hidden fallback authorization already exists")
        authorizations[event["authorization_id"]] = {
            "authorization_id": event["authorization_id"],
            "phase_key": event["phase_key"],
            "user_decision_reference": event["user_decision_reference"],
            "reason": event["reason"],
            "authorized_at": event["authorized_at"],
            "status": "ACTIVE",
        }
        return

    if event_type == "PHASE_LAUNCH_OBSERVED":
        value = phase(proc, str(event.get("phase_key") or ""))
        launch_state = event.get("launch_state")
        require(launch_state in {"QUEUED", "RUNNING", "LAUNCH_UNKNOWN", "BLOCKED"}, "invalid launch state")
        if launch_state in {"QUEUED", "RUNNING"} and value.get("unity_requirement") != "none":
            lease = value.get("unity_slot_lease")
            require(isinstance(lease, dict), "editor-backed Unity phase requires an acquired slot before launch")
            configured_lease = unity_lease_for_owner(proc, value["phase_key"])
            require(configured_lease is not None, "Unity phase slot lease is not active")
            slot_id, active_lease = configured_lease
            require(slot_id == lease.get("slot_id"), "Unity phase slot ID differs from active lease")
            require(active_lease.get("lease_id") == lease.get("lease_id"), "Unity phase lease ID mismatch")
        if launch_state == "QUEUED":
            require(bool(event.get("client_thread_id")), "queued phase requires client_thread_id")
        if launch_state == "RUNNING":
            require(bool(event.get("thread_id")), "running phase requires thread_id")
            require(event.get("thread_id") != value.get("forbidden_thread_id"), "remediation must use a fresh task context")
            execution_visibility = event.get("execution_visibility")
            require(execution_visibility in {"user-visible", "hidden-authorized"}, "running phase requires explicit visibility mode")
            if execution_visibility == "user-visible":
                require(event.get("visibility_verified") is True, "visible running phase requires verified user visibility")
                require(bool(event.get("visibility_evidence_reference")), "visible phase requires visibility evidence")
            else:
                require(event.get("visibility_verified") is False, "hidden fallback cannot claim user visibility")
                require_fields(event, ("hidden_authorization_id", "agent_session_id"), "hidden fallback launch")
                authorizations = proc.get("hidden_fallback_authorizations", {})
                authorization = authorizations.get(event["hidden_authorization_id"]) if isinstance(authorizations, dict) else None
                require(isinstance(authorization, dict), "hidden fallback lacks user authorization")
                require(authorization.get("phase_key") == value["phase_key"], "hidden fallback authorization is for another phase")
                require(authorization.get("status") == "ACTIVE", "hidden fallback authorization has already been consumed")
                require(event["agent_session_id"] == event["thread_id"], "hidden fallback thread ID must be its actual agent session ID")
                authorization["status"] = "CONSUMED"
                authorization["consumed_at"] = now_iso()
        if value.get("launch_state") == "LAUNCH_UNKNOWN":
            require(launch_state != "QUEUED" or event.get("reconciled") is True, "launch-unknown must be reconciled")
        value.update({key: event.get(key) for key in (
            "client_thread_id", "thread_id", "host_id", "visibility_verified", "visibility_verified_at",
            "visibility_evidence_reference", "execution_visibility", "hidden_authorization_id", "agent_session_id",
        ) if key in event})
        value["launch_state"] = launch_state
        value["last_observed_at"] = now_iso()
        return

    if event_type == "PHASE_COMPLETED":
        completed = complete_phase(event, proc)
        if completed["kind"] in {"implementation", "acceptance_tests"}:
            item = ticket(proc, completed["ticket_id"])
            execution = item["execution"]
            if all(phase(proc, execution[key])["launch_state"] == "COMPLETED" for key in (
                "implementation_phase_key", "acceptance_phase_key"
            )):
                item["status"] = "AWAITING_EXECUTION_INTEGRATION"
        elif completed["kind"] == "remediation":
            ticket(proc, completed["ticket_id"])["status"] = "AWAITING_REMEDIATION_VERIFICATION"
        elif completed["kind"] == "final_remediation":
            proc["finalization"]["status"] = "AWAITING_FINAL_PR_UPDATE"
        return

    if event_type == "PHASE_TERMINATED":
        terminate_phase(event, proc)
        return

    if event_type == "EXECUTION_PAIR_INTEGRATED":
        item = ticket(proc, str(event.get("ticket_id") or ""))
        require(item.get("status") == "AWAITING_EXECUTION_INTEGRATION", "execution pair is not ready for integration")
        require_fields(
            event,
            (
                "implementation_branch", "implementation_commit", "acceptance_commit",
                "combined_head", "integration_evidence_reference",
            ),
            "execution-pair integration",
        )
        execution = item["execution"]
        require(event["implementation_branch"] == execution["implementation_branch"], "combined head must stay on the implementation branch")
        implementation = phase(proc, execution["implementation_phase_key"])
        acceptance = phase(proc, execution["acceptance_phase_key"])
        implementation_commit = ((implementation.get("completion_envelope") or {}).get("artifacts") or {}).get("commit")
        acceptance_commit = ((acceptance.get("completion_envelope") or {}).get("artifacts") or {}).get("commit")
        require(event["implementation_commit"] == implementation_commit, "implementation integration commit mismatch")
        require(event["acceptance_commit"] == acceptance_commit, "acceptance-test integration commit mismatch")
        execution["implementation_commit"] = event["implementation_commit"]
        execution["acceptance_commit"] = event["acceptance_commit"]
        execution["integrated_head"] = event["combined_head"]
        execution["integration_evidence_reference"] = event["integration_evidence_reference"]
        item["status"] = "AWAITING_VERIFICATION"
        return

    if event_type == "VERIFICATION_RECORDED":
        item = ticket(proc, str(event.get("ticket_id") or ""))
        require(item.get("status") in {"AWAITING_VERIFICATION", "AWAITING_REMEDIATION_VERIFICATION"}, "execution or remediation must complete before verification")
        if uses_unity_mcp(proc):
            owner_key = f"ticket:{event.get('ticket_id')}:verification"
            requirement, _, _ = expected_unity_operation(proc, owner_key)
            if requirement != "none":
                require(unity_lease_for_owner(proc, owner_key) is not None, "Unity-backed ticket verification requires an active slot lease")
        require_fields(
            event,
            (
                "ticket_head", "baseline_red_base", "integrated_green_head", "environment_status",
                "acceptance_coverage_status", "independent_test_commit", "logs_reference",
            ),
            "verification",
        )
        validate_deterministic_verification(event, "ticket verification")
        execution = item["execution"]
        require(event["baseline_red_base"] == execution["base_commit"], "baseline red must use execution-pair base")
        require(event["integrated_green_head"] == event["ticket_head"], "green evidence must cover ticket head")
        require(event.get("status") in {"passed", "failed"}, "verification status must be passed or failed")
        if event.get("status") == "failed":
            require(any(result.get("status") != "passed" for result in event["command_results"]), "failed verification lacks a failed command")
            item["verification"] = dict(event)
            item["status"] = "VERIFICATION_FAILED"
            return
        require(all(result.get("status") == "passed" for result in event["command_results"]), "passed verification contains a failed command")
        require(event["acceptance_coverage_status"] == "complete", "acceptance coverage must be complete")
        require(event["environment_status"] in {"passed", "not-applicable"}, "environment parity is incomplete")
        require(isinstance(event.get("operational_change_applicable"), bool), "verification must classify operational-change applicability")
        if event["operational_change_applicable"]:
            require(event.get("operational_preflight_status") == "passed", "operational configuration preflight is incomplete")
            require(bool(event.get("operational_preflight_evidence")), "operational preflight evidence is missing")
            inventory = event.get("required_configuration_inventory")
            require(isinstance(inventory, list) and inventory, "operational verification requires a configuration inventory")
            require(all(isinstance(entry, dict) and entry.get("presence") == "verified" for entry in inventory), "required operational configuration is missing")
        if event.get("supabase_auth_applicable") is True:
            require(event["environment_status"] == "passed", "Supabase/Auth requires environment parity")
            require(event.get("supabase_auth_status") == "passed", "Supabase/Auth verification is incomplete")
            require(event.get("privileged_credentials_setup_only") is True, "privileged credentials crossed the tested boundary")
        require(not event.get("automatable_manual_scenarios"), "automatable scenarios cannot be left to the user")
        item["verification"] = dict(event)
        item["status"] = "FUNCTIONAL_READY"
        return

    if event_type == "VERIFICATION_FAILURE_CLASSIFIED":
        item = ticket(proc, str(event.get("ticket_id") or ""))
        require(item.get("status") == "VERIFICATION_FAILED", "ticket has no failed verification to classify")
        require_fields(event, ("failure_class", "evidence_reference", "next_step"), "verification failure classification")
        require(
            event["failure_class"] in {
                "implementation-defect", "test-defect", "environment-defect",
                "contract-ambiguity", "infrastructure-flake",
            },
            "invalid verification failure class",
        )
        require(event["next_step"] in {"remediate", "retry", "block"}, "invalid verification failure next step")
        item["verification_failure"] = dict(event)
        item["status"] = {
            "remediate": "NEEDS_REMEDIATION",
            "retry": "AWAITING_VERIFICATION",
            "block": "BLOCKED",
        }[event["next_step"]]
        return

    if event_type == "TICKET_PR_RECORDED":
        item = ticket(proc, str(event.get("ticket_id") or ""))
        require(item.get("status") in {"FUNCTIONAL_READY", "NEEDS_REMEDIATION", "AUTO_REVIEW_CLEAN"}, "ticket is not ready for a PR")
        require_fields(event, ("url", "base_branch", "base_commit", "head_branch", "head_commit"), "ticket pull request")
        require(isinstance(event.get("is_draft"), bool), "ticket pull request must record draft state")
        require(event["base_branch"] == state["run_identity"]["train_branch"], "ticket PR must target the train branch")
        if item.get("verification"):
            require(event["head_commit"] == item["verification"]["ticket_head"], "ticket PR head differs from verified head")
        item["pull_request"] = dict(event)
        return

    if event_type == "TICKET_PR_HEAD_DRIFT_RECORDED":
        item = ticket(proc, str(event.get("ticket_id") or ""))
        require(
            item.get("status") == "AWAITING_FINDING_RECONCILIATION",
            "ticket head drift can only be recorded before finding reconciliation",
        )
        pull_request = item.get("pull_request")
        require(isinstance(pull_request, dict), "ticket pull request has not been recorded")
        require_fields(
            event,
            (
                "previous_head_commit", "head_commit", "relationship",
                "relationship_evidence_reference", "observed_at", "source",
            ),
            "ticket pull request head drift",
        )
        require(
            event["previous_head_commit"] == pull_request.get("head_commit"),
            "ticket head drift previous head mismatch",
        )
        require(event["head_commit"] != event["previous_head_commit"], "ticket head drift must change the head")
        require(event["relationship"] == "descendant", "ticket head drift must be a proven descendant")
        parse_iso(str(event["observed_at"]), "ticket head drift observation time")
        drift = {
            "previous_head_commit": event["previous_head_commit"],
            "head_commit": event["head_commit"],
            "relationship": event["relationship"],
            "relationship_evidence_reference": event["relationship_evidence_reference"],
            "observed_at": event["observed_at"],
            "source": event["source"],
        }
        pull_request.setdefault("head_drift_history", []).append(drift)
        pull_request["head_commit"] = event["head_commit"]
        pull_request["head_drift_unreconciled"] = True
        verification = item.get("verification")
        if isinstance(verification, dict):
            verification["stale_due_to_head_drift"] = True
            verification["superseded_by_head"] = event["head_commit"]
        reviews = item.get("reviews")
        if isinstance(reviews, list) and reviews:
            reviews[-1]["stale_due_to_head_drift"] = True
            reviews[-1]["superseded_by_head"] = event["head_commit"]
        return

    if event_type == "TICKET_PR_READY_RECORDED":
        item = ticket(proc, str(event.get("ticket_id") or ""))
        pull_request = item.get("pull_request")
        require(isinstance(pull_request, dict), "ticket pull request has not been recorded")
        require(event.get("head_commit") == pull_request.get("head_commit"), "ticket PR ready head mismatch")
        require(event.get("is_draft") is False, "ticket pull request must be marked ready")
        pull_request["is_draft"] = False
        pull_request["ready_evidence_reference"] = event.get("evidence_reference")
        return

    if event_type == "REVIEW_DISPATCHED":
        item = ticket(proc, str(event.get("ticket_id") or ""))
        scope = event.get("scope")
        require(scope in {"initial", "followup"}, "review scope must be initial or followup")
        require_fields(event, ("phase_key", "base_commit", "head_commit", "model", "reasoning_effort"), "review dispatch")
        context_packet = validate_compact_context(event, "review dispatch")
        require(context_packet["exact_base"] == event["base_commit"], "review context base mismatch")
        require(context_packet["exact_head"] == event["head_commit"], "review context head mismatch")
        analysis = item.get("analysis") or {}
        require_fields(event, ("criticality", "complexity", "classification_evidence"), "review classification")
        criticality = event["criticality"]
        complexity = event["complexity"]
        require(criticality in CRITICALITIES and complexity in COMPLEXITIES, "invalid review classification")
        baseline_classification = item.get("effective_classification") or analysis
        require(
            criticality == higher_classification(baseline_classification["criticality"], criticality, CRITICALITIES),
            "review criticality cannot downgrade durable classification",
        )
        if scope == "initial":
            require(
                complexity == higher_classification(baseline_classification["complexity"], complexity, COMPLEXITIES),
                "initial-review complexity cannot downgrade durable classification",
            )
        if scope == "initial":
            require(item.get("status") == "FUNCTIONAL_READY", "initial review requires functional readiness")
            require(isinstance(item.get("pull_request"), dict), "initial review requires ticket PR")
            require(item["pull_request"].get("is_draft") is False, "ticket PR must be ready before review dispatch")
            require(event["base_commit"] == item["pull_request"]["base_commit"], "review base differs from ticket PR base")
            require(event["head_commit"] == item["pull_request"]["head_commit"], "review head differs from ticket PR head")
            require(not item.get("reviews"), "initial review already exists")
            require(event.get("review_kind") == "full", "initial review must be exhaustive")
            expected = routed_setting(INITIAL_REVIEW_MATRIX, criticality, complexity, bool(event.get("reasoning_authorized")))
            review_stage = "initial-review"
        else:
            reviews = item.get("reviews", [])
            baseline_review = latest_trustworthy_full_review(item)
            require(baseline_review is not None, "followup review requires a trustworthy full review")
            require(item.get("status") == "FUNCTIONAL_READY", "followup review requires post-remediation functional readiness")
            require(item.get("remediation_cycles", 0) > 0, "followup review requires remediation")
            material = event.get("material_scope_changed") is True
            require(event.get("review_kind") == ("full" if material else "focused"), "invalid followup review kind")
            delta = event.get("remediation_delta")
            require(isinstance(delta, dict), "followup review requires remediation delta evidence")
            require_fields(delta, ("change_kind", "verification_complexity", "changed_files"), "remediation delta")
            require(
                delta["change_kind"] in {"mechanical", "bounded-behavioral", "cross-cutting", "material-scope"},
                "invalid remediation delta kind",
            )
            require(isinstance(delta["changed_files"], list), "remediation delta changed_files must be a list")
            require((delta["change_kind"] == "material-scope") == material, "material scope flag conflicts with delta classification")
            complexity = delta["verification_complexity"]
            matrix = INITIAL_REVIEW_MATRIX if material else FOLLOWUP_REVIEW_MATRIX
            expected = routed_setting(matrix, criticality, complexity, bool(event.get("reasoning_authorized")))
            baseline_name = setting_name((baseline_review["actual_model"], baseline_review["actual_reasoning_effort"]))
            expected_name = setting_name(expected[:2])
            if not material:
                require(
                    expected_name in FOLLOWUP_CEILING_COMPATIBILITY.get(baseline_name, set()),
                    "focused followup route exceeds its latest trustworthy full-review baseline",
                )
            if material:
                require(event.get("scope_revision") != baseline_review.get("scope_revision"), "full re-review requires a new scope revision")
            review_stage = "initial-review" if material else "followup-review"
        validate_routing(event, expected)
        validate_reasoning_authorization(
            proc, event, expected, stage=review_stage,
            ticket_id=event["ticket_id"], head=event["head_commit"],
        )
        unity_requirement = event_unity_requirement(proc, event)
        add_phase(
            proc, key=event["phase_key"], ticket_id=event["ticket_id"], kind="review",
            model=event["model"], effort=event["reasoning_effort"], branch=None,
            base=event["base_commit"], scope=scope, context_packet=context_packet,
            unity_requirement=unity_requirement,
        )
        phase(proc, event["phase_key"])["review_kind"] = event["review_kind"]
        phase(proc, event["phase_key"])["scope_revision"] = event.get("scope_revision") or "scope-1"
        phase(proc, event["phase_key"])["remediation_delta"] = event.get("remediation_delta")
        phase(proc, event["phase_key"])["classification"] = {
            "criticality": criticality,
            "complexity": complexity,
            "evidence": event["classification_evidence"],
        }
        phase(proc, event["phase_key"])["routing_status"] = expected[2]
        item["status"] = "AUTO_REVIEW"
        return

    if event_type == "REVIEW_RECORDED":
        completed = complete_phase(event, proc)
        require(completed.get("kind") == "review", "phase is not a review")
        require_fields(event, ("reviewed_head", "status", "finding_inventory_complete"), "review result")
        require(event["status"] in {"clean", "changes_requested"}, "invalid review status")
        require(event["finding_inventory_complete"] is True, "review must return its complete finding inventory")
        item = ticket(proc, completed["ticket_id"])
        expected_head = item.get("pull_request", {}).get("head_commit")
        require(event["reviewed_head"] == expected_head, "reviewed head differs from ticket PR head")
        reviews = item.setdefault("reviews", [])
        reviews.append({
            "scope": completed["scope"], "review_kind": completed["review_kind"],
            "scope_revision": completed["scope_revision"], "reviewed_head": event["reviewed_head"],
            "status": event["status"], "findings": event.get("findings", []),
            "actual_model": completed["requested_model"],
            "actual_reasoning_effort": completed["requested_reasoning_effort"],
            "routing_status": completed.get("routing_status", "conformant"),
        })
        analysis = item["analysis"]
        criticality = event.get("effective_criticality") or analysis["criticality"]
        complexity = event.get("effective_complexity") or analysis["complexity"]
        dispatched_classification = completed.get("classification") or analysis
        require(
            criticality == higher_classification(dispatched_classification["criticality"], criticality, CRITICALITIES),
            "recorded effective criticality cannot downgrade reviewed classification",
        )
        require(
            complexity == higher_classification(dispatched_classification["complexity"], complexity, COMPLEXITIES),
            "recorded effective complexity cannot downgrade reviewed classification",
        )
        previous = item.get("effective_classification") or analysis
        item["effective_classification"] = {
            "criticality": higher_classification(previous["criticality"], criticality, CRITICALITIES),
            "complexity": higher_classification(previous["complexity"], complexity, COMPLEXITIES),
        }
        item["status"] = "AWAITING_FINDING_RECONCILIATION"
        return

    if event_type == "TICKET_FINDINGS_RECONCILED":
        item = ticket(proc, str(event.get("ticket_id") or ""))
        require(item.get("status") == "AWAITING_FINDING_RECONCILIATION", "ticket is not awaiting finding reconciliation")
        require_fields(
            event,
            (
                "head_commit", "ledger_status", "sources_dispositioned",
                "feedback_collection_started_at", "feedback_collection_deadline_at",
                "feedback_collected_at", "ci_status", "copilot_status",
                "source_counts", "feedback_evidence_reference",
            ),
            "ticket finding ledger",
        )
        # An exact-head clean review legitimately has no source findings and
        # therefore no dispositions.  Presence, rather than truthiness, is
        # required for these two list fields so their empty inventories remain
        # distinguishable from missing evidence.
        require("source_findings" in event, "ticket finding ledger is missing: source_findings")
        require("finding_dispositions" in event, "ticket finding ledger is missing: finding_dispositions")
        require(event["ledger_status"] == "complete", "ticket finding ledger is incomplete")
        require(isinstance(event["sources_dispositioned"], list), "sources_dispositioned must be a list")
        require(
            set(event["sources_dispositioned"]) == {"codex", "ci", "copilot"},
            "Codex, CI, and Copilot ticket findings must all be dispositioned",
        )
        require(event["head_commit"] == item.get("pull_request", {}).get("head_commit"), "finding ledger head differs from ticket PR")
        require(item.get("pull_request", {}).get("is_draft") is False, "ticket PR is still draft")
        started = parse_iso(str(event["feedback_collection_started_at"]), "ticket feedback start")
        deadline = parse_iso(str(event["feedback_collection_deadline_at"]), "ticket feedback deadline")
        collected = parse_iso(str(event["feedback_collected_at"]), "ticket feedback collection time")
        require(deadline >= started + timedelta(minutes=10), "ticket feedback deadline must be at least ten minutes after start")
        require(collected >= started, "ticket feedback was collected before the collection started")
        require(
            event["ci_status"] in {
                "passed", "failed", "not_configured", "unavailable_with_local_fallback",
            },
            "unacceptable ticket CI status",
        )
        require(event["copilot_status"] in {"received", "not_configured", "unavailable", "timed_out"}, "ticket Copilot collection is not terminal")
        if event["copilot_status"] in {"unavailable", "timed_out"}:
            require(collected >= deadline, "Copilot cannot be unavailable or timed out before the ticket deadline")
        if event["copilot_status"] in {"not_configured", "unavailable"}:
            require(bool(event.get("copilot_status_evidence")), "Copilot unavailable state requires evidence")
        require(isinstance(event["source_counts"], dict), "ticket source_counts must be an object")
        expected_sources = {"codex", "ci", "copilot"}
        require(set(event["source_counts"]) == expected_sources, "ticket source counts are incomplete")
        require(isinstance(event["source_findings"], list), "ticket source_findings must be a list")
        seen_ids: set[str] = set()
        calculated = {source: 0 for source in expected_sources}
        for finding in event["source_findings"]:
            require(isinstance(finding, dict), "ticket source finding must be an object")
            require_fields(finding, ("finding_id", "source"), "ticket source finding")
            require(finding["source"] in expected_sources, "unexpected ticket finding source")
            require(finding["finding_id"] not in seen_ids, "duplicate ticket source finding ID")
            seen_ids.add(finding["finding_id"])
            calculated[finding["source"]] += 1
        require(
            all(event["source_counts"][source] == calculated[source] for source in expected_sources),
            "ticket source finding counts do not match inventory",
        )
        require(isinstance(event["finding_dispositions"], list), "ticket finding_dispositions must be a list")
        disposition_ids: set[str] = set()
        for disposition in event["finding_dispositions"]:
            require(isinstance(disposition, dict), "ticket finding disposition must be an object")
            require_fields(
                disposition,
                ("finding_id", "disposition", "blocking", "remediation_status", "verification"),
                "ticket finding disposition",
            )
            require(
                disposition["disposition"] in {
                    "accepted-fixed", "accepted-deferred", "rejected-incorrect",
                    "rejected-out-of-scope", "escalated-human-decision",
                },
                "invalid ticket finding disposition",
            )
            require(disposition["finding_id"] not in disposition_ids, "duplicate ticket finding disposition")
            disposition_ids.add(disposition["finding_id"])
        require(disposition_ids == seen_ids, "every collected ticket finding requires exactly one disposition")
        require(isinstance(event.get("blocking_findings"), list), "blocking_findings must be a list")
        require(set(event["blocking_findings"]).issubset(seen_ids), "blocking ticket findings must come from the collected inventory")
        if event["ci_status"] == "failed":
            source_by_id = {
                finding["finding_id"]: finding["source"]
                for finding in event["source_findings"]
            }
            blocking = set(event["blocking_findings"])
            failed_ci_remediation = [
                disposition
                for disposition in event["finding_dispositions"]
                if source_by_id.get(disposition["finding_id"]) == "ci"
                and disposition["finding_id"] in blocking
                and disposition["blocking"] is True
                and disposition["disposition"] == "accepted-deferred"
                and disposition["remediation_status"] == "pending"
            ]
            require(
                bool(failed_ci_remediation),
                "failed ticket CI requires an accepted-deferred blocking CI finding with pending remediation",
            )
        item["finding_ledger"] = dict(event)
        if event["blocking_findings"]:
            item["status"] = "NEEDS_REMEDIATION"
        else:
            classification = item["effective_classification"]
            required = merge_human_gate(classification["criticality"], classification["complexity"], proc["approval_mode"])
            if required:
                gate_id = f"{event['ticket_id']}:pre-merge:{event['head_commit']}"
                create_gate(proc, gate_id=gate_id, kind="pre_merge", ticket_id=event["ticket_id"], revision=event["head_commit"])
                item["merge_gate_id"] = gate_id
                item["status"] = "AWAITING_PRE_MERGE_APPROVAL"
            else:
                item["status"] = "READY_TO_MERGE"
        return

    if event_type == "REMEDIATION_LIMIT_EXCEPTION_GRANTED":
        item = ticket(proc, str(event.get("ticket_id") or ""))
        require(item.get("status") == "NEEDS_REMEDIATION", "remediation exception requires requested changes")
        require(
            int(item.get("remediation_cycles", 0)) >= AUTOMATIC_REMEDIATION_CYCLE_LIMIT,
            "remediation exception requires the automatic cycle budget to be exhausted",
        )
        require_fields(
            event,
            (
                "gate_id", "additional_cycles", "user_decision_reference",
                "root_cause_reference", "reason", "authorized_at",
            ),
            "remediation limit exception",
        )
        require(
            event["additional_cycles"] == EXCEPTIONAL_REMEDIATION_CYCLE_INCREMENT,
            "remediation limit exception must grant exactly one additional cycle",
        )
        previous_exception = item.get("remediation_limit_exception")
        require(
            not previous_exception
            or (
                isinstance(previous_exception, dict)
                and previous_exception.get("status") == "CONSUMED"
            ),
            "an unconsumed remediation limit exception already exists",
        )
        parse_iso(str(event["authorized_at"]), "remediation exception authorization time")
        gate = proc.get("human_gates", {}).get(event["gate_id"])
        require(isinstance(gate, dict), "remediation exception requires a recorded human input gate")
        require(gate.get("kind") == "input", "remediation exception gate must be an input gate")
        require(gate.get("ticket_id") == event["ticket_id"], "remediation exception gate ticket mismatch")
        require(gate.get("prior_ticket_status") == "NEEDS_REMEDIATION", "remediation exception gate has invalid scope")
        require(gate.get("status") == "PROVIDED", "remediation exception gate is not resolved")
        require(
            gate.get("response_artifact") == event["user_decision_reference"],
            "remediation exception decision reference differs from the resolved gate",
        )
        if previous_exception:
            item.setdefault("remediation_limit_exception_history", []).append(dict(previous_exception))
        item["remediation_limit_exception"] = {
            "status": "GRANTED",
            "additional_cycles": EXCEPTIONAL_REMEDIATION_CYCLE_INCREMENT,
            "gate_id": event["gate_id"],
            "user_decision_reference": event["user_decision_reference"],
            "root_cause_reference": event["root_cause_reference"],
            "reason": event["reason"],
            "authorized_at": event["authorized_at"],
            "granted_at": now_iso(),
        }
        return

    if event_type == "REMEDIATION_DISPATCHED":
        item = ticket(proc, str(event.get("ticket_id") or ""))
        require(item.get("status") == "NEEDS_REMEDIATION", "remediation requires requested changes")
        cycles = int(item.get("remediation_cycles", 0))
        exception = usable_remediation_limit_exception(item)
        cycle_limit = (
            cycles + EXCEPTIONAL_REMEDIATION_CYCLE_INCREMENT
            if exception
            else AUTOMATIC_REMEDIATION_CYCLE_LIMIT
        )
        require(cycles < cycle_limit, "automatic remediation cycle limit reached")
        require_fields(
            event,
            ("phase_key", "base_commit", "branch", "criticality", "complexity", "change_kind", "model", "reasoning_effort"),
            "remediation dispatch",
        )
        require(event["change_kind"] in {"mechanical", "bounded-behavioral", "cross-cutting"}, "material-scope remediation must return to analysis reconciliation")
        context_packet = validate_compact_context(event, "remediation dispatch")
        require(context_packet["exact_base"] == event["base_commit"], "remediation context base mismatch")
        remediation_head = item.get("pull_request", {}).get("head_commit") or item.get("verification", {}).get("ticket_head")
        require(context_packet["exact_head"] == remediation_head, "remediation context head mismatch")
        effective = item.get("effective_classification") or item["analysis"]
        require(event["criticality"] == effective["criticality"], "remediation must use current effective criticality")
        expected = routed_phase_setting(
            REMEDIATION_MATRIX, event["criticality"], event["complexity"],
            bool(event.get("reasoning_authorized")), event=event,
            fast_path_field="mechanical_fast_path",
        )
        validate_routing(event, expected)
        validate_reasoning_authorization(
            proc, event, expected, stage="remediation",
            ticket_id=event["ticket_id"], head=remediation_head,
        )
        unity_requirement = event_unity_requirement(proc, event)
        implementation_phase = phase(proc, item["execution"]["implementation_phase_key"])
        add_phase(
            proc, key=event["phase_key"], ticket_id=event["ticket_id"], kind="remediation",
            model=event["model"], effort=event["reasoning_effort"], branch=event["branch"], base=event["base_commit"],
            context_packet=context_packet,
            unity_requirement=unity_requirement,
        )
        phase(proc, event["phase_key"])["forbidden_thread_id"] = implementation_phase.get("thread_id")
        phase(proc, event["phase_key"])["remediation_classification"] = {
            "criticality": event["criticality"],
            "complexity": event["complexity"],
            "change_kind": event["change_kind"],
        }
        if cycles >= AUTOMATIC_REMEDIATION_CYCLE_LIMIT:
            require(exception is not None, "exceptional remediation dispatch requires a granted exception")
            exception["status"] = "CONSUMED"
            exception["consumed_by_phase_key"] = event["phase_key"]
            exception["consumed_at"] = now_iso()
        item["remediation_cycles"] = cycles + 1
        item["status"] = "FIXING"
        return

    if event_type == "TICKET_MERGED":
        item = ticket(proc, str(event.get("ticket_id") or ""))
        require(item.get("status") == "READY_TO_MERGE", "ticket has not passed review and human gates")
        require_fields(event, ("merge_commit", "train_head"), "ticket merge")
        require(item.get("pull_request", {}).get("base_branch") == state["run_identity"]["train_branch"], "ticket PR base is not the train")
        item["merge"] = dict(event)
        item["status"] = "MERGED_INTO_TRAIN"
        proc["train_head"] = event["train_head"]
        return

    if event_type == "FINAL_BASE_MERGE_AUTHORIZED":
        final = proc["finalization"]
        require(
            final.get("status") in {"READY_FOR_COMPLETION", "COMPLETED"},
            "final train is not ready for base merge authorization",
        )
        require_fields(
            event,
            ("authorization_id", "head_commit", "pull_request_url", "user_decision_reference", "authorized_at"),
            "final base merge authorization",
        )
        require(event["head_commit"] == final.get("pull_request", {}).get("head_commit"), "final merge authorization head mismatch")
        require(event["pull_request_url"] == final.get("pull_request", {}).get("url"), "final merge authorization PR mismatch")
        parse_iso(str(event["authorized_at"]), "final merge authorization time")
        require(not final.get("base_merge_authorization"), "final base merge authorization already exists")
        final["base_merge_authorization"] = {
            "status": "AUTHORIZED",
            **{key: event[key] for key in (
                "authorization_id", "head_commit", "pull_request_url",
                "user_decision_reference", "authorized_at",
            )},
        }
        return

    if event_type == "FINAL_BASE_MERGED":
        final = proc["finalization"]
        authorization = final.get("base_merge_authorization")
        require(isinstance(authorization, dict) and authorization.get("status") == "AUTHORIZED", "final base merge lacks explicit user authorization")
        require_fields(event, ("head_commit", "merge_commit", "merged_at"), "final base merge")
        require(event["head_commit"] == final.get("pull_request", {}).get("head_commit"), "merged final head differs from reviewed head")
        require(event["head_commit"] == authorization.get("head_commit"), "merged final head differs from authorized head")
        parse_iso(str(event["merged_at"]), "final base merge time")
        authorization["status"] = "CONSUMED"
        final["base_merge"] = dict(event)
        final["status"] = "DELIVERED_IN_BASE"
        return

    if event_type == "DRY_RUN_EVIDENCE_RECORDED":
        require(state.get("execution_mode") == "dry-run", "dry-run evidence is only valid in dry-run mode")
        require(proc.get("dependencies_consolidated") is True, "dependency consolidation is incomplete")
        require(
            not any(gate.get("status", "").startswith("PENDING") for gate in proc["human_gates"].values()),
            "human analysis gates remain pending",
        )
        require_fields(
            event,
            (
                "token_reporting_status", "session_usage_ledger_ready", "analysis_reports_ready",
                "task_inventory_ready", "completion_report_ready", "ledger_reference", "ledger_sha256",
                "orchestration_metrics_ready", "orchestration_metrics_status",
                "orchestration_metrics_reference", "orchestration_metrics_sha256",
                "authoritative_phase_count", "measured_phase_count", "task_inventory_requested_count",
                "task_inventory_terminal_count",
            ),
            "dry-run evidence",
        )
        require_sha256(event["ledger_sha256"], "dry-run token ledger sha256")
        require_sha256(event["orchestration_metrics_sha256"], "dry-run orchestration metrics sha256")
        require(
            event["orchestration_metrics_status"] in {"complete", "partial", "unavailable"},
            "invalid dry-run orchestration metrics status",
        )
        require(isinstance(event.get("orchestrator_session_included"), bool), "dry-run token evidence must state orchestrator coverage")
        require(isinstance(event.get("hidden_sessions_reconciled"), bool), "dry-run token evidence must state hidden-session reconciliation")
        require("unmeasured_phase_keys" in event and isinstance(event["unmeasured_phase_keys"], list), "dry-run evidence requires unmeasured_phase_keys")
        require("unmeasured_session_ids" in event and isinstance(event["unmeasured_session_ids"], list), "dry-run evidence requires unmeasured_session_ids")
        require(event["token_reporting_status"] in {"complete", "partial", "unavailable"}, "invalid token reporting status")
        if event["token_reporting_status"] == "complete":
            require(event["orchestrator_session_included"] is True, "complete dry-run token report omits orchestrator usage")
            require(event["hidden_sessions_reconciled"] is True, "complete dry-run token report omits hidden-session reconciliation")
            require(event["measured_phase_count"] == event["authoritative_phase_count"], "complete dry-run token report has unmeasured phases")
            require(not event["unmeasured_phase_keys"] and not event["unmeasured_session_ids"], "complete dry-run token report has missing measurements")
        require(event["task_inventory_requested_count"] == len(proc["tickets"]), "dry-run task inventory requested count mismatch")
        require(event["task_inventory_terminal_count"] == len(proc["tickets"]), "dry-run task inventory terminal count mismatch")
        for field in (
            "session_usage_ledger_ready", "analysis_reports_ready", "task_inventory_ready",
            "completion_report_ready", "orchestration_metrics_ready",
        ):
            require(event[field] is True, f"{field} must be true")
        for value in proc["tickets"].values():
            require(value.get("analysis"), "every dry-run ticket requires analysis")
            value["status"] = "ANALYSIS_REPORTED"
        proc["finalization"] = {"status": "READY_FOR_COMPLETION", "dry_run_evidence": dict(event)}
        return

    if event_type == "FINALIZATION_STARTED":
        require(not any(p.get("launch_state") in ACTIVE_PHASE_STATES for p in proc["phases"].values()), "active phases remain")
        require(all(value.get("status") in TERMINAL_TICKET_STATES for value in proc["tickets"].values()), "requested tickets are not terminal")
        require(any(value.get("status") == "MERGED_INTO_TRAIN" for value in proc["tickets"].values()), "no ticket was integrated")
        if uses_unity_mcp(proc):
            proc["finalization"]["verification_unity_requirement"] = event_unity_requirement(
                proc, event, "final_verification_unity_requirement"
            )
        proc["finalization"]["status"] = "STARTED"
        proc["run_status"] = "TRAIN_FINALIZING"
        return

    if event_type == "FINAL_PR_RECORDED":
        final = proc["finalization"]
        require(final.get("status") in {"STARTED", "AWAITING_FINAL_PR_UPDATE"}, "final PR is not ready to be recorded or updated")
        require_fields(event, ("url", "base_branch", "base_commit", "head_branch", "head_commit"), "final pull request")
        require(isinstance(event.get("is_draft"), bool), "final pull request must record draft state")
        require(event["base_branch"] == proc["base_branch"], "final PR must target the resolved base branch")
        require(event["head_branch"] == state["run_identity"]["train_branch"], "final PR head must be the train branch")
        if final.get("status") == "AWAITING_FINAL_PR_UPDATE":
            require(final.get("remediation_merge", {}).get("train_head") == event["head_commit"], "final PR head does not match the merged remediation train head")
            require(event["head_commit"] != final.get("pull_request", {}).get("head_commit"), "remediation must produce a new final PR head")
            final.pop("verification", None)
            final.pop("review", None)
            final.pop("review_phase_key", None)
            final.pop("feedback_collection", None)
            final.pop("feedback_snapshot", None)
            final.pop("finding_ledger", None)
            final.pop("evidence", None)
        final["pull_request"] = dict(event)
        final["status"] = "FINAL_PR_OPEN"
        return

    if event_type == "FINAL_REMEDIATION_PR_RECORDED":
        final = proc["finalization"]
        require(final.get("status") == "AWAITING_FINAL_PR_UPDATE", "final remediation is not awaiting integration")
        require_fields(event, ("url", "base_branch", "base_commit", "head_branch", "head_commit"), "final remediation pull request")
        require(isinstance(event.get("is_draft"), bool), "final remediation pull request must record draft state")
        require(event["base_branch"] == state["run_identity"]["train_branch"], "final remediation PR must target the train branch")
        remediation_phases = [
            item for item in proc.get("phases", {}).values()
            if item.get("kind") == "final_remediation" and item.get("launch_state") == "COMPLETED"
        ]
        require(remediation_phases, "no completed final remediation phase exists")
        latest_phase = remediation_phases[-1]
        require(event["head_branch"] == latest_phase.get("branch"), "final remediation PR head branch mismatch")
        require(event["base_commit"] == latest_phase.get("base"), "final remediation PR base commit mismatch")
        require(event["head_commit"] != latest_phase.get("base"), "final remediation did not produce a new head")
        final["remediation_pull_request"] = dict(event)
        # A newly recorded remediation PR invalidates any merge evidence left
        # by a previous remediation cycle, including recovered legacy state.
        final.pop("remediation_merge", None)
        return

    if event_type == "FINAL_REMEDIATION_MERGED":
        final = proc["finalization"]
        require(final.get("status") == "AWAITING_FINAL_PR_UPDATE", "final remediation is not awaiting integration")
        pull_request = final.get("remediation_pull_request") or {}
        require_fields(event, ("head_commit", "merge_commit", "train_head", "merged_at"), "final remediation merge")
        require(event["head_commit"] == pull_request.get("head_commit"), "final remediation merge head mismatch")
        require(event["train_head"] == event["merge_commit"], "final remediation train head must equal the merge commit")
        parse_iso(str(event["merged_at"]), "final remediation merge time")
        require(not final.get("remediation_merge"), "final remediation merge is already recorded")
        final["remediation_merge"] = dict(event)
        proc["train_head"] = event["train_head"]
        return

    if event_type == "FINAL_PR_READY_RECORDED":
        final = proc["finalization"]
        require(final.get("status") == "FINAL_PR_OPEN", "final pull request is not open")
        require(event.get("head_commit") == final.get("pull_request", {}).get("head_commit"), "final PR ready head mismatch")
        require(event.get("is_draft") is False, "final pull request must be marked ready")
        final["pull_request"]["is_draft"] = False
        final["pull_request"]["ready_evidence_reference"] = event.get("evidence_reference")
        return

    if event_type == "FINAL_VERIFICATION_RECORDED":
        final = proc["finalization"]
        require(isinstance(final.get("pull_request"), dict), "final PR must exist before final verification")
        if uses_unity_mcp(proc):
            requirement, _, _ = expected_unity_operation(proc, "run:final-verification")
            if requirement != "none":
                require(
                    unity_lease_for_owner(proc, "run:final-verification") is not None,
                    "Unity-backed final verification requires an active slot lease",
                )
        validate_deterministic_verification(event, "final verification")
        require(isinstance(event.get("operational_change_applicable"), bool), "final verification must classify operational-change applicability")
        if event["operational_change_applicable"]:
            require(event.get("operational_preflight_status") == "passed", "final operational configuration preflight is incomplete")
            require(bool(event.get("operational_preflight_evidence")), "final operational preflight evidence is missing")
            inventory = event.get("required_configuration_inventory")
            require(isinstance(inventory, list) and inventory, "final operational verification requires a configuration inventory")
            require(all(isinstance(entry, dict) and entry.get("presence") == "verified" for entry in inventory), "required final operational configuration is missing")
        require(event.get("status") in {"passed", "failed"}, "final verification status must be passed or failed")
        require(event.get("head_commit") == final["pull_request"]["head_commit"], "final verification head mismatch")
        require_fields(event, ("head_commit", "evidence_reference"), "final verification")
        final["verification"] = dict(event)
        if event["status"] == "failed":
            final["status"] = "FINAL_VERIFICATION_FAILED"
        return

    if event_type == "FINAL_REVIEW_DISPATCHED":
        final = proc["finalization"]
        require(isinstance(final.get("pull_request"), dict), "final PR must exist before final review")
        require(final.get("pull_request", {}).get("is_draft") is False, "final PR must be ready before final review")
        require(final.get("verification", {}).get("status") == "passed", "final verification must pass before final review")
        require_fields(
            event,
            (
                "phase_key", "train_criticality", "train_complexity", "model",
                "reasoning_effort", "routing_conformance", "ticket_floor_evidence",
            ),
            "final review dispatch",
        )
        history = final.get("review_history", [])
        scope = event.get("scope") or ("followup" if history else "initial")
        require(scope in {"initial", "followup"}, "invalid final review scope")
        require(scope == ("followup" if history else "initial"), "final review scope does not match review history")
        material = event.get("material_scope_changed") is True
        context_packet = validate_compact_context(event, "final review dispatch")
        require(context_packet["exact_base"] == final["pull_request"]["base_commit"], "final review context base mismatch")
        require(context_packet["exact_head"] == final["pull_request"]["head_commit"], "final review context head mismatch")
        required_kind = "full" if scope == "initial" or material else "focused"
        require(event.get("review_kind") == required_kind, f"final {scope} review must be {required_kind}")
        matrix = FINAL_REVIEW_MATRIX if scope == "initial" or material else FOLLOWUP_REVIEW_MATRIX
        matrix_expected = routed_setting(
            matrix, event["train_criticality"], event["train_complexity"], bool(event.get("reasoning_authorized")),
        )
        integrated = {
            ticket_id: value
            for ticket_id, value in proc["tickets"].items()
            if value.get("status") == "MERGED_INTO_TRAIN"
        }
        require(integrated, "final review requires integrated tickets")
        floor_evidence = event["ticket_floor_evidence"]
        require(isinstance(floor_evidence, list), "ticket floor evidence must be a list")
        require(
            {entry.get("ticket_id") for entry in floor_evidence if isinstance(entry, dict)} == set(integrated),
            "ticket floor evidence must cover every integrated ticket exactly once",
        )
        require(
            len(floor_evidence) == len(integrated),
            "ticket floor evidence must cover every integrated ticket exactly once",
        )
        applicable_settings: list[tuple[str, str]] = []
        for entry in floor_evidence:
            require(isinstance(entry, dict), "ticket floor evidence entry must be an object")
            require_fields(
                entry,
                ("ticket_id", "applies", "reviewed_head", "reason", "review_reused"),
                "ticket floor evidence",
            )
            require(isinstance(entry["applies"], bool), "ticket floor applicability must be boolean")
            full_review = latest_trustworthy_full_review(integrated[entry["ticket_id"]])
            require(full_review is not None, f"ticket {entry['ticket_id']} lacks a trustworthy full review")
            require(entry["reviewed_head"] == full_review["reviewed_head"], "ticket floor reviewed head mismatch")
            triggers = (
                entry.get("exact_commit_review_invalidated") is True,
                entry.get("protected_surface_participates") is True,
                entry.get("unreviewed_integration_change") is True,
            )
            if entry["applies"]:
                require(any(triggers), "applicable ticket floor lacks integration-risk evidence")
                require(entry["review_reused"] is False, "an applicable ticket floor cannot be marked reused")
                applicable_settings.append((full_review["actual_model"], full_review["actual_reasoning_effort"]))
            else:
                require(not any(triggers), "non-applicable ticket floor contains an applicability trigger")
                require(entry["review_reused"] is True, "non-applicable ticket review must be explicitly reused")
        floor = strongest_review_setting(applicable_settings)
        selected = matrix_expected[:2]
        final_conformance = matrix_expected[2]
        if (scope == "initial" or material) and floor is not None:
            selected_name = setting_name(selected)
            floor_name = setting_name(floor)
            if floor_name not in FOLLOWUP_CEILING_COMPATIBILITY[selected_name]:
                selected = floor
            if selected[1] == "max" and not bool(event.get("reasoning_authorized")):
                selected = ("gpt-5.6-sol", "xhigh")
                final_conformance = "documented-fallback"
        if scope == "followup":
            full_history = [entry for entry in history if entry.get("review_kind") == "full"]
            require(full_history, "final followup review requires a trustworthy full-review baseline")
            baseline = (full_history[-1]["actual_model"], full_history[-1]["actual_reasoning_effort"])
            baseline_name = setting_name(baseline)
            require(
                setting_name(selected) in FOLLOWUP_CEILING_COMPATIBILITY.get(baseline_name, set()),
                "final followup review cannot exceed its full-review baseline",
            )
        require((event["model"], event["reasoning_effort"]) == selected, "final review setting does not satisfy matrix and applicable floor")
        require(event["routing_conformance"] == final_conformance, "final review routing conformance mismatch")
        auth_expected = (selected[0], selected[1], final_conformance)
        validate_reasoning_authorization(
            proc, event, auth_expected, stage="final-review",
            ticket_id="run", head=final["pull_request"]["head_commit"],
        )
        unity_requirement = event_unity_requirement(proc, event)
        add_phase(
            proc, key=event["phase_key"], ticket_id=None, kind="final_review",
            model=event["model"], effort=event["reasoning_effort"], branch=None,
            base=final["pull_request"]["head_commit"], scope=scope, context_packet=context_packet,
            unity_requirement=unity_requirement,
        )
        phase(proc, event["phase_key"])["review_kind"] = required_kind
        final["review_phase_key"] = event["phase_key"]
        final["status"] = "FINAL_PR_REVIEW"
        return

    if event_type == "FINAL_VERIFICATION_FAILURE_CLASSIFIED":
        final = proc["finalization"]
        require(final.get("status") == "FINAL_VERIFICATION_FAILED", "final train has no failed verification to classify")
        require_fields(event, ("failure_class", "evidence_reference", "next_step"), "final verification failure classification")
        require(
            event["failure_class"] in {
                "implementation-defect", "test-defect", "environment-defect",
                "contract-ambiguity", "infrastructure-flake",
            },
            "invalid final verification failure class",
        )
        require(event["next_step"] in {"remediate", "retry", "block"}, "invalid final verification failure next step")
        final["verification_failure"] = dict(event)
        if event["next_step"] == "remediate":
            final["status"] = "NEEDS_FINAL_REMEDIATION"
        elif event["next_step"] == "retry":
            final.pop("verification", None)
            final["status"] = "FINAL_PR_OPEN"
        else:
            final["status"] = "BLOCKED"
        return

    if event_type == "FINAL_REVIEW_RECORDED":
        final = proc["finalization"]
        require(isinstance(final.get("pull_request"), dict), "final PR must exist before final review")
        require(isinstance(final.get("verification"), dict), "final verification must pass before final review")
        completed = complete_phase(event, proc)
        require(completed.get("kind") == "final_review", "phase is not the final train review")
        require(event.get("review_kind") == completed.get("review_kind"), "final review kind mismatch")
        require(event.get("status") in {"clean", "changes_requested"}, "invalid final review status")
        require(event.get("finding_inventory_complete") is True, "final review inventory is incomplete")
        require(event.get("reviewed_head") == final["pull_request"]["head_commit"], "final reviewed head mismatch")
        require(event.get("routing_conformance") in {"conformant", "documented-fallback"}, "final review routing is nonconformant")
        require_fields(event, ("model", "reasoning_effort", "reviewed_head"), "final review")
        require(
            (event["model"], event["reasoning_effort"])
            == (completed["requested_model"], completed["requested_reasoning_effort"]),
            "final review actual routing differs from dispatch",
        )
        final["review"] = dict(event)
        history = final.setdefault("review_history", [])
        history.append({
            "scope": completed.get("scope"), "review_kind": completed.get("review_kind"),
            "reviewed_head": event["reviewed_head"], "status": event["status"],
            "actual_model": completed["requested_model"],
            "actual_reasoning_effort": completed["requested_reasoning_effort"],
            "routing_status": event["routing_conformance"],
        })
        final["status"] = "AWAITING_FINAL_FEEDBACK_COLLECTION"
        return

    if event_type == "FINAL_FEEDBACK_COLLECTION_STARTED":
        final = proc["finalization"]
        require(final.get("status") == "AWAITING_FINAL_FEEDBACK_COLLECTION", "final review must complete before feedback collection")
        require_fields(
            event,
            ("collection_id", "head_commit", "started_at", "deadline_at", "expected_sources"),
            "final feedback collection",
        )
        require(event["head_commit"] == final.get("pull_request", {}).get("head_commit"), "feedback collection head mismatch")
        require(isinstance(event["expected_sources"], list), "expected_sources must be a list")
        require(set(event["expected_sources"]) == {"codex", "ci", "copilot", "human"}, "all final feedback sources must be monitored")
        start_time = parse_iso(event["started_at"], "feedback start")
        deadline = parse_iso(event["deadline_at"], "feedback deadline")
        require(deadline >= start_time + timedelta(minutes=10), "feedback collection window must be at least 10 minutes")
        final["feedback_collection"] = dict(event)
        final["status"] = "FINAL_FEEDBACK_COLLECTION"
        return

    if event_type == "FINAL_FEEDBACK_SNAPSHOT_RECORDED":
        final = proc["finalization"]
        require(final.get("status") == "FINAL_FEEDBACK_COLLECTION", "final feedback collection has not started")
        collection = final.get("feedback_collection") or {}
        require_fields(
            event,
            (
                "snapshot_id", "collection_id", "head_commit", "collected_at", "ci_status",
                "copilot_status", "source_counts", "evidence_reference",
            ),
            "final feedback snapshot",
        )
        require("source_findings" in event, "final feedback snapshot is missing: source_findings")
        require("unresolved_thread_ids" in event, "final feedback snapshot is missing: unresolved_thread_ids")
        require(event["collection_id"] == collection.get("collection_id"), "feedback collection ID mismatch")
        require(event["head_commit"] == final.get("pull_request", {}).get("head_commit"), "feedback snapshot head mismatch")
        collected_at = parse_iso(event["collected_at"], "feedback collection time")
        deadline_at = parse_iso(collection["deadline_at"], "feedback deadline")
        quota_terminal_override = (
            event.get("copilot_status") == "unavailable"
            and event.get("terminal_unavailability_kind") == "quota_exhausted"
            and bool(event.get("copilot_status_evidence"))
            and bool(event.get("user_override_reference"))
            and event.get("ci_status") == "passed"
        )
        require(
            collected_at >= deadline_at or quota_terminal_override,
            "final feedback snapshot cannot close before the bounded deadline without an explicit terminal Copilot quota override",
        )
        require(event["ci_status"] in {"passed", "not_configured", "unavailable_with_local_fallback"}, "unacceptable final CI status")
        require(event["copilot_status"] in {"received", "not_configured", "unavailable", "timed_out"}, "Copilot collection is not terminal")
        if event["copilot_status"] in {"not_configured", "unavailable"}:
            require(bool(event.get("copilot_status_evidence")), "Copilot unavailable state requires evidence")
        counts = event["source_counts"]
        findings = event["source_findings"]
        require(isinstance(counts, dict), "source_counts must be an object")
        require(isinstance(findings, list), "source_findings must be a list")
        require(isinstance(event["unresolved_thread_ids"], list), "unresolved_thread_ids must be a list")
        expected_sources = set(collection["expected_sources"])
        require(set(counts) == expected_sources, "source_counts must cover every monitored source")
        seen_ids: set[str] = set()
        calculated = {source: 0 for source in expected_sources}
        for finding in findings:
            require(isinstance(finding, dict), "source finding must be an object")
            require_fields(finding, ("finding_id", "source"), "source finding")
            require(finding["source"] in expected_sources, "unexpected final finding source")
            require(finding["finding_id"] not in seen_ids, "duplicate final source finding ID")
            seen_ids.add(finding["finding_id"])
            calculated[finding["source"]] += 1
        require(all(counts[source] == calculated[source] for source in expected_sources), "source finding counts do not match inventory")
        final["feedback_snapshot"] = dict(event)
        final["status"] = "AWAITING_FINAL_FINDING_RECONCILIATION"
        return

    if event_type == "FINAL_FINDINGS_RECONCILED":
        final = proc["finalization"]
        require(final.get("status") == "AWAITING_FINAL_FINDING_RECONCILIATION", "final train is not awaiting finding reconciliation")
        snapshot = final.get("feedback_snapshot") or {}
        require_fields(
            event,
            (
                "head_commit", "feedback_snapshot_id", "ledger_status", "sources_dispositioned",
            ),
            "final finding ledger",
        )
        require("finding_dispositions" in event, "final finding ledger is missing: finding_dispositions")
        require("remaining_unresolved_thread_ids" in event, "final finding ledger is missing: remaining_unresolved_thread_ids")
        require(event["head_commit"] == final.get("pull_request", {}).get("head_commit"), "final finding ledger head mismatch")
        require(event["feedback_snapshot_id"] == snapshot.get("snapshot_id"), "finding ledger does not use the latest feedback snapshot")
        require(event["ledger_status"] == "complete", "final finding ledger is incomplete")
        require(isinstance(event["sources_dispositioned"], list), "sources_dispositioned must be a list")
        require(set(event["sources_dispositioned"]) == {"codex", "ci", "copilot", "human"}, "every final feedback source must be dispositioned")
        dispositions = event["finding_dispositions"]
        require(isinstance(dispositions, list), "finding_dispositions must be a list")
        source_ids = {finding["finding_id"] for finding in snapshot.get("source_findings", [])}
        disposition_ids: set[str] = set()
        for disposition in dispositions:
            require(isinstance(disposition, dict), "finding disposition must be an object")
            require_fields(
                disposition,
                ("finding_id", "disposition", "blocking", "remediation_status", "verification"),
                "finding disposition",
            )
            require(
                disposition["disposition"] in {
                    "accepted-fixed", "accepted-deferred", "rejected-incorrect",
                    "rejected-out-of-scope", "escalated-human-decision",
                },
                "invalid finding disposition",
            )
            require(disposition["finding_id"] not in disposition_ids, "duplicate finding disposition")
            disposition_ids.add(disposition["finding_id"])
        require(disposition_ids == source_ids, "every collected final finding requires exactly one disposition")
        require(isinstance(event.get("blocking_findings"), list), "blocking_findings must be a list")
        require(isinstance(event["remaining_unresolved_thread_ids"], list), "remaining_unresolved_thread_ids must be a list")
        require(set(event["blocking_findings"]).issubset(source_ids), "blocking findings must come from the collected snapshot")
        collected_thread_ids = {
            finding.get("thread_id") for finding in snapshot.get("source_findings", [])
            if finding.get("thread_id")
        }
        require(
            set(event["remaining_unresolved_thread_ids"]).issubset(collected_thread_ids),
            "remaining review threads must be present in the collected snapshot",
        )
        final["finding_ledger"] = dict(event)
        final["status"] = "NEEDS_FINAL_REMEDIATION" if event["blocking_findings"] else "FINAL_PR_REVIEW_CLEAN"
        return

    if event_type == "FINAL_REMEDIATION_DISPATCHED":
        final = proc["finalization"]
        require(final.get("status") == "NEEDS_FINAL_REMEDIATION", "final remediation requires requested changes")
        cycles = int(final.get("remediation_cycles", 0))
        require(cycles < 2, "final remediation cycle limit reached")
        require_fields(event, ("phase_key", "base_commit", "branch", "criticality", "complexity", "model", "reasoning_effort"), "final remediation dispatch")
        context_packet = validate_compact_context(event, "final remediation dispatch")
        require(context_packet["exact_base"] == event["base_commit"], "final remediation context base mismatch")
        require(context_packet["exact_head"] == event["base_commit"], "final remediation context head mismatch")
        expected = routed_phase_setting(
            REMEDIATION_MATRIX, event["criticality"], event["complexity"],
            bool(event.get("reasoning_authorized")), event=event,
            fast_path_field="mechanical_fast_path",
        )
        validate_routing(event, expected)
        validate_reasoning_authorization(
            proc, event, expected, stage="final-remediation",
            ticket_id="run", head=event["base_commit"],
        )
        unity_requirement = event_unity_requirement(proc, event)
        require(event["base_commit"] == final["pull_request"]["head_commit"], "final remediation base must match final PR head")
        add_phase(
            proc, key=event["phase_key"], ticket_id=None, kind="final_remediation",
            model=event["model"], effort=event["reasoning_effort"], branch=event["branch"],
            base=event["base_commit"], scope="final", context_packet=context_packet,
            unity_requirement=unity_requirement,
        )
        # Pull-request and merge evidence is scoped to one remediation cycle.
        # Keeping the previous cycle here makes the controller skip recording
        # and merging the newly dispatched remediation pull request.
        final.pop("remediation_pull_request", None)
        final.pop("remediation_merge", None)
        final["remediation_cycles"] = cycles + 1
        final["status"] = "FINAL_PR_FIXING"
        return

    if event_type == "FINAL_EVIDENCE_RECORDED":
        final = proc["finalization"]
        require(final.get("status") == "FINAL_PR_REVIEW_CLEAN", "final review must be clean first")
        require(final.get("finding_ledger", {}).get("ledger_status") == "complete", "final findings must be reconciled first")
        require_fields(
            event,
            (
                "feedback_snapshot_id", "ci_status", "copilot_status", "finding_ledger_status", "token_reporting_status",
                "session_usage_ledger_ready", "verification_summary_ready", "manual_validation_summary_ready",
                "attention_points_summary_ready", "task_inventory_ready", "completion_report_ready",
                "ledger_reference", "ledger_sha256", "authoritative_phase_count", "measured_phase_count",
                "orchestration_metrics_ready", "orchestration_metrics_status",
                "orchestration_metrics_reference", "orchestration_metrics_sha256",
                "task_inventory_requested_count", "task_inventory_terminal_count",
            ),
            "final evidence",
        )
        require_sha256(event["ledger_sha256"], "token ledger sha256")
        require_sha256(event["orchestration_metrics_sha256"], "orchestration metrics sha256")
        require(
            event["orchestration_metrics_status"] in {"complete", "partial", "unavailable"},
            "invalid orchestration metrics status",
        )
        require(isinstance(event.get("orchestrator_session_included"), bool), "token evidence must state orchestrator coverage")
        require(isinstance(event.get("hidden_sessions_reconciled"), bool), "token evidence must state hidden-session reconciliation")
        require("unmeasured_phase_keys" in event and isinstance(event["unmeasured_phase_keys"], list), "token evidence requires unmeasured_phase_keys")
        require("unmeasured_session_ids" in event and isinstance(event["unmeasured_session_ids"], list), "token evidence requires unmeasured_session_ids")
        snapshot = final.get("feedback_snapshot") or {}
        require(event["feedback_snapshot_id"] == snapshot.get("snapshot_id"), "final evidence does not use the latest feedback snapshot")
        require(event["ci_status"] == snapshot.get("ci_status"), "final CI status differs from the collected snapshot")
        require(event["copilot_status"] == snapshot.get("copilot_status"), "final Copilot status differs from the collected snapshot")
        require(event["ci_status"] in {"passed", "not_configured", "unavailable_with_local_fallback"}, "unacceptable final CI status")
        require(event["copilot_status"] in {"received", "not_configured", "unavailable", "timed_out"}, "invalid Copilot status")
        require(event["finding_ledger_status"] == "complete", "final finding ledger is incomplete")
        require(event["token_reporting_status"] in {"complete", "partial", "unavailable"}, "invalid token reporting status")
        if event["token_reporting_status"] == "complete":
            require(event["orchestrator_session_included"] is True, "complete token report omits orchestrator usage")
            require(event["hidden_sessions_reconciled"] is True, "complete token report omits hidden-session reconciliation")
            require(event["measured_phase_count"] == event["authoritative_phase_count"], "complete token report has unmeasured phases")
            require(not event["unmeasured_phase_keys"] and not event["unmeasured_session_ids"], "complete token report has missing measurements")
        require(
            event["task_inventory_requested_count"] == len(proc["tickets"]),
            "task inventory requested count differs from selected tickets",
        )
        require(
            event["task_inventory_terminal_count"] == len(proc["tickets"]),
            "task inventory omits a non-terminal ticket",
        )
        for field in (
            "session_usage_ledger_ready", "verification_summary_ready", "manual_validation_summary_ready",
            "attention_points_summary_ready", "task_inventory_ready", "completion_report_ready",
            "orchestration_metrics_ready",
        ):
            require(event[field] is True, f"{field} must be true")
        final["evidence"] = dict(event)
        final["status"] = "READY_FOR_COMPLETION"
        return

    if event_type == "COST_ANOMALY_RESOLVED":
        anomalies = unresolved_cost_anomalies(proc)
        anomaly = next((item for item in anomalies if item["anomaly_id"] == event.get("anomaly_id")), None)
        require(isinstance(anomaly, dict), "unknown or already resolved cost anomaly")
        resolution = event.get("resolution")
        require(
            resolution in {"restart-fresh-compact", "continue-quality-neutral", "user-approved-quality-tradeoff"},
            "invalid cost anomaly resolution",
        )
        if resolution == "user-approved-quality-tradeoff":
            require(bool(event.get("user_decision_reference")), "quality tradeoff requires explicit user authorization")
        anomaly["status"] = "RESOLVED"
        anomaly["resolution"] = resolution
        anomaly["resolution_evidence"] = event.get("resolution_evidence")
        anomaly["resolved_at"] = now_iso()
        if not unresolved_cost_anomalies(proc):
            proc["cost_control"]["status"] = "CLEAR"
        return

    if event_type == "RUN_COMPLETED":
        issues = completion_issues(state)
        require(not issues, "completion rejected: " + "; ".join(issues))
        proc["run_status"] = "COMPLETED"
        proc["finalization"]["status"] = "COMPLETED"
        proc["supervision"]["status"] = "INACTIVE"
        state["supervision"] = dict(proc["supervision"])
        state["run_status"] = "COMPLETED"
        return

    raise ControllerError(f"unsupported event type: {event_type}")


def completion_issues(state: dict[str, Any]) -> list[str]:
    proc = procedure(state)
    issues: list[str] = []
    allowed_terminal = (
        TERMINAL_TICKET_STATES
        if state.get("execution_mode") == "dry-run"
        else {"MERGED_INTO_TRAIN", "BLOCKED", "FAILED", "CANCELLED"}
    )
    if not all(value.get("status") in allowed_terminal for value in proc["tickets"].values()):
        issues.append("requested tickets are not terminal")
    if any(value.get("launch_state") in ACTIVE_PHASE_STATES for value in proc["phases"].values()):
        issues.append("active or ambiguous phases remain")
    if unresolved_cost_anomalies(proc):
        issues.append("unresolved cost anomaly checkpoint remains")
    final = proc.get("finalization", {})
    # RUN_COMPLETED transitions the state from READY_FOR_COMPLETION to COMPLETED.
    # A post-transition audit must therefore accept both values; otherwise the
    # controller reports its own successfully completed run as incomplete.
    if final.get("status") not in {"READY_FOR_COMPLETION", "COMPLETED"}:
        issues.append("finalization evidence is incomplete")
    if state.get("execution_mode") == "dry-run":
        evidence = final.get("dry_run_evidence") or {}
        if not evidence.get("completion_report_ready"):
            issues.append("dry-run completion report is missing")
        return issues
    pr = final.get("pull_request") or {}
    verification = final.get("verification") or {}
    review = final.get("review") or {}
    snapshot = final.get("feedback_snapshot") or {}
    ledger = final.get("finding_ledger") or {}
    if not pr.get("url"):
        issues.append("final train PR is missing")
    if pr.get("is_draft") is not False:
        issues.append("final train PR is still draft or readiness is unknown")
    if verification.get("status") != "passed":
        issues.append("final deterministic verification did not pass")
    if pr.get("head_commit") != verification.get("head_commit"):
        issues.append("final verification does not cover the PR head")
    if pr.get("head_commit") != review.get("reviewed_head"):
        issues.append("final review does not cover the PR head")
    if pr.get("head_commit") != snapshot.get("head_commit"):
        issues.append("final GitHub feedback snapshot does not cover the PR head")
    if ledger.get("feedback_snapshot_id") != snapshot.get("snapshot_id"):
        issues.append("final finding ledger does not use the latest GitHub feedback snapshot")
    evidence = final.get("evidence") or {}
    if evidence.get("token_reporting_status") == "complete":
        if evidence.get("orchestrator_session_included") is not True:
            issues.append("complete token ledger omits orchestrator usage")
        if evidence.get("hidden_sessions_reconciled") is not True:
            issues.append("complete token ledger omits hidden-session reconciliation")
        if evidence.get("measured_phase_count") != evidence.get("authoritative_phase_count"):
            issues.append("complete token ledger has unmeasured authoritative phases")
    if uses_unity_mcp(proc) and active_unity_leases(proc):
        issues.append("Unity editor slot leases remain active")
    return issues


def unity_release_actions(proc: dict[str, Any]) -> list[dict[str, Any]]:
    if not uses_unity_mcp(proc) or environment(proc).get("status") != "READY":
        return []
    releases: list[dict[str, Any]] = []
    for slot_id, slot in unity_slots(proc).items():
        lease = slot.get("lease") if isinstance(slot, dict) else None
        if not isinstance(lease, dict):
            continue
        owner_key = str(lease.get("owner_key") or "")
        keep = False
        if owner_key in proc.get("phases", {}):
            keep = phase(proc, owner_key).get("launch_state") in ACTIVE_PHASE_STATES | {"INPUT_READY"}
        elif owner_key.startswith("ticket:") and owner_key.endswith(":verification"):
            ticket_id = owner_key[len("ticket:") : -len(":verification")]
            keep = ticket(proc, ticket_id).get("status") in {
                "AWAITING_VERIFICATION", "AWAITING_REMEDIATION_VERIFICATION",
            }
        elif owner_key == "run:final-verification":
            final = proc.get("finalization", {})
            keep = isinstance(final.get("pull_request"), dict) and not final.get("verification")
        if not keep:
            releases.append(
                {
                    "action": "RELEASE_UNITY_SLOT_DETERMINISTICALLY",
                    "owner_key": owner_key,
                    "slot_id": slot_id,
                    "lease_id": lease.get("lease_id"),
                    "registry_reference": environment(proc).get("registry_reference"),
                    "close_editor": False,
                    "model_tokens": 0,
                }
            )
    return releases


def unity_slot_payload(proc: dict[str, Any], owner_key: str) -> dict[str, Any] | None:
    found = unity_lease_for_owner(proc, owner_key)
    if found is None:
        return None
    slot_id, lease = found
    slot = unity_slots(proc)[slot_id]
    return {
        "slot_id": slot_id,
        "path": slot.get("path"),
        "lease_id": lease.get("lease_id"),
        "requirement": lease.get("requirement"),
        "registry_reference": environment(proc).get("registry_reference"),
        "mcp_mode": "local",
    }


def unity_acquire_action(
    proc: dict[str, Any], *, owner_key: str, requirement: str, branch: str | None,
    expected_head: str,
) -> dict[str, Any]:
    return {
        "action": "ACQUIRE_UNITY_SLOT_DETERMINISTICALLY",
        "owner_key": owner_key,
        "requirement": validate_unity_requirement(requirement, "Unity slot requirement"),
        "branch": branch,
        "expected_head": expected_head,
        "registry_reference": environment(proc).get("registry_reference"),
        "mcp_mode": "local",
        "recovery_attempts": 2,
        "model_tokens": 0,
    }


def next_actions(state: dict[str, Any]) -> list[dict[str, Any]]:
    proc = procedure(state)
    if proc.get("run_status") == "COMPLETED":
        return []
    pending_handoff = state.get("pending_orchestrator_handoff")
    if isinstance(pending_handoff, dict) and pending_handoff.get("status") == "PREPARED":
        return [{
            "action": "COMPLETE_CONTROLLED_ORCHESTRATOR_HANDOFF",
            "from_thread_id": pending_handoff.get("from_thread_id"),
            "packet_reference": pending_handoff.get("packet_reference"),
            "expires_at": pending_handoff.get("expires_at"),
        }]
    if not proc.get("orchestrator_confirmed"):
        return [{"action": "REQUEST_ORCHESTRATOR_CONFIRMATION"}]
    supervision = proc.get("supervision", {})
    current_owner_thread_id = (state.get("orchestrator_lease") or {}).get("owner_thread_id")
    if (
        supervision.get("status") == "ACTIVE"
        and supervision.get("mode") == "BACKGROUND_WATCHER"
        and supervision.get("watcher_consumes_model_tokens") is not False
    ):
        return [{
            "action": "REPLACE_MODEL_WAKING_WATCHER",
            "watcher_id": supervision.get("watcher_id"),
            "allowed_replacements": ["FOREGROUND_WAIT", "EVENT_CALLBACK", "ZERO_MODEL_BACKGROUND_WATCHER"],
        }]
    if (
        supervision.get("status") == "ACTIVE"
        and supervision.get("mode") == "EVENT_CALLBACK"
        and supervision.get("callback_target_thread_id") != current_owner_thread_id
    ):
        return [{
            "action": "RECONFIGURE_EVENT_CALLBACKS_FOR_CURRENT_OWNER",
            "target_thread_id": current_owner_thread_id,
            "phase_keys": [
                value.get("phase_key") for value in proc.get("phases", {}).values()
                if isinstance(value, dict) and value.get("launch_state") in ACTIVE_PHASE_STATES
            ],
        }]
    if supervision.get("status") == "PAUSED_HUMAN_GATE":
        waiting_gates = [
            gate for gate in proc["human_gates"].values()
            if gate.get("status") == "PENDING_ANNOUNCED"
        ]
        pending_action = state.get("pending_human_action")
        if waiting_gates and isinstance(pending_action, dict):
            return [{"action": "AWAIT_HUMAN_GATE", "gate": gate} for gate in waiting_gates]
    if supervision.get("status") != "ACTIVE":
        return [{"action": "CONFIGURE_SUPERVISION_BEFORE_DISPATCH"}]
    anomalies = unresolved_cost_anomalies(proc)
    if anomalies:
        return [{"action": "RESOLVE_COST_ANOMALY_CHECKPOINT", "anomalies": anomalies}]
    if uses_unity_mcp(proc) and environment(proc).get("status") != "READY":
        return [{
            "action": "INITIALIZE_UNITY_SLOTS_DETERMINISTICALLY",
            "repository": environment(proc).get("repository"),
            "base_ref": proc.get("train_head") or proc.get("base_branch"),
            "max_editors": proc["limits"]["max_unity_editors"],
            "mcp_mode": "local",
            "script": "scripts/unity_slot_manager.py",
            "model_tokens": 0,
        }]

    releases = unity_release_actions(proc)
    if releases:
        return releases

    unannounced = [gate for gate in proc["human_gates"].values() if gate["status"] == "PENDING_UNANNOUNCED"]
    if unannounced and not state.get("pending_human_action"):
        return [{"action": "ANNOUNCE_HUMAN_GATE", "gate": unannounced[0]}]
    waiting_gates = [gate for gate in proc["human_gates"].values() if gate["status"] == "PENDING_ANNOUNCED"]
    gate_actions = [{"action": "AWAIT_HUMAN_GATE", "gate": gate} for gate in waiting_gates]

    intents = [value for value in proc["phases"].values() if value.get("launch_state") == "INTENT_RECORDED"]
    if intents:
        actions: list[dict[str, Any]] = []
        available_unity = max(0, int(proc["limits"].get("max_unity_editors", 0)) - active_unity_leases(proc))
        for value in intents:
            requirement = value.get("unity_requirement", "none")
            slot_payload = unity_slot_payload(proc, value["phase_key"])
            if requirement != "none" and slot_payload is None:
                if available_unity > 0:
                    actions.append(unity_acquire_action(
                        proc,
                        owner_key=value["phase_key"],
                        requirement=requirement,
                        branch=value.get("branch"),
                        expected_head=value.get("resume_head") or value["base"],
                    ))
                    available_unity -= 1
                else:
                    actions.append({
                        "action": "WAIT_FOR_UNITY_SLOT",
                        "owner_key": value["phase_key"],
                        "requirement": requirement,
                    })
                continue
            action = {
                "action": "DISPATCH_VISIBLE_PHASE",
                "phase_key": value["phase_key"],
                "kind": value["kind"],
                "ticket_id": value.get("ticket_id"),
                "model": value["requested_model"],
                "reasoning_effort": value["requested_reasoning_effort"],
                "base": value["base"],
                "branch": value.get("branch"),
                "context_packet": value.get("context_packet"),
                "required_visibility": "user-visible",
                "completion_callback": {
                    "target_thread_id": (state.get("orchestrator_lease") or {}).get("owner_thread_id"),
                    "notify_on": ["completed", "failed", "blocked", "needs_input"],
                    "message_contract": "ticket-train-phase-envelope-v1",
                },
            }
            if slot_payload:
                action["unity_slot"] = slot_payload
            actions.append(action)
        return actions + gate_actions
    input_ready = [value for value in proc["phases"].values() if value.get("launch_state") == "INPUT_READY"]
    if input_ready:
        actions = []
        available_unity = max(0, int(proc["limits"].get("max_unity_editors", 0)) - active_unity_leases(proc))
        for value in input_ready:
            requirement = value.get("unity_requirement", "none")
            slot_payload = unity_slot_payload(proc, value["phase_key"])
            if requirement != "none" and slot_payload is None:
                if available_unity > 0:
                    actions.append(unity_acquire_action(
                        proc,
                        owner_key=value["phase_key"],
                        requirement=requirement,
                        branch=value.get("branch"),
                        expected_head=value.get("resume_head") or value["base"],
                    ))
                    available_unity -= 1
                else:
                    actions.append({"action": "WAIT_FOR_UNITY_SLOT", "owner_key": value["phase_key"], "requirement": requirement})
                continue
            action = {
                "action": "RESUME_VISIBLE_PHASE_WITH_INPUT",
                "phase_key": value["phase_key"],
                "thread_id": value.get("thread_id"),
                "provided_input": value.get("provided_input"),
            }
            if slot_payload:
                action["unity_slot"] = slot_payload
            actions.append(action)
        return actions + gate_actions
    unknown = [value for value in proc["phases"].values() if value.get("launch_state") == "LAUNCH_UNKNOWN"]
    if unknown:
        return [{"action": "RECONCILE_AMBIGUOUS_LAUNCH", "phase_keys": [value["phase_key"] for value in unknown]}] + gate_actions
    active = [value for value in proc["phases"].values() if value.get("launch_state") in {"QUEUED", "RUNNING"}]

    untriaged = [ticket_id for ticket_id, value in proc["tickets"].items() if value["status"] == "DISCOVERED"]
    if untriaged:
        triage_phases = [value for value in proc["phases"].values() if value.get("kind") == "triage"]
        if not triage_phases:
            return [{"action": "RECORD_BATCH_TRIAGE_DISPATCH_INTENT", "tickets": untriaged[:5]}]
        if triage_phases[-1].get("launch_state") == "COMPLETED":
            return [{"action": "RECORD_TRIAGE_RESULTS", "tickets": untriaged[:5]}]
        return [{"action": "WAIT_FOR_PHASE_TRANSITION", "phase_keys": [triage_phases[-1]["phase_key"]]}] + gate_actions
    unanalyzed = [ticket_id for ticket_id, value in proc["tickets"].items() if value["status"] == "TRIAGED"]
    if unanalyzed:
        actions: list[dict[str, Any]] = []
        active_analysis_count = sum(
            1 for value in proc["phases"].values()
            if value.get("kind") == "analysis" and value.get("launch_state") in {"INTENT_RECORDED", "QUEUED", "RUNNING"}
        )
        capacity = max(0, int(proc["limits"]["max_active_analyses"]) - active_analysis_count)
        for ticket_id in unanalyzed:
            analysis_phases = [
                value for value in proc["phases"].values()
                if value.get("kind") == "analysis" and value.get("ticket_id") == ticket_id
            ]
            if analysis_phases and analysis_phases[-1].get("launch_state") == "COMPLETED":
                actions.append({"action": "RECORD_ANALYSIS_RESULT", "ticket_id": ticket_id})
            elif analysis_phases and analysis_phases[-1].get("launch_state") == "BLOCKED" and capacity > 0:
                actions.append({
                    "action": "RECORD_ANALYSIS_DISPATCH_INTENT",
                    "ticket_id": ticket_id,
                    "unity_requirement": proc["tickets"][ticket_id].get("triage", {}).get("analysis_unity_requirement", "none"),
                    "retry_of_phase_key": analysis_phases[-1]["phase_key"],
                })
                capacity -= 1
            elif not analysis_phases and capacity > 0:
                actions.append({
                    "action": "RECORD_ANALYSIS_DISPATCH_INTENT",
                    "ticket_id": ticket_id,
                    "unity_requirement": proc["tickets"][ticket_id].get("triage", {}).get("analysis_unity_requirement", "none"),
                })
                capacity -= 1
        if active:
            actions.append({"action": "WAIT_FOR_PHASE_TRANSITION", "phase_keys": [value["phase_key"] for value in active]})
        return actions + gate_actions
    route_validation_actions: list[dict[str, Any]] = []
    route_validation_pending = False
    for ticket_id, value in proc["tickets"].items():
        if value.get("status") != "ANALYSIS_ROUTE_VALIDATION_REQUIRED":
            continue
        route_validation_pending = True
        validation_phases = [
            phase_value for phase_value in proc["phases"].values()
            if phase_value.get("kind") == "analysis_route_validation"
            and phase_value.get("ticket_id") == ticket_id
        ]
        if validation_phases and validation_phases[-1].get("launch_state") == "COMPLETED":
            route_validation_actions.append({
                "action": "RECORD_ANALYSIS_ROUTE_VALIDATION_RESULT",
                "ticket_id": ticket_id,
                "phase_key": validation_phases[-1]["phase_key"],
                "analysis_revision": value["analysis"]["analysis_revision"],
            })
        elif not validation_phases:
            route_validation_actions.append({
                "action": "RECORD_ANALYSIS_ROUTE_VALIDATION_DISPATCH_INTENT",
                "ticket_id": ticket_id,
                "analysis_revision": value["analysis"]["analysis_revision"],
                "required_model": value["analysis_route_validation_required"]["required_model"],
                "required_reasoning_effort": value["analysis_route_validation_required"]["required_reasoning_effort"],
            })
    if route_validation_pending:
        if active:
            route_validation_actions.append({
                "action": "WAIT_FOR_PHASE_TRANSITION",
                "phase_keys": [value["phase_key"] for value in active],
            })
        if not route_validation_actions:
            route_validation_actions.append({
                "action": "BLOCKED_OR_INCONSISTENT_STATE",
                "reason": "Analysis route validation is pending without an active or completed phase.",
            })
        return route_validation_actions + gate_actions
    reconciliation_required = [
        {
            "ticket_id": ticket_id,
            "status": value.get("status"),
            "required_transition": (
                "reconcile the targeted analysis validation"
                if value.get("status") == "ANALYSIS_RECONCILIATION_REQUIRED"
                else "amend and revalidate the implementation contract"
            ),
        }
        for ticket_id, value in proc["tickets"].items()
        if value.get("status") in {"ANALYSIS_RECONCILIATION_REQUIRED", "NEEDS_CONTRACT_AMENDMENT"}
    ]
    if reconciliation_required:
        return [{
            "action": "BLOCKED_OR_INCONSISTENT_STATE",
            "reason": "A failed routed validation requires an explicit reconciliation transition.",
            "tickets": reconciliation_required,
        }] + gate_actions
    if not proc.get("dependencies_consolidated"):
        return [{"action": "CONSOLIDATE_DEPENDENCIES"}]

    if state.get("execution_mode") == "dry-run":
        if waiting_gates:
            return [{"action": "AWAIT_HUMAN_GATE", "gate": gate} for gate in waiting_gates]
        if not proc["finalization"].get("dry_run_evidence"):
            return [{"action": "RECORD_DRY_RUN_REPORT_AND_USAGE_EVIDENCE"}]
        if proc.get("run_status") != "COMPLETED":
            return [{"action": "COMPLETE_RUN"}]
    contract_actions: list[dict[str, Any]] = []
    for ticket_id, value in proc["tickets"].items():
        if (
            value.get("status") == "READY_FOR_IMPLEMENTATION"
            and value.get("analysis", {}).get("complexity") in {"HIGH", "MAXIMUM"}
            and value.get("plan_contract_validation", {}).get("status") != "passed"
        ):
            phases = [
                phase_value for phase_value in proc["phases"].values()
                if phase_value.get("kind") == "plan_contract_validation"
                and phase_value.get("ticket_id") == ticket_id
            ]
            if phases and phases[-1].get("launch_state") == "COMPLETED":
                contract_actions.append({
                    "action": "RECORD_PLAN_CONTRACT_VALIDATION_RESULT",
                    "ticket_id": ticket_id,
                    "phase_key": phases[-1]["phase_key"],
                })
            elif not phases:
                contract_actions.append({
                    "action": "RECORD_PLAN_CONTRACT_VALIDATION_DISPATCH_INTENT",
                    "ticket_id": ticket_id,
                })
    if contract_actions:
        return contract_actions + gate_actions
    ready = [
        ticket_id for ticket_id, value in proc["tickets"].items()
        if value["status"] == "READY_FOR_IMPLEMENTATION"
        and dependencies_satisfied(proc, value)
        and (
            value.get("analysis", {}).get("complexity") not in {"HIGH", "MAXIMUM"}
            or value.get("plan_contract_validation", {}).get("status") == "passed"
        )
    ]
    capacity = int(proc["limits"]["max_active_execution_pairs"]) - active_execution_pairs(proc)
    if ready and capacity > 0:
        actions = [
            {"action": "DISPATCH_EXECUTION_PAIR_ATOMICALLY", "ticket_id": ticket_id}
            for ticket_id in ready[:capacity]
        ]
        if active:
            actions.append({"action": "WAIT_FOR_PHASE_TRANSITION", "phase_keys": [value["phase_key"] for value in active]})
        return actions + gate_actions
    if active:
        return [{"action": "WAIT_FOR_PHASE_TRANSITION", "phase_keys": [value["phase_key"] for value in active]}] + gate_actions
    if waiting_gates:
        return [{"action": "AWAIT_HUMAN_GATE", "gate": gate} for gate in waiting_gates]

    actions: list[dict[str, Any]] = []
    available_unity = max(0, int(proc["limits"].get("max_unity_editors", 0)) - active_unity_leases(proc))
    for ticket_id, value in proc["tickets"].items():
        status = value["status"]
        if status == "AWAITING_EXECUTION_INTEGRATION":
            actions.append({
                "action": "INTEGRATE_EXECUTION_PAIR_DETERMINISTICALLY",
                "ticket_id": ticket_id,
                "implementation_branch": value.get("execution", {}).get("implementation_branch"),
                "acceptance_branch": value.get("execution", {}).get("acceptance_branch"),
                "model_tokens": 0,
            })
        elif status in {"AWAITING_VERIFICATION", "AWAITING_REMEDIATION_VERIFICATION"}:
            owner_key = f"ticket:{ticket_id}:verification"
            requirement, branch, expected_head = expected_unity_operation(proc, owner_key) if uses_unity_mcp(proc) else ("none", None, "")
            slot_payload = unity_slot_payload(proc, owner_key)
            if requirement != "none" and slot_payload is None:
                if available_unity > 0:
                    actions.append(unity_acquire_action(
                        proc, owner_key=owner_key, requirement=requirement,
                        branch=branch, expected_head=expected_head,
                    ))
                    available_unity -= 1
                else:
                    actions.append({"action": "WAIT_FOR_UNITY_SLOT", "owner_key": owner_key, "requirement": requirement})
            else:
                action = {"action": "RUN_DETERMINISTIC_TICKET_VERIFICATION", "ticket_id": ticket_id, "model_tokens": 0}
                if slot_payload:
                    action["unity_slot"] = slot_payload
                actions.append(action)
        elif status == "VERIFICATION_FAILED":
            actions.append({"action": "CLASSIFY_VERIFICATION_FAILURE", "ticket_id": ticket_id})
        elif status == "FUNCTIONAL_READY" and (
            not value.get("pull_request")
            or value["pull_request"].get("head_commit") != value.get("verification", {}).get("ticket_head")
        ):
            actions.append({"action": "CREATE_OR_UPDATE_TICKET_PR_TARGETING_TRAIN", "ticket_id": ticket_id})
        elif status == "FUNCTIONAL_READY" and value.get("pull_request", {}).get("is_draft") is True:
            actions.append({"action": "MARK_TICKET_PR_READY", "ticket_id": ticket_id})
        elif status == "FUNCTIONAL_READY" and value.get("reviews"):
            actions.append({"action": "DISPATCH_FOCUSED_FOLLOWUP_REVIEW", "ticket_id": ticket_id})
        elif status == "FUNCTIONAL_READY":
            actions.append({"action": "DISPATCH_EXHAUSTIVE_INITIAL_REVIEW", "ticket_id": ticket_id})
        elif status == "AWAITING_FINDING_RECONCILIATION":
            actions.append({"action": "RECONCILE_CODEX_CI_COPILOT_FINDINGS", "ticket_id": ticket_id})
        elif status == "NEEDS_REMEDIATION":
            cycles = int(value.get("remediation_cycles", 0))
            if cycles < AUTOMATIC_REMEDIATION_CYCLE_LIMIT or usable_remediation_limit_exception(value):
                actions.append({"action": "DISPATCH_FRESH_BATCHED_REMEDIATION", "ticket_id": ticket_id})
            else:
                actions.append({
                    "action": "ROOT_CAUSE_CHECKPOINT_REQUIRED",
                    "ticket_id": ticket_id,
                    "remediation_cycles": cycles,
                })
        elif status == "READY_TO_MERGE":
            actions.append({"action": "MERGE_TICKET_PR_INTO_TRAIN", "ticket_id": ticket_id})
    if actions:
        return actions

    if all(value["status"] in TERMINAL_TICKET_STATES for value in proc["tickets"].values()):
        final = proc["finalization"]
        if final.get("status") == "NOT_STARTED":
            return [{
                "action": "START_FINALIZATION",
                "classify_final_verification_unity_requirement": uses_unity_mcp(proc),
            }]
        if final.get("status") == "NEEDS_FINAL_REMEDIATION":
            return [{"action": "RECORD_FINAL_REMEDIATION_DISPATCH_INTENT"}]
        if final.get("status") == "FINAL_VERIFICATION_FAILED":
            return [{"action": "CLASSIFY_FINAL_VERIFICATION_FAILURE"}]
        if final.get("status") == "AWAITING_FINAL_FEEDBACK_COLLECTION":
            return [{
                "action": "START_FINAL_GITHUB_FEEDBACK_COLLECTION",
                "head_commit": final.get("pull_request", {}).get("head_commit"),
                "sources": ["codex", "ci", "copilot", "human"],
            }]
        if final.get("status") == "FINAL_FEEDBACK_COLLECTION":
            return [{
                "action": "POLL_FINAL_FEEDBACK_DETERMINISTICALLY",
                "head_commit": final.get("pull_request", {}).get("head_commit"),
                "collection_id": final.get("feedback_collection", {}).get("collection_id"),
                "deadline_at": final.get("feedback_collection", {}).get("deadline_at"),
                "model_tokens": 0,
            }]
        if final.get("status") == "AWAITING_FINAL_FINDING_RECONCILIATION":
            return [{"action": "RECONCILE_FINAL_CODEX_CI_COPILOT_FINDINGS"}]
        if final.get("status") == "AWAITING_FINAL_PR_UPDATE":
            if not final.get("remediation_pull_request"):
                return [{"action": "RECORD_FINAL_REMEDIATION_PR"}]
            if not final.get("remediation_merge"):
                return [{"action": "MERGE_FINAL_REMEDIATION_PR_INTO_TRAIN"}]
            return [{"action": "UPDATE_FINAL_TRAIN_PR_HEAD"}]
        if not final.get("pull_request"):
            return [{"action": "CREATE_FINAL_TRAIN_PR"}]
        if final.get("pull_request", {}).get("is_draft") is True:
            return [{"action": "MARK_FINAL_TRAIN_PR_READY"}]
        if not final.get("verification"):
            owner_key = "run:final-verification"
            requirement, branch, expected_head = expected_unity_operation(proc, owner_key) if uses_unity_mcp(proc) else ("none", None, "")
            slot_payload = unity_slot_payload(proc, owner_key)
            if requirement != "none" and slot_payload is None:
                if active_unity_leases(proc) < int(proc["limits"]["max_unity_editors"]):
                    return [unity_acquire_action(
                        proc, owner_key=owner_key, requirement=requirement,
                        branch=branch, expected_head=expected_head,
                    )]
                return [{"action": "WAIT_FOR_UNITY_SLOT", "owner_key": owner_key, "requirement": requirement}]
            action = {"action": "RUN_FINAL_EXACT_HEAD_VERIFICATION_DETERMINISTICALLY", "model_tokens": 0}
            if slot_payload:
                action["unity_slot"] = slot_payload
            return [action]
        if not final.get("review"):
            if not final.get("review_phase_key"):
                return [{"action": "RECORD_FINAL_REVIEW_DISPATCH_INTENT"}]
            return [{"action": "RECORD_FINAL_REVIEW_RESULT", "phase_key": final["review_phase_key"]}]
        if final.get("status") == "FINAL_PR_REVIEW_CLEAN" and not final.get("evidence"):
            return [{"action": "COLLECT_FINAL_TOKENS_AND_REPORTS"}]
        if proc.get("run_status") != "COMPLETED":
            return [{"action": "COMPLETE_RUN"}]
    return [{"action": "BLOCKED_OR_INCONSISTENT_STATE", "status": compact_status(state)}]


def active_phase_inventory(state: dict[str, Any]) -> list[dict[str, Any]]:
    proc = procedure(state)
    inventory: list[dict[str, Any]] = []
    for value in proc.get("phases", {}).values():
        if not isinstance(value, dict) or value.get("launch_state") not in ACTIVE_PHASE_STATES:
            continue
        thread_id = value.get("thread_id")
        user_visible = (
            value.get("execution_visibility") == "user-visible"
            and value.get("visibility_verified") is True
            and bool(thread_id)
        )
        ticket_id = value.get("ticket_id") or "train"
        kind = value.get("kind") or "phase"
        inventory.append({
            "phase_key": value.get("phase_key"),
            "ticket_id": value.get("ticket_id"),
            "kind": value.get("kind"),
            "display_label": f"{ticket_id} · {kind}",
            "state": value.get("launch_state"),
            "thread_id": thread_id,
            "client_thread_id": value.get("client_thread_id"),
            "execution_visibility": value.get("execution_visibility"),
            "visibility_verified": value.get("visibility_verified") is True,
            "user_visible": user_visible,
        })
    return inventory


def supervision_projection(
    state: dict[str, Any], actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    proc = procedure(state)
    resolved_actions = actions if actions is not None else next_actions(state)
    inventory = active_phase_inventory(state)
    visible = [item for item in inventory if item["user_visible"]]
    invisible = [item for item in inventory if not item["user_visible"]]
    pending = state.get("pending_human_action")
    automatic = [
        action for action in resolved_actions
        if action.get("action") not in {
            "WAIT_FOR_PHASE_TRANSITION", "WAIT_FOR_UNITY_SLOT", "AWAIT_HUMAN_GATE"
        }
    ]
    completed = proc.get("run_status") == "COMPLETED"
    human_only = (
        bool(pending)
        and not inventory
        and bool(resolved_actions)
        and all(action.get("action") == "AWAIT_HUMAN_GATE" for action in resolved_actions)
    )
    if completed:
        activity_state = "COMPLETED"
    elif human_only:
        activity_state = "AWAITING_HUMAN_ONLY"
    elif invisible:
        activity_state = "ACTIVE_WITH_VISIBILITY_GAP"
    elif visible:
        activity_state = "ACTIVE_VISIBLE_TASKS"
    elif automatic:
        activity_state = "AUTOMATIC_ACTION_READY"
    else:
        activity_state = "IDLE_OR_INCONSISTENT"
    return {
        "activity_state": activity_state,
        "active_visible_tasks": visible,
        "active_unverified_or_hidden_phases": invisible,
        "active_phase_count": len(inventory),
        "visible_task_count": len(visible),
        "pending_human_action": bool(pending),
        "automatic_action_count": len(automatic),
        "orchestrator_status_required": bool(invisible),
        "periodic_model_liveness_allowed": False,
        "may_pause_or_delete_watcher": completed or human_only,
        "watcher_action": "PAUSE_OR_DELETE" if human_only else ("DELETE" if completed else "KEEP"),
        "user_signal": (
            "visible-child-tasks" if visible
            else "single-action-required-message" if human_only
            else "orchestrator-transition"
        ),
    }


def compact_status(state: dict[str, Any]) -> dict[str, Any]:
    proc = procedure(state)
    runtime = environment(proc)
    phase_inventory = active_phase_inventory(state)
    return {
        "run_status": proc.get("run_status"),
        "revision": proc.get("revision"),
        "routing_policy_version": proc.get("routing_policy_version"),
        "tickets": {ticket_id: value.get("status") for ticket_id, value in proc["tickets"].items()},
        "active_phases": [
            value["phase_key"] for value in phase_inventory
        ],
        "active_visible_tasks": [value for value in phase_inventory if value["user_visible"]],
        "active_unverified_or_hidden_phases": [
            value for value in phase_inventory if not value["user_visible"]
        ],
        "pending_human_gates": [
            gate["gate_id"] for gate in proc["human_gates"].values()
            if gate.get("status", "").startswith("PENDING")
        ],
        "pending_human_action": state.get("pending_human_action"),
        "supervision": proc.get("supervision"),
        "orchestrator_owner": (state.get("orchestrator_lease") or {}).get("owner_thread_id"),
        "pending_orchestrator_handoff": state.get("pending_orchestrator_handoff"),
        "finalization": proc.get("finalization", {}).get("status"),
        "environment": {
            "profile": runtime.get("profile"),
            "status": runtime.get("status"),
            "mcp_mode": runtime.get("mcp_mode"),
            "unity_editors": {
                "limit": proc.get("limits", {}).get("max_unity_editors"),
                "leased": active_unity_leases(proc),
            } if runtime.get("profile") == "unity-mcp-local" else None,
        },
    }


def migrate_procedure(state: dict[str, Any]) -> bool:
    proc = procedure(state)
    changed = False
    if int(proc.get("schema_version", 1)) < 7:
        proc["schema_version"] = 7
        changed = True
    if proc.get("routing_policy_version") != ROUTING_POLICY_VERSION:
        proc["routing_policy_version"] = ROUTING_POLICY_VERSION
        changed = True
    if "environment" not in proc:
        proc["environment"] = {
            "profile": "generic",
            "status": "NOT_APPLICABLE",
            "mcp_mode": None,
            "slots": {},
        }
        changed = True
    limits = proc.setdefault("limits", {})
    if "max_unity_editors" not in limits:
        limits["max_unity_editors"] = DEFAULT_MAX_UNITY_EDITORS
        changed = True
    if "cost_control" not in proc:
        proc["cost_control"] = {
            "status": "CLEAR",
            "phase_token_limit": 50_000_000,
            "followup_to_initial_ratio_limit": 2.0,
            "context_compaction_limit": 1,
            "anomalies": [],
        }
        changed = True
    if "hidden_fallback_authorizations" not in proc:
        proc["hidden_fallback_authorizations"] = {}
        changed = True
    if "reasoning_authorizations" not in proc:
        proc["reasoning_authorizations"] = {}
        changed = True
    if "pending_human_action" not in state:
        state["pending_human_action"] = None
        changed = True
    if "pending_orchestrator_handoff" not in state:
        state["pending_orchestrator_handoff"] = None
        changed = True
    supervision_projection = dict(proc.get("supervision") or {
        "status": "INACTIVE", "mode": None, "watcher_id": None,
    })
    if state.get("supervision") != supervision_projection:
        state["supervision"] = supervision_projection
        changed = True
    if "orchestrator_rotation_policy" not in state:
        state["orchestrator_rotation_policy"] = {
            "mode": "automatic-budgeted-handoff",
            "soft_total_tokens": 10_000_000,
            "hard_total_tokens": 25_000_000,
            "max_model_wakes": 50,
            "max_tool_calls": 500,
            "max_context_compactions": 1,
            "decision_packet_max_bytes": 16_384,
        }
        changed = True
    return changed


def heartbeat(args: argparse.Namespace) -> int:
    state = run_registry.load_json(args.state)
    status = compact_status(state)
    actions = next_actions(state)
    projection = supervision_projection(state, actions)
    if procedure(state).get("run_status") == "COMPLETED":
        decision = "STOP_COMPLETED"
    elif any(action["action"] == "BLOCKED_OR_INCONSISTENT_STATE" for action in actions):
        decision = "ESCALATE_CONTROLLER_INCONSISTENCY"
    elif any(action["action"] not in {"WAIT_FOR_PHASE_TRANSITION", "WAIT_FOR_UNITY_SLOT", "AWAIT_HUMAN_GATE"} for action in actions):
        decision = "CONTINUE_AUTOMATICALLY"
    elif projection["activity_state"] == "ACTIVE_WITH_VISIBILITY_GAP":
        decision = "ESCALATE_VISIBILITY_GAP"
    elif status["active_phases"]:
        decision = "WAIT_FOR_VISIBLE_TASK_TRANSITION"
    elif projection["activity_state"] == "AWAITING_HUMAN_ONLY":
        decision = "WAIT_FOR_HUMAN_WITHOUT_WATCHER"
    else:
        decision = "ESCALATE_CONTROLLER_INCONSISTENCY"
    output = {
        **status,
        "supervision_projection": projection,
        "heartbeat_decision": decision,
        "requires_model_wake": decision in {
            "CONTINUE_AUTOMATICALLY", "ESCALATE_VISIBILITY_GAP", "ESCALATE_CONTROLLER_INCONSISTENCY"
        },
        "may_pause_or_delete_watcher": projection["may_pause_or_delete_watcher"],
        "watcher_action": projection["watcher_action"],
        "next_actions": actions,
        "controller_revision": procedure(state).get("revision"),
        "controller_updated_at": procedure(state).get("updated_at"),
    }
    sys.stdout.write(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    return 0


def bootstrap(args: argparse.Namespace) -> int:
    path = args.state.expanduser().resolve()
    with run_registry.directory_lock(path.parent):
        state = run_registry.load_json(path)
        if isinstance(state.get("procedure"), dict):
            if migrate_procedure(state):
                procedure(state)["updated_at"] = now_iso()
                state["manifest_updated_at"] = procedure(state)["updated_at"]
                run_registry.save_json(path, state)
            output = {"status": "already-bootstrapped", "state": str(path), **compact_status(state)}
            sys.stdout.write(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
            return 0
        identity = state.get("run_identity")
        require(isinstance(identity, dict) and isinstance(identity.get("tickets"), list), "run_identity.tickets is required")
        environment_profile = getattr(args, "environment_profile", "generic")
        require(environment_profile in ENVIRONMENT_PROFILES, "invalid environment profile")
        max_unity_editors = getattr(args, "max_unity_editors", DEFAULT_MAX_UNITY_EDITORS)
        require(
            isinstance(max_unity_editors, int)
            and not isinstance(max_unity_editors, bool)
            and 1 <= max_unity_editors <= MAX_CONFIGURABLE_UNITY_EDITORS,
            f"max Unity editors must be between 1 and {MAX_CONFIGURABLE_UNITY_EDITORS}",
        )
        unity_repository = getattr(args, "unity_repository", None)
        if environment_profile == "unity-mcp-local":
            require(unity_repository is not None, "unity-mcp-local requires --unity-repository")
            unity_repository = str(Path(unity_repository).expanduser().resolve())
        timestamp = now_iso()
        state["schema_version"] = max(int(state.get("schema_version", 0)), 7)
        state["pending_orchestrator_handoff"] = None
        state["orchestrator_rotation_policy"] = {
            "mode": "automatic-budgeted-handoff",
            "soft_total_tokens": 10_000_000,
            "hard_total_tokens": 25_000_000,
            "max_model_wakes": 50,
            "max_tool_calls": 500,
            "max_context_compactions": 1,
            "decision_packet_max_bytes": 16_384,
        }
        state["procedure"] = {
            "schema_version": 7,
            "routing_policy_version": ROUTING_POLICY_VERSION,
            "revision": 0,
            "run_status": "ACTIVE",
            "base_branch": args.base_branch,
            "approval_mode": args.approval_mode,
            "orchestrator_confirmed": False,
            "supervision": {"status": "INACTIVE", "mode": None, "watcher_id": None},
            "limits": {
                "max_active_analyses": 5,
                "max_active_execution_pairs": 2,
                "max_integrated_tickets": 5,
                "max_unity_editors": max_unity_editors,
            },
            "environment": {
                "profile": environment_profile,
                "status": "UNINITIALIZED" if environment_profile == "unity-mcp-local" else "NOT_APPLICABLE",
                "mcp_mode": "local" if environment_profile == "unity-mcp-local" else None,
                "repository": unity_repository if environment_profile == "unity-mcp-local" else None,
                "registry_reference": None,
                "slots": {},
            },
            "tickets": {
                ticket_id: {
                    "status": "DISCOVERED", "triage": None, "analysis": None,
                    "hard_dependencies": [], "collision_domains": [], "reviews": [],
                    "remediation_cycles": 0,
                }
                for ticket_id in identity["tickets"]
            },
            "phases": {},
            "hidden_fallback_authorizations": {},
            "reasoning_authorizations": {},
            "cost_control": {
                "status": "CLEAR",
                "phase_token_limit": 50_000_000,
                "followup_to_initial_ratio_limit": 2.0,
                "context_compaction_limit": 1,
                "anomalies": [],
            },
            "human_gates": {},
            "dependencies_consolidated": False,
            "train_head": None,
            "finalization": {"status": "NOT_STARTED"},
            "applied_events": {},
            "event_log": [],
            "updated_at": timestamp,
        }
        run_registry.save_json(path, state)
    sys.stdout.write(json.dumps({"status": "bootstrapped", "state": str(path), **compact_status(state)}, ensure_ascii=False, indent=2) + "\n")
    return 0


def apply_event(args: argparse.Namespace) -> int:
    path = args.state.expanduser().resolve()
    event = json.loads(args.event_json) if args.event_json else run_registry.load_json(args.event)
    require(isinstance(event, dict), "event must be a JSON object")
    require_fields(event, ("event_id", "type"), "event")
    digest = hashlib.sha256(json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    with run_registry.directory_lock(path.parent):
        state = run_registry.load_json(path)
        proc = procedure(state)
        applied = proc.setdefault("applied_events", {})
        prior = applied.get(event["event_id"])
        if prior:
            require(prior == digest, "event_id collision with different payload")
            sys.stdout.write(json.dumps({"status": "duplicate-idempotent", **compact_status(state)}, indent=2) + "\n")
            return 0
        require(args.expected_revision == proc.get("revision"), f"revision conflict: expected {args.expected_revision}, current {proc.get('revision')}")
        handle_event(state, event)
        proc["revision"] += 1
        proc["updated_at"] = now_iso()
        applied[event["event_id"]] = digest
        log = proc.setdefault("event_log", [])
        log.append({
            "sequence": proc["revision"], "event_id": event["event_id"], "type": event["type"],
            "ticket_id": event.get("ticket_id"), "phase_key": event.get("phase_key"), "applied_at": proc["updated_at"],
        })
        state["manifest_updated_at"] = proc["updated_at"]
        run_registry.save_json(path, state)
    output = {"status": "applied", "event": event["type"], **compact_status(state), "next_actions": next_actions(state)}
    sys.stdout.write(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    return 0


def show(args: argparse.Namespace) -> int:
    state = run_registry.load_json(args.state)
    output = compact_status(state)
    output["next_actions"] = next_actions(state)
    sys.stdout.write(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    return 0


def check(args: argparse.Namespace) -> int:
    state = run_registry.load_json(args.state)
    issues = completion_issues(state) if args.mode == "completion" else []
    if args.mode == "yield":
        actions = next_actions(state)
        automatic = [
            action for action in actions
            if action["action"] not in {"WAIT_FOR_PHASE_TRANSITION", "WAIT_FOR_UNITY_SLOT", "AWAIT_HUMAN_GATE"}
        ]
        if automatic:
            issues.append("automatic actions remain: " + ", ".join(action["action"] for action in automatic))
        active = compact_status(state)["active_phases"]
        supervision = procedure(state).get("supervision", {})
        if active and supervision.get("status") != "ACTIVE":
            issues.append("active phases have no deterministic supervision")
        if active and supervision.get("mode") == "FOREGROUND_WAIT":
            issues.append("foreground supervision cannot survive a yielded orchestrator turn")
        if active and supervision.get("mode") == "BACKGROUND_WATCHER" and not supervision.get("watcher_id"):
            issues.append("background supervision has no verified watcher ID")
        if active and supervision.get("mode") == "BACKGROUND_WATCHER" and supervision.get("watcher_consumes_model_tokens") is not False:
            issues.append("background supervision is not verified as a zero-model process")
        if active and supervision.get("mode") == "EVENT_CALLBACK" and supervision.get("callback_verified") is not True:
            issues.append("event-callback supervision is not verified")
        if (
            active
            and supervision.get("mode") == "EVENT_CALLBACK"
            and supervision.get("callback_target_thread_id")
            != (state.get("orchestrator_lease") or {}).get("owner_thread_id")
        ):
            issues.append("event-callback supervision targets a previous orchestrator owner")
        pending_handoff = state.get("pending_orchestrator_handoff")
        if isinstance(pending_handoff, dict) and pending_handoff.get("status") == "PREPARED":
            issues.append("controlled orchestrator handoff is prepared but not accepted")
    output = {"status": "pass" if not issues else "fail", "mode": args.mode, "issues": issues}
    sys.stdout.write(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    return 0 if not issues else 2


def merge_permit_issues(
    state: dict[str, Any], *, action: str, ticket_id: str | None, head_commit: str,
) -> list[str]:
    proc = procedure(state)
    issues: list[str] = []
    if action == "ticket":
        if not ticket_id:
            return ["ticket merge permit requires ticket_id"]
        item = ticket(proc, ticket_id)
        pull_request = item.get("pull_request") or {}
        ledger = item.get("finding_ledger") or {}
        if item.get("status") != "READY_TO_MERGE":
            issues.append("ticket is not READY_TO_MERGE")
        if pull_request.get("is_draft") is not False:
            issues.append("ticket pull request is draft or readiness is unknown")
        if pull_request.get("head_commit") != head_commit:
            issues.append("ticket pull-request head differs from requested merge head")
        if ledger.get("head_commit") != head_commit:
            issues.append("ticket finding ledger does not cover requested merge head")
        if ledger.get("ledger_status") != "complete":
            issues.append("ticket finding ledger is incomplete")
        if ledger.get("ci_status") not in {"passed", "not_configured", "unavailable_with_local_fallback"}:
            issues.append("ticket CI is not acceptable")
        if ledger.get("copilot_status") not in {"received", "not_configured", "unavailable", "timed_out"}:
            issues.append("ticket Copilot collection is incomplete")
    elif action == "final-remediation":
        final = proc.get("finalization", {})
        pull_request = final.get("remediation_pull_request") or {}
        remediation_phases = [
            item for item in proc.get("phases", {}).values()
            if item.get("kind") == "final_remediation" and item.get("launch_state") == "COMPLETED"
        ]
        if final.get("status") != "AWAITING_FINAL_PR_UPDATE":
            issues.append("final remediation is not awaiting train integration")
        if proc.get("approval_mode") not in {"auto-merge", "full-auto"}:
            issues.append("final remediation merge requires an auto-merge approval mode")
        if not remediation_phases:
            issues.append("no completed final remediation phase exists")
        else:
            latest_phase = remediation_phases[-1]
            if pull_request.get("head_branch") != latest_phase.get("branch"):
                issues.append("final remediation PR branch differs from the completed phase")
        if pull_request.get("is_draft") is not False:
            issues.append("final remediation pull request is draft or readiness is unknown")
        if pull_request.get("base_branch") != state["run_identity"]["train_branch"]:
            issues.append("final remediation pull request does not target the train branch")
        if pull_request.get("head_commit") != head_commit:
            issues.append("final remediation pull-request head differs from requested merge head")
        if final.get("remediation_merge"):
            issues.append("final remediation merge is already recorded")
    elif action == "final":
        final = proc.get("finalization", {})
        pull_request = final.get("pull_request") or {}
        authorization = final.get("base_merge_authorization") or {}
        if final.get("status") not in {"READY_FOR_COMPLETION", "COMPLETED"}:
            issues.append("final train is not READY_FOR_COMPLETION")
        if pull_request.get("is_draft") is not False:
            issues.append("final pull request is draft or readiness is unknown")
        if pull_request.get("head_commit") != head_commit:
            issues.append("final pull-request head differs from requested merge head")
        if authorization.get("status") != "AUTHORIZED":
            issues.append("final base merge lacks explicit user authorization")
        if authorization.get("head_commit") != head_commit:
            issues.append("final base merge authorization does not cover requested head")
    else:
        issues.append(f"unsupported merge permit action: {action}")
    return issues


def permit_merge(args: argparse.Namespace) -> int:
    state = run_registry.load_json(args.state)
    issues = merge_permit_issues(
        state,
        action=args.action,
        ticket_id=args.ticket_id,
        head_commit=args.head_commit,
    )
    output = {
        "status": "pass" if not issues else "fail",
        "action": f"{args.action}-merge",
        "ticket_id": args.ticket_id,
        "head_commit": args.head_commit,
        "issues": issues,
    }
    sys.stdout.write(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    return 0 if not issues else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("bootstrap")
    start.add_argument("--state", type=Path, required=True)
    start.add_argument("--base-branch", required=True)
    start.add_argument("--approval-mode", choices=("standard", "auto-analysis", "auto-merge", "full-auto"), required=True)
    start.add_argument("--environment-profile", choices=ENVIRONMENT_PROFILES, default="generic")
    start.add_argument("--max-unity-editors", type=int, default=DEFAULT_MAX_UNITY_EDITORS)
    start.add_argument("--unity-repository", type=Path)
    start.set_defaults(handler=bootstrap)

    event = commands.add_parser("apply")
    event.add_argument("--state", type=Path, required=True)
    event.add_argument("--expected-revision", type=int, required=True)
    source = event.add_mutually_exclusive_group(required=True)
    source.add_argument("--event", type=Path)
    source.add_argument("--event-json")
    event.set_defaults(handler=apply_event)

    status = commands.add_parser("status")
    status.add_argument("--state", type=Path, required=True)
    status.set_defaults(handler=show)

    pulse = commands.add_parser("heartbeat")
    pulse.add_argument("--state", type=Path, required=True)
    pulse.set_defaults(handler=heartbeat)

    verify = commands.add_parser("check")
    verify.add_argument("--state", type=Path, required=True)
    verify.add_argument("--mode", choices=("yield", "completion"), required=True)
    verify.set_defaults(handler=check)

    permit = commands.add_parser("permit-merge")
    permit.add_argument("--state", type=Path, required=True)
    permit.add_argument("--action", choices=("ticket", "final-remediation", "final"), required=True)
    permit.add_argument("--ticket-id")
    permit.add_argument("--head-commit", required=True)
    permit.set_defaults(handler=permit_merge)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.handler(args))
    except (OSError, json.JSONDecodeError, ControllerError, ValueError) as error:
        sys.stderr.write(json.dumps({"status": "rejected", "error": str(error)}, ensure_ascii=False) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
