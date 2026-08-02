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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import run_registry


CRITICALITIES = ("LOW", "NORMAL", "HIGH", "CRITICAL")
COMPLEXITIES = ("LOW", "MEDIUM", "HIGH", "MAXIMUM")
ACTIVE_PHASE_STATES = {"INTENT_RECORDED", "QUEUED", "RUNNING", "LAUNCH_UNKNOWN"}
INPUT_PHASE_STATES = {"NEEDS_INPUT", "INPUT_READY"}
TERMINAL_TICKET_STATES = {"ANALYSIS_REPORTED", "MERGED_INTO_TRAIN", "BLOCKED", "FAILED", "CANCELLED"}
SETTING_ORDER = {
    ("gpt-5.6-terra", "medium"): 1,
    ("gpt-5.6-terra", "high"): 2,
    ("gpt-5.6-sol", "high"): 3,
    ("gpt-5.6-sol", "xhigh"): 4,
    ("gpt-5.6-sol", "max"): 5,
    ("gpt-5.6-sol", "ultra"): 6,
}

ANALYSIS_MATRIX = {
    "LOW": ("Sol/H", "Sol/H", "Sol/XH", "Sol/XH"),
    "NORMAL": ("Sol/H", "Sol/H", "Sol/XH", "Sol/XH"),
    "HIGH": ("Sol/H", "Sol/H", "Sol/XH", "Sol/XH"),
    "CRITICAL": ("Sol/XH", "Sol/XH", "Sol/XH", "Sol/Max"),
}
IMPLEMENTATION_MATRIX = {
    "LOW": ("Terra/M", "Terra/H", "Sol/H", "Sol/XH"),
    "NORMAL": ("Terra/H", "Sol/H", "Sol/H", "Sol/XH"),
    "HIGH": ("Sol/H", "Sol/H", "Sol/H", "Sol/XH"),
    "CRITICAL": ("Sol/H", "Sol/H", "Sol/XH", "Sol/XH"),
}
INITIAL_REVIEW_MATRIX = {
    "LOW": ("Terra/H", "Sol/H", "Sol/H", "Sol/XH"),
    "NORMAL": ("Sol/H", "Sol/H", "Sol/H", "Sol/XH"),
    "HIGH": ("Sol/H", "Sol/XH", "Sol/XH", "Sol/XH"),
    "CRITICAL": ("Sol/XH", "Sol/XH", "Sol/XH", "Sol/Max"),
}
FOLLOWUP_REVIEW_MATRIX = {
    "LOW": ("Terra/H", "Sol/H", "Sol/H", "Sol/XH"),
    "NORMAL": ("Sol/H", "Sol/H", "Sol/H", "Sol/XH"),
    "HIGH": ("Sol/H", "Sol/H", "Sol/XH", "Sol/XH"),
    "CRITICAL": ("Sol/H", "Sol/XH", "Sol/XH", "Sol/Max"),
}
SETTING_NAMES = {
    "Terra/M": ("gpt-5.6-terra", "medium"),
    "Terra/H": ("gpt-5.6-terra", "high"),
    "Sol/H": ("gpt-5.6-sol", "high"),
    "Sol/XH": ("gpt-5.6-sol", "xhigh"),
    "Sol/Max": ("gpt-5.6-sol", "max"),
    "Sol/Ultra": ("gpt-5.6-sol", "ultra"),
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


def routed_setting(
    matrix: dict[str, tuple[str, str, str, str]],
    criticality: str,
    complexity: str,
    reasoning_authorized: bool,
) -> tuple[str, str, str]:
    model, effort = setting_from_matrix(matrix, criticality, complexity)
    if effort in {"max", "ultra"} and not reasoning_authorized:
        return "gpt-5.6-sol", "xhigh", "documented-fallback"
    return model, effort, "conformant"


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


def phase(proc: dict[str, Any], phase_key: str) -> dict[str, Any]:
    phases = proc.get("phases")
    require(isinstance(phases, dict) and phase_key in phases, f"unknown phase: {phase_key}")
    value = phases[phase_key]
    require(isinstance(value, dict), f"invalid phase: {phase_key}")
    return value


def add_phase(
    proc: dict[str, Any], *, key: str, ticket_id: str | None, kind: str,
    model: str, effort: str, branch: str | None, base: str, scope: str | None = None
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
    item["launch_state"] = "COMPLETED"
    item["completion_envelope"] = envelope
    item["usage_captured"] = True
    item["completed_at"] = now_iso()
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
    item["completion_envelope"] = envelope
    item["usage_captured"] = True
    item["completed_at"] = now_iso()
    ticket_id = item.get("ticket_id")
    if outcome == "needs_input":
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

    if event_type == "ORCHESTRATOR_CONFIRMED":
        require(proc.get("orchestrator_confirmed") is False, "orchestrator already confirmed")
        proc["orchestrator_confirmed"] = True
        return

    if event_type == "SUPERVISION_CONFIGURED":
        require(proc.get("orchestrator_confirmed") is True, "confirm orchestrator first")
        mode = event.get("mode")
        require(mode in {"FOREGROUND_WAIT", "BACKGROUND_WATCHER"}, "invalid supervision mode")
        if mode == "BACKGROUND_WATCHER":
            require(bool(event.get("watcher_id")), "background supervision requires watcher_id")
        proc["supervision"] = {
            "status": "ACTIVE", "mode": mode, "watcher_id": event.get("watcher_id"),
            "last_check_at": event.get("last_check_at") or now_iso(),
            "next_check_at": event.get("next_check_at"),
        }
        return

    if event_type == "PHASE_DISPATCHED":
        kind = event.get("kind")
        require(kind in {"triage", "analysis", "analysis_reconciliation", "plan_contract_validation"}, "unsupported generic phase kind")
        require(proc.get("supervision", {}).get("status") == "ACTIVE", "configure supervision first")
        require_fields(event, ("phase_key", "base_commit", "model", "reasoning_effort"), "phase dispatch")
        ticket_id = event.get("ticket_id")
        if kind == "triage":
            require(ticket_id in (None, "run"), "triage is a run-level batch phase")
            require(event["model"] == "gpt-5.6-terra" and event["reasoning_effort"] == "high", "triage must use Terra/High")
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
            active_analyses = sum(
                1 for p in proc["phases"].values()
                if p.get("kind") == "analysis" and p.get("launch_state") in ACTIVE_PHASE_STATES
            )
            require(active_analyses < int(proc["limits"]["max_active_analyses"]), "analysis concurrency limit reached")
        else:
            require(ticket_id, f"{kind} requires ticket_id")
            ticket(proc, str(ticket_id))
        add_phase(
            proc, key=event["phase_key"], ticket_id=ticket_id, kind=kind,
            model=event["model"], effort=event["reasoning_effort"], branch=None,
            base=event["base_commit"], scope=event.get("scope"),
        )
        return

    if event_type == "TICKET_TRIAGED":
        require(proc.get("supervision", {}).get("status") == "ACTIVE", "configure supervision first")
        item = ticket(proc, str(event.get("ticket_id") or ""))
        require(item.get("triage") is None, "ticket already triaged")
        source_phase = phase(proc, str(event.get("phase_key") or ""))
        require(source_phase.get("kind") == "triage" and source_phase.get("launch_state") == "COMPLETED", "triage result requires a completed visible batch-triage phase")
        criticality, complexity = event.get("criticality"), event.get("complexity")
        expected = routed_setting(ANALYSIS_MATRIX, criticality, complexity, bool(event.get("reasoning_authorized")))
        validate_routing(event, expected, "analysis_")
        require(event.get("triage_model") == "gpt-5.6-terra", "triage must use gpt-5.6-terra")
        require(event.get("triage_reasoning_effort") == "high", "triage must use high effort")
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
            ),
            "analysis",
        )
        expected = routed_setting(
            ANALYSIS_MATRIX, event["criticality"], event["complexity"], bool(event.get("reasoning_authorized"))
        )
        validate_routing(event, expected)
        item["analysis"] = dict(event)
        require(event["report_thread_id"] == source_phase.get("thread_id"), "analysis report thread does not match the visible phase")
        required = analysis_human_gate(event["criticality"], event["complexity"], proc["approval_mode"])
        item["analysis_gate_required"] = required
        if required:
            gate_id = f"{event['ticket_id']}:analysis:{event['analysis_revision']}"
            create_gate(proc, gate_id=gate_id, kind="analysis", ticket_id=event["ticket_id"], revision=event["analysis_revision"])
            item["analysis_gate_id"] = gate_id
            item["status"] = "AWAITING_ANALYSIS_APPROVAL"
        else:
            item["status"] = "ANALYZED"
        return

    if event_type == "DEPENDENCIES_CONSOLIDATED":
        require(not proc.get("dependencies_consolidated"), "dependencies already consolidated")
        require(all(value.get("analysis") for value in proc["tickets"].values()), "all analyses must finish first")
        graph = event.get("graph")
        require(isinstance(graph, dict), "dependency graph must be an object")
        for ticket_id, value in proc["tickets"].items():
            entry = graph.get(ticket_id)
            require(isinstance(entry, dict), f"dependency graph missing ticket: {ticket_id}")
            hard = entry.get("hard_dependencies", [])
            require(isinstance(hard, list), f"hard_dependencies must be a list: {ticket_id}")
            require(all(dep in proc["tickets"] and dep != ticket_id for dep in hard), f"invalid dependency: {ticket_id}")
            value["hard_dependencies"] = hard
            value["collision_domains"] = entry.get("collision_domains", [])
            if value["status"] == "ANALYZED":
                value["status"] = "READY_FOR_IMPLEMENTATION"
        proc["dependencies_consolidated"] = True
        proc["dependency_revision"] = event.get("dependency_revision") or f"dependencies-r{proc['revision'] + 1}"
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
        analysis = item["analysis"]
        implementation_expected = routed_setting(
            IMPLEMENTATION_MATRIX, analysis["criticality"], analysis["complexity"], bool(event.get("reasoning_authorized"))
        )
        acceptance_expected = routed_setting(
            IMPLEMENTATION_MATRIX, analysis["criticality"], event["verification_complexity"], bool(event.get("reasoning_authorized"))
        )
        if acceptance_expected[:2] == ("gpt-5.6-terra", "medium"):
            acceptance_expected = ("gpt-5.6-terra", "high", acceptance_expected[2])
        validate_routing(event, implementation_expected, "implementation_")
        validate_routing(event, acceptance_expected, "acceptance_")
        add_phase(
            proc, key=event["implementation_phase_key"], ticket_id=event["ticket_id"], kind="implementation",
            model=event["implementation_model"], effort=event["implementation_reasoning_effort"],
            branch=event["implementation_branch"], base=event["base_commit"],
        )
        add_phase(
            proc, key=event["acceptance_phase_key"], ticket_id=event["ticket_id"], kind="acceptance_tests",
            model=event["acceptance_model"], effort=event["acceptance_reasoning_effort"],
            branch=event["acceptance_branch"], base=event["base_commit"],
        )
        item["execution"] = {
            "base_commit": event["base_commit"],
            "implementation_phase_key": event["implementation_phase_key"],
            "acceptance_phase_key": event["acceptance_phase_key"],
        }
        item["status"] = "EXECUTION_PAIR_RUNNING"
        return

    if event_type == "PHASE_LAUNCH_OBSERVED":
        value = phase(proc, str(event.get("phase_key") or ""))
        launch_state = event.get("launch_state")
        require(launch_state in {"QUEUED", "RUNNING", "LAUNCH_UNKNOWN", "BLOCKED"}, "invalid launch state")
        if launch_state == "QUEUED":
            require(bool(event.get("client_thread_id")), "queued phase requires client_thread_id")
        if launch_state == "RUNNING":
            require(bool(event.get("thread_id")), "running phase requires thread_id")
            require(event.get("visibility_verified") is True, "running phase requires verified user visibility")
            require(event.get("thread_id") != value.get("forbidden_thread_id"), "remediation must use a fresh task context")
        if value.get("launch_state") == "LAUNCH_UNKNOWN":
            require(launch_state != "QUEUED" or event.get("reconciled") is True, "launch-unknown must be reconciled")
        value.update({key: event.get(key) for key in (
            "client_thread_id", "thread_id", "host_id", "visibility_verified", "visibility_verified_at"
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
                item["status"] = "AWAITING_VERIFICATION"
        elif completed["kind"] == "remediation":
            ticket(proc, completed["ticket_id"])["status"] = "AWAITING_REMEDIATION_VERIFICATION"
        elif completed["kind"] == "final_remediation":
            proc["finalization"]["status"] = "AWAITING_FINAL_PR_UPDATE"
        return

    if event_type == "PHASE_TERMINATED":
        terminate_phase(event, proc)
        return

    if event_type == "VERIFICATION_RECORDED":
        item = ticket(proc, str(event.get("ticket_id") or ""))
        require(item.get("status") in {"AWAITING_VERIFICATION", "AWAITING_REMEDIATION_VERIFICATION"}, "execution or remediation must complete before verification")
        require_fields(
            event,
            (
                "ticket_head", "baseline_red_base", "integrated_green_head", "environment_status",
                "acceptance_coverage_status", "independent_test_commit", "logs_reference",
            ),
            "verification",
        )
        execution = item["execution"]
        require(event["baseline_red_base"] == execution["base_commit"], "baseline red must use execution-pair base")
        require(event["integrated_green_head"] == event["ticket_head"], "green evidence must cover ticket head")
        require(event.get("status") == "passed", "functional readiness requires passed verification")
        require(event["acceptance_coverage_status"] == "complete", "acceptance coverage must be complete")
        require(event["environment_status"] in {"passed", "not-applicable"}, "environment parity is incomplete")
        if event.get("supabase_auth_applicable") is True:
            require(event["environment_status"] == "passed", "Supabase/Auth requires environment parity")
            require(event.get("supabase_auth_status") == "passed", "Supabase/Auth verification is incomplete")
            require(event.get("privileged_credentials_setup_only") is True, "privileged credentials crossed the tested boundary")
        require(not event.get("automatable_manual_scenarios"), "automatable scenarios cannot be left to the user")
        item["verification"] = dict(event)
        item["status"] = "FUNCTIONAL_READY"
        return

    if event_type == "TICKET_PR_RECORDED":
        item = ticket(proc, str(event.get("ticket_id") or ""))
        require(item.get("status") in {"FUNCTIONAL_READY", "NEEDS_REMEDIATION", "AUTO_REVIEW_CLEAN"}, "ticket is not ready for a PR")
        require_fields(event, ("url", "base_branch", "head_branch", "head_commit"), "ticket pull request")
        require(event["base_branch"] == state["run_identity"]["train_branch"], "ticket PR must target the train branch")
        if item.get("verification"):
            require(event["head_commit"] == item["verification"]["ticket_head"], "ticket PR head differs from verified head")
        item["pull_request"] = dict(event)
        return

    if event_type == "REVIEW_DISPATCHED":
        item = ticket(proc, str(event.get("ticket_id") or ""))
        scope = event.get("scope")
        require(scope in {"initial", "followup"}, "review scope must be initial or followup")
        require_fields(event, ("phase_key", "base_commit", "head_commit", "model", "reasoning_effort"), "review dispatch")
        analysis = item.get("analysis") or {}
        criticality = event.get("criticality") or analysis.get("criticality")
        complexity = event.get("complexity") or analysis.get("complexity")
        if scope == "initial":
            require(item.get("status") == "FUNCTIONAL_READY", "initial review requires functional readiness")
            require(isinstance(item.get("pull_request"), dict), "initial review requires ticket PR")
            require(not item.get("reviews"), "initial review already exists")
            require(event.get("review_kind") == "full", "initial review must be exhaustive")
            expected = routed_setting(INITIAL_REVIEW_MATRIX, criticality, complexity, bool(event.get("reasoning_authorized")))
        else:
            reviews = item.get("reviews", [])
            require(reviews and reviews[0].get("scope") == "initial", "followup review requires an initial full review")
            require(item.get("status") == "FUNCTIONAL_READY", "followup review requires post-remediation functional readiness")
            require(item.get("remediation_cycles", 0) > 0, "followup review requires remediation")
            material = event.get("material_scope_changed") is True
            require(event.get("review_kind") == ("full" if material else "focused"), "invalid followup review kind")
            expected = routed_setting(FOLLOWUP_REVIEW_MATRIX, criticality, complexity, bool(event.get("reasoning_authorized")))
            baseline = (reviews[0]["actual_model"], reviews[0]["actual_reasoning_effort"])
            require(SETTING_ORDER[expected[:2]] <= SETTING_ORDER[baseline], "followup review cannot exceed initial review setting")
            if material:
                require(event.get("scope_revision") != reviews[0].get("scope_revision"), "full re-review requires a new scope revision")
        validate_routing(event, expected)
        add_phase(
            proc, key=event["phase_key"], ticket_id=event["ticket_id"], kind="review",
            model=event["model"], effort=event["reasoning_effort"], branch=None,
            base=event["base_commit"], scope=scope,
        )
        phase(proc, event["phase_key"])["review_kind"] = event["review_kind"]
        phase(proc, event["phase_key"])["scope_revision"] = event.get("scope_revision") or "scope-1"
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
        })
        analysis = item["analysis"]
        criticality = event.get("effective_criticality") or analysis["criticality"]
        complexity = event.get("effective_complexity") or analysis["complexity"]
        item["effective_classification"] = {"criticality": criticality, "complexity": complexity}
        item["status"] = "AWAITING_FINDING_RECONCILIATION"
        return

    if event_type == "TICKET_FINDINGS_RECONCILED":
        item = ticket(proc, str(event.get("ticket_id") or ""))
        require(item.get("status") == "AWAITING_FINDING_RECONCILIATION", "ticket is not awaiting finding reconciliation")
        require_fields(event, ("head_commit", "ledger_status", "sources_dispositioned"), "ticket finding ledger")
        require(event["ledger_status"] == "complete", "ticket finding ledger is incomplete")
        require(isinstance(event["sources_dispositioned"], list), "sources_dispositioned must be a list")
        require("codex" in event["sources_dispositioned"], "Codex review findings were not dispositioned")
        require(event["head_commit"] == item.get("pull_request", {}).get("head_commit"), "finding ledger head differs from ticket PR")
        require(isinstance(event.get("blocking_findings"), list), "blocking_findings must be a list")
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

    if event_type == "REMEDIATION_DISPATCHED":
        item = ticket(proc, str(event.get("ticket_id") or ""))
        require(item.get("status") == "NEEDS_REMEDIATION", "remediation requires requested changes")
        cycles = int(item.get("remediation_cycles", 0))
        require(cycles < 2, "automatic remediation cycle limit reached")
        require_fields(event, ("phase_key", "base_commit", "branch", "model", "reasoning_effort"), "remediation dispatch")
        analysis = item["analysis"]
        expected = routed_setting(
            IMPLEMENTATION_MATRIX, analysis["criticality"], event.get("complexity") or analysis["complexity"],
            bool(event.get("reasoning_authorized")),
        )
        validate_routing(event, expected)
        implementation_phase = phase(proc, item["execution"]["implementation_phase_key"])
        add_phase(
            proc, key=event["phase_key"], ticket_id=event["ticket_id"], kind="remediation",
            model=event["model"], effort=event["reasoning_effort"], branch=event["branch"], base=event["base_commit"],
        )
        phase(proc, event["phase_key"])["forbidden_thread_id"] = implementation_phase.get("thread_id")
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

    if event_type == "DRY_RUN_EVIDENCE_RECORDED":
        require(state.get("execution_mode") == "dry-run", "dry-run evidence is only valid in dry-run mode")
        require(proc.get("dependencies_consolidated") is True, "dependency consolidation is incomplete")
        require(
            not any(gate.get("status", "").startswith("PENDING") for gate in proc["human_gates"].values()),
            "human analysis gates remain pending",
        )
        require_fields(
            event,
            ("token_reporting_status", "session_usage_ledger_ready", "analysis_reports_ready", "task_inventory_ready", "completion_report_ready"),
            "dry-run evidence",
        )
        require(event["token_reporting_status"] in {"complete", "partial", "unavailable"}, "invalid token reporting status")
        for field in ("session_usage_ledger_ready", "analysis_reports_ready", "task_inventory_ready", "completion_report_ready"):
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
        proc["finalization"]["status"] = "STARTED"
        proc["run_status"] = "TRAIN_FINALIZING"
        return

    if event_type == "FINAL_PR_RECORDED":
        final = proc["finalization"]
        require(final.get("status") in {"STARTED", "AWAITING_FINAL_PR_UPDATE"}, "final PR is not ready to be recorded or updated")
        require_fields(event, ("url", "base_branch", "head_branch", "head_commit"), "final pull request")
        require(event["base_branch"] == proc["base_branch"], "final PR must target the resolved base branch")
        require(event["head_branch"] == state["run_identity"]["train_branch"], "final PR head must be the train branch")
        if final.get("status") == "AWAITING_FINAL_PR_UPDATE":
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

    if event_type == "FINAL_VERIFICATION_RECORDED":
        final = proc["finalization"]
        require(isinstance(final.get("pull_request"), dict), "final PR must exist before final verification")
        require(event.get("status") == "passed", "final verification must pass")
        require(event.get("head_commit") == final["pull_request"]["head_commit"], "final verification head mismatch")
        require_fields(event, ("head_commit", "evidence_reference"), "final verification")
        final["verification"] = dict(event)
        return

    if event_type == "FINAL_REVIEW_DISPATCHED":
        final = proc["finalization"]
        require(isinstance(final.get("pull_request"), dict), "final PR must exist before final review")
        require(isinstance(final.get("verification"), dict), "final verification must pass before final review")
        require_fields(
            event,
            ("phase_key", "train_criticality", "train_complexity", "model", "reasoning_effort", "routing_conformance"),
            "final review dispatch",
        )
        history = final.get("review_history", [])
        scope = event.get("scope") or ("followup" if history else "initial")
        require(scope in {"initial", "followup"}, "invalid final review scope")
        require(scope == ("followup" if history else "initial"), "final review scope does not match review history")
        material = event.get("material_scope_changed") is True
        required_kind = "full" if scope == "initial" or material else "focused"
        require(event.get("review_kind") == required_kind, f"final {scope} review must be {required_kind}")
        matrix = INITIAL_REVIEW_MATRIX if scope == "initial" or material else FOLLOWUP_REVIEW_MATRIX
        matrix_expected = routed_setting(
            matrix, event["train_criticality"], event["train_complexity"], bool(event.get("reasoning_authorized")),
        )
        integrated_reviews = [
            value["reviews"][0]
            for value in proc["tickets"].values()
            if value.get("status") == "MERGED_INTO_TRAIN" and value.get("reviews")
        ]
        require(integrated_reviews, "final review requires trustworthy integrated ticket reviews")
        floor = max(
            ((review["actual_model"], review["actual_reasoning_effort"]) for review in integrated_reviews),
            key=lambda setting: SETTING_ORDER[setting],
        )
        selected = matrix_expected[:2]
        if (scope == "initial" or material) and SETTING_ORDER[floor] > SETTING_ORDER[selected]:
            selected = floor
        if scope == "followup":
            baseline = (history[0]["actual_model"], history[0]["actual_reasoning_effort"])
            require(SETTING_ORDER[selected] <= SETTING_ORDER[baseline], "final followup review cannot exceed its full-review baseline")
        require((event["model"], event["reasoning_effort"]) == selected, "final review setting does not satisfy matrix and applicable floor")
        require(event["routing_conformance"] == matrix_expected[2], "final review routing conformance mismatch")
        add_phase(
            proc, key=event["phase_key"], ticket_id=None, kind="final_review",
            model=event["model"], effort=event["reasoning_effort"], branch=None,
            base=final["pull_request"]["head_commit"], scope=scope,
        )
        phase(proc, event["phase_key"])["review_kind"] = required_kind
        final["review_phase_key"] = event["phase_key"]
        final["status"] = "FINAL_PR_REVIEW"
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
        require(parse_iso(event["deadline_at"], "feedback deadline") > parse_iso(event["started_at"], "feedback start"), "feedback deadline must follow start")
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
        require(event["ci_status"] in {"passed", "not_configured", "unavailable_with_local_fallback"}, "unacceptable final CI status")
        require(event["copilot_status"] in {"received", "not_configured", "unavailable", "timed_out"}, "Copilot collection is not terminal")
        if event["copilot_status"] == "timed_out":
            require(
                parse_iso(event["collected_at"], "feedback collection time") >= parse_iso(collection["deadline_at"], "feedback deadline"),
                "Copilot cannot time out before the bounded deadline",
            )
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
        expected = routed_setting(
            IMPLEMENTATION_MATRIX, event["criticality"], event["complexity"], bool(event.get("reasoning_authorized"))
        )
        validate_routing(event, expected)
        require(event["base_commit"] == final["pull_request"]["head_commit"], "final remediation base must match final PR head")
        add_phase(
            proc, key=event["phase_key"], ticket_id=None, kind="final_remediation",
            model=event["model"], effort=event["reasoning_effort"], branch=event["branch"],
            base=event["base_commit"], scope="final",
        )
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
            ),
            "final evidence",
        )
        snapshot = final.get("feedback_snapshot") or {}
        require(event["feedback_snapshot_id"] == snapshot.get("snapshot_id"), "final evidence does not use the latest feedback snapshot")
        require(event["ci_status"] == snapshot.get("ci_status"), "final CI status differs from the collected snapshot")
        require(event["copilot_status"] == snapshot.get("copilot_status"), "final Copilot status differs from the collected snapshot")
        require(event["ci_status"] in {"passed", "not_configured", "unavailable_with_local_fallback"}, "unacceptable final CI status")
        require(event["copilot_status"] in {"received", "not_configured", "unavailable", "timed_out"}, "invalid Copilot status")
        require(event["finding_ledger_status"] == "complete", "final finding ledger is incomplete")
        require(event["token_reporting_status"] in {"complete", "partial", "unavailable"}, "invalid token reporting status")
        for field in (
            "session_usage_ledger_ready", "verification_summary_ready", "manual_validation_summary_ready",
            "attention_points_summary_ready", "task_inventory_ready", "completion_report_ready",
        ):
            require(event[field] is True, f"{field} must be true")
        final["evidence"] = dict(event)
        final["status"] = "READY_FOR_COMPLETION"
        return

    if event_type == "RUN_COMPLETED":
        issues = completion_issues(state)
        require(not issues, "completion rejected: " + "; ".join(issues))
        proc["run_status"] = "COMPLETED"
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
    final = proc.get("finalization", {})
    if final.get("status") != "READY_FOR_COMPLETION":
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
    if pr.get("head_commit") != verification.get("head_commit"):
        issues.append("final verification does not cover the PR head")
    if pr.get("head_commit") != review.get("reviewed_head"):
        issues.append("final review does not cover the PR head")
    if pr.get("head_commit") != snapshot.get("head_commit"):
        issues.append("final GitHub feedback snapshot does not cover the PR head")
    if ledger.get("feedback_snapshot_id") != snapshot.get("snapshot_id"):
        issues.append("final finding ledger does not use the latest GitHub feedback snapshot")
    return issues


