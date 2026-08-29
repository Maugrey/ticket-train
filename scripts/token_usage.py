#!/usr/bin/env python3
"""Capture and aggregate token usage for known Codex thread IDs."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
THREAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{5,127}$")
PHASE_ATTEMPT_PATTERN = re.compile(r"^(?P<family>.+):(?P<attempt>[0-9]+)$")
TICKET_PHASE_COLUMNS = (
    ("analysis", "Analysis"),
    ("analysis_validation", "Analysis validation"),
    ("contract_validation", "Contract validation"),
    ("acceptance_tests", "Acceptance tests"),
    ("implementation", "Implementation"),
    ("verification", "Verification"),
    ("initial_review", "Initial review"),
    ("remediation", "Remediation"),
    ("followup_review", "Follow-up review"),
)
OPTIONAL_TICKET_PHASES = {"analysis_validation", "remediation", "followup_review"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def empty_usage() -> dict[str, int]:
    return {field: 0 for field in USAGE_FIELDS}


def normalize_usage(raw: Any) -> dict[str, int] | None:
    if not isinstance(raw, dict):
        return None

    usage: dict[str, int] = {}
    for field in USAGE_FIELDS:
        value = raw.get(field, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        usage[field] = value
    return usage


def parse_thread_spec(spec: str) -> tuple[str, str]:
    thread_id, separator, label = spec.partition("=")
    if not THREAD_ID_PATTERN.fullmatch(thread_id):
        raise ValueError(f"Invalid thread ID: {thread_id!r}")
    return thread_id, label if separator and label else thread_id


def resolve_codex_home(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".codex").resolve()


def candidate_session_files(codex_home: Path, thread_id: str) -> list[Path]:
    candidates: list[Path] = []
    for dirname in ("sessions", "archived_sessions"):
        root = codex_home / dirname
        if root.is_dir():
            candidates.extend(root.rglob(f"*{thread_id}*.jsonl"))
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)


def read_session_usage_bounds(
    path: Path, expected_thread_id: str
) -> tuple[dict[str, int], dict[str, int]] | None:
    session_id: str | None = None
    first_usage: dict[str, int] | None = None
    latest_usage: dict[str, int] | None = None

    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    # A concurrently written final line may be incomplete.
                    continue

                if event.get("type") == "session_meta":
                    payload = event.get("payload")
                    if isinstance(payload, dict):
                        session_id = payload.get("id") or payload.get("session_id")

                payload = event.get("payload")
                if not isinstance(payload, dict) or payload.get("type") != "token_count":
                    continue
                info = payload.get("info")
                if not isinstance(info, dict):
                    continue
                normalized = normalize_usage(info.get("total_token_usage"))
                if normalized is not None:
                    if first_usage is None:
                        first_usage = normalized
                    latest_usage = normalized
    except OSError:
        return None

    if session_id != expected_thread_id or first_usage is None or latest_usage is None:
        return None
    return first_usage, latest_usage


def read_session_usage(path: Path, expected_thread_id: str) -> dict[str, int] | None:
    bounds = read_session_usage_bounds(path, expected_thread_id)
    return bounds[1] if bounds is not None else None


def read_session_diagnostics(path: Path) -> dict[str, int]:
    diagnostics = {
        "token_counter_events": 0,
        "assistant_messages": 0,
        "tool_calls": 0,
        "context_compactions": 0,
    }
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_type = str(event.get("type") or "")
                payload = event.get("payload")
                payload_type = str(payload.get("type") or "") if isinstance(payload, dict) else ""
                combined_type = f"{event_type}/{payload_type}".lower()
                if event_type == "event_msg" and payload_type == "token_count":
                    diagnostics["token_counter_events"] += 1
                if event_type == "event_msg" and payload_type == "agent_message":
                    diagnostics["assistant_messages"] += 1
                if event_type == "response_item" and payload_type in {
                    "custom_tool_call",
                    "function_call",
                }:
                    diagnostics["tool_calls"] += 1
                if "compact" in combined_type:
                    diagnostics["context_compactions"] += 1
    except OSError:
        return diagnostics
    return diagnostics


def read_started_subagents(path: Path) -> dict[str, str]:
    """Return hidden agent sessions explicitly started by an orchestrator session."""

    discovered: dict[str, str] = {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                if payload.get("type") != "sub_agent_activity" or payload.get("kind") != "started":
                    continue
                thread_id = payload.get("agent_thread_id")
                if isinstance(thread_id, str) and THREAD_ID_PATTERN.fullmatch(thread_id):
                    discovered[thread_id] = str(payload.get("agent_path") or "hidden-agent")
    except OSError:
        return discovered
    return discovered


def capture_thread(codex_home: Path, thread_id: str, label: str) -> dict[str, Any]:
    candidates = candidate_session_files(codex_home, thread_id)
    if not candidates:
        return {
            "thread_id": thread_id,
            "label": label,
            "status": "unavailable",
            "reason": "session-not-found",
        }

    for path in candidates:
        usage = read_session_usage(path, thread_id)
        if usage is not None:
            return {
                "thread_id": thread_id,
                "label": label,
                "status": "available",
                "usage": usage,
            }

    return {
        "thread_id": thread_id,
        "label": label,
        "status": "unavailable",
        "reason": "token-count-not-found",
    }


def write_document(document: dict[str, Any], output: Path | None) -> None:
    rendered = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is None:
        sys.stdout.write(rendered)
        return
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


def load_document(path: Path) -> dict[str, Any]:
    with path.expanduser().resolve().open("r", encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported usage document: {path}")
    return document


def sum_usage(items: list[dict[str, int]]) -> dict[str, int]:
    result = empty_usage()
    for usage in items:
        for field in USAGE_FIELDS:
            result[field] += usage[field]
    return result


def subtract_usage(final: dict[str, int], baseline: dict[str, int]) -> dict[str, int] | None:
    delta = {field: final[field] - baseline[field] for field in USAGE_FIELDS}
    return None if any(value < 0 for value in delta.values()) else delta


def load_manifest(path: Path) -> dict[str, Any]:
    with path.expanduser().resolve().open("r", encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise ValueError("Manifest must be a JSON object")
    return document


def collect_manifest_session_records(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect session references without inspecting prompts or transcripts."""

    records: list[dict[str, Any]] = []
    seen_objects: set[tuple[str, str, str]] = set()

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            thread_id = value.get("thread_id") or value.get("threadId")
            if isinstance(thread_id, str) and THREAD_ID_PATTERN.fullmatch(thread_id):
                phase_key = str(value.get("phase_key") or value.get("phaseKey") or "")
                label = str(
                    value.get("label")
                    or value.get("phase")
                    or value.get("display_title")
                    or phase_key
                    or thread_id
                )
                key = (thread_id, phase_key, label)
                if key not in seen_objects:
                    seen_objects.add(key)
                    records.append(
                        {
                            "thread_id": thread_id,
                            "phase_key": phase_key or None,
                            "label": label,
                            "ticket_id": value.get("ticket_id") or value.get("ticketId"),
                            "attempt": value.get("attempt") or value.get("launch_attempts"),
                            "launch_state": value.get("launch_state"),
                            "authoritative": value.get("authoritative"),
                            "duplicate_of": value.get("duplicate_of"),
                            "manifest_path": path,
                        }
                    )
            for child_key, child in value.items():
                visit(child, f"{path}.{child_key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(manifest, "manifest")
    for owner in collect_orchestrator_thread_ids(manifest):
        if THREAD_ID_PATTERN.fullmatch(owner):
            records.append(
                {
                    "thread_id": owner,
                    "phase_key": None,
                    "label": "orchestrator",
                    "ticket_id": None,
                    "attempt": None,
                    "launch_state": None,
                    "authoritative": True,
                    "duplicate_of": None,
                    "manifest_path": "manifest.orchestrator-ownership-history",
                }
            )
    return records


def collect_orchestrator_thread_ids(manifest: dict[str, Any]) -> list[str]:
    thread_ids: set[str] = set()
    lease = manifest.get("orchestrator_lease")
    if isinstance(lease, dict) and isinstance(lease.get("owner_thread_id"), str):
        owner = lease["owner_thread_id"]
        if THREAD_ID_PATTERN.fullmatch(owner):
            thread_ids.add(owner)
    history = manifest.get("handoff_history")
    if isinstance(history, list):
        for handoff in history:
            if not isinstance(handoff, dict):
                continue
            for field in ("from_thread_id", "to_thread_id"):
                value = handoff.get(field)
                if isinstance(value, str) and THREAD_ID_PATTERN.fullmatch(value):
                    thread_ids.add(value)
    return sorted(thread_ids)


def ledger_session(
    codex_home: Path, thread_id: str, metadata: list[dict[str, Any]]
) -> dict[str, Any]:
    candidates = candidate_session_files(codex_home, thread_id)
    phase_keys = sorted(
        {str(item["phase_key"]) for item in metadata if item.get("phase_key")}
    )
    labels = sorted({str(item["label"]) for item in metadata if item.get("label")})
    duplicate_marked = any(item.get("duplicate_of") for item in metadata)

    for path in candidates:
        bounds = read_session_usage_bounds(path, thread_id)
        if bounds is None:
            continue
        first_counter, final_counter = bounds
        baseline = empty_usage()
        delta = subtract_usage(final_counter, baseline)
        return {
            "thread_id": thread_id,
            "labels": labels,
            "phase_keys": phase_keys,
            "status": "available",
            "authoritative": not duplicate_marked,
            "duplicate": duplicate_marked,
            "baseline_mode": "zero-session-baseline",
            "baseline_usage": baseline,
            "first_observed_counter": first_counter,
            "final_usage": final_counter,
            "delta_usage": delta,
            "diagnostics": read_session_diagnostics(path),
            "session_file": str(path),
        }

    return {
        "thread_id": thread_id,
        "labels": labels,
        "phase_keys": phase_keys,
        "status": "unavailable",
        "authoritative": not duplicate_marked,
        "duplicate": duplicate_marked,
        "reason": "session-or-token-count-not-found",
    }


def aggregate_status(available: int, unavailable: int) -> str:
    if available == 0:
        return "unavailable"
    if unavailable:
        return "partial"
    return "complete"


def phase_matrix_category(phase: dict[str, Any]) -> str | None:
    kind = str(phase.get("kind") or "")
    phase_key = str(phase.get("phase_key") or "")
    if kind == "analysis":
        return "analysis"
    if kind == "analysis_route_validation":
        return "analysis_validation"
    if kind == "plan_contract_validation":
        return "contract_validation"
    if kind == "acceptance_tests":
        return "acceptance_tests"
    if kind == "implementation":
        return "implementation"
    if kind == "remediation":
        return "remediation"
    if kind == "review":
        return "followup_review" if "followup" in phase_key.lower() or phase.get("scope") == "followup" else "initial_review"
    return None


def unavailable_measurement(reason: str, phase_keys: list[str] | None = None) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "usage": None,
        "phase_keys": list(phase_keys or []),
        "missing_reasons": [reason],
    }


