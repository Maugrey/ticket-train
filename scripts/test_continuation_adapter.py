"""Regression tests for acknowledged-but-unresumed trains and ghost workers."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import continuation_adapter as adapter
import context_packet
import run_registry
import thread_runtime
import train_controller as controller
from test_train_controller import Harness


class ContinuationTests(unittest.TestCase):
    def running(self, root):
        run = Harness(root)
        run.confirm()
        run.analyze()
        run.dispatch_pair()
        run.materialize("run:T-1:implementation:1", "impl")
        run.materialize("run:T-1:acceptance:1", "tests")
        self.assertEqual(run.apply("SUPERVISION_CONFIGURED", mode="EVENT_CALLBACK",
                                  callback_verified=True, callback_target_thread_id="thread-main"), 0)
        return run

    def test_manifest_flags_alone_cannot_authorize_yield(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.running(Path(tmp))
            self.assertFalse(controller.turn_control(run.state())["may_end_turn"])
            self.assertEqual(controller.next_actions(run.state())[0]["action"], "OBSERVE_ACTIVE_PHASES")
            self.assertEqual(controller.supervision_projection(run.state())["runtime_confirmed_active_count"], 0)
            run.observe("impl")
            self.assertFalse(controller.turn_control(run.state())["may_end_turn"])
            run.observe("tests")
            self.assertTrue(controller.turn_control(run.state())["may_end_turn"])
            self.assertEqual(controller.supervision_projection(run.state())["runtime_confirmed_active_count"], 2)

    def test_finished_child_is_collected_not_relaunched_and_never_expires_to_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.running(Path(tmp))
            run.observe("impl", status="completed")
            state = run.state()
            state["procedure"]["phases"]["run:T-1:implementation:1"]["runtime_observation"]["observed_at"] = "2020-01-01T00:00:00Z"
            action = controller.next_actions(state)[0]
            self.assertEqual(action["action"], "COLLECT_OBSERVED_PHASE_RESULTS")
            self.assertFalse(action["may_relaunch"])
            self.assertFalse(controller.turn_control(state)["may_end_turn"])
            run.complete_phase("run:T-1:implementation:1", "gpt-5.6-terra", "medium")
            self.assertNotIn("COLLECT_OBSERVED_PHASE_RESULTS", [a["action"] for a in controller.next_actions(run.state())])

    def test_stale_or_owner_mismatched_observation_is_not_liveness(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.running(Path(tmp))
            run.observe("impl")
            run.observe("tests")
            for field, value in (("observed_at", (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()),
                                 ("owner_thread_id", "former-owner"), ("runtime_status", "unknown")):
                state = run.state()
                state["procedure"]["phases"]["run:T-1:implementation:1"]["runtime_observation"][field] = value
                self.assertFalse(controller.turn_control(state)["may_end_turn"])

    def test_resume_invalidates_old_running_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.running(Path(tmp))
            run.observe("impl")
            run.materialize("run:T-1:implementation:1", "impl")
            self.assertNotIn("runtime_observation", run.state()["procedure"]["phases"]["run:T-1:implementation:1"])

    def test_partial_tool_poll_never_invents_other_targets(self):
        raw = {"polls": [{"thread": {"id": "a", "status": {"type": "idle"}},
                          "latestTurn": {"id": "t", "status": "completed"}}]}
        wrapped = {"content": [{"type": "text", "text": json.dumps(raw)}]}
        self.assertEqual([x["thread_id"] for x in thread_runtime.parse_wait_result(wrapped)], ["a"])
        with self.assertRaises(ValueError):
            thread_runtime.parse_wait_result({"polls": [], "errors": [{"threadId": "b"}]})
        raw["polls"][0]["thread"]["status"] = {"type": "active", "activeFlags": ["waitingOnUserInput"]}
        self.assertEqual(thread_runtime.parse_wait_result(raw)[0]["runtime_status"], "needs_input")

    def test_snapshot_hash_and_owner_are_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.running(Path(tmp))
            source = Path(tmp) / "snapshot.json"
            source.write_text('{"polls": []}', encoding="utf-8")
            for owner, sha in (("other", hashlib.sha256(source.read_bytes()).hexdigest()), ("thread-main", "f" * 64)):
                self.assertEqual(run.apply("RUNTIME_OBSERVED", owner_thread_id=owner,
                                           snapshot_reference=str(source), snapshot_sha256=sha), 2)

    def ask(self, run):
        run.confirm()
        run.analyze()
        fields = dict(gate_id="input-1", revision="i1", blocked_scope="T-1", continuing_scope="none", accepted_replies=["Answer"])
        self.assertEqual(run.apply("HUMAN_INPUT_REQUESTED", ticket_id="T-1", question="Choose the value", reason="Unspecified", **fields), 0)
        self.assertEqual(run.apply("GATE_ANNOUNCED", decision_summary="Choose the value", evidence_summary="Not in source", **fields), 0)

    def test_reply_and_successor_are_one_restartable_adapter_operation(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Harness(Path(tmp))
            self.ask(run)
            event = Path(tmp) / "reply.json"
            event.write_text(json.dumps({"event_id": "reply-1", "type": "INPUT_PROVIDED", "gate_id": "input-1", "revision": "i1",
                                         "response_summary": "User chose A", "response_artifact": "owner:user-message-1"}), encoding="utf-8")
            args = argparse.Namespace(command="advance", state=run.path, owner="thread-main", owner_epoch=run.state()["orchestrator_lease"]["epoch"],
                                      expected_revision=run.state()["procedure"]["revision"], event=event)
            with patch.object(adapter.runner, "step", side_effect=OSError("simulated interruption")):
                with self.assertRaises(OSError):
                    adapter.advance(args)
            result = adapter.advance(args)
            self.assertEqual(result["event_status"], "duplicate-idempotent")
            self.assertFalse(result["turn_control"]["may_end_turn"])
            self.assertTrue(result["next_actions"])
            self.assertIsNone(run.state()["pending_human_action"])
            args.owner = "wrong-owner"
            with self.assertRaises(ValueError):
                adapter.advance(args)

    def test_user_only_wait_needs_no_poll_or_repeated_wake(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Harness(Path(tmp))
            self.ask(run)
            args = argparse.Namespace(command="advance", state=run.path, owner="thread-main", owner_epoch=run.state()["orchestrator_lease"]["epoch"],
                                      expected_revision=run.state()["procedure"]["revision"], event=None)
            result = adapter.advance(args)
            self.assertTrue(result["turn_control"]["may_end_turn"])
            self.assertEqual(result["wake_kind"], "NO_MODEL_WAKE")
            self.assertEqual(adapter.advance(args)["status"], "unchanged-suppressed")

    def test_answer_resumes_the_same_task_without_rerunning_its_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.running(Path(tmp))
            phase_key = "run:T-1:implementation:1"
            request = {"gate_id": "phase-question", "revision": "q1", "question": "Which value?", "reason": "Missing",
                       "blocked_scope": "T-1", "continuing_scope": "tests", "accepted_replies": ["A", "B"]}
            self.assertEqual(run.apply("PHASE_TERMINATED", phase_key=phase_key, envelope={
                "phase_key": phase_key, "phase_status": "needs_input", "actual_model": "gpt-5.6-terra",
                "actual_reasoning_effort": "medium", "result_summary": "Need value", "artifacts": {},
                "tests_and_checks": ["not run: waiting for value"], "residual_risks": "Missing value", "requested_or_recommended_next_action": "ask user",
                "files_modified": "none", "usage": {"measurement": "unavailable"}, "input_request": request}), 0)
            self.assertEqual(run.apply("GATE_ANNOUNCED", gate_id="phase-question", revision="q1",
                                      decision_summary="Choose value", evidence_summary="Unspecified", blocked_scope="T-1",
                                      continuing_scope="tests", accepted_replies=["A: preserve current behavior", "B: adopt the proposed behavior"]), 0)
            count = len(run.state()["procedure"]["phases"])
            event = Path(tmp) / "reply.json"
            event.write_text(json.dumps({"event_id": "phase-reply", "type": "INPUT_PROVIDED", "gate_id": "phase-question",
                                         "revision": "q1", "response_summary": "B", "response_artifact": "owner:reply"}), encoding="utf-8")
            result = adapter.advance(argparse.Namespace(command="advance", state=run.path, owner="thread-main", owner_epoch=run.state()["orchestrator_lease"]["epoch"],
                                                       expected_revision=run.state()["procedure"]["revision"], event=event))
            action = result["next_actions"][0]
            self.assertEqual(action["action"], "RESUME_VISIBLE_PHASE_WITH_INPUT")
            self.assertEqual(action["thread_id"], "impl")
            self.assertEqual(action["provided_input"]["summary"], "B")
            self.assertFalse(result["turn_control"]["may_end_turn"])
            self.assertEqual(run.apply("PHASE_RESUMED", phase_key=phase_key, thread_id="impl", visibility_verified=True), 0)
            self.assertNotIn("runtime_observation", run.state()["procedure"]["phases"][phase_key])
            self.assertEqual(len(run.state()["procedure"]["phases"]), count)



if __name__ == "__main__":
    unittest.main()
