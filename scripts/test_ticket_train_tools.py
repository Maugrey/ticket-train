#!/usr/bin/env python3
"""Regression tests for ticket-train deterministic control utilities."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import control_guard
import token_usage
import train_supervisor


def valid_state() -> dict:
    return {
        "execution_mode": "dry-run",
        "integrated_ticket_count": 0,
        "control": {
            "manifest_updated_at": "2026-07-30T12:00:00+00:00",
            "manifest_reconciled": True,
            "terminal_reason": "COMPLETED",
            "next_automatic_action": "none",
            "launch_unknown_phase_keys": [],
            "pending_human_gates": [],
            "blocking_conditions": [],
            "requested_ticket_states": {"T-1": "ANALYSIS_REPORTED"},
            "phases": [],
            "proportionality_profile_revision": "profile-1",
            "train_size_budget": {
                "material_files": 0,
                "schema_or_data_transformations": 0,
                "structural_domains": [],
                "checkpoint_crossed": False,
            },
            "review_pass_budgets": {},
            "verification_gates": {},
            "duplicate_session_inventory": [],
            "unmeasured_phase_inventory": [],
            "cost_anomaly_status": "clear",
            "finalization": {
                "integrated_ticket_count": 0,
                "token_reporting_status": "complete",
                "session_usage_ledger_ready": True,
                "verification_summary_ready": True,
                "manual_validation_summary_ready": True,
                "attention_points_summary_ready": True,
                "task_inventory_ready": True,
                "completion_report_ready": True,
            },
        },
    }


class ControlGuardTests(unittest.TestCase):
    def test_valid_dry_run_completion(self) -> None:
        self.assertEqual(control_guard.validate_yield(valid_state()), [])

    def test_third_remediation_cycle_is_rejected(self) -> None:
        state = valid_state()
        state["control"]["review_pass_budgets"] = {
            "T-1:scope-1": {"complete_reviews": 1, "remediation_cycles": 3}
        }
        issues = control_guard.validate_yield(state)
        self.assertTrue(any("remediation cycle budget exceeded" in issue for issue in issues))

    def test_duplicate_session_requires_cost_checkpoint(self) -> None:
        state = valid_state()
        state["control"]["duplicate_session_inventory"] = ["thread-duplicate"]
        issues = control_guard.validate_yield(state)
        self.assertTrue(any("require an open or resolved cost checkpoint" in issue for issue in issues))

    def test_reused_implementation_remediation_thread_is_rejected(self) -> None:
        state = valid_state()
        state["control"]["phases"] = [
            {
                "phase_key": "run:T-1:implementation:1",
                "ticket_id": "T-1",
                "phase": "implementation",
                "launch_state": "COMPLETED",
                "thread_id": "thread-123456",
                "final_report_captured": True,
                "usage_captured": True,
            },
            {
                "phase_key": "run:T-1:remediation:1",
                "ticket_id": "T-1",
                "phase": "remediation",
                "launch_state": "COMPLETED",
                "thread_id": "thread-123456",
                "final_report_captured": True,
                "usage_captured": True,
            },
        ]
        issues = control_guard.validate_yield(state)
        self.assertTrue(any("fresh context" in issue for issue in issues))


class TokenUsageTests(unittest.TestCase):
    def test_phase_attempt_family_pattern(self) -> None:
        match = token_usage.PHASE_ATTEMPT_PATTERN.fullmatch("run:T-1:review:2")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group("family"), "run:T-1:review")
        self.assertEqual(match.group("attempt"), "2")


def valid_verification_gate() -> dict:
    return {
        "implementation_contract_revision": "implementation-1",
        "verification_contract_revision": "verification-1",
        "execution_pair_base": "base-sha",
        "implementation_thread_id": "thread-impl-123",
        "implementation_branch": "codex/T-1-implementation",
        "acceptance_test_thread_id": "thread-test-123",
        "acceptance_test_branch": "codex/T-1-acceptance-tests",
        "acceptance_test_commit": "test-sha",
        "independent_test_authorship": "complete",
        "implementation_disclosed_before_test_commit": False,
        "acceptance_coverage_status": "complete",
        "baseline_red_status": "demonstrated",
        "baseline_red_base": "base-sha",
        "acceptance_tests_integrated": True,
        "integrated_green_status": "passed",
        "integrated_green_head": "ticket-sha",
        "ticket_head": "ticket-sha",
        "environment_parity_status": "passed",
        "environment_fingerprint": "local-supabase:test-config-hash",
        "supabase_auth_applicable": True,
        "supabase_auth_verification_status": "passed",
        "privileged_credentials_setup_only": True,
        "automatable_manual_scenarios": [],
        "unresolved_validation_failures": [],
        "logs_captured": True,
    }


class VerificationGateTests(unittest.TestCase):
    def test_valid_supabase_verification_gate(self) -> None:
        self.assertEqual(
            control_guard.verification_gate_issues("T-1", valid_verification_gate()),
            [],
        )

    def test_privileged_supabase_boundary_bypass_is_rejected(self) -> None:
        gate = valid_verification_gate()
        gate["privileged_credentials_setup_only"] = False
        issues = control_guard.verification_gate_issues("T-1", gate)
        self.assertTrue(any("privileged credentials" in issue for issue in issues))

    def test_stale_green_head_is_rejected(self) -> None:
        gate = valid_verification_gate()
        gate["ticket_head"] = "new-ticket-sha"
        issues = control_guard.verification_gate_issues("T-1", gate)
        self.assertTrue(any("green head differs" in issue for issue in issues))

    def test_shared_implementation_and_test_thread_is_rejected(self) -> None:
        gate = valid_verification_gate()
        gate["acceptance_test_thread_id"] = gate["implementation_thread_id"]
        issues = control_guard.verification_gate_issues("T-1", gate)
        self.assertTrue(any("reused one thread" in issue for issue in issues))

    def test_supervisor_records_verification_event(self) -> None:
        state = valid_state()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(state), encoding="utf-8")
            args = argparse.Namespace(
                state=path,
                ticket="T-1",
                event_json=json.dumps(
                    {
                        "verification_contract_revision": "verification-1",
                        "baseline_red_status": "demonstrated",
                    }
                ),
            )
            with contextlib.redirect_stdout(io.StringIO()):
                train_supervisor.verification_event(args)
            updated = json.loads(path.read_text(encoding="utf-8"))
            gate = updated["control"]["verification_gates"]["T-1"]
            self.assertEqual(gate["verification_contract_revision"], "verification-1")
            self.assertEqual(gate["baseline_red_status"], "demonstrated")


if __name__ == "__main__":
    unittest.main()
