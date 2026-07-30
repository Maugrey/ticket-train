#!/usr/bin/env python3
"""Validate ticket-train yield and completion control state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ACTIVE_PHASE_STATES = {
    "INTENT_RECORDED",
    "LAUNCH_REQUESTED",
    "LAUNCH_UNKNOWN",
    "QUEUED",
    "RUNNING",
}
TERMINAL_REASONS = {
    "AWAITING_REQUIRED_USER_INPUT",
    "BLOCKED",
    "COMPLETED",
    "CHECKPOINT",
}
NO_AUTOMATIC_ACTION = {None, "", "none", "await_user", "resume_after_blocker"}
ACCEPTABLE_USAGE_STATUS = {"complete", "partial", "unavailable"}
ACCEPTABLE_CI_STATUS = {
    "passed",
    "not_configured",
    "unavailable_with_local_fallback",
}
ACCEPTABLE_COPILOT_STATUS = {
    "received",
    "not_configured",
    "unavailable",
    "timed_out",
}
ACCEPTABLE_ROUTING_STATUS = {"conformant", "documented-fallback"}
ACCEPTABLE_COST_STATUS = {"clear", "checkpoint-resolved", "checkpoint-open"}
TERMINAL_TICKET_STATES = {
    "ANALYSIS_REPORTED",
    "REPORTED",
    "MERGED_INTO_TRAIN",
    "BLOCKED",
    "FAILED",
    "CANCELLED",
}


class GuardError(ValueError):
    """Raised for invalid control-state input."""


def load_state(path: Path) -> dict[str, Any]:
    try:
        with path.expanduser().resolve().open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise GuardError(f"Cannot read control state: {error}") from error
    if not isinstance(value, dict):
        raise GuardError("Control state must be a JSON object")
    return value


def as_list(value: Any, field: str, issues: list[str]) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        issues.append(f"{field} must be a list")
        return []
    return value


def validate_phases(control: dict[str, Any], issues: list[str]) -> None:
    phases = as_list(control.get("phases"), "control.phases", issues)
    seen_keys: set[str] = set()
    implementation_threads: dict[str, str] = {}
    remediation_threads: list[tuple[str, str, str]] = []
    for index, raw_phase in enumerate(phases):
        prefix = f"control.phases[{index}]"
        if not isinstance(raw_phase, dict):
            issues.append(f"{prefix} must be an object")
            continue
        phase_key = raw_phase.get("phase_key")
        if not isinstance(phase_key, str) or not phase_key.strip():
            issues.append(f"{prefix}.phase_key is required")
            phase_key = prefix
        elif phase_key in seen_keys:
            issues.append(f"duplicate phase_key: {phase_key}")
        else:
            seen_keys.add(phase_key)

        state = raw_phase.get("launch_state")
        if state in ACTIVE_PHASE_STATES:
            issues.append(f"active phase remains: {phase_key} ({state})")

        if state == "QUEUED" and not (
            raw_phase.get("client_thread_id") or raw_phase.get("thread_id")
        ):
            issues.append(f"queued phase has no client or thread ID: {phase_key}")
        if state in {"RUNNING", "COMPLETED", "FAILED", "CANCELLED"} and not raw_phase.get(
            "thread_id"
        ):
            issues.append(f"materialized phase has no thread ID: {phase_key}")

        visibility = raw_phase.get("visibility", "user-visible")
        hidden_authorized = raw_phase.get("hidden_authorized") is True
        if visibility != "user-visible" and not hidden_authorized:
            issues.append(f"unauthorized hidden phase: {phase_key}")

        if state == "COMPLETED":
            if raw_phase.get("final_report_captured") is not True:
                issues.append(f"completed phase report not captured: {phase_key}")
            if raw_phase.get("usage_captured") is not True:
                issues.append(f"completed phase usage not captured: {phase_key}")

        ticket_id = str(raw_phase.get("ticket_id") or "run")
        phase_name = str(raw_phase.get("phase") or phase_key).lower()
        thread_id = raw_phase.get("thread_id")
        if thread_id and "implementation" in phase_name and "remediation" not in phase_name:
            implementation_threads[ticket_id] = str(thread_id)
        if thread_id and "remediation" in phase_name:
            remediation_threads.append((ticket_id, str(thread_id), str(phase_key)))

    for ticket_id, thread_id, phase_key in remediation_threads:
        if implementation_threads.get(ticket_id) == thread_id:
            issues.append(
                f"remediation reused implementation thread instead of a fresh context: {phase_key}"
            )


def validate_cost_controls(control: dict[str, Any], issues: list[str]) -> None:
    if not control.get("proportionality_profile_revision"):
        issues.append("control.proportionality_profile_revision is required")

    size_budget = control.get("train_size_budget")
    if not isinstance(size_budget, dict):
        issues.append("control.train_size_budget must be an object")
    else:
        for field in ("material_files", "schema_or_data_transformations"):
            value = size_budget.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                issues.append(f"control.train_size_budget.{field} must be a non-negative integer")
        domains = size_budget.get("structural_domains")
        if not isinstance(domains, list):
            issues.append("control.train_size_budget.structural_domains must be a list")
        if not isinstance(size_budget.get("checkpoint_crossed"), bool):
            issues.append("control.train_size_budget.checkpoint_crossed must be boolean")

    budgets = control.get("review_pass_budgets")
    if not isinstance(budgets, dict):
        issues.append("control.review_pass_budgets must be an object")
    else:
        for scope, budget in budgets.items():
            if not isinstance(budget, dict):
                issues.append(f"review budget must be an object: {scope}")
                continue
            complete_reviews = budget.get("complete_reviews", 0)
            remediation_cycles = budget.get("remediation_cycles", 0)
            if not isinstance(complete_reviews, int) or complete_reviews < 0:
                issues.append(f"invalid complete review count: {scope}")
            elif complete_reviews > 1:
                issues.append(f"complete review budget exceeded for stable scope: {scope}")
            if not isinstance(remediation_cycles, int) or remediation_cycles < 0:
                issues.append(f"invalid remediation cycle count: {scope}")
            elif remediation_cycles > 2:
                issues.append(f"remediation cycle budget exceeded: {scope}")

    cost_status = control.get("cost_anomaly_status")
    if cost_status not in ACCEPTABLE_COST_STATUS:
        issues.append(f"invalid or missing cost anomaly status: {cost_status!r}")

    duplicates = control.get("duplicate_session_inventory")
    if not isinstance(duplicates, list):
        issues.append("control.duplicate_session_inventory must be a list")
        duplicates = []
    unmeasured = control.get("unmeasured_phase_inventory")
    if not isinstance(unmeasured, list):
        issues.append("control.unmeasured_phase_inventory must be a list")
        unmeasured = []
    if (duplicates or unmeasured) and cost_status == "clear":
        issues.append(
            "duplicate sessions or unmeasured phases require an open or resolved cost checkpoint"
        )


def validate_common(state: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    issues: list[str] = []
    control = state.get("control")
    if not isinstance(control, dict):
        return {}, ["missing control object"]

    if not control.get("manifest_updated_at"):
        issues.append("control.manifest_updated_at is required")
    if control.get("manifest_reconciled") is not True:
        issues.append("control.manifest_reconciled must be true")

    validate_phases(control, issues)
    validate_cost_controls(control, issues)

    launch_unknown = as_list(
        control.get("launch_unknown_phase_keys"),
        "control.launch_unknown_phase_keys",
        issues,
    )
    if launch_unknown:
        issues.append("launch-unknown phases remain: " + ", ".join(map(str, launch_unknown)))

    next_action = control.get("next_automatic_action")
    if next_action not in NO_AUTOMATIC_ACTION:
        issues.append(f"automatic action remains: {next_action}")

    return control, issues


def validate_yield(state: dict[str, Any]) -> list[str]:
    control, issues = validate_common(state)
    if not control:
        return issues

    reason = control.get("terminal_reason")
    if reason not in TERMINAL_REASONS:
        issues.append(f"invalid or missing terminal_reason: {reason!r}")
        return issues

    size_budget = control.get("train_size_budget")
    checkpoint_crossed = isinstance(size_budget, dict) and size_budget.get("checkpoint_crossed") is True
    cost_checkpoint = control.get("cost_anomaly_status") == "checkpoint-open"
    if (checkpoint_crossed or cost_checkpoint) and reason not in {
        "AWAITING_REQUIRED_USER_INPUT",
        "BLOCKED",
        "CHECKPOINT",
    }:
        issues.append("open size or cost checkpoint requires a checkpoint-compatible terminal reason")

    if reason == "AWAITING_REQUIRED_USER_INPUT":
        gates = as_list(control.get("pending_human_gates"), "control.pending_human_gates", issues)
        if not gates:
            issues.append("AWAITING_REQUIRED_USER_INPUT requires a pending human gate or decision")
    elif reason == "BLOCKED":
        blockers = as_list(control.get("blocking_conditions"), "control.blocking_conditions", issues)
        if not blockers:
            issues.append("BLOCKED requires at least one blocking condition")
    elif reason in {"COMPLETED", "CHECKPOINT"}:
        issues.extend(validate_completion(state, include_common=False))

    return issues


def validate_completion(state: dict[str, Any], include_common: bool = True) -> list[str]:
    if include_common:
        control, issues = validate_common(state)
    else:
        control = state.get("control")
        issues = []
    if not isinstance(control, dict):
        return issues or ["missing control object"]

    if control.get("cost_anomaly_status") == "checkpoint-open":
        issues.append("completion cannot retain an open cost anomaly checkpoint")

    finalization = control.get("finalization")
    if not isinstance(finalization, dict):
        return issues + ["missing control.finalization object"]

    ticket_states = control.get("requested_ticket_states")
    if not isinstance(ticket_states, dict) or not ticket_states:
        issues.append("control.requested_ticket_states must list every requested ticket")
    else:
        for ticket_id, ticket_state in ticket_states.items():
            if ticket_state not in TERMINAL_TICKET_STATES:
                issues.append(f"requested ticket is not terminal: {ticket_id} ({ticket_state})")

    execution_mode = state.get("execution_mode") or state.get("executionMode")
    integrated = state.get("integrated_ticket_count")
    if integrated is None:
        integrated = finalization.get("integrated_ticket_count", 0)
    if not isinstance(integrated, int) or isinstance(integrated, bool) or integrated < 0:
        issues.append("integrated ticket count must be a non-negative integer")
        integrated = 0

    if execution_mode == "live" and integrated > 0:
        required_truthy = {
            "final_pull_request_url": "final pull request URL",
            "final_pull_request_head": "final pull request head",
            "final_reviewed_head": "final reviewed head",
        }
        for field, label in required_truthy.items():
            if not finalization.get(field):
                issues.append(f"missing {label}")
        if finalization.get("final_pr_created_before_review") is not True:
            issues.append("final pull request was not recorded before final review")
        if finalization.get("full_verification_status") != "passed":
            issues.append("full project verification is not passed")
        if finalization.get("final_review_status") != "clean":
            issues.append("final Codex review is not clean")
        if finalization.get("final_review_routing_conformance") not in ACCEPTABLE_ROUTING_STATUS:
            issues.append("final review routing is not conformant")
        if (
            finalization.get("final_pull_request_head")
            and finalization.get("final_reviewed_head")
            and finalization.get("final_pull_request_head")
            != finalization.get("final_reviewed_head")
        ):
            issues.append("final reviewed head differs from final pull-request head")
        if finalization.get("ci_status") not in ACCEPTABLE_CI_STATUS:
            issues.append("final CI status is missing or not acceptable")
        if finalization.get("copilot_status") not in ACCEPTABLE_COPILOT_STATUS:
            issues.append("final Copilot status is missing")
        if finalization.get("finding_ledger_status") != "complete":
            issues.append("final finding ledger is incomplete")

    if finalization.get("token_reporting_status") not in ACCEPTABLE_USAGE_STATUS:
        issues.append("token reporting status is missing")
    if finalization.get("session_usage_ledger_ready") is not True:
        issues.append("session usage ledger is not ready")
    if finalization.get("manual_validation_summary_ready") is not True:
        issues.append("manual validation summary is not ready")
    if finalization.get("attention_points_summary_ready") is not True:
        issues.append("code/application attention summary is not ready")
    if finalization.get("task_inventory_ready") is not True:
        issues.append("visible task inventory is not ready")
    if finalization.get("completion_report_ready") is not True:
        issues.append("completion report is not ready")

    return issues


def render_result(mode: str, state_path: Path, issues: list[str]) -> None:
    document = {
        "mode": mode,
        "state": str(state_path.expanduser().resolve()),
        "status": "pass" if not issues else "fail",
        "issues": issues,
    }
    sys.stdout.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate whether a ticket-train orchestrator may yield or complete."
    )
    parser.add_argument("mode", choices=("check-yield", "check-completion"))
    parser.add_argument("--state", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        state = load_state(args.state)
        if args.mode == "check-yield":
            issues = validate_yield(state)
        else:
            issues = validate_completion(state)
    except GuardError as error:
        issues = [str(error)]
    render_result(args.mode, args.state, issues)
    return 0 if not issues else 2


if __name__ == "__main__":
    raise SystemExit(main())