def exact_measurement(usage: dict[str, int], phase_keys: list[str] | None = None) -> dict[str, Any]:
    return {
        "status": "complete",
        "usage": usage,
        "phase_keys": list(phase_keys or []),
        "missing_reasons": [],
    }


def not_applicable_measurement(reason: str) -> dict[str, Any]:
    return {
        "status": "not_applicable",
        "usage": None,
        "phase_keys": [],
        "missing_reasons": [reason],
    }


def combine_measurements(measurements: list[dict[str, Any]]) -> dict[str, Any]:
    available = [
        normalize_usage(item.get("usage"))
        for item in measurements
        if item.get("status") in {"complete", "partial"}
    ]
    exact = [item for item in available if item is not None]
    missing = [
        reason
        for item in measurements
        if item.get("status") in {"partial", "unavailable"}
        for reason in item.get("missing_reasons", [])
    ]
    phase_keys = sorted({
        str(phase_key)
        for item in measurements
        for phase_key in item.get("phase_keys", [])
        if phase_key
    })
    if not measurements or all(item.get("status") == "not_applicable" for item in measurements):
        return not_applicable_measurement("no applicable phase was recorded")
    if exact:
        return {
            "status": "partial" if missing else "complete",
            "usage": sum_usage(exact),
            "phase_keys": phase_keys,
            "missing_reasons": sorted(set(missing)),
        }
    return unavailable_measurement(
        "; ".join(sorted(set(missing))) or "exact counters unavailable",
        phase_keys,
    )


