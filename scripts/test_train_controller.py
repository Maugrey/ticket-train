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
import control_plane_runner
import merge_pull_request


class Harness:
    def __init__(
        self,
        root: Path,
        tickets: str = "T-1",
        approval_mode: str = "full-auto",
        execution_mode: str = "live",
        environment_profile: str = "generic",
        max_unity_editors: int = 3,
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
            state=self.path,
            base_branch="main",
            approval_mode=approval_mode,
            environment_profile=environment_profile,
            max_unity_editors=max_unity_editors,
            unity_repository=(root / "unity-project") if environment_profile == "unity-mcp-local" else None,
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
        self.assert_code(self.apply("ORCHESTRATOR_CONFIRMED", **self.orchestrator_confirmation_fields()), 0)
        self.assert_code(
            self.apply(
                "SUPERVISION_CONFIGURED",
                mode="FOREGROUND_WAIT",
                last_check_at="2026-08-01T10:00:00+00:00",
                next_check_at="2026-08-01T10:05:00+00:00",
            ),
            0,
        )

    @staticmethod
    def orchestrator_confirmation_fields() -> dict[str, object]:
        return {
            "orchestration_profile": "normal",
            "recommended_model": "gpt-5.6-terra",
            "recommended_reasoning_effort": "medium",
            "current_model": "gpt-5.6-terra",
            "current_reasoning_effort": "medium",
            "confirmation_reference": "thread-main:confirmation-1",
        }

    @staticmethod
    def context_packet(base: str = "base-sha", head: str = "ticket-sha") -> dict[str, object]:
        return {
            "format": "fresh-compact-v1",
            "reference": "artifacts/context.json",
            "sha256": "a" * 64,
            "byte_count": 1024,
            "history_turns_included": 0,
            "profile_revision": "profile-1",
            "exact_base": base,
            "exact_head": head,
        }

    @staticmethod
    def deterministic_verification_fields() -> dict[str, object]:
        return {
            "execution_mode": "deterministic",
            "model_tokens": 0,
            "operational_change_applicable": False,
            "runner_version": "1",
            "runner_result_reference": "logs/verification-result.json",
            "runner_result_sha256": "b" * 64,
            "command_results": [{
                "command_id": "tests",
                "status": "passed",
                "exit_code": 0,
                "duration_seconds": 1.0,
                "log_reference": "logs/tests.log",
            }],
        }

    @staticmethod
    def scope_assessment(
        proposals: list[dict[str, object]] | None = None,
        specification_deviations: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        proposals = proposals or []
        specification_deviations = specification_deviations or []
        items: list[dict[str, object]] = [{
            "item_id": "source-criterion-1",
            "description": "Implement the explicit ticket acceptance criterion.",
            "scope_origin": "source-explicit",
        }]
        for proposal in proposals:
            items.append({
                "item_id": f"proposed-{proposal['proposal_id']}",
                "description": proposal["description"],
                "scope_origin": "scope-expansion-proposed",
                "proposal_id": proposal["proposal_id"],
            })
        for deviation in specification_deviations:
            items.append({
                "item_id": deviation["item_id"],
                "description": deviation["unresolved_point"],
                "scope_origin": "specification-deviation-proposed",
                "deviation_id": deviation["deviation_id"],
            })
        return {
            "assessment_revision": "scope-1",
            "product_lifecycle_stage": "pre-MVP",
            "existing_state_compatibility_posture": "disposable",
            "compatibility_source_evidence": "profile-1: pre-MVP saves are disposable",
            "classification_basis": "authorized-scope-only",
            "classification_scope_item_ids": ["source-criterion-1"],
            "specification_alignment": "decision-required" if specification_deviations else "exact",
            "specification_deviations": specification_deviations,
            "items": items,
            "proposals": proposals,
        }

    @staticmethod
    def scope_expansion_proposal() -> dict[str, object]:
        return {
            "proposal_id": "legacy-save-migration",
            "category": "migration",
            "description": "Migrate unpublished pre-MVP local saves.",
            "source_gap": "The ticket asks for asset migration, not save compatibility.",
            "minimal_variant": "Allow pre-MVP saves to reset.",
            "expanded_variant": "Add a versioned save migration and compatibility tests.",
            "impact": {
                "scope": "save schema and loader",
                "cost": "additional implementation and review",
                "latency": "longer delivery",
                "risk": "migration defects",
                "tests": "legacy fixtures and upgrade paths",
                "classification_if_approved": {"criticality": "NORMAL", "complexity": "MEDIUM"},
            },
            "recommendation": "Use the minimal MVP variant.",
        }

    @staticmethod
    def specification_deviation() -> dict[str, object]:
        return {
            "deviation_id": "save-reset-semantics",
            "item_id": "spec-save-reset-semantics",
            "kind": "compatibility-semantics",
            "spec_reference": "The ticket does not define existing-save behavior.",
            "unresolved_point": "Whether incompatible pre-MVP saves reset or are preserved.",
            "why_resolution_required": "The loader and acceptance oracle differ between the options.",
            "options": [
                {
                    "option_id": "reset",
                    "description": "Treat pre-MVP saves as disposable and reset them.",
                    "impact": {
                        "scope": "no migration",
                        "cost": "low",
                        "latency": "none",
                        "risk": "pre-MVP progress loss",
                        "tests": "reset behavior",
                        "classification_if_selected": {"criticality": "LOW", "complexity": "LOW"},
                    },
                },
                {
                    "option_id": "preserve",
                    "description": "Preserve old saves through an explicit migration.",
                    "impact": {
                        "scope": "save migration",
                        "cost": "higher",
                        "latency": "longer",
                        "risk": "migration defects",
                        "tests": "legacy fixtures and upgrade paths",
                        "classification_if_selected": {"criticality": "NORMAL", "complexity": "MEDIUM"},
                    },
                },
            ],
            "recommended_option_id": "reset",
        }

    def analyze(
        self,
        criticality: str = "LOW",
        complexity: str = "LOW",
        scope_proposals: list[dict[str, object]] | None = None,
        specification_deviations: list[dict[str, object]] | None = None,
    ) -> None:
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
                reasoning_effort="medium",
                routing_conformance="conformant",
                triage_profile="standard",
                context_packet=self.context_packet("base-sha", "base-sha"),
            ),
            0,
        )
        self.materialize("run:run:triage:1", "thread-triage")
        self.complete_phase("run:run:triage:1", "gpt-5.6-terra", "medium")
        self.assert_code(
            self.apply(
                "TICKET_TRIAGED",
                ticket_id="T-1",
                phase_key="run:run:triage:1",
                criticality=criticality,
                complexity=complexity,
                confidence="high",
                triage_model="gpt-5.6-terra",
                triage_reasoning_effort="medium",
                analysis_model=analysis_model,
                analysis_reasoning_effort=analysis_effort,
                analysis_routing_conformance="conformant",
                analysis_unity_requirement="none",
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
                unity_requirement="none",
                context_packet=self.context_packet("base-sha", "base-sha"),
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
                residual_implementation_complexity=complexity,
                verification_complexity=complexity,
                complexity_reduction_evidence="no reduction claimed",
                unresolved_implementation_difficulty=[],
                scope_assessment=self.scope_assessment(scope_proposals, specification_deviations),
                report_thread_id="thread-analysis",
                model=analysis_model,
                reasoning_effort=analysis_effort,
                routing_conformance="conformant",
                reasoning_authorized=True,
            ),
            0,
        )
        if scope_proposals or specification_deviations:
            return
        self.assert_code(
            self.apply(
                "DEPENDENCIES_CONSOLIDATED",
                dependency_revision="dependencies-1",
                graph={"T-1": {
                    "hard_dependencies": [],
                    "collision_domains": ["module-a"],
                    "planned_material_files": ["src/module-a.ts"],
                    "structural_domains": ["module-a"],
                    "schema_or_data_transformations": [],
                }},
                schedule={"T-1": {"mode": "sequential", "reason": "single ticket"}},
                train_size_budget={
                    "material_file_count": 1,
                    "schema_or_data_transformation_count": 0,
                    "structural_domain_count": 1,
                    "checkpoint": "clear",
                },
            ),
            0,
        )

    def dispatch_pair(
        self,
        implementation_unity_requirement: str = "none",
        acceptance_unity_requirement: str = "none",
        verification_unity_requirement: str = "none",
    ) -> None:
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
                acceptance_reasoning_effort="medium",
                acceptance_routing_conformance="conformant",
                scope_assessment_revision="scope-1",
                scope_conformance="within-authorized-scope",
                implementation_unity_requirement=implementation_unity_requirement,
                acceptance_unity_requirement=acceptance_unity_requirement,
                verification_unity_requirement=verification_unity_requirement,
                reasoning_authorized=True,
                implementation_context_packet=self.context_packet("base-sha", "base-sha"),
                acceptance_context_packet=self.context_packet("base-sha", "base-sha"),
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
                execution_visibility="user-visible",
                visibility_evidence_reference=f"tasks/{thread_id}.json",
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
        self.complete_phase("run:T-1:acceptance:1", "gpt-5.6-terra", "medium")
        self.assert_code(
            self.apply(
                "EXECUTION_PAIR_INTEGRATED",
                ticket_id="T-1",
                implementation_branch="codex/t-1",
                implementation_commit="ticket-sha",
                acceptance_commit="ticket-sha",
                combined_head="ticket-sha",
                integration_evidence_reference="logs/execution-pair-integration.json",
            ),
            0,
        )
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
                **self.deterministic_verification_fields(),
            ),
            0,
        )

    def record_pr(self, base_branch: str = "codex/train-test") -> int:
        return self.apply(
            "TICKET_PR_RECORDED",
            ticket_id="T-1",
            url="https://example.invalid/pr/1",
            base_branch=base_branch,
            base_commit="base-sha",
            head_branch="codex/t-1",
            head_commit="ticket-sha",
            is_draft=False,
        )

    def clean_review(self, reconcile: bool = True) -> None:
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
                reasoning_effort="medium",
                routing_conformance="conformant",
                criticality="LOW",
                complexity="LOW",
                classification_evidence="bounded diff with direct oracle",
                scope_revision="scope-1",
                reasoning_authorized=True,
                context_packet=self.context_packet("base-sha", "ticket-sha"),
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
                    "actual_reasoning_effort": "medium",
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
        if not reconcile:
            return
        self.assert_code(
            self.apply(
                "TICKET_FINDINGS_RECONCILED",
                ticket_id="T-1",
                head_commit="ticket-sha",
                ledger_status="complete",
                sources_dispositioned=["codex", "ci", "copilot"],
                feedback_collection_started_at="2026-08-01T10:00:00+00:00",
                feedback_collection_deadline_at="2026-08-01T10:10:00+00:00",
                feedback_collected_at="2026-08-01T10:02:00+00:00",
                ci_status="passed",
                copilot_status="received",
                source_counts={"codex": 1, "ci": 1, "copilot": 1},
                source_findings=[
                    {"finding_id": "codex-1", "source": "codex"},
                    {"finding_id": "ci-1", "source": "ci"},
                    {"finding_id": "copilot-1", "source": "copilot"},
                ],
                finding_dispositions=[
                    {
                        "finding_id": finding_id,
                        "disposition": "rejected-incorrect",
                        "blocking": False,
                        "remediation_status": "not-required",
                        "verification": "reviewed",
                    }
                    for finding_id in ("codex-1", "ci-1", "copilot-1")
                ],
                feedback_evidence_reference="artifacts/ticket-feedback.json",
                blocking_findings=[],
            ),
            0,
        )


