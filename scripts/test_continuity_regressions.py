"""Behavioral regressions for lost work, premature yields, and captured results."""
import argparse
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import control_plane_runner as runner
import run_registry
import train_controller as controller
import verification_adapter
import verification_runner
import train_supervisor
from test_train_controller import Harness


class ContinuityRegressions(unittest.TestCase):
    def test_local_candidate_discovery_handles_desktop_creation_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stamp = datetime.now(timezone.utc)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "run_id": "run-example", "orchestrator_lease": {"owner_thread_id": "owner"},
                "procedure": {"phases": {"phase-1": {"created_at": stamp.isoformat()}}},
            }), encoding="utf-8")
            sessions = root / "sessions"
            day = sessions / stamp.strftime("%Y/%m/%d")
            day.mkdir(parents=True)
            prompt = "<codex_delegation><input>run-example phase-1</input></codex_delegation>"
            for task_id, kind, content in (("child", "create_thread", prompt), ("echo", "exec_command", prompt), ("prefix", "create_thread", prompt.replace("phase-1", "phase-10"))):
                records = [
                    {"type": "session_meta", "payload": {"id": task_id, "timestamp": stamp.isoformat(), "cwd": "checkout"}},
                    {"type": "response_item", "payload": {"role": "user", "content": [{"text": "Environment instructions"}]}},
                    {"type": "response_item", "payload": {"type": "function_call_output", "name": kind, "output": content}},
                ]
                (day / f"rollout-{task_id}.jsonl").write_text("\n".join(json.dumps(x) for x in records), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                train_supervisor.phase_candidates(argparse.Namespace(state=manifest, phase_key="phase-1", sessions_root=sessions))
            result = json.loads(output.getvalue())
            self.assertEqual([x["thread_id"] for x in result["candidates"]], ["child"])
            self.assertFalse(result["may_create_replacement"])
            self.assertFalse(result["candidates"][0]["visibility_verified"])

    def step(self, run):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            runner.step(argparse.Namespace(state=run.path, output_dir=None, owner_thread_id="thread-main", owner_epoch=run.state()["orchestrator_lease"].get("epoch")))
        return json.loads(stream.getvalue())

    def test_repeated_verification_and_remediation_actions_are_not_waits(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Harness(Path(directory))
            run.confirm()
            run.analyze()
            for status in ("AWAITING_VERIFICATION", "NEEDS_REMEDIATION"):
                state = run.state()
                state["procedure"]["tickets"]["T-1"]["status"] = status
                run_registry.save_json(run.path, state)
                first, second = self.step(run), self.step(run)
                self.assertEqual(second["status"], "action-pending")
                self.assertEqual(second["packet_reference"], first["packet_reference"])
                self.assertNotEqual(second["wake_kind"], "NO_MODEL_WAKE")
                self.assertFalse(second["turn_control"]["may_end_turn"])

    def test_real_unchanged_foreground_wait_is_quiet_but_cannot_end_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Harness(Path(directory))
            run.confirm()
            run.analyze()
            run.dispatch_pair()
            run.materialize("run:T-1:implementation:1", "impl")
            run.materialize("run:T-1:acceptance:1", "tests")
            self.step(run)
            repeated = self.step(run)
            self.assertEqual(repeated["status"], "unchanged-suppressed")
            self.assertEqual(repeated["wake_kind"], "NO_MODEL_WAKE")
            self.assertFalse(repeated["turn_control"]["may_end_turn"])

    def test_packet_preserves_unity_route_and_provided_answer(self):
        for action in (
            {"action": "ACQUIRE_UNITY_SLOT_DETERMINISTICALLY", "owner_key": "phase-1", "expected_head": "sha", "requirement": "editor-read", "max_editors": 3},
            {"action": "RESUME_VISIBLE_PHASE_WITH_INPUT", "provided_input": {"response_summary": "Approved value", "response_artifact": "decision.json"}},
            {"action": "RECORD_ANALYSIS_ROUTE_VALIDATION_DISPATCH_INTENT", "required_model": "gpt-5.6-sol", "required_reasoning_effort": "high", "analysis_revision": "a2", "retry_of_phase_key": "v1"},
        ):
            compact = runner.compact_action(action)
            for key, value in action.items():
                self.assertEqual(compact[key], value)

    def test_queued_phase_cannot_yield_using_a_callback_that_does_not_exist_yet(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Harness(Path(directory))
            run.confirm()
            run.analyze()
            run.dispatch_pair()
            run.materialize("run:T-1:implementation:1", "impl")
            run.apply("PHASE_LAUNCH_OBSERVED", phase_key="run:T-1:acceptance:1", launch_state="QUEUED", client_thread_id="queued-id")
            run.apply("SUPERVISION_CONFIGURED", mode="EVENT_CALLBACK", callback_verified=True, callback_target_thread_id="thread-main")
            self.assertFalse(controller.turn_control(run.state())["may_end_turn"])

    def test_independent_verification_is_not_hidden_by_another_gate_or_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Harness(Path(directory))
            run.confirm()
            run.analyze()
            state = run.state()
            proc = state["procedure"]
            proc["tickets"]["T-2"] = copy.deepcopy(proc["tickets"]["T-1"])
            proc["execution_order"] = ["T-1", "T-2"]
            proc["tickets"]["T-2"]["status"] = "AWAITING_VERIFICATION"
            proc["tickets"]["T-1"]["status"] = "AWAITING_REQUIRED_INPUT"
            proc["human_gates"]["g"] = {"status": "PENDING_ANNOUNCED", "gate_id": "g"}
            state["pending_human_action"] = {"gate_id": "g", "notification_status": "ANNOUNCED"}
            names = {a["action"] for a in controller.next_actions(state)}
            self.assertIn("RUN_DETERMINISTIC_TICKET_VERIFICATION", names)
            self.assertIn("AWAIT_HUMAN_GATE", names)
            proc["phases"]["active"] = {"phase_key": "active", "kind": "remediation", "ticket_id": "T-1", "launch_state": "RUNNING"}
            names = {a["action"] for a in controller.next_actions(state)}
            self.assertIn("RUN_DETERMINISTIC_TICKET_VERIFICATION", names)
            self.assertIn("WAIT_FOR_PHASE_TRANSITION", names)

    def test_sequential_ticket_waits_for_merge_not_just_coding_end(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Harness(Path(directory))
            run.confirm()
            run.analyze()
            state = run.state()
            proc = state["procedure"]
            proc["tickets"]["T-2"] = copy.deepcopy(proc["tickets"]["T-1"])
            proc["execution_order"] = ["T-1", "T-2"]
            for status in ("READY_FOR_IMPLEMENTATION", "AWAITING_VERIFICATION", "NEEDS_REMEDIATION", "AWAITING_PRE_MERGE_APPROVAL", "READY_TO_MERGE"):
                proc["tickets"]["T-1"]["status"] = status
                self.assertFalse(controller.execution_schedule_satisfied(proc, "T-2"), status)
            proc["tickets"]["T-1"]["status"] = "MERGED_INTO_TRAIN"
            self.assertTrue(controller.execution_schedule_satisfied(proc, "T-2"))

    def test_event_cannot_bypass_sequential_schedule(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Harness(Path(directory))
            run.confirm()
            run.analyze()
            state = run.state()
            proc = state["procedure"]
            proc["tickets"]["T-2"] = copy.deepcopy(proc["tickets"]["T-1"])
            proc["execution_order"] = ["T-1", "T-2"]
            proc["tickets"]["T-1"]["status"] = "AWAITING_VERIFICATION"
            run_registry.save_json(run.path, state)
            self.assertEqual(run.apply("EXECUTION_PAIR_DISPATCHED", ticket_id="T-2"), 2)
            self.assertEqual(run.state()["procedure"]["phases"], proc["phases"])

    def test_parallel_group_still_requires_disjoint_surfaces(self):
        a = {"status": "READY_FOR_IMPLEMENTATION", "schedule": {"mode": "parallel-safe", "parallel_group": "g"}, "collision_domains": ["a"]}
        b = {**a, "collision_domains": ["b"]}
        proc = {"tickets": {"T-1": a, "T-2": b}}
        self.assertTrue(controller.execution_schedule_satisfied(proc, "T-2"))
        b["collision_domains"] = ["a"]
        self.assertFalse(controller.execution_schedule_satisfied(proc, "T-2"))

    def test_dependency_can_run_before_earlier_listed_dependent(self):
        proc = {"tickets": {
            "T-1": {"status": "READY_FOR_IMPLEMENTATION", "hard_dependencies": ["T-2"]},
            "T-2": {"status": "READY_FOR_IMPLEMENTATION", "hard_dependencies": []},
        }}
        self.assertTrue(controller.execution_schedule_satisfied(proc, "T-2"))
        proc["tickets"]["T-2"]["hard_dependencies"] = ["T-3"]
        proc["tickets"]["T-3"] = {"status": "READY_FOR_IMPLEMENTATION"}
        self.assertTrue(controller.execution_schedule_satisfied(proc, "T-3"))

    def test_schedule_order_survives_sorted_manifest_keys(self):
        proc = {"execution_order": ["T-2", "T-1"], "tickets": {
            "T-1": {"status": "READY_FOR_IMPLEMENTATION"},
            "T-2": {"status": "READY_FOR_IMPLEMENTATION"},
        }}
        self.assertFalse(controller.execution_schedule_satisfied(proc, "T-1"))
        self.assertTrue(controller.execution_schedule_satisfied(proc, "T-2"))

    def test_priority_cannot_deadlock_a_later_prerequisite(self):
        proc = {"tickets": {
            "T-1": {"status": "READY_FOR_IMPLEMENTATION", "hard_dependencies": ["T-3"]},
            "T-2": {"status": "READY_FOR_IMPLEMENTATION"},
            "T-3": {"status": "READY_FOR_IMPLEMENTATION"},
        }}
        self.assertEqual(controller.ordered_execution_tickets(proc), ["T-3", "T-1", "T-2"])
        self.assertTrue(controller.execution_schedule_satisfied(proc, "T-3"))
        proc["tickets"]["T-3"]["hard_dependencies"] = ["T-1"]
        with self.assertRaisesRegex(ValueError, "cycle"):
            controller.ordered_execution_tickets(proc)


class VerificationAdapterRegressions(unittest.TestCase):
    def setUp(self):
        detached = patch.object(verification_runner, "run_detached_plan", side_effect=verification_runner.run_plan)
        detached.start()
        self.addCleanup(detached.stop)
        # These adapter tests use a synthetic Git head. Real filesystem and
        # interruption behavior is covered by the runtime integration tests.
        self.enterContext(patch.object(verification_runner, "worktree_fingerprint", return_value="fixture-state"))

    def test_execution_lock_excludes_duplicates_and_releases_on_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "execution.lock"
            with self.assertRaisesRegex(RuntimeError, "simulated"):
                with verification_adapter.execution_lock(path):
                    with self.assertRaisesRegex(ValueError, "locked"):
                        with verification_adapter.execution_lock(path):
                            self.fail("duplicate entered")
                    raise RuntimeError("simulated crash")
            with verification_adapter.execution_lock(path):
                pass

    def setup_run(self, root, exit_code=0):
        run = Harness(root)
        run.confirm()
        run.analyze()
        run.functional_ready()
        state = run.state()
        state["procedure"]["tickets"]["T-1"]["status"] = "AWAITING_VERIFICATION"
        run_registry.save_json(run.path, state)
        plan = root / "plan.json"
        plan.write_text(json.dumps({"schema_version": 1, "workdir": str(root), "expected_head": "ticket-sha", "commands": [
            {"id": "check", "argv": [sys.executable, "-c", f"print('bounded failure evidence'); raise SystemExit({exit_code})"]}
        ]}), encoding="utf-8")
        evidence = root / "evidence.json"
        evidence.write_text(json.dumps({
            "type": "VERIFICATION_RECORDED", "ticket_id": "T-1", "baseline_red_base": "base-sha",
            "independent_test_commit": "tests-sha", "environment_status": "not-applicable",
            "acceptance_coverage_status": "complete", "operational_change_applicable": False,
        }), encoding="utf-8")
        return run, argparse.Namespace(state=run.path, plan=plan, evidence=evidence, output=root / "result.json", logs_dir=root / "logs", owner="thread-main", owner_epoch=run.state()["orchestrator_lease"]["epoch"])

    def test_run_records_success_and_returns_successor_without_another_model_turn(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(verification_runner, "git_head", return_value="ticket-sha"):
            run, args = self.setup_run(Path(directory))
            result = verification_adapter.execute(args)
            self.assertEqual(result["status"], "recorded")
            self.assertEqual(run.state()["procedure"]["tickets"]["T-1"]["status"], "FUNCTIONAL_READY")
            self.assertFalse(result["next"]["turn_control"]["may_end_turn"])
            with patch.object(verification_runner, "run_detached_plan", side_effect=AssertionError("must not rerun")):
                self.assertEqual(verification_adapter.execute(args)["status"], "recorded")

    def test_failed_command_is_recorded_and_its_stdout_is_not_lost(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(verification_runner, "git_head", return_value="ticket-sha"):
            run, args = self.setup_run(Path(directory), exit_code=1)
            verification_adapter.execute(args)
            self.assertEqual(run.state()["procedure"]["tickets"]["T-1"]["status"], "VERIFICATION_FAILED")
            result = json.loads(args.output.read_text(encoding="utf-8"))
            self.assertIn("bounded failure evidence", result["command_results"][0]["error_excerpt"])

    def test_result_survives_recording_failure_and_retry_does_not_rerun_tests(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(verification_runner, "git_head", return_value="ticket-sha"):
            run, args = self.setup_run(Path(directory))
            with patch.object(controller, "apply_event", side_effect=ValueError("stale revision")):
                with self.assertRaises(ValueError):
                    verification_adapter.execute(args)
            self.assertTrue(args.output.exists())
            with patch.object(verification_runner, "run_detached_plan", side_effect=AssertionError("must not rerun")):
                self.assertEqual(verification_adapter.execute(args)["status"], "recorded")

    def test_reuse_rejects_changed_plan_or_checkout(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(verification_runner, "git_head", return_value="ticket-sha"):
            run, args = self.setup_run(Path(directory))
            verification_adapter.execute(args)
            with patch.object(verification_runner, "git_head", return_value="other-sha"):
                with self.assertRaisesRegex(ValueError, "head changed"):
                    verification_adapter.execute(args)
            args.plan.write_text(args.plan.read_text() + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "another/legacy plan"):
                verification_adapter.execute(args)

    def test_missing_executable_produces_durable_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(verification_runner, "git_head", return_value="ticket-sha"):
            run, args = self.setup_run(Path(directory))
            plan = json.loads(args.plan.read_text())
            plan["commands"][0]["argv"] = [str(Path(directory) / "missing-executable")]
            args.plan.write_text(json.dumps(plan), encoding="utf-8")
            verification_adapter.execute(args)
            self.assertEqual(run.state()["procedure"]["tickets"]["T-1"]["status"], "VERIFICATION_FAILED")

    def test_timeout_is_recordable_with_no_exit_code(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(verification_runner, "git_head", return_value="ticket-sha"):
            run, args = self.setup_run(Path(directory))
            plan = json.loads(args.plan.read_text())
            plan["commands"][0].update(argv=[sys.executable, "-c", "import time; time.sleep(5)"], timeout_seconds=1)
            args.plan.write_text(json.dumps(plan), encoding="utf-8")
            verification_adapter.execute(args)
            result = json.loads(args.output.read_text())
            self.assertEqual(result["command_results"][0]["status"], "timed_out")
            self.assertIsNone(result["command_results"][0]["exit_code"])
            self.assertEqual(run.state()["procedure"]["tickets"]["T-1"]["status"], "VERIFICATION_FAILED")


if __name__ == "__main__":
    unittest.main()