def phase_measurement(
    phase: dict[str, Any], sessions: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    phase_key = str(phase.get("phase_key") or "")
    thread_id = phase.get("thread_id")
    if not thread_id:
        return unavailable_measurement("thread ID missing", [phase_key])
    session = sessions.get(str(thread_id))
    if not session or session.get("status") != "available":
        return unavailable_measurement("session counters unavailable", [phase_key])
    if phase.get("usage_captured") is not True:
        return unavailable_measurement("phase measurement boundary was not captured", [phase_key])
    usage = normalize_usage(session.get("delta_usage"))
    if usage is None:
        return unavailable_measurement("invalid session counters", [phase_key])
    if len(session.get("phase_keys", [])) > 1:
        return unavailable_measurement("shared session window was not isolated by phase", [phase_key])
    return exact_measurement(usage, [phase_key])


def expected_transverse_task_ids(manifest: dict[str, Any]) -> list[str]:
    procedure = manifest.get("procedure") if isinstance(manifest.get("procedure"), dict) else {}
    phases = procedure.get("phases") if isinstance(procedure.get("phases"), dict) else {}
    task_ids = {"run:orchestration", "run:usage-reporting"}
    for phase in phases.values():
        if not isinstance(phase, dict) or phase.get("ticket_id") not in {None, "run"}:
            continue
        phase_key = phase.get("phase_key")
        if phase_key:
            task_ids.add(f"phase:{phase_key}")
    event_types = {
        str(item.get("type"))
        for item in procedure.get("event_log", [])
        if isinstance(item, dict)
    }
    if "DEPENDENCIES_CONSOLIDATED" in event_types:
        task_ids.add("run:dependency-consolidation")
    if "FINAL_VERIFICATION_RECORDED" in event_types:
        task_ids.add("run:final-verification")
    if event_types & {"FINAL_FEEDBACK_SNAPSHOT_RECORDED", "FINAL_FINDINGS_RECONCILED"}:
        task_ids.add("run:github-feedback")
    return sorted(task_ids)


def build_usage_matrix(
    manifest: dict[str, Any],
    sessions: dict[str, dict[str, Any]],
    orchestrator_thread_ids: list[str],
    aggregate_usage: dict[str, int],
) -> dict[str, Any]:
    procedure = manifest.get("procedure") if isinstance(manifest.get("procedure"), dict) else {}
    phases_object = procedure.get("phases") if isinstance(procedure.get("phases"), dict) else {}
    phases = [phase for phase in phases_object.values() if isinstance(phase, dict)]
    tickets = procedure.get("tickets") if isinstance(procedure.get("tickets"), dict) else {}
    ticket_rows: dict[str, Any] = {}

    for ticket_id, ticket_state in sorted(tickets.items()):
        ticket_phases = [phase for phase in phases if str(phase.get("ticket_id") or "") == str(ticket_id)]
        cells: dict[str, Any] = {}
        merged = isinstance(ticket_state, dict) and ticket_state.get("status") == "MERGED_INTO_TRAIN"
        for column, _label in TICKET_PHASE_COLUMNS:
            matching = [phase for phase in ticket_phases if phase_matrix_category(phase) == column]
            if column == "verification":
                verification = ticket_state.get("verification") if isinstance(ticket_state, dict) else None
                if isinstance(verification, dict) and verification.get("model_tokens") == 0:
                    cells[column] = exact_measurement(empty_usage(), [])
                elif isinstance(verification, dict):
                    cells[column] = unavailable_measurement("verification token boundary unavailable")
                elif merged:
                    cells[column] = unavailable_measurement("required verification evidence missing")
                else:
                    cells[column] = not_applicable_measurement("ticket did not reach verification")
                continue
            if matching:
                cells[column] = combine_measurements([
                    phase_measurement(phase, sessions) for phase in matching
                ])
            elif column in OPTIONAL_TICKET_PHASES:
                cells[column] = not_applicable_measurement("phase not required for this ticket")
            elif merged:
                cells[column] = unavailable_measurement("required phase missing from manifest")
            else:
                cells[column] = not_applicable_measurement("ticket stopped before this phase")

        total = combine_measurements([
            cell for cell in cells.values() if cell.get("status") != "not_applicable"
        ])
        ticket_rows[str(ticket_id)] = {
            "scope_type": "ticket",
            "cells": cells,
            "total": total,
        }

    phase_totals = {
        column: combine_measurements([
            row["cells"][column] for row in ticket_rows.values()
            if row["cells"][column].get("status") != "not_applicable"
        ])
        for column, _label in TICKET_PHASE_COLUMNS
    }

    transverse_rows: dict[str, Any] = {}
    orchestration_measurements = []
    for thread_id in orchestrator_thread_ids:
        session = sessions.get(thread_id)
        usage = normalize_usage(session.get("delta_usage")) if isinstance(session, dict) else None
        if isinstance(session, dict) and session.get("status") == "available" and usage is not None:
            orchestration_measurements.append(exact_measurement(usage, []))
        else:
            orchestration_measurements.append(unavailable_measurement(f"orchestrator segment unavailable: {thread_id}"))
    transverse_rows["run:orchestration"] = {
        **combine_measurements(orchestration_measurements),
        "accounting_mode": "independent",
        "notes": "Non-overlapping orchestrator segments, measured before report publication.",
    }

    for phase in phases:
        if phase.get("ticket_id") not in {None, "run"}:
            continue
        phase_key = str(phase.get("phase_key") or "")
        if not phase_key:
            continue
        transverse_rows[f"phase:{phase_key}"] = {
            **phase_measurement(phase, sessions),
            "accounting_mode": "independent",
            "notes": str(phase.get("kind") or "run-level model phase"),
        }

    event_types = {
        str(item.get("type"))
        for item in procedure.get("event_log", [])
        if isinstance(item, dict)
    }
    if "DEPENDENCIES_CONSOLIDATED" in event_types:
        transverse_rows["run:dependency-consolidation"] = {
            **unavailable_measurement("not isolated from orchestrator session"),
            "status": "included_in_orchestration",
            "accounting_mode": "included-in-orchestration",
            "notes": "Reported explicitly without double-counting the orchestrator total.",
        }
    finalization = procedure.get("finalization") if isinstance(procedure.get("finalization"), dict) else {}
    if "FINAL_VERIFICATION_RECORDED" in event_types:
        verification = finalization.get("verification") if isinstance(finalization.get("verification"), dict) else {}
        measurement = (
            exact_measurement(empty_usage())
            if verification.get("model_tokens") == 0
            else unavailable_measurement("final verification token boundary unavailable")
        )
        transverse_rows["run:final-verification"] = {
            **measurement,
            "accounting_mode": "independent",
            "notes": "Deterministic verification reports zero model tokens only when confirmed by evidence.",
        }
    if event_types & {"FINAL_FEEDBACK_SNAPSHOT_RECORDED", "FINAL_FINDINGS_RECONCILED"}:
        transverse_rows["run:github-feedback"] = {
            **unavailable_measurement("not isolated from orchestrator session"),
            "status": "included_in_orchestration",
            "accounting_mode": "included-in-orchestration",
            "notes": "Collection and reconciliation are listed explicitly; their model usage remains in orchestration.",
        }
    transverse_rows["run:usage-reporting"] = {
        **exact_measurement(empty_usage()),
        "accounting_mode": "independent",
        "notes": "Ledger and matrix generation are deterministic; prose publication remains in orchestration.",
    }

    reported_measurements = [
        cell
        for row in ticket_rows.values()
        for cell in row["cells"].values()
        if cell.get("status") != "not_applicable"
    ] + [
        row
        for row in transverse_rows.values()
        if row.get("accounting_mode") == "independent"
    ]
    has_exact = any(normalize_usage(item.get("usage")) is not None for item in reported_measurements)
    has_missing = any(item.get("status") in {"partial", "unavailable"} for item in reported_measurements)
    coverage_status = "partial" if has_exact and has_missing else ("complete" if has_exact else "unavailable")

    return {
        "schema_version": 1,
        "coverage_status": coverage_status,
        "ticket_phase_columns": [column for column, _label in TICKET_PHASE_COLUMNS],
        "ticket_phase_labels": {column: label for column, label in TICKET_PHASE_COLUMNS},
        "ticket_rows": ticket_rows,
        "phase_totals": phase_totals,
        "transverse_rows": dict(sorted(transverse_rows.items())),
        "expected_ticket_ids": sorted(str(ticket_id) for ticket_id in tickets),
        "expected_transverse_task_ids": expected_transverse_task_ids(manifest),
        "unreported_cell_count": 0,
        "aggregate_usage": aggregate_usage,
    }


DISPLAY_PHASE_COLUMNS = (
    ("analysis", ("analysis", "analysis_validation", "contract_validation")),
    ("implementation", ("implementation",)),
    ("acceptance", ("acceptance_tests", "verification")),
    ("remediation", ("remediation",)),
    ("review", ("initial_review", "followup_review")),
)


REPORT_TEXT = {
    "en": {
        "title": "Token consumption summary",
        "scope": "Ticket / responsibility",
        "analysis": "Analysis",
        "implementation": "Implementation",
        "acceptance": "Acceptance",
        "remediation": "Remediation",
        "review": "Review",
        "total": "Total",
        "column_total": "Total",
        "included": "Included in orchestrator",
        "measured_total": "Measured train total",
        "breakdown": "Input: {input}; cached input: {cached}; output: {output}; reasoning output: {reasoning}.",
        "missing": "`N/A` means that the exact interval is unavailable; `—` means not applicable.",
        "partial": "`*` marks a partial value: displayed measured passes are included, but at least one interval is unavailable.",
        "orchestration": "Orchestrator",
        "orchestration_detail": "Coordination, controls, supervision, pull requests, and reports",
        "dependency": "Dependency consolidation",
        "final_verification": "Final train verification",
        "github_feedback": "GitHub feedback reconciliation",
        "usage_reporting": "Usage reporting",
        "triage": "Train triage",
        "final_review": "Final train review",
        "final_remediation": "Final train remediation",
        "passes": "passes",
        "credit": "Token counters are not subscription-credit or billing counters.",
    },
    "fr": {
        "title": "Synthèse de la consommation de tokens",
        "scope": "Ticket / responsabilité",
        "analysis": "Analyse",
        "implementation": "Implémentation",
        "acceptance": "Acceptation",
        "remediation": "Remédiation",
        "review": "Revue",
        "total": "Total",
        "column_total": "Total",
        "included": "Inclus dans l’orchestrateur",
        "measured_total": "Total mesuré du train",
        "breakdown": "Entrée : {input} ; entrée en cache : {cached} ; sortie : {output} ; raisonnement en sortie : {reasoning}.",
        "missing": "`N/D` signifie que l’intervalle exact est indisponible ; `—` signifie non applicable.",
        "partial": "`*` signale une valeur partielle : les passes mesurées sont incluses, mais au moins un intervalle est indisponible.",
        "orchestration": "Orchestrateur",
        "orchestration_detail": "Coordination, contrôles, supervision, PR et rapports",
        "dependency": "Consolidation des dépendances",
        "final_verification": "Vérification finale du train",
        "github_feedback": "Réconciliation des retours GitHub",
        "usage_reporting": "Rapport de consommation",
        "triage": "Triage du train",
        "final_review": "Revue finale du train",
        "final_remediation": "Remédiation finale du train",
        "passes": "passes",
        "credit": "Les compteurs de tokens ne sont pas des compteurs de crédits d’abonnement ou de facturation.",
    },
}


def format_millions(total_tokens: int, *, language: str) -> str:
    if total_tokens == 0:
        return "0"
    value = total_tokens / 1_000_000
    rendered = f"{value:.2f}".replace(".", ",") if language == "fr" else f"{value:.2f}"
    return f"{rendered} M"


def compact_markdown_tokens(
    measurement: dict[str, Any], *, language: str, show_passes: bool = False
) -> str:
    status = measurement.get("status")
    if status == "not_applicable":
        return "—"
    if status == "included_in_orchestration":
        return REPORT_TEXT[language]["included"]
    usage = normalize_usage(measurement.get("usage"))
    if usage is None:
        return "N/D" if language == "fr" else "N/A"
    rendered = format_millions(usage["total_tokens"], language=language)
    if status == "partial":
        rendered += "*"
    pass_count = len(measurement.get("phase_keys", []))
    if show_passes and pass_count > 1:
        rendered += f" ({pass_count} {REPORT_TEXT[language]['passes']})"
    return rendered


def transverse_display_column(task_id: str) -> str | None:
    lowered = task_id.lower()
    if "triage" in lowered or "dependency-consolidation" in lowered:
        return "analysis"
    if "final-verification" in lowered:
        return "acceptance"
    if "final-remediation" in lowered:
        return "remediation"
    if "final-review" in lowered or "github-feedback" in lowered:
        return "review"
    return None


def transverse_display_label(task_id: str, *, language: str) -> str:
    text = REPORT_TEXT[language]
    if task_id == "run:orchestration":
        return text["orchestration"]
    if task_id == "run:dependency-consolidation":
        return text["dependency"]
    if task_id == "run:final-verification":
        return text["final_verification"]
    if task_id == "run:github-feedback":
        return text["github_feedback"]
    if task_id == "run:usage-reporting":
        return text["usage_reporting"]
    lowered = task_id.lower()
    if "triage" in lowered:
        return text["triage"]
    if "final-remediation" in lowered:
        return text["final_remediation"]
    if "final-review" in lowered:
        return text["final_review"]
    return task_id.removeprefix("phase:")


def transverse_display_order(task_id: str) -> tuple[int, str]:
    lowered = task_id.lower()
    if "triage" in lowered:
        return (10, task_id)
    if task_id == "run:dependency-consolidation":
        return (20, task_id)
    if task_id == "run:final-verification":
        return (30, task_id)
    if "final-remediation" in lowered:
        return (40, task_id)
    if "final-review" in lowered:
        return (50, task_id)
    if task_id == "run:github-feedback":
        return (60, task_id)
    if task_id == "run:orchestration":
        return (70, task_id)
    if task_id == "run:usage-reporting":
        return (80, task_id)
    return (65, task_id)


def render_usage_matrix_markdown(matrix: dict[str, Any], *, language: str = "en") -> str:
    if language not in REPORT_TEXT:
        raise ValueError(f"Unsupported report language: {language}")
    text = REPORT_TEXT[language]
    column_keys = [column for column, _sources in DISPLAY_PHASE_COLUMNS]
    headers = [text[column] for column in column_keys]
    lines = [
        f"## {text['title']}",
        "",
        f"| {text['scope']} | " + " | ".join(headers) + f" | {text['total']} |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    column_measurements: dict[str, list[dict[str, Any]]] = {
        column: [] for column in column_keys
    }

    for ticket_id, row in matrix["ticket_rows"].items():
        display_cells: dict[str, dict[str, Any]] = {}
        for column, sources in DISPLAY_PHASE_COLUMNS:
            display_cells[column] = combine_measurements([
                row["cells"][source]
                for source in sources
                if row["cells"][source].get("status") != "not_applicable"
            ])
            column_measurements[column].append(display_cells[column])
        rendered_cells = [
            compact_markdown_tokens(
                display_cells[column], language=language,
                show_passes=column in {"remediation", "review"},
            )
            for column in column_keys
        ]
        lines.append(
            f"| {ticket_id} | " + " | ".join(rendered_cells)
            + f" | **{compact_markdown_tokens(row['total'], language=language)}** |"
        )

    for task_id in sorted(matrix["transverse_rows"], key=transverse_display_order):
        row = matrix["transverse_rows"][task_id]
        display_column = transverse_display_column(task_id)
        cells = {column: not_applicable_measurement("not applicable") for column in column_keys}
        if display_column:
            cells[display_column] = row
            column_measurements[display_column].append(
                unavailable_measurement("included in orchestrator")
                if row.get("status") == "included_in_orchestration"
                else row
            )
        rendered_cells = [
            compact_markdown_tokens(
                cells[column], language=language,
                show_passes=column in {"remediation", "review"},
            )
            for column in column_keys
        ]
        if task_id == "run:orchestration":
            rendered_cells[0] = text["orchestration_detail"]
        lines.append(
            f"| {transverse_display_label(task_id, language=language)} | "
            + " | ".join(rendered_cells)
            + f" | **{compact_markdown_tokens(row, language=language)}** |"
        )

    aggregate = normalize_usage(matrix.get("aggregate_usage")) or empty_usage()
    column_totals = {
        column: combine_measurements(measurements)
        for column, measurements in column_measurements.items()
    }
    lines.append(
        f"| **{text['column_total']}** | "
        + " | ".join(
            f"**{compact_markdown_tokens(column_totals[column], language=language)}**"
            for column in column_keys
        )
        + f" | **{format_millions(aggregate['total_tokens'], language=language)}** |"
    )
    lines.extend([
        "",
        f"- {text['measured_total']}: **{format_millions(aggregate['total_tokens'], language=language)}**.",
        "- " + text["breakdown"].format(
            input=format_millions(aggregate["input_tokens"], language=language),
            cached=format_millions(aggregate["cached_input_tokens"], language=language),
            output=format_millions(aggregate["output_tokens"], language=language),
            reasoning=format_millions(aggregate["reasoning_output_tokens"], language=language),
        ),
        "- " + text["missing"],
        "- " + text["partial"],
        "- " + text["credit"],
        "",
    ])
    return "\n".join(lines)


def capture_command(args: argparse.Namespace) -> None:
    specs = list(args.thread or [])
    if args.current:
        current_id = os.environ.get("CODEX_THREAD_ID")
        if not current_id:
            raise ValueError("CODEX_THREAD_ID is unavailable")
        specs.append(f"{current_id}=orchestrator")
    if not specs:
        raise ValueError("Provide at least one --thread or --current")

    parsed: dict[str, str] = {}
    for spec in specs:
        thread_id, label = parse_thread_spec(spec)
        if thread_id in parsed:
            raise ValueError(f"Duplicate thread ID: {thread_id}")
        parsed[thread_id] = label

    codex_home = resolve_codex_home(args.codex_home)
    threads = {
        thread_id: capture_thread(codex_home, thread_id, label)
        for thread_id, label in parsed.items()
    }
    write_document(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "capture",
            "captured_at": now_iso(),
            "threads": threads,
        },
        args.output,
    )