class TrainControllerTests(unittest.TestCase):
    def test_scope_assessment_requires_explicit_specification_alignment_inventory(self) -> None:
        assessment = Harness.scope_assessment()
        assessment.pop("specification_deviations")
        with self.assertRaises(train_controller.ControllerError):
            train_controller.validate_scope_assessment({"scope_assessment": assessment})

    def test_usage_matrix_evidence_rejects_a_missing_transverse_task(self) -> None:
        proc = {
            "tickets": {"T-1": {}},
            "phases": {
                "run:run:triage:1": {
                    "phase_key": "run:run:triage:1",
                    "ticket_id": None,
                },
            },
            "event_log": [{"type": "DEPENDENCIES_CONSOLIDATED"}],
        }
        event = {
            "usage_matrix_ready": True,
            "usage_matrix_reference": "reports/token-matrix.md",
            "usage_matrix_sha256": "f" * 64,
            "usage_matrix_status": "partial",
            "usage_matrix_ticket_ids": ["T-1"],
            "usage_matrix_ticket_phase_columns": list(train_controller.USAGE_TICKET_PHASE_COLUMNS),
            "usage_matrix_transverse_task_ids": [
                "phase:run:run:triage:1",
                "run:orchestration",
                "run:usage-reporting",
            ],
            "usage_matrix_unreported_cell_count": 0,
        }
        with self.assertRaisesRegex(train_controller.ControllerError, "transverse task rows"):
            train_controller.validate_usage_matrix_evidence(
                proc, event, label="test matrix"
            )

    def test_active_routing_policy_snapshot(self) -> None:
        self.assertEqual(train_controller.ROUTING_POLICY_VERSION, "2026-08-27-v2")
        self.assertEqual(train_controller.ANALYSIS_MATRIX, {
            "LOW": ("Terra/M", "Terra/H", "Sol/H", "Sol/XH"),
            "NORMAL": ("Terra/H", "Sol/M", "Sol/H", "Sol/XH"),
            "HIGH": ("Sol/H", "Sol/H", "Sol/XH", "Sol/XH"),
            "CRITICAL": ("Sol/XH", "Sol/XH", "Sol/XH", "Sol/Max"),
        })
        self.assertEqual(train_controller.IMPLEMENTATION_MATRIX, {
            "LOW": ("Terra/M", "Terra/M", "Sol/H", "Sol/XH"),
            "NORMAL": ("Terra/M", "Terra/H", "Sol/H", "Sol/XH"),
            "HIGH": ("Sol/M", "Sol/H", "Sol/XH", "Sol/XH"),
            "CRITICAL": ("Sol/H", "Sol/XH", "Sol/XH", "Sol/Max"),
        })
        self.assertEqual(train_controller.ACCEPTANCE_MATRIX, {
            "LOW": ("Terra/M", "Terra/H", "Sol/H", "Sol/XH"),
            "NORMAL": ("Terra/H", "Terra/H", "Sol/H", "Sol/XH"),
            "HIGH": ("Sol/H", "Sol/H", "Sol/XH", "Sol/XH"),
            "CRITICAL": ("Sol/H", "Sol/XH", "Sol/XH", "Sol/Max"),
        })
        self.assertEqual(train_controller.INITIAL_REVIEW_MATRIX, {
            "LOW": ("Terra/M", "Terra/H", "Sol/H", "Sol/XH"),
            "NORMAL": ("Terra/H", "Sol/H", "Sol/H", "Sol/XH"),
            "HIGH": ("Sol/H", "Sol/XH", "Sol/XH", "Sol/XH"),
            "CRITICAL": ("Sol/XH", "Sol/XH", "Sol/XH", "Sol/Max"),
        })
        self.assertEqual(train_controller.FOLLOWUP_REVIEW_MATRIX, {
            "LOW": ("Terra/M", "Terra/H", "Sol/H", "Sol/XH"),
            "NORMAL": ("Terra/H", "Sol/H", "Sol/H", "Sol/XH"),
            "HIGH": ("Sol/H", "Sol/H", "Sol/XH", "Sol/XH"),
            "CRITICAL": ("Sol/H", "Sol/XH", "Sol/XH", "Sol/Max"),
        })
        self.assertIs(train_controller.REMEDIATION_MATRIX, train_controller.IMPLEMENTATION_MATRIX)
        self.assertIs(train_controller.FINAL_REVIEW_MATRIX, train_controller.INITIAL_REVIEW_MATRIX)

    def test_route_vocabulary_has_luna_and_sol_medium_but_not_ultra(self) -> None:
        self.assertEqual(train_controller.SETTING_NAMES["Luna/M"], ("gpt-5.6-luna", "medium"))
        self.assertEqual(train_controller.SETTING_NAMES["Sol/M"], ("gpt-5.6-sol", "medium"))
        self.assertNotIn("Sol/Ultra", train_controller.SETTING_NAMES)

    def test_human_validation_matrices_remain_independent_from_model_routing(self) -> None:
        analysis_yes = {
            (criticality, complexity)
            for criticality in train_controller.CRITICALITIES
            for complexity in train_controller.COMPLEXITIES
            if train_controller.analysis_human_gate(criticality, complexity, "standard")
        }
        merge_yes = {
            (criticality, complexity)
            for criticality in train_controller.CRITICALITIES
            for complexity in train_controller.COMPLEXITIES
            if train_controller.merge_human_gate(criticality, complexity, "standard")
        }
        self.assertEqual(analysis_yes, {
            ("HIGH", "MAXIMUM"),
            ("CRITICAL", "LOW"), ("CRITICAL", "MEDIUM"),
            ("CRITICAL", "HIGH"), ("CRITICAL", "MAXIMUM"),
        })
        self.assertEqual(merge_yes, {
            ("LOW", "MAXIMUM"),
            ("NORMAL", "HIGH"), ("NORMAL", "MAXIMUM"),
            ("HIGH", "LOW"), ("HIGH", "MEDIUM"),
            ("HIGH", "HIGH"), ("HIGH", "MAXIMUM"),
            ("CRITICAL", "LOW"), ("CRITICAL", "MEDIUM"),
            ("CRITICAL", "HIGH"), ("CRITICAL", "MAXIMUM"),
        })

    def test_phase_local_route_comparison_rejects_sol_medium_for_review(self) -> None:
        self.assertTrue(train_controller.route_is_covered(
            train_controller.ANALYSIS_ROUTE_COVERAGE,
            ("gpt-5.6-sol", "high"),
            ("gpt-5.6-sol", "medium"),
        ))
        with self.assertRaises(train_controller.ControllerError):
            train_controller.strongest_review_setting([
                ("gpt-5.6-sol", "medium"),
                ("gpt-5.6-terra", "high"),
            ])

    def test_triage_routes_are_profile_bound(self) -> None:
        self.assertEqual(
            train_controller.triage_setting({"triage_profile": "standard"}),
            ("gpt-5.6-terra", "medium", "conformant"),
        )
        self.assertEqual(
            train_controller.triage_setting({
                "triage_profile": "sensitive",
                "triage_escalation_reasons": ["low confidence crosses a routing boundary"],
            }),
            ("gpt-5.6-terra", "high", "conformant"),
        )
        proof = {field: True for field in train_controller.MECHANICAL_FAST_PATH_FIELDS}
        self.assertEqual(
            train_controller.triage_setting({
                "triage_profile": "mechanical",
                "mechanical_fast_path": proof,
            }),
            ("gpt-5.6-luna", "medium", "conformant"),
        )

    def test_max_authorization_is_scoped_not_a_dispatch_boolean(self) -> None:
        expected = ("gpt-5.6-sol", "max", "conformant")
        proc = {"reasoning_authorizations": {}}
        with self.assertRaises(train_controller.ControllerError):
            train_controller.validate_reasoning_authorization(
                proc,
                {"reasoning_authorized": True},
                expected,
                stage="analysis",
                ticket_id="T-1",
                head="base-sha",
            )
        proc["reasoning_authorizations"]["auth-1"] = {
            "status": "ACTIVE",
            "stage": "analysis",
            "ticket_id": "T-1",
            "head": "base-sha",
        }
        train_controller.validate_reasoning_authorization(
            proc,
            {"reasoning_authorization_id": "auth-1"},
            expected,
            stage="analysis",
            ticket_id="T-1",
            head="base-sha",
        )
        with self.assertRaises(train_controller.ControllerError):
            train_controller.validate_reasoning_authorization(
                proc,
                {"reasoning_authorization_id": "auth-1"},
                expected,
                stage="implementation",
                ticket_id="T-1",
                head="base-sha",
            )

    def test_under_routed_analysis_gets_one_targeted_validation_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            self.assertEqual(run.apply(
                "PHASE_DISPATCHED",
                kind="triage",
                phase_key="run:run:triage:1",
                base_commit="base-sha",
                model="gpt-5.6-terra",
                reasoning_effort="medium",
                routing_conformance="conformant",
                triage_profile="standard",
                context_packet=run.context_packet("base-sha", "base-sha"),
            ), 0)
            run.materialize("run:run:triage:1", "thread-triage")
            run.complete_phase("run:run:triage:1", "gpt-5.6-terra", "medium")
            self.assertEqual(run.apply(
                "TICKET_TRIAGED",
                ticket_id="T-1",
                phase_key="run:run:triage:1",
                criticality="LOW",
                complexity="LOW",
                confidence="medium",
                triage_model="gpt-5.6-terra",
                triage_reasoning_effort="medium",
                analysis_model="gpt-5.6-terra",
                analysis_reasoning_effort="medium",
                analysis_routing_conformance="conformant",
                analysis_unity_requirement="none",
            ), 0)
            self.assertEqual(run.apply(
                "PHASE_DISPATCHED",
                kind="analysis",
                ticket_id="T-1",
                phase_key="run:T-1:analysis:1",
                base_commit="base-sha",
                model="gpt-5.6-terra",
                reasoning_effort="medium",
                routing_conformance="conformant",
                unity_requirement="none",
                context_packet=run.context_packet("base-sha", "base-sha"),
            ), 0)
            run.materialize("run:T-1:analysis:1", "thread-analysis")
            run.complete_phase("run:T-1:analysis:1", "gpt-5.6-terra", "medium")
            self.assertEqual(run.apply(
                "ANALYSIS_RECORDED",
                ticket_id="T-1",
                phase_key="run:T-1:analysis:1",
                analysis_revision="analysis-1",
                analysis_base_commit="base-sha",
                source_revision="source-1",
                profile_revision="profile-1",
                criticality="NORMAL",
                complexity="MEDIUM",
                criticality_evidence="bounded feature impact",
                complexity_evidence="several known dependencies",
                structural_digest="all structural domains assessed",
                implementation_contract_revision="implementation-1",
                verification_contract_revision="verification-1",
                residual_implementation_complexity="MEDIUM",
                verification_complexity="MEDIUM",
                complexity_reduction_evidence="bounded design resolved",
                unresolved_implementation_difficulty=[],
                scope_assessment=run.scope_assessment(),
                report_thread_id="thread-analysis",
                model="gpt-5.6-terra",
                reasoning_effort="medium",
                routing_conformance="conformant",
            ), 0)
            item = run.state()["procedure"]["tickets"]["T-1"]
            self.assertEqual(item["status"], "ANALYSIS_ROUTE_VALIDATION_REQUIRED")
            action = train_controller.next_actions(run.state())[0]
            self.assertEqual(action["action"], "RECORD_ANALYSIS_ROUTE_VALIDATION_DISPATCH_INTENT")
            self.assertEqual(
                (action["required_model"], action["required_reasoning_effort"]),
                ("gpt-5.6-sol", "medium"),
            )
            self.assertEqual(run.apply(
                "PHASE_DISPATCHED",
                kind="analysis_route_validation",
                ticket_id="T-1",
                phase_key="run:T-1:analysis-route-validation:1",
                base_commit="base-sha",
                model="gpt-5.6-sol",
                reasoning_effort="medium",
                routing_conformance="conformant",
                unity_requirement="none",
                context_packet=run.context_packet("base-sha", "base-sha"),
            ), 0)
            self.assertEqual(
                train_controller.next_actions(run.state())[0]["action"],
                "DISPATCH_VISIBLE_PHASE",
            )
            run.materialize("run:T-1:analysis-route-validation:1", "thread-route-validation")
            self.assertEqual(
                train_controller.next_actions(run.state())[0]["action"],
                "WAIT_FOR_PHASE_TRANSITION",
            )
            run.complete_phase(
                "run:T-1:analysis-route-validation:1", "gpt-5.6-sol", "medium"
            )
            result_action = train_controller.next_actions(run.state())[0]
            self.assertEqual(result_action["action"], "RECORD_ANALYSIS_ROUTE_VALIDATION_RESULT")
            self.assertEqual(result_action["analysis_revision"], "analysis-1")
            self.assertEqual(run.apply(
                "ANALYSIS_ROUTE_VALIDATION_RECORDED",
                ticket_id="T-1",
                phase_key="run:T-1:analysis-route-validation:1",
                analysis_revision="analysis-1",
                status="failed",
                validated_sections=["classification evidence missing from compact packet"],
                report_reference="tasks/thread-route-validation.json",
            ), 0)
            self.assertEqual(
                run.state()["procedure"]["tickets"]["T-1"]["status"],
                "ANALYSIS_RECONCILIATION_REQUIRED",
            )
            self.assertEqual(run.apply(
                "ANALYSIS_ROUTE_VALIDATION_RECONCILED",
                ticket_id="T-1",
                analysis_revision="analysis-1",
                failed_phase_key="run:T-1:analysis-route-validation:1",
                reconciliation_reference="artifacts/reconciliation.json",
                reconciliation_summary="The compact packet now contains the recorded classification evidence.",
                updated_unresolved_implementation_difficulty=[],
                context_packet=run.context_packet("base-sha", "base-sha"),
            ), 0)
            retry_action = train_controller.next_actions(run.state())[0]
            self.assertEqual(retry_action["action"], "RECORD_ANALYSIS_ROUTE_VALIDATION_DISPATCH_INTENT")
            self.assertEqual(retry_action["retry_of_phase_key"], "run:T-1:analysis-route-validation:1")
            self.assertEqual(retry_action["context_packet"]["reference"], "artifacts/context.json")

    def test_failed_plan_contract_validation_can_be_amended_without_reanalysis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            run.analyze(criticality="HIGH", complexity="HIGH")
            self.assertEqual(
                train_controller.next_actions(run.state())[0]["action"],
                "RECORD_PLAN_CONTRACT_VALIDATION_DISPATCH_INTENT",
            )
            self.assertEqual(run.apply(
                "PHASE_DISPATCHED",
                kind="plan_contract_validation",
                ticket_id="T-1",
                phase_key="run:T-1:plan-contract:1",
                base_commit="base-sha",
                model="gpt-5.6-terra",
                reasoning_effort="medium",
                routing_conformance="conformant",
                contract_validation_profile="standard",
                unity_requirement="none",
                context_packet=run.context_packet("base-sha", "base-sha"),
            ), 0)
            run.materialize("run:T-1:plan-contract:1", "thread-plan-contract")
            run.complete_phase("run:T-1:plan-contract:1", "gpt-5.6-terra", "medium")
            self.assertEqual(run.apply(
                "PLAN_CONTRACT_VALIDATION_RECORDED",
                ticket_id="T-1",
                phase_key="run:T-1:plan-contract:1",
                status="failed",
                analysis_complexity="HIGH",
                residual_implementation_complexity="HIGH",
                verification_complexity="HIGH",
                complexity_reduction_evidence="The compact packet was incomplete.",
                unresolved_implementation_difficulty=["missing manifest"],
                validation_reference="tasks/thread-plan-contract.json",
            ), 0)
            self.assertEqual(
                run.state()["procedure"]["tickets"]["T-1"]["status"],
                "NEEDS_CONTRACT_AMENDMENT",
            )
            self.assertEqual(run.apply(
                "PLAN_CONTRACT_AMENDMENT_RECORDED",
                ticket_id="T-1",
                analysis_revision="analysis-1",
                failed_phase_key="run:T-1:plan-contract:1",
                implementation_contract_revision="implementation-2",
                verification_contract_revision="verification-2",
                amendment_reference="artifacts/contract-amendment.json",
                amendment_summary="The missing manifest and oracles are now present.",
                updated_unresolved_implementation_difficulty=[],
                context_packet=run.context_packet("base-sha", "base-sha"),
            ), 0)
            retry_action = train_controller.next_actions(run.state())[0]
            self.assertEqual(retry_action["action"], "RECORD_PLAN_CONTRACT_VALIDATION_DISPATCH_INTENT")
            self.assertEqual(retry_action["retry_of_phase_key"], "run:T-1:plan-contract:1")
            self.assertEqual(retry_action["context_packet"]["reference"], "artifacts/context.json")

    def test_post_consolidation_analysis_readiness_can_be_reconciled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            run.analyze()
            state = run.state()
            state["procedure"]["tickets"]["T-1"]["status"] = "ANALYZED"
            run_registry.save_json(run.path, state)

            action = train_controller.next_actions(run.state())[0]
            self.assertEqual(action["action"], "RECORD_ANALYSIS_READINESS_RECONCILIATION")
            self.assertEqual(action["analysis_revision"], "analysis-1")
            self.assertEqual(run.apply(
                "ANALYSIS_READINESS_RECONCILED",
                ticket_id="T-1",
                analysis_revision="analysis-1",
                reason="analysis completed after dependency consolidation",
            ), 0)
            self.assertEqual(
                run.state()["procedure"]["tickets"]["T-1"]["status"],
                "READY_FOR_IMPLEMENTATION",
            )

    def test_analysis_finalized_after_consolidation_is_immediately_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            run.analyze()
            state = run.state()
            item = state["procedure"]["tickets"]["T-1"]
            item["status"] = "ANALYZED"
            train_controller.finalize_analysis_gate(state["procedure"], item)
            self.assertEqual(item["status"], "READY_FOR_IMPLEMENTATION")

    def harness(self, directory: str) -> Harness:
        return Harness(Path(directory))

    def reach_final_review(self, run: Harness) -> None:
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
                base_commit="base-sha",
                head_branch="codex/train-test",
                head_commit="train-sha",
                is_draft=False,
            ),
            0,
        )
        self.assertEqual(
            run.apply(
                "FINAL_VERIFICATION_RECORDED",
                status="passed",
                head_commit="train-sha",
                evidence_reference="logs/final.json",
                **run.deterministic_verification_fields(),
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
                reasoning_effort="medium",
                routing_conformance="conformant",
                ticket_floor_evidence=[{
                    "ticket_id": "T-1",
                    "applies": False,
                    "reviewed_head": "ticket-sha",
                    "reason": "Exact ticket review remains valid and is outside the integration delta.",
                    "review_reused": True,
                }],
                reasoning_authorized=True,
                context_packet=run.context_packet("base-sha", "train-sha"),
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
                reasoning_effort="medium",
                envelope={
                    "phase_key": "run:run:final-review:1",
                    "phase_status": "completed",
                    "actual_model": "gpt-5.6-terra",
                    "actual_reasoning_effort": "medium",
                    "result_summary": "clean final review",
                    "artifacts": {"reviewed_head": "train-sha"},
                    "tests_and_checks": ["exact-head evidence checked"],
                    "residual_risks": "none",
                    "requested_or_recommended_next_action": "collect GitHub feedback",
                    "files_modified": "none",
                    "usage": {"measurement": "complete", "total_tokens": 100},
                },
            ),
            0,
        )

    @staticmethod
    def configure_unity(run: Harness, slot_count: int = 3) -> None:
        slots = [
            {
                "slot_id": f"unity-slot-{index}",
                "path": f"C:/unity-slots/unity-slot-{index}",
                "status": "IDLE",
                "config_profile_sha256": f"{index}" * 64,
            }
            for index in range(1, slot_count + 1)
        ]
        run.assert_code(
            run.apply(
                "UNITY_ENVIRONMENT_CONFIGURED",
                registry_reference="C:/state/unity-slots.json",
                repository=run.state()["procedure"]["environment"]["repository"],
                slot_root="C:/unity-slots",
                max_editors=slot_count,
                mcp_mode="local",
                cli_package="unity-mcp-cli@0.90.0",
                plugin_package="com.ivanmurzak.unity.mcp@0.90.0",
                slots=slots,
            ),
            0,
        )

    def test_unity_profile_defaults_to_three_slots_and_initializes_before_triage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Harness(Path(directory), environment_profile="unity-mcp-local")
            self.assertEqual(run.state()["procedure"]["limits"]["max_unity_editors"], 3)
            run.confirm()
            action = train_controller.next_actions(run.state())[0]
            self.assertEqual(action["action"], "INITIALIZE_UNITY_SLOTS_DETERMINISTICALLY")
            self.assertEqual(action["max_editors"], 3)
            self.assertEqual(action["mcp_mode"], "local")

    def test_unity_editor_limit_can_be_overridden_at_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Harness(
                Path(directory),
                environment_profile="unity-mcp-local",
                max_unity_editors=5,
            )
            self.assertEqual(run.state()["procedure"]["limits"]["max_unity_editors"], 5)

    def test_unity_phase_must_acquire_slot_before_visible_launch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Harness(Path(directory), environment_profile="unity-mcp-local")
            run.confirm()
            self.configure_unity(run)
            self.assertEqual(
                run.apply(
                    "PHASE_DISPATCHED",
                    kind="analysis_reconciliation",
                    ticket_id="T-1",
                    phase_key="run:T-1:reconcile:1",
                    base_commit="base-sha",
                    model="gpt-5.6-sol",
                    reasoning_effort="high",
                    unity_requirement="editor-read",
                    context_packet=run.context_packet("base-sha", "base-sha"),
                ),
                0,
            )
            action = train_controller.next_actions(run.state())[0]
            self.assertEqual(action["action"], "ACQUIRE_UNITY_SLOT_DETERMINISTICALLY")
            self.assertEqual(action["requirement"], "editor-read")
            self.assertEqual(run.apply(
                "PHASE_LAUNCH_OBSERVED",
                phase_key="run:T-1:reconcile:1",
                launch_state="RUNNING",
                thread_id="thread-reconcile",
                visibility_verified=True,
                execution_visibility="user-visible",
                visibility_evidence_reference="tasks/thread-reconcile.json",
            ), 2)
            self.assertEqual(run.apply(
                "UNITY_SLOT_ACQUIRED",
                owner_key="run:T-1:reconcile:1",
                slot_id="unity-slot-1",
                lease_id="lease-1",
                requirement="editor-read",
                path="C:/unity-slots/unity-slot-1",
                branch=None,
                expected_head="base-sha",
                observed_head="base-sha",
                readiness_evidence_reference="C:/state/readiness-1.json",
            ), 0)
            self.assertEqual(train_controller.next_actions(run.state())[0]["action"], "DISPATCH_VISIBLE_PHASE")
            self.assertEqual(run.apply(
                "PHASE_LAUNCH_OBSERVED",
                phase_key="run:T-1:reconcile:1",
                launch_state="RUNNING",
                thread_id="thread-reconcile",
                visibility_verified=True,
                execution_visibility="user-visible",
                visibility_evidence_reference="tasks/thread-reconcile.json",
            ), 0)
            run.complete_phase("run:T-1:reconcile:1", "gpt-5.6-sol", "high")
            release = train_controller.next_actions(run.state())[0]
            self.assertEqual(release["action"], "RELEASE_UNITY_SLOT_DETERMINISTICALLY")
            self.assertEqual(release["slot_id"], "unity-slot-1")

    def test_unity_configuration_rejects_cloud_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Harness(Path(directory), environment_profile="unity-mcp-local")
            run.confirm()
            self.assertEqual(run.apply(
                "UNITY_ENVIRONMENT_CONFIGURED",
                registry_reference="C:/state/unity-slots.json",
                repository=run.state()["procedure"]["environment"]["repository"],
                slot_root="C:/unity-slots",
                max_editors=3,
                mcp_mode="cloud",
                cli_package="unity-mcp-cli@0.90.0",
                plugin_package="com.ivanmurzak.unity.mcp@0.90.0",
                slots=[{
                    "slot_id": f"unity-slot-{index}",
                    "path": f"C:/unity-slots/unity-slot-{index}",
                    "status": "IDLE",
                    "config_profile_sha256": f"{index}" * 64,
                } for index in range(1, 4)],
            ), 2)

    def test_unity_execution_pair_shares_one_global_editor_pool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Harness(
                Path(directory),
                environment_profile="unity-mcp-local",
                max_unity_editors=1,
            )
            run.confirm()
            self.configure_unity(run, slot_count=1)
            run.analyze()
            run.dispatch_pair(
                implementation_unity_requirement="editor-write",
                acceptance_unity_requirement="playmode-ui",
                verification_unity_requirement="playmode-ui",
            )
            actions = train_controller.next_actions(run.state())
            self.assertEqual(
                [action["action"] for action in actions],
                ["ACQUIRE_UNITY_SLOT_DETERMINISTICALLY", "WAIT_FOR_UNITY_SLOT"],
            )
            self.assertEqual(
                {action["owner_key"] for action in actions},
                {"run:T-1:implementation:1", "run:T-1:acceptance:1"},
            )

    def test_foreground_wait_cannot_yield_with_a_running_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            self.assertEqual(
                run.apply(
                    "PHASE_DISPATCHED",
                    kind="triage",
                    phase_key="run:run:triage:1",
                    base_commit="base-sha",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    routing_conformance="conformant",
                    triage_profile="standard",
                    context_packet=run.context_packet("base-sha", "base-sha"),
                ),
                0,
            )
            run.materialize("run:run:triage:1", "thread-triage")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    train_controller.check(argparse.Namespace(state=run.path, mode="yield")),
                    2,
                )
            self.assertIn("foreground supervision cannot survive", output.getvalue())

    def test_verified_event_callback_can_supervise_visible_child_without_polling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            self.assertEqual(run.apply("ORCHESTRATOR_CONFIRMED", **run.orchestrator_confirmation_fields()), 0)
            self.assertEqual(
                run.apply(
                    "SUPERVISION_CONFIGURED",
                    mode="EVENT_CALLBACK",
                    callback_verified=True,
                    callback_target_thread_id="thread-main",
                ),
                0,
            )
            self.assertEqual(
                run.apply(
                    "PHASE_DISPATCHED",
                    kind="triage",
                    phase_key="run:run:triage:1",
                    base_commit="base-sha",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    routing_conformance="conformant",
                    triage_profile="standard",
                    context_packet=run.context_packet("base-sha", "base-sha"),
                ),
                0,
            )
            dispatch = train_controller.next_actions(run.state())[0]
            self.assertEqual(dispatch["action"], "DISPATCH_VISIBLE_PHASE")
            self.assertEqual(dispatch["completion_callback"]["target_thread_id"], "thread-main")
            self.assertIn("needs_input", dispatch["completion_callback"]["notify_on"])
            packet_output = io.StringIO()
            with contextlib.redirect_stdout(packet_output):
                self.assertEqual(
                    control_plane_runner.step(argparse.Namespace(state=run.path, output_dir=None)),
                    0,
                )
            packet_result = json.loads(packet_output.getvalue())
            packet = json.loads(Path(packet_result["packet_reference"]).read_text(encoding="utf-8"))
            self.assertEqual(packet["next_actions"][0]["executor_kind"], "adapter")
            self.assertEqual(
                packet["next_actions"][0]["authorized_handler"],
                "main-thread-controller-adapter",
            )
            self.assertEqual(
                packet["next_actions"][0]["completion_callback"]["target_thread_id"],
                "thread-main",
            )
            run.materialize("run:run:triage:1", "thread-triage")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    train_controller.check(argparse.Namespace(state=run.path, mode="yield")),
                    0,
                )
            handed_off = run.state()
            handed_off["orchestrator_lease"]["owner_thread_id"] = "thread-successor"
            run_registry.save_json(run.path, handed_off)
            reconfigure = train_controller.next_actions(run.state())[0]
            self.assertEqual(
                reconfigure["action"],
                "RECONFIGURE_EVENT_CALLBACKS_FOR_CURRENT_OWNER",
            )
            self.assertEqual(reconfigure["target_thread_id"], "thread-successor")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    train_controller.check(argparse.Namespace(state=run.path, mode="yield")),
                    2,
                )
            self.assertEqual(
                run.apply(
                    "SUPERVISION_CONFIGURED",
                    mode="EVENT_CALLBACK",
                    callback_verified=True,
                    callback_target_thread_id="thread-successor",
                ),
                0,
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    train_controller.check(argparse.Namespace(state=run.path, mode="yield")),
                    0,
                )

    def test_model_waking_recurring_watcher_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            self.assertEqual(run.apply("ORCHESTRATOR_CONFIRMED", **run.orchestrator_confirmation_fields()), 0)
            self.assertEqual(
                run.apply(
                    "SUPERVISION_CONFIGURED",
                    mode="BACKGROUND_WATCHER",
                    watcher_id="codex-heartbeat-automation",
                    watcher_consumes_model_tokens=True,
                ),
                2,
            )
            legacy = run.state()
            legacy["procedure"]["supervision"] = {
                "status": "ACTIVE",
                "mode": "BACKGROUND_WATCHER",
                "watcher_id": "legacy-codex-heartbeat",
            }
            run_registry.save_json(run.path, legacy)
            replacement = train_controller.next_actions(run.state())[0]
            self.assertEqual(replacement["action"], "REPLACE_MODEL_WAKING_WATCHER")
            self.assertIn("EVENT_CALLBACK", replacement["allowed_replacements"])

    def test_ticket_merge_permit_requires_reconciled_ci_and_copilot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            run.analyze()
            run.functional_ready()
            self.assertEqual(run.record_pr(), 0)
            run.clean_review()
            self.assertEqual(
                train_controller.merge_permit_issues(
                    run.state(), action="ticket", ticket_id="T-1", head_commit="ticket-sha"
                ),
                [],
            )
            state = run.state()
            state["procedure"]["tickets"]["T-1"]["finding_ledger"]["ci_status"] = "failed"
            self.assertIn(
                "ticket CI is not acceptable",
                train_controller.merge_permit_issues(
                    state, action="ticket", ticket_id="T-1", head_commit="ticket-sha"
                ),
            )

    def test_failed_ticket_ci_requires_a_blocking_pending_remediation_finding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            run.analyze()
            run.functional_ready()
            self.assertEqual(run.record_pr(), 0)
            run.clean_review(reconcile=False)
            fields = {
                "ticket_id": "T-1",
                "head_commit": "ticket-sha",
                "ledger_status": "complete",
                "sources_dispositioned": ["codex", "ci", "copilot"],
                "feedback_collection_started_at": "2026-08-01T10:00:00+00:00",
                "feedback_collection_deadline_at": "2026-08-01T10:10:00+00:00",
                "feedback_collected_at": "2026-08-01T10:02:00+00:00",
                "ci_status": "failed",
                "copilot_status": "received",
                "source_counts": {"codex": 0, "ci": 1, "copilot": 0},
                "source_findings": [{"finding_id": "ci-1", "source": "ci"}],
                "feedback_evidence_reference": "artifacts/ticket-feedback.json",
                "blocking_findings": ["ci-1"],
            }
            invalid_disposition = [{
                "finding_id": "ci-1",
                "disposition": "rejected-incorrect",
                "blocking": False,
                "remediation_status": "not-required",
                "verification": "CI failed",
            }]
            self.assertEqual(
                run.apply(
                    "TICKET_FINDINGS_RECONCILED",
                    finding_dispositions=invalid_disposition,
                    **fields,
                ),
                2,
            )
            valid_disposition = [{
                "finding_id": "ci-1",
                "disposition": "accepted-deferred",
                "blocking": True,
                "remediation_status": "pending",
                "verification": "Exact-head CI failure requires remediation",
            }]
            self.assertEqual(
                run.apply(
                    "TICKET_FINDINGS_RECONCILED",
                    finding_dispositions=valid_disposition,
                    **fields,
                ),
                0,
            )
            self.assertEqual(
                run.state()["procedure"]["tickets"]["T-1"]["status"],
                "NEEDS_REMEDIATION",
            )
            self.assertIn(
                "ticket CI is not acceptable",
                train_controller.merge_permit_issues(
                    run.state(), action="ticket", ticket_id="T-1", head_commit="ticket-sha"
                ),
            )

    def test_out_of_scope_review_finding_cannot_block_or_trigger_remediation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            run.analyze()
            run.functional_ready()
            self.assertEqual(run.record_pr(), 0)
            run.clean_review(reconcile=False)
            fields = {
                "ticket_id": "T-1",
                "head_commit": "ticket-sha",
                "ledger_status": "complete",
                "sources_dispositioned": ["codex", "ci", "copilot"],
                "feedback_collection_started_at": "2026-08-01T10:00:00+00:00",
                "feedback_collection_deadline_at": "2026-08-01T10:10:00+00:00",
                "feedback_collected_at": "2026-08-01T10:02:00+00:00",
                "ci_status": "passed",
                "copilot_status": "received",
                "source_counts": {"codex": 1, "ci": 0, "copilot": 0},
                "source_findings": [{"finding_id": "scope-1", "source": "codex"}],
                "feedback_evidence_reference": "artifacts/ticket-feedback.json",
            }
            self.assertEqual(run.apply(
                "TICKET_FINDINGS_RECONCILED",
                finding_dispositions=[{
                    "finding_id": "scope-1",
                    "disposition": "rejected-out-of-scope",
                    "blocking": True,
                    "remediation_status": "pending",
                    "verification": "Not required by the ticket.",
                }],
                blocking_findings=["scope-1"],
                **fields,
            ), 2)
            self.assertEqual(run.apply(
                "TICKET_FINDINGS_RECONCILED",
                finding_dispositions=[{
                    "finding_id": "scope-1",
                    "disposition": "rejected-out-of-scope",
                    "blocking": False,
                    "remediation_status": "not-required",
                    "verification": "Not required by the ticket.",
                }],
                blocking_findings=[],
                **fields,
            ), 0)
            self.assertEqual(
                run.state()["procedure"]["tickets"]["T-1"]["status"],
                "READY_TO_MERGE",
            )

    def test_external_ticket_pr_head_drift_is_recorded_before_finding_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            run.analyze()
            run.functional_ready()
            self.assertEqual(run.record_pr(), 0)
            run.clean_review(reconcile=False)

            self.assertEqual(
                run.apply(
                    "TICKET_PR_HEAD_DRIFT_RECORDED",
                    ticket_id="T-1",
                    previous_head_commit="ticket-sha",
                    head_commit="external-descendant-sha",
                    relationship="descendant",
                    relationship_evidence_reference="artifacts/head-drift.json",
                    observed_at="2026-08-23T06:22:39+00:00",
                    source="copilot",
                ),
                0,
            )
            item = run.state()["procedure"]["tickets"]["T-1"]
            self.assertEqual(item["status"], "AWAITING_FINDING_RECONCILIATION")
            self.assertEqual(item["pull_request"]["head_commit"], "external-descendant-sha")
            self.assertTrue(item["pull_request"]["head_drift_unreconciled"])
            self.assertTrue(item["verification"]["stale_due_to_head_drift"])
            self.assertTrue(item["reviews"][-1]["stale_due_to_head_drift"])

    def test_external_ticket_pr_head_drift_requires_matching_proven_descendant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            run.analyze()
            run.functional_ready()
            self.assertEqual(run.record_pr(), 0)
            run.clean_review(reconcile=False)

            self.assertEqual(
                run.apply(
                    "TICKET_PR_HEAD_DRIFT_RECORDED",
                    ticket_id="T-1",
                    previous_head_commit="wrong-head",
                    head_commit="external-sha",
                    relationship="descendant",
                    relationship_evidence_reference="artifacts/head-drift.json",
                    observed_at="2026-08-23T06:22:39+00:00",
                    source="copilot",
                ),
                2,
            )
            self.assertEqual(
                run.apply(
                    "TICKET_PR_HEAD_DRIFT_RECORDED",
                    ticket_id="T-1",
                    previous_head_commit="ticket-sha",
                    head_commit="external-sha",
                    relationship="unknown",
                    relationship_evidence_reference="artifacts/head-drift.json",
                    observed_at="2026-08-23T06:22:39+00:00",
                    source="copilot",
                ),
                2,
            )

    def seed_exhausted_ticket_remediation(self, run: Harness) -> None:
        run.confirm()
        run.analyze()
        run.functional_ready()
        self.assertEqual(run.record_pr(), 0)
        state = run.state()
        item = state["procedure"]["tickets"]["T-1"]
        item["status"] = "NEEDS_REMEDIATION"
        item["remediation_cycles"] = train_controller.AUTOMATIC_REMEDIATION_CYCLE_LIMIT
        run_registry.save_json(run.path, state)

    def provide_remediation_exception_input(self, run: Harness) -> None:
        self.assertEqual(
            run.apply(
                "HUMAN_INPUT_REQUESTED",
                ticket_id="T-1",
                gate_id="T-1:remediation-exception:1",
                revision="root-cause-1",
                question="Authorize one additional remediation cycle?",
                reason="The root-cause checkpoint found a bounded broken test oracle.",
                blocked_scope="T-1 remediation",
                continuing_scope="none",
                accepted_replies=["Authorize", "Reject"],
            ),
            0,
        )
        self.assertEqual(
            run.apply(
                "GATE_ANNOUNCED",
                gate_id="T-1:remediation-exception:1",
                revision="root-cause-1",
                decision_summary="One ticket-scoped cycle requires explicit authorization.",
                evidence_summary="Two automatic cycles are exhausted and the remaining fix is bounded.",
                blocked_scope="T-1 remediation",
                continuing_scope="none",
                accepted_replies=["Authorize", "Reject"],
            ),
            0,
        )
        self.assertEqual(
            run.apply(
                "INPUT_PROVIDED",
                gate_id="T-1:remediation-exception:1",
                revision="root-cause-1",
                response_summary="The user authorized one additional remediation cycle.",
                response_artifact="main-thread:user-message-3",
            ),
            0,
        )

    def grant_remediation_exception(self, run: Harness, additional_cycles: int = 1) -> int:
        return run.apply(
            "REMEDIATION_LIMIT_EXCEPTION_GRANTED",
            ticket_id="T-1",
            gate_id="T-1:remediation-exception:1",
            additional_cycles=additional_cycles,
            user_decision_reference="main-thread:user-message-3",
            root_cause_reference="artifacts/root-cause.json",
            reason="Bounded test-oracle correction after the automatic budget was exhausted.",
            authorized_at="2026-08-01T10:02:00+00:00",
        )

    def dispatch_remediation(self, run: Harness, phase_key: str) -> int:
        return run.apply(
            "REMEDIATION_DISPATCHED",
            ticket_id="T-1",
            phase_key=phase_key,
            base_commit="ticket-sha",
            branch="codex/t-1-remediation",
            criticality="LOW",
            complexity="LOW",
            change_kind="bounded-behavioral",
            model="gpt-5.6-terra",
            reasoning_effort="medium",
            routing_conformance="conformant",
            reasoning_authorized=True,
            scope_assessment_revision="scope-1",
            scope_conformance="within-authorized-scope",
            context_packet=run.context_packet("ticket-sha", "ticket-sha"),
        )

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

    def test_blocked_analysis_launch_offers_a_fresh_retry_intent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            model, effort = train_controller.setting_from_matrix(
                train_controller.ANALYSIS_MATRIX, "LOW", "LOW"
            )
            self.assertEqual(run.apply(
                "PHASE_DISPATCHED",
                kind="triage",
                phase_key="run:run:triage:1",
                base_commit="base-sha",
                model="gpt-5.6-terra",
                reasoning_effort="medium",
                routing_conformance="conformant",
                triage_profile="standard",
                context_packet=run.context_packet("base-sha", "base-sha"),
            ), 0)
            run.materialize("run:run:triage:1", "thread-triage")
            run.complete_phase("run:run:triage:1", "gpt-5.6-terra", "medium")
            self.assertEqual(run.apply(
                "TICKET_TRIAGED",
                ticket_id="T-1",
                phase_key="run:run:triage:1",
                criticality="LOW",
                complexity="LOW",
                confidence="high",
                triage_model="gpt-5.6-terra",
                triage_reasoning_effort="medium",
                analysis_model=model,
                analysis_reasoning_effort=effort,
                analysis_routing_conformance="conformant",
                analysis_unity_requirement="none",
                reasoning_authorized=True,
            ), 0)
            self.assertEqual(run.apply(
                "PHASE_DISPATCHED",
                kind="analysis",
                ticket_id="T-1",
                phase_key="run:T-1:analysis:1",
                base_commit="base-sha",
                model=model,
                reasoning_effort=effort,
                routing_conformance="conformant",
                reasoning_authorized=True,
                unity_requirement="none",
                context_packet=run.context_packet("base-sha", "base-sha"),
            ), 0)
            self.assertEqual(run.apply(
                "PHASE_LAUNCH_OBSERVED",
                phase_key="run:T-1:analysis:1",
                launch_state="BLOCKED",
                reconciliation_evidence_reference="logs/launch-reconciliation.json",
            ), 0)
            action = train_controller.next_actions(run.state())[0]
            self.assertEqual(action["action"], "RECORD_ANALYSIS_DISPATCH_INTENT")
            self.assertEqual(action["ticket_id"], "T-1")
            self.assertEqual(action["retry_of_phase_key"], "run:T-1:analysis:1")

    def test_prelaunch_input_rearms_same_phase_without_a_fake_thread_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            model, effort = train_controller.setting_from_matrix(
                train_controller.ANALYSIS_MATRIX, "LOW", "LOW"
            )
            self.assertEqual(run.apply(
                "PHASE_DISPATCHED",
                kind="triage",
                phase_key="run:run:triage:1",
                base_commit="base-sha",
                model="gpt-5.6-terra",
                reasoning_effort="medium",
                routing_conformance="conformant",
                triage_profile="standard",
                context_packet=run.context_packet("base-sha", "base-sha"),
            ), 0)
            run.materialize("run:run:triage:1", "thread-triage")
            run.complete_phase("run:run:triage:1", "gpt-5.6-terra", "medium")
            self.assertEqual(run.apply(
                "TICKET_TRIAGED",
                ticket_id="T-1",
                phase_key="run:run:triage:1",
                criticality="LOW",
                complexity="LOW",
                confidence="high",
                triage_model="gpt-5.6-terra",
                triage_reasoning_effort="medium",
                analysis_model=model,
                analysis_reasoning_effort=effort,
                analysis_routing_conformance="conformant",
                analysis_unity_requirement="none",
            ), 0)
            self.assertEqual(run.apply(
                "PHASE_DISPATCHED",
                kind="analysis",
                ticket_id="T-1",
                phase_key="run:T-1:analysis:1",
                base_commit="base-sha",
                model=model,
                reasoning_effort=effort,
                routing_conformance="conformant",
                unity_requirement="none",
                context_packet=run.context_packet("base-sha", "base-sha"),
            ), 0)
            self.assertEqual(run.apply(
                "PHASE_LAUNCH_OBSERVED",
                phase_key="run:T-1:analysis:1",
                launch_state="BLOCKED",
                reconciliation_evidence_reference="logs/prelaunch-block.json",
            ), 0)
            self.assertEqual(run.apply(
                "HUMAN_INPUT_REQUESTED",
                ticket_id="T-1",
                phase_key="run:T-1:analysis:1",
                gate_id="T-1:prelaunch-input:1",
                revision="prelaunch-1",
                question="Authorize reversible environment preservation?",
                reason="A managed local override blocks the checkout.",
                blocked_scope="T-1 analysis",
                continuing_scope="none",
                accepted_replies=["Authorize", "Cancel"],
            ), 0)
            gate = run.state()["procedure"]["human_gates"]["T-1:prelaunch-input:1"]
            self.assertEqual(gate["resume_mode"], "prelaunch-retry")
            self.assertEqual(run.apply(
                "GATE_ANNOUNCED",
                gate_id="T-1:prelaunch-input:1",
                revision="prelaunch-1",
                decision_summary="Authorize the reversible preservation step.",
                evidence_summary="No user code or task thread exists yet.",
                blocked_scope="T-1 analysis",
                continuing_scope="none",
                accepted_replies=["Authorize", "Cancel"],
            ), 0)
            self.assertEqual(run.apply(
                "INPUT_PROVIDED",
                gate_id="T-1:prelaunch-input:1",
                revision="prelaunch-1",
                response_summary="Authorized.",
                response_artifact="main-thread:user-message-1",
            ), 0)
            state = run.state()
            phase = state["procedure"]["phases"]["run:T-1:analysis:1"]
            self.assertEqual(phase["launch_state"], "INTENT_RECORDED")
            self.assertIsNone(phase["thread_id"])
            self.assertEqual(phase["prelaunch_retry_count"], 1)
            self.assertEqual(state["procedure"]["tickets"]["T-1"]["status"], "TRIAGED")
            self.assertEqual(len(state["procedure"]["phases"]), 2)
            self.assertEqual(
                train_controller.next_actions(state)[0]["action"],
                "DISPATCH_VISIBLE_PHASE",
            )

    def test_hidden_phase_requires_phase_specific_user_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            self.assertEqual(
                run.apply(
                    "PHASE_DISPATCHED",
                    kind="triage",
                    phase_key="run:run:triage:1",
                    base_commit="base-sha",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    routing_conformance="conformant",
                    triage_profile="standard",
                    context_packet=run.context_packet("base-sha", "base-sha"),
                ),
                0,
            )
            hidden_launch = {
                "phase_key": "run:run:triage:1",
                "launch_state": "RUNNING",
                "thread_id": "hidden-session-1",
                "agent_session_id": "hidden-session-1",
                "visibility_verified": False,
                "execution_visibility": "hidden-authorized",
                "hidden_authorization_id": "hidden-auth-1",
            }
            self.assertEqual(run.apply("PHASE_LAUNCH_OBSERVED", **hidden_launch), 2)
            self.assertEqual(
                run.apply(
                    "HIDDEN_FALLBACK_AUTHORIZED",
                    authorization_id="hidden-auth-1",
                    phase_key="run:run:triage:1",
                    user_decision_reference="main-thread:user-message-1",
                    reason="Visible task creation is unavailable.",
                    authorized_at="2026-08-01T10:00:00+00:00",
                ),
                0,
            )
            self.assertEqual(run.apply("PHASE_LAUNCH_OBSERVED", **hidden_launch), 0)
            phase = run.state()["procedure"]["phases"]["run:run:triage:1"]
            self.assertFalse(phase["visibility_verified"])
            self.assertEqual(phase["execution_visibility"], "hidden-authorized")

    def test_cost_anomaly_stops_successor_until_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            self.assertEqual(
                run.apply(
                    "PHASE_DISPATCHED",
                    kind="triage",
                    phase_key="run:run:triage:1",
                    base_commit="base-sha",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    routing_conformance="conformant",
                    triage_profile="standard",
                    context_packet=run.context_packet("base-sha", "base-sha"),
                ),
                0,
            )
            run.materialize("run:run:triage:1", "thread-triage")
            self.assertEqual(
                run.apply(
                    "PHASE_COMPLETED",
                    phase_key="run:run:triage:1",
                    envelope={
                        "phase_key": "run:run:triage:1",
                        "phase_status": "completed",
                        "actual_model": "gpt-5.6-terra",
                        "actual_reasoning_effort": "medium",
                        "result_summary": "done",
                        "artifacts": {"triage": "triage.json"},
                        "tests_and_checks": ["routing complete"],
                        "residual_risks": "none",
                        "requested_or_recommended_next_action": "continue",
                        "files_modified": "none",
                        "usage": {"measurement": "complete", "total_tokens": 50_000_001},
                    },
                ),
                0,
            )
            action = train_controller.next_actions(run.state())[0]
            self.assertEqual(action["action"], "RESOLVE_COST_ANOMALY_CHECKPOINT")
            self.assertEqual(
                run.apply(
                    "HIDDEN_FALLBACK_AUTHORIZED",
                    authorization_id="hidden-auth-cost",
                    phase_key="run:run:triage:1",
                    user_decision_reference="main-thread:user-message-2",
                    reason="test",
                    authorized_at="2026-08-01T10:01:00+00:00",
                ),
                2,
            )
            anomaly_id = action["anomalies"][0]["anomaly_id"]
            self.assertEqual(
                run.apply(
                    "COST_ANOMALY_RESOLVED",
                    anomaly_id=anomaly_id,
                    resolution="restart-fresh-compact",
                    resolution_evidence="new compact task will be used",
                ),
                0,
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

    def test_third_remediation_requires_a_recorded_exception(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            self.seed_exhausted_ticket_remediation(run)
            self.assertEqual(self.dispatch_remediation(run, "run:T-1:remediation:3"), 2)
            action = train_controller.next_actions(run.state())[0]
            self.assertEqual(action["action"], "ROOT_CAUSE_CHECKPOINT_REQUIRED")

    def test_remediation_exception_requires_the_resolved_ticket_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            self.seed_exhausted_ticket_remediation(run)
            self.assertEqual(self.grant_remediation_exception(run), 2)

    def test_remediation_exception_grants_exactly_one_ticket_scoped_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            self.seed_exhausted_ticket_remediation(run)
            self.provide_remediation_exception_input(run)
            self.assertEqual(self.grant_remediation_exception(run, additional_cycles=2), 2)
            self.assertEqual(self.grant_remediation_exception(run), 0)
            self.assertEqual(
                train_controller.next_actions(run.state())[0]["action"],
                "DISPATCH_FRESH_BATCHED_REMEDIATION",
            )
            self.assertEqual(self.dispatch_remediation(run, "run:T-1:remediation:3"), 0)
            state = run.state()
            item = state["procedure"]["tickets"]["T-1"]
            self.assertEqual(item["remediation_cycles"], 3)
            self.assertEqual(item["remediation_limit_exception"]["status"], "CONSUMED")

            item["status"] = "NEEDS_REMEDIATION"
            run_registry.save_json(run.path, state)
            self.assertEqual(self.dispatch_remediation(run, "run:T-1:remediation:4"), 2)

    def test_consumed_exception_can_be_reauthorized_for_one_more_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            self.seed_exhausted_ticket_remediation(run)
            self.provide_remediation_exception_input(run)
            self.assertEqual(self.grant_remediation_exception(run), 0)
            self.assertEqual(self.dispatch_remediation(run, "run:T-1:remediation:3"), 0)

            state = run.state()
            state["procedure"]["tickets"]["T-1"]["status"] = "NEEDS_REMEDIATION"
            run_registry.save_json(run.path, state)
            for event_type, fields in (
                (
                    "HUMAN_INPUT_REQUESTED",
                    {
                        "ticket_id": "T-1",
                        "gate_id": "T-1:remediation-exception:2",
                        "revision": "root-cause-2",
                        "question": "Authorize one final bounded remediation cycle?",
                        "reason": "A new root-cause checkpoint found a separate bounded defect.",
                        "blocked_scope": "T-1 remediation",
                        "continuing_scope": "none",
                        "accepted_replies": ["Authorize", "Reject"],
                    },
                ),
                (
                    "GATE_ANNOUNCED",
                    {
                        "gate_id": "T-1:remediation-exception:2",
                        "revision": "root-cause-2",
                        "decision_summary": "One more ticket-scoped cycle requires explicit authorization.",
                        "evidence_summary": "The previous exception is consumed and the new correction is bounded.",
                        "blocked_scope": "T-1 remediation",
                        "continuing_scope": "none",
                        "accepted_replies": ["Authorize", "Reject"],
                    },
                ),
                (
                    "INPUT_PROVIDED",
                    {
                        "gate_id": "T-1:remediation-exception:2",
                        "revision": "root-cause-2",
                        "response_summary": "The user authorized one final bounded remediation cycle.",
                        "response_artifact": "main-thread:user-message-4",
                    },
                ),
            ):
                self.assertEqual(run.apply(event_type, **fields), 0)

            self.assertEqual(
                run.apply(
                    "REMEDIATION_LIMIT_EXCEPTION_GRANTED",
                    ticket_id="T-1",
                    gate_id="T-1:remediation-exception:2",
                    additional_cycles=1,
                    user_decision_reference="main-thread:user-message-4",
                    root_cause_reference="artifacts/root-cause-2.json",
                    reason="A second bounded correction after the prior exceptional cycle was consumed.",
                    authorized_at="2026-08-01T10:03:00+00:00",
                ),
                0,
            )
            self.assertEqual(self.dispatch_remediation(run, "run:T-1:remediation:4"), 0)
            item = run.state()["procedure"]["tickets"]["T-1"]
            self.assertEqual(item["remediation_cycles"], 4)
            self.assertEqual(item["remediation_limit_exception"]["status"], "CONSUMED")
            self.assertEqual(len(item["remediation_limit_exception_history"]), 1)

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

    def test_scope_expansion_gate_is_non_bypassable_in_full_auto(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            proposal = run.scope_expansion_proposal()
            run.analyze(scope_proposals=[proposal])
            item = run.state()["procedure"]["tickets"]["T-1"]
            self.assertEqual(item["status"], "AWAITING_SPECIFICATION_DECISION")
            gate_id = item["scope_expansion_gate_id"]
            self.assertEqual(train_controller.next_actions(run.state())[0]["action"], "ANNOUNCE_HUMAN_GATE")
            self.assertEqual(run.apply(
                "GATE_ANNOUNCED",
                gate_id=gate_id,
                revision="scope-1",
                decision_summary="Choose minimal MVP scope or approve save migration.",
                evidence_summary="Save migration is not required by the source and the product is pre-MVP.",
                blocked_scope="T-1 implementation and acceptance tests",
                continuing_scope="Independent analyses",
                accepted_replies=["Minimal MVP", "Approve migration", "Defer migration"],
            ), 0)
            self.assertEqual(run.apply(
                "GATE_RESOLVED", gate_id=gate_id, revision="scope-1", decision="approved"
            ), 2)
            self.assertEqual(run.apply(
                "SCOPE_EXPANSION_DECIDED",
                ticket_id="T-1",
                gate_id=gate_id,
                revision="scope-1",
                decisions=[{
                    "proposal_id": "legacy-save-migration",
                    "decision": "deferred",
                    "selected_variant": "minimal",
                }],
                specification_decisions=[],
                user_decision_reference="thread-main:user-message-1",
                active_scope_revision="scope-active-2",
                implementation_contract_revision="implementation-2",
                verification_contract_revision="verification-2",
            ), 0)
            item = run.state()["procedure"]["tickets"]["T-1"]
            self.assertEqual(item["status"], "ANALYZED")
            self.assertEqual(item["analysis"]["scope_assessment"]["status"], "RESOLVED")
            self.assertEqual(
                item["analysis"]["scope_assessment"]["decisions"][0]["decision"],
                "deferred",
            )

    def test_approved_scope_expansion_reclassifies_and_uses_targeted_route_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            run.analyze(scope_proposals=[run.scope_expansion_proposal()])
            gate_id = run.state()["procedure"]["tickets"]["T-1"]["scope_expansion_gate_id"]
            self.assertEqual(run.apply(
                "GATE_ANNOUNCED",
                gate_id=gate_id,
                revision="scope-1",
                decision_summary="Choose minimal MVP scope or approve save migration.",
                evidence_summary="The expanded variant changes routing and tests.",
                blocked_scope="T-1 implementation and acceptance tests",
                continuing_scope="Independent analyses",
                accepted_replies=["Minimal MVP", "Approve migration", "Defer migration"],
            ), 0)
            self.assertEqual(run.apply(
                "SCOPE_EXPANSION_DECIDED",
                ticket_id="T-1",
                gate_id=gate_id,
                revision="scope-1",
                decisions=[{
                    "proposal_id": "legacy-save-migration",
                    "decision": "approved",
                    "selected_variant": "expanded",
                }],
                specification_decisions=[],
                user_decision_reference="thread-main:user-message-2",
                active_scope_revision="scope-active-2",
                implementation_contract_revision="implementation-2",
                verification_contract_revision="verification-2",
                criticality="NORMAL",
                complexity="MEDIUM",
                criticality_evidence="The approved migration changes persisted state but remains recoverable.",
                complexity_evidence="The approved migration adds versioned loading and legacy fixtures.",
                residual_implementation_complexity="MEDIUM",
                verification_complexity="MEDIUM",
                complexity_reduction_evidence="The variants and oracle are explicit.",
                unresolved_implementation_difficulty=[],
                classification_scope_item_ids=["source-criterion-1", "proposed-legacy-save-migration"],
            ), 0)
            item = run.state()["procedure"]["tickets"]["T-1"]
            self.assertEqual(item["status"], "ANALYSIS_ROUTE_VALIDATION_REQUIRED")
            self.assertEqual(item["analysis"]["criticality"], "NORMAL")
            approved_item = next(
                scope_item for scope_item in item["analysis"]["scope_assessment"]["items"]
                if scope_item["item_id"] == "proposed-legacy-save-migration"
            )
            self.assertEqual(approved_item["scope_origin"], "user-approved")
            self.assertEqual(
                train_controller.next_actions(run.state())[0]["action"],
                "RECORD_ANALYSIS_ROUTE_VALIDATION_DISPATCH_INTENT",
            )

    def test_specification_precision_requires_a_distinct_non_bypassable_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            run.analyze(specification_deviations=[run.specification_deviation()])
            item = run.state()["procedure"]["tickets"]["T-1"]
            self.assertEqual(item["status"], "AWAITING_SPECIFICATION_DECISION")
            gate_id = item["specification_gate_id"]
            gate = run.state()["procedure"]["human_gates"][gate_id]
            self.assertEqual(gate["kind"], "specification_deviation")
            self.assertFalse(gate["bypassable"])
            self.assertEqual(run.apply(
                "GATE_ANNOUNCED",
                gate_id=gate_id,
                revision="scope-1",
                decision_summary="Choose the behavior for incompatible pre-MVP saves.",
                evidence_summary="The source does not specify reset versus preservation.",
                blocked_scope="T-1 contract, implementation, and acceptance tests",
                continuing_scope="Independent analyses",
                accepted_replies=["Reset saves", "Preserve saves"],
            ), 0)
            self.assertEqual(run.apply(
                "GATE_RESOLVED", gate_id=gate_id, revision="scope-1", decision="approved"
            ), 2)
            self.assertEqual(run.apply(
                "SPECIFICATION_DECISIONS_RECORDED",
                ticket_id="T-1",
                gate_id=gate_id,
                revision="scope-1",
                decisions=[],
                specification_decisions=[{
                    "deviation_id": "save-reset-semantics",
                    "selected_option_id": "reset",
                }],
                user_decision_reference="thread-main:user-message-3",
                active_scope_revision="scope-active-2",
                implementation_contract_revision="implementation-2",
                verification_contract_revision="verification-2",
                criticality="LOW",
                complexity="LOW",
                criticality_evidence="Pre-MVP save loss is explicitly accepted and contained.",
                complexity_evidence="The selected reset behavior has a direct deterministic oracle.",
                residual_implementation_complexity="LOW",
                verification_complexity="LOW",
                complexity_reduction_evidence="The user resolved the only behavioral ambiguity.",
                unresolved_implementation_difficulty=[],
                classification_scope_item_ids=["source-criterion-1", "spec-save-reset-semantics"],
            ), 0)
            item = run.state()["procedure"]["tickets"]["T-1"]
            self.assertEqual(item["status"], "ANALYZED")
            selected_item = next(
                scope_item for scope_item in item["analysis"]["scope_assessment"]["items"]
                if scope_item["item_id"] == "spec-save-reset-semantics"
            )
            self.assertEqual(selected_item["scope_origin"], "user-approved")
            self.assertEqual(selected_item["selected_option_id"], "reset")

    def test_missing_information_is_a_visible_durable_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            run.analyze()
            self.assertEqual(
                run.apply(
                    "HUMAN_INPUT_REQUESTED",
                    ticket_id="T-1",
                    gate_id="T-1:product-input:1",
                    revision="input-1",
                    question="Which validated legal identity should be published?",
                    reason="The repository contains no validated publisher identity.",
                    blocked_scope="T-1 implementation only",
                    continuing_scope="No other ticket is blocked",
                    accepted_replies=["Provide values", "Authorize placeholders"],
                ),
                0,
            )
            self.assertEqual(train_controller.next_actions(run.state())[0]["action"], "ANNOUNCE_HUMAN_GATE")
            check_args = argparse.Namespace(state=run.path, mode="yield")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(train_controller.check(check_args), 2)
            self.assertEqual(
                run.apply(
                    "GATE_ANNOUNCED",
                    gate_id="T-1:product-input:1",
                    revision="input-1",
                    decision_summary="Choose the validated identity or authorize placeholders.",
                    evidence_summary="No validated identity exists in repository evidence.",
                    blocked_scope="T-1 implementation only",
                    continuing_scope="No other ticket is blocked",
                    accepted_replies=["Provide values", "Authorize placeholders"],
                ),
                0,
            )
            pending = run.state()["pending_human_action"]
            self.assertEqual(pending["question"], "Which validated legal identity should be published?")
            heartbeat_args = argparse.Namespace(state=run.path)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(train_controller.heartbeat(heartbeat_args), 0)
            pulse = json.loads(output.getvalue())
            self.assertEqual(pulse["heartbeat_decision"], "WAIT_FOR_HUMAN_WITHOUT_WATCHER")
            self.assertEqual(pulse["supervision_projection"]["activity_state"], "AWAITING_HUMAN_ONLY")
            self.assertEqual(pulse["active_visible_tasks"], [])
            self.assertTrue(pulse["may_pause_or_delete_watcher"])
            self.assertFalse(pulse["requires_model_wake"])
            self.assertEqual(
                run.apply("SUPERVISION_PAUSED_FOR_HUMAN_GATE"),
                0,
            )
            self.assertEqual(
                run.state()["procedure"]["supervision"]["status"],
                "PAUSED_HUMAN_GATE",
            )
            self.assertEqual(train_controller.next_actions(run.state())[0]["action"], "AWAIT_HUMAN_GATE")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(train_controller.check(check_args), 0)
                self.assertEqual(
                    control_plane_runner.record_activity(argparse.Namespace(
                        state=run.path,
                        thread_id="thread-main",
                        baseline_total_tokens=0,
                        latest_total_tokens=25_000_000,
                        model_wakes=50,
                        tool_calls=500,
                        context_compactions=1,
                    )),
                    0,
                )
            first_step = io.StringIO()
            with contextlib.redirect_stdout(first_step):
                self.assertEqual(
                    control_plane_runner.step(argparse.Namespace(state=run.path, output_dir=None)),
                    0,
                )
            self.assertEqual(json.loads(first_step.getvalue())["wake_kind"], "NO_MODEL_WAKE")
            repeated_step = io.StringIO()
            with contextlib.redirect_stdout(repeated_step):
                self.assertEqual(
                    control_plane_runner.step(argparse.Namespace(state=run.path, output_dir=None)),
                    0,
                )
            repeated = json.loads(repeated_step.getvalue())
            self.assertEqual(repeated["status"], "unchanged-suppressed")
            self.assertEqual(repeated["wake_kind"], "NO_MODEL_WAKE")
            self.assertEqual(
                run.apply(
                    "INPUT_PROVIDED",
                    gate_id="T-1:product-input:1",
                    revision="input-1",
                    response_summary="Use explicit placeholders.",
                    response_artifact="main-thread:user-message-1",
                ),
                0,
            )
            self.assertIsNone(run.state()["pending_human_action"])
            self.assertEqual(run.state()["procedure"]["tickets"]["T-1"]["status"], "READY_FOR_IMPLEMENTATION")
            self.assertEqual(
                train_controller.next_actions(run.state())[0]["action"],
                "CONFIGURE_SUPERVISION_BEFORE_DISPATCH",
            )

    def test_visible_running_children_are_the_primary_progress_signal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            run.analyze()
            run.dispatch_pair()
            run.materialize("run:T-1:implementation:1", "thread-impl")
            run.materialize("run:T-1:acceptance:1", "thread-tests")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    train_controller.heartbeat(argparse.Namespace(state=run.path)),
                    0,
                )
            pulse = json.loads(output.getvalue())
            self.assertEqual(pulse["heartbeat_decision"], "WAIT_FOR_VISIBLE_TASK_TRANSITION")
            self.assertEqual(pulse["supervision_projection"]["activity_state"], "ACTIVE_VISIBLE_TASKS")
            self.assertEqual(pulse["supervision_projection"]["user_signal"], "visible-child-tasks")
            self.assertEqual(len(pulse["active_visible_tasks"]), 2)
            self.assertFalse(pulse["requires_model_wake"])
            self.assertFalse(pulse["may_pause_or_delete_watcher"])

    def test_active_phase_without_verified_visible_thread_is_escalated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            run.analyze()
            run.dispatch_pair()
            for phase_key in ("run:T-1:implementation:1", "run:T-1:acceptance:1"):
                self.assertEqual(
                    run.apply(
                        "PHASE_LAUNCH_OBSERVED",
                        phase_key=phase_key,
                        launch_state="QUEUED",
                        client_thread_id=f"client-{phase_key}",
                    ),
                    0,
                )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    train_controller.heartbeat(argparse.Namespace(state=run.path)),
                    0,
                )
            pulse = json.loads(output.getvalue())
            self.assertEqual(pulse["heartbeat_decision"], "ESCALATE_VISIBILITY_GAP")
            self.assertTrue(pulse["requires_model_wake"])
            self.assertEqual(pulse["active_visible_tasks"], [])
            self.assertEqual(len(pulse["active_unverified_or_hidden_phases"]), 2)

    def test_phase_needing_input_cannot_become_a_silent_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            run.confirm()
            run.analyze()
            run.dispatch_pair()
            run.materialize("run:T-1:implementation:1", "thread-impl")
            run.materialize("run:T-1:acceptance:1", "thread-tests")
            self.assertEqual(
                run.apply(
                    "PHASE_TERMINATED",
                    phase_key="run:T-1:implementation:1",
                    envelope={
                        "phase_key": "run:T-1:implementation:1",
                        "phase_status": "needs_input",
                        "actual_model": "gpt-5.6-terra",
                        "actual_reasoning_effort": "medium",
                        "result_summary": "A product choice is missing.",
                        "artifacts": {"branch": "codex/t-1"},
                        "tests_and_checks": ["no code changed"],
                        "residual_risks": "Cannot choose safely.",
                        "requested_or_recommended_next_action": "ask user",
                        "files_modified": "none",
                        "usage": {"measurement": "complete", "total_tokens": 50},
                        "input_request": {
                            "gate_id": "T-1:phase-input:1",
                            "revision": "phase-input-1",
                            "question": "Choose A or B?",
                            "reason": "Both are valid product behaviors.",
                            "blocked_scope": "implementation phase",
                            "continuing_scope": "acceptance authoring continues",
                            "accepted_replies": ["A", "B"],
                        },
                    },
                ),
                0,
            )
            self.assertEqual(train_controller.next_actions(run.state())[0]["action"], "ANNOUNCE_HUMAN_GATE")

    def test_finalization_is_automatic_and_cannot_be_skipped_on_yield(self) -> None:
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
            self.assertEqual(train_controller.next_actions(run.state())[0]["action"], "START_FINALIZATION")
            check_args = argparse.Namespace(state=run.path, mode="yield")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(train_controller.check(check_args), 2)

    def test_final_findings_cannot_be_reconciled_without_github_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            self.reach_final_review(run)
            self.assertEqual(
                run.apply(
                    "FINAL_FINDINGS_RECONCILED",
                    head_commit="train-sha",
                    feedback_snapshot_id="missing",
                    ledger_status="complete",
                    sources_dispositioned=["codex", "ci", "copilot", "human"],
                    finding_dispositions=[],
                    remaining_unresolved_thread_ids=[],
                    blocking_findings=[],
                ),
                2,
            )

    def test_every_copilot_comment_requires_an_explicit_disposition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            self.reach_final_review(run)
            self.assertEqual(
                run.apply(
                    "FINAL_FEEDBACK_COLLECTION_STARTED",
                    collection_id="feedback-1",
                    head_commit="train-sha",
                    started_at="2026-08-01T10:00:00+00:00",
                    deadline_at="2026-08-01T10:15:00+00:00",
                    expected_sources=["codex", "ci", "copilot", "human"],
                ),
                0,
            )
            self.assertEqual(
                run.apply(
                    "FINAL_FEEDBACK_SNAPSHOT_RECORDED",
                    snapshot_id="snapshot-1",
                    collection_id="feedback-1",
                    head_commit="train-sha",
                    collected_at="2026-08-01T10:15:00+00:00",
                    ci_status="passed",
                    copilot_status="received",
                    source_counts={"codex": 0, "ci": 0, "copilot": 1, "human": 0},
                    source_findings=[{"finding_id": "copilot-1", "source": "copilot"}],
                    unresolved_thread_ids=["thread-copilot-1"],
                    evidence_reference="logs/github-feedback.json",
                ),
                0,
            )
            base_fields = {
                "head_commit": "train-sha",
                "feedback_snapshot_id": "snapshot-1",
                "ledger_status": "complete",
                "sources_dispositioned": ["codex", "ci", "copilot", "human"],
                "remaining_unresolved_thread_ids": [],
                "blocking_findings": [],
            }
            self.assertEqual(
                run.apply("FINAL_FINDINGS_RECONCILED", finding_dispositions=[], **base_fields),
                2,
            )
            self.assertEqual(
                run.apply(
                    "FINAL_FINDINGS_RECONCILED",
                    finding_dispositions=[{
                        "finding_id": "copilot-1",
                        "disposition": "accepted-fixed",
                        "blocking": False,
                        "remediation_status": "fixed",
                        "verification": "regression test passed",
                    }],
                    **base_fields,
                ),
                0,
            )

    def test_copilot_timeout_cannot_be_declared_before_collection_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            self.reach_final_review(run)
            self.assertEqual(
                run.apply(
                    "FINAL_FEEDBACK_COLLECTION_STARTED",
                    collection_id="feedback-1",
                    head_commit="train-sha",
                    started_at="2026-08-01T10:00:00+00:00",
                    deadline_at="2026-08-01T10:15:00+00:00",
                    expected_sources=["codex", "ci", "copilot", "human"],
                ),
                0,
            )
            self.assertEqual(
                run.apply(
                    "FINAL_FEEDBACK_SNAPSHOT_RECORDED",
                    snapshot_id="snapshot-1",
                    collection_id="feedback-1",
                    head_commit="train-sha",
                    collected_at="2026-08-01T10:05:00+00:00",
                    ci_status="passed",
                    copilot_status="timed_out",
                    source_counts={"codex": 0, "ci": 0, "copilot": 0, "human": 0},
                    source_findings=[],
                    unresolved_thread_ids=[],
                    evidence_reference="logs/github-feedback.json",
                ),
                2,
            )

    def test_copilot_unavailable_cannot_bypass_feedback_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            self.reach_final_review(run)
            self.assertEqual(
                run.apply(
                    "FINAL_FEEDBACK_COLLECTION_STARTED",
                    collection_id="feedback-1",
                    head_commit="train-sha",
                    started_at="2026-08-01T10:00:00+00:00",
                    deadline_at="2026-08-01T10:15:00+00:00",
                    expected_sources=["codex", "ci", "copilot", "human"],
                ),
                0,
            )
            self.assertEqual(
                run.apply(
                    "FINAL_FEEDBACK_SNAPSHOT_RECORDED",
                    snapshot_id="snapshot-1",
                    collection_id="feedback-1",
                    head_commit="train-sha",
                    collected_at="2026-08-01T10:05:00+00:00",
                    ci_status="passed",
                    copilot_status="unavailable",
                    copilot_status_evidence="GitHub API returned no review yet",
                    source_counts={"codex": 0, "ci": 0, "copilot": 0, "human": 0},
                    source_findings=[],
                    unresolved_thread_ids=[],
                    evidence_reference="logs/github-feedback.json",
                ),
                2,
            )

    def test_terminal_copilot_quota_with_user_override_can_close_feedback_early(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            self.reach_final_review(run)
            self.assertEqual(
                run.apply(
                    "FINAL_FEEDBACK_COLLECTION_STARTED",
                    collection_id="feedback-1",
                    head_commit="train-sha",
                    started_at="2026-08-01T10:00:00+00:00",
                    deadline_at="2026-08-01T10:15:00+00:00",
                    expected_sources=["codex", "ci", "copilot", "human"],
                ),
                0,
            )
            self.assertEqual(
                run.apply(
                    "FINAL_FEEDBACK_SNAPSHOT_RECORDED",
                    snapshot_id="snapshot-1",
                    collection_id="feedback-1",
                    head_commit="train-sha",
                    collected_at="2026-08-01T10:05:00+00:00",
                    ci_status="passed",
                    copilot_status="unavailable",
                    terminal_unavailability_kind="quota_exhausted",
                    copilot_status_evidence="GitHub Copilot returned an explicit quota-exhausted review response.",
                    user_override_reference="main-thread:user-explicitly-skipped-quota-wait",
                    source_counts={"codex": 0, "ci": 0, "copilot": 0, "human": 0},
                    source_findings=[],
                    unresolved_thread_ids=[],
                    evidence_reference="logs/github-feedback.json",
                ),
                0,
            )

    def test_new_final_remediation_cycle_clears_prior_cycle_pr_and_merge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            self.reach_final_review(run)
            self.assertEqual(
                run.apply(
                    "FINAL_FEEDBACK_COLLECTION_STARTED",
                    collection_id="feedback-1",
                    head_commit="train-sha",
                    started_at="2026-08-01T10:00:00+00:00",
                    deadline_at="2026-08-01T10:10:00+00:00",
                    expected_sources=["codex", "ci", "copilot", "human"],
                ),
                0,
            )
            self.assertEqual(
                run.apply(
                    "FINAL_FEEDBACK_SNAPSHOT_RECORDED",
                    snapshot_id="snapshot-1",
                    collection_id="feedback-1",
                    head_commit="train-sha",
                    collected_at="2026-08-01T10:10:00+00:00",
                    ci_status="passed",
                    copilot_status="received",
                    source_counts={"codex": 1, "ci": 0, "copilot": 0, "human": 0},
                    source_findings=[{"finding_id": "finding-1", "source": "codex"}],
                    unresolved_thread_ids=[],
                    evidence_reference="logs/github-feedback.json",
                ),
                0,
            )
            self.assertEqual(
                run.apply(
                    "FINAL_FINDINGS_RECONCILED",
                    head_commit="train-sha",
                    feedback_snapshot_id="snapshot-1",
                    ledger_status="complete",
                    sources_dispositioned=["codex", "ci", "copilot", "human"],
                    finding_dispositions=[{
                        "finding_id": "finding-1",
                        "disposition": "accepted-deferred",
                        "blocking": True,
                        "remediation_status": "pending",
                        "verification": "regression required",
                    }],
                    remaining_unresolved_thread_ids=[],
                    blocking_findings=["finding-1"],
                ),
                0,
            )
            state = run.state()
            final = state["procedure"]["finalization"]
            final["remediation_cycles"] = 1
            final["remediation_pull_request"] = {"url": "https://example.invalid/pr/old"}
            final["remediation_merge"] = {"merge_commit": "old-merge"}
            run_registry.save_json(run.path, state)
            self.assertEqual(
                run.apply(
                    "FINAL_REMEDIATION_DISPATCHED",
                    phase_key="run:run:final-remediation:2",
                    base_commit="train-sha",
                    branch="codex/final-remediation-2",
                    criticality="LOW",
                    complexity="LOW",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    routing_conformance="conformant",
                    scope_conformance="within-authorized-scope",
                    context_packet=run.context_packet("train-sha", "train-sha"),
                ),
                0,
            )
            final = run.state()["procedure"]["finalization"]
            self.assertNotIn("remediation_pull_request", final)
            self.assertNotIn("remediation_merge", final)

    def test_same_event_id_is_idempotent_even_with_old_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = self.harness(directory)
            event = {
                "event_id": "stable-event",
                "type": "ORCHESTRATOR_CONFIRMED",
                **run.orchestrator_confirmation_fields(),
            }
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

    def test_final_remediation_merge_permit_uses_completed_phase_and_train_pr(self) -> None:
        state = {
            "run_identity": {"train_branch": "codex/train-test"},
            "procedure": {
                "approval_mode": "auto-merge",
                "phases": {
                    "run:final:remediation:1": {
                        "kind": "final_remediation",
                        "launch_state": "COMPLETED",
                        "branch": "codex/final-remediation-1",
                        "base": "old-train-sha",
                    }
                },
                "finalization": {
                    "status": "AWAITING_FINAL_PR_UPDATE",
                    "remediation_pull_request": {
                        "url": "https://example.invalid/pull/2",
                        "base_branch": "codex/train-test",
                        "base_commit": "old-train-sha",
                        "head_branch": "codex/final-remediation-1",
                        "head_commit": "remediation-sha",
                        "is_draft": False,
                    },
                },
            },
        }
        self.assertEqual(
            train_controller.merge_permit_issues(
                state,
                action="final-remediation",
                ticket_id=None,
                head_commit="remediation-sha",
            ),
            [],
        )
        state["procedure"]["finalization"]["remediation_pull_request"]["head_commit"] = "other-sha"
        self.assertIn(
            "final remediation pull-request head differs from requested merge head",
            train_controller.merge_permit_issues(
                state,
                action="final-remediation",
                ticket_id=None,
                head_commit="remediation-sha",
            ),
        )

    def test_live_github_gate_rejects_pending_final_remediation_ci(self) -> None:
        issues = merge_pull_request.github_gate_issues(
            {
                "state": "OPEN",
                "isDraft": False,
                "headRefOid": "remediation-sha",
                "baseRefName": "codex/train-test",
                "headRefName": "codex/final-remediation-1",
                "statusCheckRollup": [{
                    "name": "quality",
                    "status": "IN_PROGRESS",
                    "conclusion": None,
                }],
            },
            expected_head="remediation-sha",
            expected_base="codex/train-test",
            expected_head_branch="codex/final-remediation-1",
            ci_not_configured=False,
        )
        self.assertTrue(any("not complete" in issue for issue in issues))

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
                    orchestration_metrics_ready=True,
                    orchestration_metrics_status="complete",
                    orchestration_metrics_reference="reports/dry-run-orchestration-metrics.json",
                    orchestration_metrics_sha256="e" * 64,
                    ledger_reference="reports/dry-run-token-ledger.json",
                    ledger_sha256="d" * 64,
                    authoritative_phase_count=2,
                    measured_phase_count=2,
                    orchestrator_session_included=True,
                    hidden_sessions_reconciled=True,
                    unmeasured_phase_keys=[],
                    unmeasured_session_ids=[],
                    task_inventory_requested_count=1,
                    task_inventory_terminal_count=1,
                    usage_matrix_ready=True,
                    usage_matrix_reference="reports/dry-run-token-matrix.md",
                    usage_matrix_sha256="f" * 64,
                    usage_matrix_status="complete",
                    usage_matrix_ticket_ids=["T-1"],
                    usage_matrix_ticket_phase_columns=list(train_controller.USAGE_TICKET_PHASE_COLUMNS),
                    usage_matrix_transverse_task_ids=[
                        "phase:run:run:triage:1",
                        "run:dependency-consolidation",
                        "run:orchestration",
                        "run:usage-reporting",
                    ],
                    usage_matrix_unreported_cell_count=0,
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
                    base_commit="base-sha",
                    head_branch="codex/train-test",
                    head_commit="train-sha",
                    is_draft=False,
                ),
                0,
            )
            self.assertEqual(
                run.apply(
                    "FINAL_VERIFICATION_RECORDED",
                    status="passed",
                    head_commit="train-sha",
                    evidence_reference="logs/final.json",
                    **run.deterministic_verification_fields(),
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
                    reasoning_effort="medium",
                    routing_conformance="conformant",
                    ticket_floor_evidence=[{
                        "ticket_id": "T-1",
                        "applies": False,
                        "reviewed_head": "ticket-sha",
                        "reason": "Exact ticket review remains valid and is outside the integration delta.",
                        "review_reused": True,
                    }],
                    reasoning_authorized=True,
                    context_packet=run.context_packet("base-sha", "train-sha"),
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
                    reasoning_effort="medium",
                    envelope={
                        "phase_key": "run:run:final-review:1",
                        "phase_status": "completed",
                        "actual_model": "gpt-5.6-terra",
                        "actual_reasoning_effort": "medium",
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
                    "FINAL_FEEDBACK_COLLECTION_STARTED",
                    collection_id="feedback-1",
                    head_commit="train-sha",
                    started_at="2026-08-01T10:00:00+00:00",
                    deadline_at="2026-08-01T10:15:00+00:00",
                    expected_sources=["codex", "ci", "copilot", "human"],
                ),
                0,
            )
            self.assertEqual(
                run.apply(
                    "FINAL_FEEDBACK_SNAPSHOT_RECORDED",
                    snapshot_id="snapshot-1",
                    collection_id="feedback-1",
                    head_commit="train-sha",
                    collected_at="2026-08-01T10:15:00+00:00",
                    ci_status="passed",
                    copilot_status="received",
                    source_counts={"codex": 0, "ci": 0, "copilot": 0, "human": 0},
                    source_findings=[],
                    unresolved_thread_ids=[],
                    evidence_reference="logs/github-feedback.json",
                ),
                0,
            )
            self.assertEqual(
                run.apply(
                    "FINAL_FINDINGS_RECONCILED",
                    head_commit="train-sha",
                    feedback_snapshot_id="snapshot-1",
                    ledger_status="complete",
                    sources_dispositioned=["codex", "ci", "copilot", "human"],
                    finding_dispositions=[],
                    remaining_unresolved_thread_ids=[],
                    blocking_findings=[],
                ),
                0,
            )
            self.assertEqual(
                run.apply(
                    "FINAL_EVIDENCE_RECORDED",
                    feedback_snapshot_id="snapshot-1",
                    ci_status="passed",
                    copilot_status="received",
                    finding_ledger_status="complete",
                    token_reporting_status="complete",
                    session_usage_ledger_ready=True,
                    verification_summary_ready=True,
                    manual_validation_summary_ready=True,
                    attention_points_summary_ready=True,
                    task_inventory_ready=True,
                    completion_report_ready=True,
                    orchestration_metrics_ready=True,
                    orchestration_metrics_status="complete",
                    orchestration_metrics_reference="reports/orchestration-metrics.json",
                    orchestration_metrics_sha256="e" * 64,
                    ledger_reference="reports/token-ledger.json",
                    ledger_sha256="c" * 64,
                    authoritative_phase_count=5,
                    measured_phase_count=5,
                    orchestrator_session_included=True,
                    hidden_sessions_reconciled=True,
                    unmeasured_phase_keys=[],
                    unmeasured_session_ids=[],
                    task_inventory_requested_count=1,
                    task_inventory_terminal_count=1,
                    usage_matrix_ready=True,
                    usage_matrix_reference="reports/final-token-matrix.md",
                    usage_matrix_sha256="f" * 64,
                    usage_matrix_status="complete",
                    usage_matrix_ticket_ids=["T-1"],
                    usage_matrix_ticket_phase_columns=list(train_controller.USAGE_TICKET_PHASE_COLUMNS),
                    usage_matrix_transverse_task_ids=[
                        "phase:run:run:final-review:1",
                        "phase:run:run:triage:1",
                        "run:dependency-consolidation",
                        "run:final-verification",
                        "run:github-feedback",
                        "run:orchestration",
                        "run:usage-reporting",
                    ],
                    usage_matrix_unreported_cell_count=0,
                ),
                0,
            )
            self.assertEqual(run.apply("RUN_COMPLETED"), 0)
            self.assertEqual(run.state()["procedure"]["run_status"], "COMPLETED")
            self.assertEqual(
                run.apply(
                    "FINAL_BASE_MERGE_AUTHORIZED",
                    authorization_id="auth-1",
                    head_commit="train-sha",
                    pull_request_url="https://example.invalid/pr/final",
                    user_decision_reference="thread-message-1",
                    authorized_at="2026-08-01T10:20:00+00:00",
                ),
                0,
            )
            self.assertEqual(
                train_controller.merge_permit_issues(
                    run.state(), action="final", ticket_id=None, head_commit="train-sha"
                ),
                [],
            )
            self.assertEqual(
                run.apply(
                    "FINAL_BASE_MERGED",
                    head_commit="train-sha",
                    merge_commit="base-merge-sha",
                    merged_at="2026-08-01T10:21:00+00:00",
                ),
                0,
            )
            self.assertEqual(run.state()["procedure"]["finalization"]["status"], "DELIVERED_IN_BASE")


if __name__ == "__main__":
    unittest.main()
