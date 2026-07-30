#!/usr/bin/env python3
"""Regression tests for ticket-train deterministic control utilities."""

from __future__ import annotations

import unittest

import control_guard
import token_usage


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
            "duplicate_session_inventory": [],
            "unmeasured_phase_inventory": [],
            "cost_anomaly_status": "clear",
            "finalization": {
                "integrated_ticket_count": 0,
                "token_reporting_status": "complete",
                "session_usage_ledger_ready": True,
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


if __name__ == "__main__":
    unittest.main()