def diff_command(args: argparse.Namespace) -> None:
    before = load_document(args.before)
    after = load_document(args.after)
    if before.get("kind") != "capture" or after.get("kind") != "capture":
        raise ValueError("diff inputs must be capture documents")

    before_threads = before.get("threads")
    after_threads = after.get("threads")
    if not isinstance(before_threads, dict) or not isinstance(after_threads, dict):
        raise ValueError("Capture document is missing threads")

    new_threads = set(args.new_thread or [])
    for thread_id in new_threads:
        if not THREAD_ID_PATTERN.fullmatch(thread_id):
            raise ValueError(f"Invalid new thread ID: {thread_id!r}")

    results: dict[str, Any] = {}
    available_usage: list[dict[str, int]] = []
    for thread_id in sorted(set(before_threads) | set(after_threads)):
        before_record = before_threads.get(thread_id)
        after_record = after_threads.get(thread_id)
        label = thread_id
        if isinstance(after_record, dict):
            label = str(after_record.get("label") or label)
        elif isinstance(before_record, dict):
            label = str(before_record.get("label") or label)

        if not isinstance(after_record, dict) or after_record.get("status") != "available":
            results[thread_id] = {
                "thread_id": thread_id,
                "label": label,
                "status": "unavailable",
                "reason": "ending-counter-unavailable",
            }
            continue

        after_usage = normalize_usage(after_record.get("usage"))
        if after_usage is None:
            results[thread_id] = {
                "thread_id": thread_id,
                "label": label,
                "status": "unavailable",
                "reason": "invalid-ending-counter",
            }
            continue

        baseline_mode = "captured"
        before_usage: dict[str, int] | None = None
        if isinstance(before_record, dict) and before_record.get("status") == "available":
            before_usage = normalize_usage(before_record.get("usage"))
        elif thread_id in new_threads:
            before_usage = empty_usage()
            baseline_mode = "zero-new-thread"

        if before_usage is None:
            results[thread_id] = {
                "thread_id": thread_id,
                "label": label,
                "status": "unavailable",
                "reason": "baseline-unavailable",
            }
            continue

        delta = {
            field: after_usage[field] - before_usage[field]
            for field in USAGE_FIELDS
        }
        if any(value < 0 for value in delta.values()):
            results[thread_id] = {
                "thread_id": thread_id,
                "label": label,
                "status": "unavailable",
                "reason": "counter-decreased",
            }
            continue

        results[thread_id] = {
            "thread_id": thread_id,
            "label": label,
            "status": "available",
            "baseline_mode": baseline_mode,
            "usage": delta,
        }
        available_usage.append(delta)

    unavailable_count = len(results) - len(available_usage)
    write_document(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "delta",
            "captured_at": now_iso(),
            "threads": results,
            "aggregate": {
                "status": aggregate_status(len(available_usage), unavailable_count),
                "usage": sum_usage(available_usage),
                "available_threads": len(available_usage),
                "unavailable_threads": unavailable_count,
            },
        },
        args.output,
    )