def next_actions(state: dict[str, Any]) -> list[dict[str, Any]]:
    proc = procedure(state)
    if not proc.get("orchestrator_confirmed"):
        return [{"action": "REQUEST_ORCHESTRATOR_CONFIRMATION"}]
    if proc.get("supervision", {}).get("status") != "ACTIVE":
        return [{"action": "CONFIGURE_SUPERVISION_BEFORE_DISPATCH"}]

    unannounced = [gate for gate in proc["human_gates"].values() if gate["status"] == "PENDING_UNANNOUNCED"]
    if unannounced and not state.get("pending_human_action"):
        return [{"action": "ANNOUNCE_HUMAN_GATE", "gate": unannounced[0]}]
    waiting_gates = [gate for gate in proc["human_gates"].values() if gate["status"] == "PENDING_ANNOUNCED"]
    gate_actions = [{"action": "AWAIT_HUMAN_GATE", "gate": gate} for gate in waiting_gates]

    intents = [value for value in proc["phases"].values() if value.get("launch_state") == "INTENT_RECORDED"]
    if intents:
        return [
            {
                "action": "DISPATCH_VISIBLE_PHASE",
                "phase_key": value["phase_key"],
                "kind": value["kind"],
                "ticket_id": value.get("ticket_id"),
                "model": value["requested_model"],
                "reasoning_effort": value["requested_reasoning_effort"],
                "base": value["base"],
                "branch": value.get("branch"),
            }
            for value in intents
        ] + gate_actions
    input_ready = [value for value in proc["phases"].values() if value.get("launch_state") == "INPUT_READY"]
    if input_ready:
        return [
            {
                "action": "RESUME_VISIBLE_PHASE_WITH_INPUT",
                "phase_key": value["phase_key"],
                "thread_id": value.get("thread_id"),
                "provided_input": value.get("provided_input"),
            }
            for value in input_ready
        ] + gate_actions
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
            elif not analysis_phases and capacity > 0:
                actions.append({"action": "RECORD_ANALYSIS_DISPATCH_INTENT", "ticket_id": ticket_id})
                capacity -= 1
        if active:
            actions.append({"action": "WAIT_FOR_PHASE_TRANSITION", "phase_keys": [value["phase_key"] for value in active]})
        return actions + gate_actions
    if not proc.get("dependencies_consolidated"):
        return [{"action": "CONSOLIDATE_DEPENDENCIES"}]

    if state.get("execution_mode") == "dry-run":
        if waiting_gates:
            return [{"action": "AWAIT_HUMAN_GATE", "gate": gate} for gate in waiting_gates]
        if not proc["finalization"].get("dry_run_evidence"):
            return [{"action": "RECORD_DRY_RUN_REPORT_AND_USAGE_EVIDENCE"}]
        if proc.get("run_status") != "COMPLETED":
            return [{"action": "COMPLETE_RUN"}]
    ready = [
        ticket_id for ticket_id, value in proc["tickets"].items()
        if value["status"] == "READY_FOR_IMPLEMENTATION" and dependencies_satisfied(proc, value)
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
    for ticket_id, value in proc["tickets"].items():
        status = value["status"]
        if status in {"AWAITING_VERIFICATION", "AWAITING_REMEDIATION_VERIFICATION"}:
            actions.append({"action": "INTEGRATE_TESTS_AND_RUN_VERIFICATION", "ticket_id": ticket_id})
        elif status == "FUNCTIONAL_READY" and (
            not value.get("pull_request")
            or value["pull_request"].get("head_commit") != value.get("verification", {}).get("ticket_head")
        ):
            actions.append({"action": "CREATE_OR_UPDATE_TICKET_PR_TARGETING_TRAIN", "ticket_id": ticket_id})
        elif status == "FUNCTIONAL_READY" and value.get("reviews"):
            actions.append({"action": "DISPATCH_FOCUSED_FOLLOWUP_REVIEW", "ticket_id": ticket_id})
        elif status == "FUNCTIONAL_READY":
            actions.append({"action": "DISPATCH_EXHAUSTIVE_INITIAL_REVIEW", "ticket_id": ticket_id})
        elif status == "AWAITING_FINDING_RECONCILIATION":
            actions.append({"action": "RECONCILE_CODEX_CI_COPILOT_FINDINGS", "ticket_id": ticket_id})
        elif status == "NEEDS_REMEDIATION":
            actions.append({"action": "DISPATCH_FRESH_BATCHED_REMEDIATION", "ticket_id": ticket_id})
        elif status == "READY_TO_MERGE":
            actions.append({"action": "MERGE_TICKET_PR_INTO_TRAIN", "ticket_id": ticket_id})
    if actions:
        return actions

    if all(value["status"] in TERMINAL_TICKET_STATES for value in proc["tickets"].values()):
        final = proc["finalization"]
        if final.get("status") == "NOT_STARTED":
            return [{"action": "START_FINALIZATION"}]
        if final.get("status") == "NEEDS_FINAL_REMEDIATION":
            return [{"action": "RECORD_FINAL_REMEDIATION_DISPATCH_INTENT"}]
        if final.get("status") == "AWAITING_FINAL_FEEDBACK_COLLECTION":
            return [{
                "action": "START_FINAL_GITHUB_FEEDBACK_COLLECTION",
                "head_commit": final.get("pull_request", {}).get("head_commit"),
                "sources": ["codex", "ci", "copilot", "human"],
            }]
        if final.get("status") == "FINAL_FEEDBACK_COLLECTION":
            return [{
                "action": "CAPTURE_FINAL_GITHUB_FEEDBACK_SNAPSHOT",
                "head_commit": final.get("pull_request", {}).get("head_commit"),
                "collection_id": final.get("feedback_collection", {}).get("collection_id"),
                "deadline_at": final.get("feedback_collection", {}).get("deadline_at"),
            }]
        if final.get("status") == "AWAITING_FINAL_FINDING_RECONCILIATION":
            return [{"action": "RECONCILE_FINAL_CODEX_CI_COPILOT_FINDINGS"}]
        if final.get("status") == "AWAITING_FINAL_PR_UPDATE":
            return [{"action": "UPDATE_FINAL_TRAIN_PR_HEAD"}]
        if not final.get("pull_request"):
            return [{"action": "CREATE_FINAL_TRAIN_PR"}]
        if not final.get("verification"):
            return [{"action": "RUN_FINAL_EXACT_HEAD_VERIFICATION"}]
        if not final.get("review"):
            if not final.get("review_phase_key"):
                return [{"action": "RECORD_FINAL_REVIEW_DISPATCH_INTENT"}]
            return [{"action": "RECORD_FINAL_REVIEW_RESULT", "phase_key": final["review_phase_key"]}]
        if final.get("status") == "FINAL_PR_REVIEW_CLEAN" and not final.get("evidence"):
            return [{"action": "COLLECT_FINAL_TOKENS_AND_REPORTS"}]
        if proc.get("run_status") != "COMPLETED":
            return [{"action": "COMPLETE_RUN"}]
    return [{"action": "BLOCKED_OR_INCONSISTENT_STATE", "status": compact_status(state)}]


def compact_status(state: dict[str, Any]) -> dict[str, Any]:
    proc = procedure(state)
    return {
        "run_status": proc.get("run_status"),
        "revision": proc.get("revision"),
        "tickets": {ticket_id: value.get("status") for ticket_id, value in proc["tickets"].items()},
        "active_phases": [
            value["phase_key"] for value in proc["phases"].values()
            if value.get("launch_state") in ACTIVE_PHASE_STATES
        ],
        "pending_human_gates": [
            gate["gate_id"] for gate in proc["human_gates"].values()
            if gate.get("status", "").startswith("PENDING")
        ],
        "pending_human_action": state.get("pending_human_action"),
        "finalization": proc.get("finalization", {}).get("status"),
    }


def migrate_procedure(state: dict[str, Any]) -> bool:
    proc = procedure(state)
    changed = False
    if int(proc.get("schema_version", 1)) < 2:
        proc["schema_version"] = 2
        changed = True
    if "pending_human_action" not in state:
        state["pending_human_action"] = None
        changed = True
    return changed


def heartbeat(args: argparse.Namespace) -> int:
    state = run_registry.load_json(args.state)
    status = compact_status(state)
    actions = next_actions(state)
    pending = state.get("pending_human_action")
    if procedure(state).get("run_status") == "COMPLETED":
        decision = "STOP_COMPLETED"
    elif pending:
        decision = "NOTIFY_ACTION_REQUIRED"
    elif any(action["action"] == "BLOCKED_OR_INCONSISTENT_STATE" for action in actions):
        decision = "ESCALATE_CONTROLLER_INCONSISTENCY"
    elif any(action["action"] not in {"WAIT_FOR_PHASE_TRANSITION", "AWAIT_HUMAN_GATE"} for action in actions):
        decision = "CONTINUE_AUTOMATICALLY"
    elif status["active_phases"]:
        decision = "WAIT_FOR_TRANSITION"
    else:
        decision = "WAIT_FOR_USER_GATE"
    output = {
        **status,
        "heartbeat_decision": decision,
        "may_pause_or_delete_watcher": decision == "STOP_COMPLETED",
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
        timestamp = now_iso()
        state["schema_version"] = max(int(state.get("schema_version", 0)), 3)
        state["procedure"] = {
            "schema_version": 2,
            "revision": 0,
            "run_status": "ACTIVE",
            "base_branch": args.base_branch,
            "approval_mode": args.approval_mode,
            "orchestrator_confirmed": False,
            "supervision": {"status": "INACTIVE", "mode": None, "watcher_id": None},
            "limits": {"max_active_analyses": 5, "max_active_execution_pairs": 2, "max_integrated_tickets": 5},
            "tickets": {
                ticket_id: {
                    "status": "DISCOVERED", "triage": None, "analysis": None,
                    "hard_dependencies": [], "collision_domains": [], "reviews": [],
                    "remediation_cycles": 0,
                }
                for ticket_id in identity["tickets"]
            },
            "phases": {},
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
        automatic = [action for action in actions if action["action"] not in {"WAIT_FOR_PHASE_TRANSITION", "AWAIT_HUMAN_GATE"}]
        if automatic:
            issues.append("automatic actions remain: " + ", ".join(action["action"] for action in automatic))
        active = compact_status(state)["active_phases"]
        if active and procedure(state).get("supervision", {}).get("status") != "ACTIVE":
            issues.append("active phases have no deterministic supervision")
    output = {"status": "pass" if not issues else "fail", "mode": args.mode, "issues": issues}
    sys.stdout.write(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    return 0 if not issues else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("bootstrap")
    start.add_argument("--state", type=Path, required=True)
    start.add_argument("--base-branch", required=True)
    start.add_argument("--approval-mode", choices=("standard", "auto-analysis", "auto-merge", "full-auto"), required=True)
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
