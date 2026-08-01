#!/usr/bin/env python3
"""Incident-oriented regression tests for the procedural controller."""

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

import run_registry
import train_controller


class Harness:
    def __init__(
        self,
        root: Path,
        tickets: str = "T-1",
        approval_mode: str = "full-auto",
        execution_mode: str = "live",
    ) -> None:
        self.root = root
        init = argparse.Namespace(
            root=root / "runs",
            legacy_root=root / "legacy",
            repository="owner/repo",
            train_branch="codex/train-test",
            source="issues:1",
            tickets=tickets,
            orchestrator_thread="thread-main",
            execution_mode=execution_mode,
            run_id="run-test",
            adopt_legacy=False,
            lease_minutes=30,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assert_code(run_registry.init_run(init), 0)
        self.path = root / "runs" / "run-test" / "manifest.json"
        bootstrap = argparse.Namespace(
            state=self.path, base_branch="main", approval_mode=approval_mode
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assert_code(train_controller.bootstrap(bootstrap), 0)
        self.event_number = 0

    @staticmethod
    def assert_code(actual: int, expected: int) -> None:
        if actual != expected:
            raise AssertionError(f"expected exit code {expected}, got {actual}")

    def state(self) -> dict:
        return run_registry.load_json(self.path)

    def apply(self, event_type: str, **fields: object) -> int:
        self.event_number += 1
        event = {"event_id": f"event-{self.event_number}", "type": event_type, **fields}
        revision = self.state()["procedure"]["revision"]
        args = argparse.Namespace(
            state=self.path,
            expected_revision=revision,
            event_json=json.dumps(event),
            event=None,
        )
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                return train_controller.apply_event(args)
            except (train_controller.ControllerError, ValueError):
                return 2

    def confirm(self) -> None:
        self.assert_code(self.apply("ORCHESTRATOR_CONFIRMED"), 0)
        self.assert_code(
            self.apply(
                "SUPERVISION_CONFIGURED",
                mode="FOREGROUND_WAIT",
                last_check_at="2026-08-01T10:00:00+00:00",
                next_check_at="2026-08-01T10:05:00+00:00",
            ),
            0,
        )
    def analyze(self, criticality: str = "LOW", complexity: str = "LOW") -> None:
        analysis_model, analysis_effort = train_controller.setting_from_matrix(
            train_controller.ANALYSIS_MATRIX, criticality, complexity
        )
        self.assert_code(
            self.apply(
                "PHASE_DISPATCHED",
                kind="triage",
                phase_key="run:run:triage:1",
                base_commit="base-sha",
                model="gpt-5.6-terra",
                reasoning_effort="high",
            ),
            0,
        )
        self.materialize("run:run:triage:1", "thread-triage")
        self.complete_phase("run:run:triage:1", "gpt-5.6-terra", "high")
        self.assert_code(
            self.apply(
                "TICKET_TRIAGED",
                ticket_id="T-1",
                phase_key="run:run:triage:1",
                criticality=criticality,
                complexity=complexity,
                triage_model="gpt-5.6-terra",
                triage_reasoning_effort="high",
                analysis_model=analysis_model,
                analysis_reasoning_effort=analysis_effort,
                analysis_routing_conformance="conformant",
                reasoning_authorized=True,
            ),
            0,
        )
        self.assert_code(
            self.apply(
                "PHASE_DISPATCHED",
                kind="analysis",
                ticket_id="T-1",
                phase_key="run:T-1:analysis:1",
                base_commit="base-sha",
                model=analysis_model,
                reasoning_effort=analysis_effort,
                routing_conformance="conformant",
                reasoning_authorized=True,
            ),
            0,
        )
        self.materialize("run:T-1:analysis:1", "thread-analysis")
        self.complete_phase("run:T-1:analysis:1", analysis_model, analysis_effort)
        self.assert_code(
            self.apply(
                "ANALYSIS_RECORDED",
                ticket_id="T-1",
                phase_key="run:T-1:analysis:1",
                analysis_revision="analysis-1",
                analysis_base_commit="base-sha",
                source_revision="source-1",
                profile_revision="profile-1",
                criticality=criticality,
                complexity=complexity,
                criticality_evidence="bounded reversible failure",
                complexity_evidence="one local module",
                structural_digest="all six domains assessed",
                implementation_contract_revision="implementation-1",
                verification_contract_revision="verification-1",
                report_thread_id="thread-analysis",
                model=analysis_model,
                reasoning_effort=analysis_effort,
                routing_conformance="conformant",
                reasoning_authorized=True,
            ),
            0,
        )
        self.assert_code(
            self.apply(
                "DEPENDENCIES_CONSOLIDATED",
                dependency_revision="dependencies-1",
                graph={"T-1": {"hard_dependencies": [], "collision_domains": ["module-a"]}},
            ),
            0,
        )

    def dispatch_pair(self) -> None:
        self.assert_code(
            self.apply(
                "EXECUTION_PAIR_DISPATCHED",
                ticket_id="T-1",
                base_commit="base-sha",
                implementation_phase_key="run:T-1:implementation:1",
                acceptance_phase_key="run:T-1:acceptance:1",
                implementation_branch="codex/t-1",
                acceptance_branch="codex/t-1-tests",
                verification_complexity="LOW",
                implementation_model="gpt-5.6-terra",
                implementation_reasoning_effort="medium",
                implementation_routing_conformance="conformant",
                acceptance_model="gpt-5.6-terra",
                acceptance_reasoning_effort="high",
                acceptance_routing_conformance="conformant",
                reasoning_authorized=True,
            ),
            0,
        )

    def materialize(self, phase_key: str, thread_id: str) -> None:
        self.assert_code(
            self.apply(
                "PHASE_LAUNCH_OBSERVED",
                phase_key=phase_key,
                launch_state="RUNNING",
                thread_id=thread_id,
                host_id=f"host-{thread_id}",
                visibility_verified=True,
                visibility_verified_at="2026-08-01T10:00:00+00:00",
            ),
            0,
        )

    def complete_phase(self, phase_key: str, model: str, effort: str) -> None:
        self.assert_code(
            self.apply(
                "PHASE_COMPLETED",
                phase_key=phase_key,
                envelope={
                    "phase_key": phase_key,
                    "phase_status": "completed",
                    "actual_model": model,
                    "actual_reasoning_effort": effort,
                    "result_summary": "done",
                    "artifacts": {"commit": "ticket-sha"},
                    "tests_and_checks": ["targeted checks passed"],
                    "residual_risks": "none",
                    "requested_or_recommended_next_action": "continue",
                    "files_modified": "explicit list in report",
                    "usage": {"measurement": "complete", "total_tokens": 100},
                },
            ),
            0,
        )

    def functional_ready(self) -> None:
        self.dispatch_pair()
        self.materialize("run:T-1:implementation:1", "thread-impl")
        self.materialize("run:T-1:acceptance:1", "thread-tests")
        self.complete_phase("run:T-1:implementation:1", "gpt-5.6-terra", "medium")
        self.complete_phase("run:T-1:acceptance:1", "gpt-5.6-terra", "high")
        self.assert_code(
            self.apply(
                "VERIFICATION_RECORDED",
                ticket_id="T-1",
                status="passed",
                ticket_head="ticket-sha",
                baseline_red_base="base-sha",
                integrated_green_head="ticket-sha",
                environment_status="not-applicable",
                acceptance_coverage_status="complete",
                independent_test_commit="tests-sha",
                logs_reference="logs/verification.json",
                supabase_auth_applicable=False,
                automatable_manual_scenarios=[],
            ),
            0,
        )

    def record_pr(self, base_branch: str = "codex/train-test") -> int:
        return self.apply(
            "TICKET_PR_RECORDED",
            ticket_id="T-1",
            url="https://example.invalid/pr/1",
            base_branch=base_branch,
            head_branch="codex/t-1",
            head_commit="ticket-sha",
        )

    def clean_review(self) -> None:
        self.assert_code(
            self.apply(
                "REVIEW_DISPATCHED",
                ticket_id="T-1",
                scope="initial",
                review_kind="full",
                phase_key="run:T-1:review:1",
                base_commit="base-sha",
                head_commit="ticket-sha",
                model="gpt-5.6-terra",
                reasoning_effort="high",
                routing_conformance="conformant",
                scope_revision="scope-1",
                reasoning_authorized=True,
            ),
            0,
        )
        self.materialize("run:T-1:review:1", "thread-review")
        self.assert_code(
            self.apply(
                "REVIEW_RECORDED",
                phase_key="run:T-1:review:1",
                reviewed_head="ticket-sha",
                status="clean",
                finding_inventory_complete=True,
                findings=[],
                envelope={
                    "phase_key": "run:T-1:review:1",
                    "phase_status": "completed",
                    "actual_model": "gpt-5.6-terra",
                    "actual_reasoning_effort": "high",
                    "result_summary": "clean",
                    "artifacts": {"reviewed_head": "ticket-sha"},
                    "tests_and_checks": ["evidence checked"],
                    "residual_risks": "none",
                    "requested_or_recommended_next_action": "merge",
                    "files_modified": "none",
                    "usage": {"measurement": "complete", "total_tokens": 100},
                },
            ),
            0,
        )
        self.assert_code(
            self.apply(
                "TICKET_FINDINGS_RECONCILED",
                ticket_id="T-1",
                head_commit="ticket-sha",
                ledger_status="complete",
                sources_dispositioned=["codex", "ci", "copilot"],
                blocking_findings=[],
            ),
            0,
        )


class TrainControllerTests(unittest.TestCase):
    def harness(self, directory: str) -> Harness:
        return Harness(Path(directory))

    def test_execution_dispatch_is_an_atomic_implementation_test_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            run.analyze()
            run.dispatch_pair()
            phases = run.state()["procedure"]["phases"]
            pair = {key for key, value in phases.items() if value["kind"] in {"implementation", "acceptance_tests"}}
            self.assertEqual(pair, {"run:T-1:implementation:1", "run:T-1:acceptance:1"})
            self.assertEqual(phases["run:T-1:implementation:1"]["base"], phases["run:T-1:acceptance:1"]["base"])

    def test_silence_does_not_change_running_phase_to_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            run.analyze()
            run.dispatch_pair()
            run.materialize("run:T-1:implementation:1", "thread-impl")
            run.materialize("run:T-1:acceptance:1", "thread-tests")
            before = run.state()["procedure"]["revision"]
            actions = train_controller.next_actions(run.state())
            after = run.state()["procedure"]["revision"]
            self.assertEqual(actions[0]["action"], "WAIT_FOR_PHASE_TRANSITION")
            self.assertEqual(before, after)
            self.assertEqual(
                run.state()["procedure"]["phases"]["run:T-1:implementation:1"]["launch_state"],
                "RUNNING",
            )

    def test_review_before_functional_readiness_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            run.analyze()
            code = run.apply(
                "REVIEW_DISPATCHED",
                ticket_id="T-1",
                scope="initial",
                review_kind="full",
                phase_key="run:T-1:review:1",
                base_commit="base-sha",
                head_commit="ticket-sha",
                model="gpt-5.6-terra",
                reasoning_effort="high",
                routing_conformance="conformant",
            )
            self.assertEqual(code, 2)

    def test_ticket_pr_targeting_base_instead_of_train_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            run.analyze()
            run.functional_ready()
            self.assertEqual(run.record_pr("main"), 2)
            self.assertIsNone(run.state()["procedure"]["tickets"]["T-1"].get("pull_request"))

    def test_followup_review_without_full_baseline_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            run.analyze()
            run.functional_ready()
            self.assertEqual(run.record_pr(), 0)
            run.state()
            code = run.apply(
                "REVIEW_DISPATCHED",
                ticket_id="T-1",
                scope="followup",
                review_kind="focused",
                phase_key="run:T-1:review:2",
                base_commit="base-sha",
                head_commit="ticket-sha",
                model="gpt-5.6-terra",
                reasoning_effort="high",
                routing_conformance="conformant",
            )
            self.assertEqual(code, 2)

    def test_human_gate_cannot_be_resolved_before_announcement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Harness(Path(directory), approval_mode="standard")
            run.confirm()
            run.analyze("CRITICAL", "LOW")
            gate_id = run.state()["procedure"]["tickets"]["T-1"]["analysis_gate_id"]
            self.assertEqual(
                run.apply(
                    "GATE_RESOLVED",
                    gate_id=gate_id,
                    revision="analysis-1",
                    decision="approved",
                ),
                2,
            )

    def test_same_event_id_is_idempotent_even_with_old_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            event = {"event_id": "stable-event", "type": "ORCHESTRATOR_CONFIRMED"}
            args = argparse.Namespace(
                state=run.path, expected_revision=0, event_json=json.dumps(event), event=None
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(train_controller.apply_event(args), 0)
                self.assertEqual(train_controller.apply_event(args), 0)
            self.assertEqual(run.state()["procedure"]["revision"], 1)

    def test_completion_is_rejected_without_final_pr_review_and_usage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            issues = train_controller.completion_issues(run.state())
            self.assertIn("finalization evidence is incomplete", issues)
            self.assertIn("final train PR is missing", issues)

    def test_dry_run_stops_after_analysis_report_and_usage_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Harness(Path(directory), execution_mode="dry-run")
            run.confirm()
            run.analyze()
            self.assertEqual(
                train_controller.next_actions(run.state())[0]["action"],
                "RECORD_DRY_RUN_REPORT_AND_USAGE_EVIDENCE",
            )
            self.assertEqual(
                run.apply(
                    "DRY_RUN_EVIDENCE_RECORDED",
                    token_reporting_status="complete",
                    session_usage_ledger_ready=True,
                    analysis_reports_ready=True,
                    task_inventory_ready=True,
                    completion_report_ready=True,
                ),
                0,
            )
            self.assertEqual(run.apply("RUN_COMPLETED"), 0)
            self.assertEqual(
                run.state()["procedure"]["tickets"]["T-1"]["status"],
                "ANALYSIS_REPORTED",
            )

    def test_happy_path_requires_exact_final_head_and_report_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            run.analyze()
            run.functional_ready()
            self.assertEqual(run.record_pr(), 0)
            run.clean_review()
            self.assertEqual(
                run.apply("TICKET_MERGED", ticket_id="T-1", merge_commit="merge-sha", train_head="train-sha"),
                0,
            )
            self.assertEqual(run.apply("FINALIZATION_STARTED"), 0)
            self.assertEqual(
                run.apply(
                    "FINAL_PR_RECORDED",
                    url="https://example.invalid/pr/final",
                    base_branch="main",
                    head_branch="codex/train-test",
                    head_commit="train-sha",
                ),
                0,
            )
            self.assertEqual(
                run.apply(
                    "FINAL_VERIFICATION_RECORDED",
                    status="passed",
                    head_commit="train-sha",
                    evidence_reference="logs/final.json",
                ),
                0,
            )
            self.assertEqual(
                run.apply(
                    "FINAL_REVIEW_DISPATCHED",
                    phase_key="run:run:final-review:1",
                    scope="initial",
                    review_kind="full",
                    train_criticality="LOW",
                    train_complexity="LOW",
                    model="gpt-5.6-terra",
                    reasoning_effort="high",
                    routing_conformance="conformant",
                    reasoning_authorized=True,
                ),
                0,
            )
            run.materialize("run:run:final-review:1", "thread-final-review")
            self.assertEqual(
                run.apply(
                    "FINAL_REVIEW_RECORDED",
                    phase_key="run:run:final-review:1",
                    review_kind="full",
                    status="clean",
                    finding_inventory_complete=True,
                    reviewed_head="train-sha",
                    routing_conformance="conformant",
                    model="gpt-5.6-terra",
                    reasoning_effort="high",
                    envelope={
                        "phase_key": "run:run:final-review:1",
                        "phase_status": "completed",
                        "actual_model": "gpt-5.6-terra",
                        "actual_reasoning_effort": "high",
                        "result_summary": "clean final review",
                        "artifacts": {"reviewed_head": "train-sha"},
                        "tests_and_checks": ["exact-head evidence checked"],
                        "residual_risks": "none",
                        "requested_or_recommended_next_action": "complete",
                        "files_modified": "none",
                        "usage": {"measurement": "complete", "total_tokens": 100},
                    },
                ),
                0,
            )
            self.assertEqual(
                run.apply(
                    "FINAL_FINDINGS_RECONCILED",
                    head_commit="train-sha",
                    ledger_status="complete",
                    sources_dispositioned=["codex", "ci", "copilot"],
                    blocking_findings=[],
                ),
                0,
            )
            self.assertEqual(
                run.apply(
                    "FINAL_EVIDENCE_RECORDED",
                    ci_status="passed",
                    copilot_status="not_configured",
                    finding_ledger_status="complete",
                    token_reporting_status="complete",
                    session_usage_ledger_ready=True,
                    verification_summary_ready=True,
                    manual_validation_summary_ready=True,
                    attention_points_summary_ready=True,
                    task_inventory_ready=True,
                    completion_report_ready=True,
                ),
                0,
            )
            self.assertEqual(run.apply("RUN_COMPLETED"), 0)
            self.assertEqual(run.state()["procedure"]["run_status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