def sum_command(args: argparse.Namespace) -> None:
    measurements: list[dict[str, Any]] = []
    available_usage: list[dict[str, int]] = []
    unavailable = 0

    for input_path in args.input:
        document = load_document(input_path)
        if document.get("kind") != "delta":
            raise ValueError(f"sum input must be a delta document: {input_path}")
        threads = document.get("threads")
        if not isinstance(threads, dict):
            raise ValueError(f"Delta document is missing threads: {input_path}")
        for record in threads.values():
            if not isinstance(record, dict):
                continue
            measurement = {
                "source": input_path.name,
                "thread_id": record.get("thread_id"),
                "label": record.get("label"),
                "status": record.get("status"),
            }
            usage = normalize_usage(record.get("usage"))
            if record.get("status") == "available" and usage is not None:
                measurement["usage"] = usage
                available_usage.append(usage)
            else:
                measurement["reason"] = record.get("reason", "usage-unavailable")
                unavailable += 1
            measurements.append(measurement)

    write_document(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "aggregate",
            "captured_at": now_iso(),
            "measurements": measurements,
            "aggregate": {
                "status": aggregate_status(len(available_usage), unavailable),
                "usage": sum_usage(available_usage),
                "available_measurements": len(available_usage),
                "unavailable_measurements": unavailable,
            },
            "warning": "Only sum non-overlapping phase deltas.",
        },
        args.output,
    )


