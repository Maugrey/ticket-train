"""Real controller tests of one-shot launch, interruption and duplicate safety."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import context_packet
import phase_dispatch as dispatch
import run_registry
import train_controller as controller
from test_train_controller import Harness


class PhaseDispatchTests(unittest.TestCase):
    def setup_run(self, root):
        run = Harness(root)
        run.confirm()
        run.analyze(criticality="HIGH", complexity="HIGH")
        payload = root / "payload.json"
        run_registry.save_json(payload, {"task": "Check the supplied implementation contract only"})
        descriptor = context_packet.build_packet(payload, root / "context.json", "profile-1", "base-sha", "base-sha")
        spec = {"schema_version": 1, "state": str(run.path), "owner": "thread-main", "phase_key": "run:T-1:plan:1",
                "title": "T-1 contract validation", "target": {"type": "project", "projectId": "project-1", "environment": {"type": "local"}},
                "intent_event": {"event_id": "plan-intent-1", "type": "PHASE_DISPATCHED", "kind": "plan_contract_validation",
                                 "ticket_id": "T-1", "phase_key": "run:T-1:plan:1", "base_commit": "base-sha",
                                 "model": "gpt-5.6-terra", "reasoning_effort": "medium", "routing_conformance": "conformant",
                                 "contract_validation_profile": "standard", "unity_requirement": "none", "context_packet": descriptor}}
        path = root / "dispatch-spec.json"
        run_registry.save_json(path, spec)
        return run, path, spec

    def receipt(self, prepared, task="validator"):
        raw = {"content": [{"type": "text", "text": json.dumps({"threadId": task, "hostId": "local"})}], "isError": False}
        run_registry.save_json(Path(prepared["receipt_path"]), raw)

    def observation(self, prepared, task="validator", status="inProgress", suffix="1"):
        path = Path(prepared["attempt_directory"]) / f"observation-{suffix}.json"
        run_registry.save_json(path, {"polls": [{"thread": {"id": task, "hostId": "local", "status": {"type": "active" if status == "inProgress" else "idle"}},
                                               "latestTurn": {"id": "turn-1", "status": status}}]})
        return path

    def test_real_controller_launch_and_observation_then_idempotent_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, spec, _ = self.setup_run(Path(tmp))
            prepared = dispatch.begin(spec)
            self.assertTrue(prepared["may_create"])
            self.assertEqual(prepared["tool_request"]["model"], "gpt-5.6-terra")
            self.assertIn("thread-main", prepared["tool_request"]["prompt"])
            self.assertEqual(run.state()["procedure"]["phases"]["run:T-1:plan:1"]["launch_state"], "LAUNCH_UNKNOWN")
            self.receipt(prepared)
            result = dispatch.record(spec, self.observation(prepared))
            dispatch.observe(spec, self.observation(prepared, suffix="fresh"))
            self.assertEqual(result["thread_id"], "validator")
            phase = run.state()["procedure"]["phases"]["run:T-1:plan:1"]
            self.assertEqual(phase["runtime_observation"]["runtime_status"], "running")
            self.assertTrue(phase["visibility_verified"])
            self.assertFalse(dispatch.begin(spec)["may_create"])

    def test_armed_without_receipt_never_recreates(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, spec, _ = self.setup_run(Path(tmp))
            dispatch.begin(spec)
            with self.assertRaisesRegex(ValueError, "NEVER create again"):
                dispatch.begin(spec)

    def test_receipt_recovery_does_not_create_or_add_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, spec, _ = self.setup_run(Path(tmp))
            prepared = dispatch.begin(spec)
            self.receipt(prepared)
            count = len(run.state()["procedure"]["phases"])
            recovered = dispatch.begin(spec)
            self.assertEqual(recovered["status"], "recover-receipt")
            self.assertFalse(recovered["may_create"])
            dispatch.record(spec, self.observation(prepared))
            self.assertEqual(len(run.state()["procedure"]["phases"]), count)

    def test_interruption_after_receipt_apply_reuses_exact_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, spec, _ = self.setup_run(Path(tmp))
            prepared = dispatch.begin(spec)
            self.receipt(prepared)
            original = dispatch.run_registry.save_json
            def fail_final_write(path, value):
                if value.get("status") == "RECORDED":
                    raise OSError("interrupt")
                return original(path, value)
            with patch.object(dispatch.run_registry, "save_json", side_effect=fail_final_write):
                with self.assertRaises(OSError):
                    dispatch.record(spec, self.observation(prepared))
            # A fresh observation has a different path, but the already-applied
            # receipt event must retain its original payload for idempotency.
            result = dispatch.record(spec, self.observation(prepared, suffix="2"))
            self.assertEqual(result["status"], "recorded")

    def test_wrong_task_observation_and_missing_observation_reject(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, spec, _ = self.setup_run(Path(tmp))
            prepared = dispatch.begin(spec)
            self.receipt(prepared)
            for observation in (None, self.observation(prepared, task="another-task")):
                with self.assertRaises(ValueError):
                    dispatch.record(spec, observation)
            self.assertEqual(run.state()["procedure"]["phases"]["run:T-1:plan:1"]["launch_state"], "LAUNCH_UNKNOWN")

    def test_task_finishes_before_capture_is_collected_not_redispatched(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, spec, _ = self.setup_run(Path(tmp))
            prepared = dispatch.begin(spec)
            self.receipt(prepared)
            dispatch.record(spec, self.observation(prepared, status="completed"))
            result = dispatch.observe(spec, self.observation(prepared, status="completed", suffix="fresh"))
            self.assertEqual(result["next_actions"][0]["action"], "COLLECT_OBSERVED_PHASE_RESULTS")
            self.assertFalse(result["turn_control"]["may_end_turn"])

    def test_queued_is_not_a_running_or_repeatable_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, spec, _ = self.setup_run(Path(tmp))
            prepared = dispatch.begin(spec)
            run_registry.save_json(Path(prepared["receipt_path"]), {"clientThreadId": "client-1"})
            result = dispatch.record(spec)
            self.assertIsNone(result["thread_id"])
            self.assertEqual(run.state()["procedure"]["phases"]["run:T-1:plan:1"]["launch_state"], "QUEUED")
            self.assertFalse(dispatch.begin(spec)["may_create"])

    def test_wrong_owner_model_gate_and_changed_packet_rejected(self):
        for mutation in ("owner", "model", "gate", "packet"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                run, path, spec = self.setup_run(Path(tmp))
                if mutation == "owner":
                    spec["owner"] = "helper-not-owner"
                elif mutation == "model":
                    spec["intent_event"]["model"] = "gpt-5.6-sol"
                elif mutation == "gate":
                    self.assertEqual(run.apply("HUMAN_INPUT_REQUESTED", ticket_id="T-1", gate_id="q", revision="r",
                                               question="Choose", reason="Missing", blocked_scope="T-1", continuing_scope="none", accepted_replies=["A"]), 0)
                else:
                    Path(spec["intent_event"]["context_packet"]["reference"]).write_text("{}")
                run_registry.save_json(path, spec)
                with self.assertRaises(ValueError):
                    dispatch.begin(path)
                self.assertEqual(list((run.path.parent / "dispatches").glob("*/creation-receipt.json")), [])


if __name__ == "__main__":
    unittest.main()