def ledger_command(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest)
    records = collect_manifest_session_records(manifest)

    for spec in args.thread or []:
        thread_id, label = parse_thread_spec(spec)
        records.append(
            {
                "thread_id": thread_id,
                "phase_key": None,
                "label": label,
                "ticket_id": None,
                "attempt": None,
                "launch_state": None,
                "authoritative": None,
                "duplicate_of": None,
                "manifest_path": "command-line",
            }
        )

    by_thread: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_thread.setdefault(str(record["thread_id"]), []).append(record)

    orchestrator_thread_ids = collect_orchestrator_thread_ids(manifest)
    for orchestrator_thread_id in orchestrator_thread_ids:
        for candidate in candidate_session_files(resolve_codex_home(args.codex_home), orchestrator_thread_id):
            if read_session_usage_bounds(candidate, orchestrator_thread_id) is None:
                continue
            for child_id, agent_path in read_started_subagents(candidate).items():
                by_thread.setdefault(child_id, []).append(
                    {
                        "thread_id": child_id,
                        "phase_key": None,
                        "label": f"hidden:{agent_path}",
                        "ticket_id": None,
                        "attempt": None,
                        "launch_state": None,
                        "authoritative": None,
                        "duplicate_of": None,
                        "manifest_path": "orchestrator-session:sub_agent_activity",
                        "discovered_hidden_session": True,
                    }
                )
            break

    phase_to_threads: dict[str, set[str]] = {}
    for thread_id, metadata in by_thread.items():
        for item in metadata:
            phase_key = item.get("phase_key")
            if phase_key:
                phase_to_threads.setdefault(str(phase_key), set()).add(thread_id)

    duplicate_phase_keys = {
        phase_key: sorted(thread_ids)
        for phase_key, thread_ids in phase_to_threads.items()
        if len(thread_ids) > 1
    }
    phase_attempt_families: dict[str, list[dict[str, Any]]] = {}
    for phase_key, thread_ids in phase_to_threads.items():
        match = PHASE_ATTEMPT_PATTERN.fullmatch(phase_key)
        family = match.group("family") if match else phase_key
        attempt = int(match.group("attempt")) if match else 1
        for thread_id in thread_ids:
            phase_attempt_families.setdefault(family, []).append(
                {"attempt": attempt, "phase_key": phase_key, "thread_id": thread_id}
            )

    codex_home = resolve_codex_home(args.codex_home)
    sessions: dict[str, dict[str, Any]] = {}
    available_usage: list[dict[str, int]] = []
    for thread_id, metadata in sorted(by_thread.items()):
        session = ledger_session(codex_home, thread_id, metadata)
        if any(
            phase_key in duplicate_phase_keys
            for phase_key in session.get("phase_keys", [])
        ):
            session["duplicate"] = True
            explicitly_authoritative = any(
                item.get("authoritative") is True for item in metadata
            )
            session["authoritative"] = explicitly_authoritative
        session["discovered_hidden_session"] = any(
            item.get("discovered_hidden_session") is True for item in metadata
        )
        session["ticket_ids"] = sorted(
            {str(item["ticket_id"]) for item in metadata if item.get("ticket_id")}
        )
        sessions[thread_id] = session
        usage = normalize_usage(session.get("delta_usage"))
        if session.get("status") == "available" and usage is not None:
            available_usage.append(usage)

    procedure = manifest.get("procedure") if isinstance(manifest.get("procedure"), dict) else {}
    phases_object = procedure.get("phases") if isinstance(procedure, dict) else {}
    phases = list(phases_object.values()) if isinstance(phases_object, dict) else []
    unmeasured_phases: list[dict[str, Any]] = []
    for phase in phases:
        if not isinstance(phase, dict):
            continue
        phase_key = phase.get("phase_key")
        thread_id = phase.get("thread_id")
        if not thread_id:
            unmeasured_phases.append(
                {"phase_key": phase_key, "reason": "thread-id-missing"}
            )
            continue
        session = sessions.get(str(thread_id))
        if not session or session.get("status") != "available":
            unmeasured_phases.append(
                {
                    "phase_key": phase_key,
                    "thread_id": thread_id,
                    "reason": "session-usage-unavailable",
                }
            )
        elif phase.get("usage_captured") is not True:
            unmeasured_phases.append(
                {
                    "phase_key": phase_key,
                    "thread_id": thread_id,
                    "reason": "phase-window-not-captured",
                }
            )

    mapped_phase_threads = {
        str(phase.get("thread_id")) for phase in phases if isinstance(phase, dict) and phase.get("thread_id")
    }
    unmapped_hidden_sessions = sorted(
        thread_id
        for thread_id, session in sessions.items()
        if session.get("discovered_hidden_session") is True and thread_id not in mapped_phase_threads
    )
    missing_orchestrator_sessions = sorted(
        thread_id
        for thread_id in orchestrator_thread_ids
        if sessions.get(thread_id, {}).get("status") != "available"
    )
    orchestrator_session_included = bool(orchestrator_thread_ids) and not missing_orchestrator_sessions

    phase_usage: dict[str, dict[str, int]] = {}
    ticket_usage: dict[str, list[dict[str, int]]] = {}
    orchestration_usage = empty_usage()
    for session in sessions.values():
        usage = normalize_usage(session.get("delta_usage"))
        if session.get("status") != "available" or usage is None:
            continue
        if session.get("thread_id") in orchestrator_thread_ids:
            orchestration_usage = sum_usage([orchestration_usage, usage])
        for phase_key in session.get("phase_keys", []):
            phase_usage[phase_key] = usage
        for ticket_id in session.get("ticket_ids", []):
            ticket_usage.setdefault(ticket_id, []).append(usage)

    unavailable_sessions = sum(
        1 for session in sessions.values() if session.get("status") != "available"
    )
    aggregate_usage = sum_usage(available_usage)
    usage_matrix = build_usage_matrix(
        manifest, sessions, orchestrator_thread_ids, aggregate_usage
    )
    ledger = {
            "schema_version": SCHEMA_VERSION,
            "kind": "ledger",
            "captured_at": now_iso(),
            "manifest": str(args.manifest.expanduser().resolve()),
            "sessions": sessions,
            "duplicate_phase_keys": duplicate_phase_keys,
            "phase_attempt_families": phase_attempt_families,
            "unmeasured_phases": unmeasured_phases,
            "unmapped_hidden_sessions": unmapped_hidden_sessions,
            "orchestrator_session_included": orchestrator_session_included,
            "orchestrator_session_ids": orchestrator_thread_ids,
            "missing_orchestrator_sessions": missing_orchestrator_sessions,
            "orchestrator_segments_known": len(orchestrator_thread_ids),
            "orchestrator_segments_measured": (
                len(orchestrator_thread_ids) - len(missing_orchestrator_sessions)
            ),
            "orchestrator_segments_complete": orchestrator_session_included,
            "phase_usage": phase_usage,
            "ticket_usage": {
                ticket_id: sum_usage(items) for ticket_id, items in sorted(ticket_usage.items())
            },
            "orchestration_usage": orchestration_usage,
            "usage_matrix": usage_matrix,
            "aggregate": {
                "status": aggregate_status(
                    len(available_usage),
                    unavailable_sessions
                    + len(unmeasured_phases)
                    + len(unmapped_hidden_sessions)
                    + (0 if orchestrator_session_included else 1),
                ),
                "usage": aggregate_usage,
                "available_sessions": len(available_usage),
                "unavailable_sessions": unavailable_sessions,
                "unmeasured_phases": len(unmeasured_phases),
                "unmapped_hidden_sessions": len(unmapped_hidden_sessions),
                "authoritative_phase_count": len(phases),
                "measured_phase_count": len(phases) - len(unmeasured_phases),
                "duplicate_sessions": sum(
                    1 for session in sessions.values() if session.get("duplicate")
                ),
                "diagnostics": {
                    field: sum(
                        int(session.get("diagnostics", {}).get(field, 0))
                        for session in sessions.values()
                    )
                    for field in (
                        "token_counter_events",
                        "assistant_messages",
                        "tool_calls",
                        "context_compactions",
                    )
                },
            },
            "warning": (
                "Session deltas use a zero session baseline. Reused-thread phase "
                "windows still require explicit capture/diff measurements. Token "
                "counts are not subscription-credit counters."
            ),
        }
    write_document(ledger, args.output)
    matrix_output = getattr(args, "matrix_output", None)
    if matrix_output is not None:
        matrix_path = matrix_output.expanduser().resolve()
        matrix_path.parent.mkdir(parents=True, exist_ok=True)
        matrix_path.write_text(
            render_usage_matrix_markdown(
                usage_matrix,
                language=getattr(args, "report_language", "en"),
            ),
            encoding="utf-8",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture and aggregate token usage for known Codex threads."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser("capture", help="Capture cumulative counters.")
    capture.add_argument(
        "--thread",
        action="append",
        help="Known thread ID, optionally THREAD_ID=LABEL. Repeat as needed.",
    )
    capture.add_argument(
        "--current",
        action="store_true",
        help="Capture the thread named by CODEX_THREAD_ID.",
    )
    capture.add_argument("--codex-home", type=Path)
    capture.add_argument("--output", type=Path)
    capture.set_defaults(handler=capture_command)

    diff = subparsers.add_parser("diff", help="Calculate usage between captures.")
    diff.add_argument("--before", type=Path, required=True)
    diff.add_argument("--after", type=Path, required=True)
    diff.add_argument(
        "--new-thread",
        action="append",
        help="Treat this known-new thread as having a zero baseline. Repeat as needed.",
    )
    diff.add_argument("--output", type=Path)
    diff.set_defaults(handler=diff_command)

    aggregate = subparsers.add_parser(
        "sum", help="Sum non-overlapping delta documents."
    )
    aggregate.add_argument("--input", type=Path, action="append", required=True)
    aggregate.add_argument("--output", type=Path)
    aggregate.set_defaults(handler=sum_command)

    ledger = subparsers.add_parser(
        "ledger", help="Reconcile manifest sessions, duplicates, and phase coverage."
    )
    ledger.add_argument("--manifest", type=Path, required=True)
    ledger.add_argument(
        "--thread",
        action="append",
        help="Additional known session THREAD_ID=LABEL, including duplicate attempts.",
    )
    ledger.add_argument("--codex-home", type=Path)
    ledger.add_argument("--output", type=Path)
    ledger.add_argument(
        "--matrix-output",
        type=Path,
        help="Write the mandatory ticket/phase and transverse-task Markdown matrices.",
    )
    ledger.add_argument(
        "--report-language",
        choices=sorted(REPORT_TEXT),
        default="en",
        help="Language for the user-facing Markdown matrix (default: en).",
    )
    ledger.set_defaults(handler=ledger_command)

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
