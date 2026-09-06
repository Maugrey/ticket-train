"""Launch-to-result regression: collection needs evidence, never a new spec/task."""
import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import continuation_adapter as adapter
import phase_dispatch
import run_registry
import train_controller as controller
import test_phase_dispatch


class ContractCollectionTests(unittest.TestCase):
    def fixture(self, root, status="passed"):
        helper = test_phase_dispatch.PhaseDispatchTests()
        run, spec_path, spec = helper.setup_run(root)
        prepared = phase_dispatch.begin(spec_path)
        helper.receipt(prepared)
        phase_dispatch.record(spec_path, helper.observation(prepared))
        snapshot = helper.observation(prepared, status="completed", suffix="completed")
        descriptor = spec["intent_event"]["context_packet"]
        result = {
            "status": status, "ticket_id": "T-1", "phase_key": spec["phase_key"],
            "analysis_complexity": "HIGH", "residual_implementation_complexity": "HIGH",
            "verification_complexity": "HIGH", "complexity_reduction_evidence": ["No reduction"],
            "unresolved_implementation_difficulty": ["Existing integration work"],
            "checked_references": [{"reference": descriptor["reference"], "sha256": descriptor["sha256"]}],
            "findings": {"defects": [] if status == "passed" else ["Missing deterministic oracle"]},
            "tests_and_checks": ["Documentary validation only"], "residual_risks": "Runtime not yet tested",
            "files_modified": ["private result only"], "usage": {"measurement": "unavailable"},
            "recommended_next_action": "Record existing result", "actual_model": "unavailable-in-child",
        }
        path = run.path.parent / "result.json"
        run_registry.save_json(path, result)
        args = argparse.Namespace(command="collect-contract", state=run.path, owner="thread-main",
                                  expected_revision=run.state()["procedure"]["revision"],
                                  phase_key=spec["phase_key"], result=path, snapshot=snapshot)
        return run, args, result

    def test_completed_visible_contract_collected_in_one_revision_without_spec_or_new_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, args, _ = self.fixture(Path(tmp))
            before = run.state()
            result = adapter.collect_contract(args)
            state = run.state()
            self.assertEqual(state["procedure"]["revision"], before["procedure"]["revision"] + 1)
            self.assertEqual(set(state["procedure"]["phases"]), set(before["procedure"]["phases"]))
            value = state["procedure"]["phases"][args.phase_key]
            self.assertEqual(value["launch_state"], "COMPLETED")
            self.assertEqual(value["completion_envelope"]["actual_model"], "gpt-5.6-terra")
            self.assertEqual(state["procedure"]["tickets"]["T-1"]["plan_contract_validation"]["status"], "passed")
            self.assertFalse(result["turn_control"]["may_end_turn"])
            self.assertFalse(list(Path(tmp).rglob("*-collect.json")))
            self.assertEqual(adapter.collect_contract(args)["event_status"], "duplicate-idempotent")

    def test_failure_is_collected_and_requests_targeted_amendment_not_another_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, args, _ = self.fixture(Path(tmp), "failed")
            result = adapter.collect_contract(args)
            self.assertEqual(run.state()["procedure"]["tickets"]["T-1"]["status"], "NEEDS_CONTRACT_AMENDMENT")
            self.assertTrue(result["next_actions"])
            self.assertFalse(result["turn_control"]["may_end_turn"])

    def test_interruption_after_atomic_apply_replays_identical_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, args, _ = self.fixture(Path(tmp))
            with patch.object(adapter.runner, "step", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):
                    adapter.collect_contract(args)
            revision = run.state()["procedure"]["revision"]
            args.snapshot = None
            self.assertEqual(adapter.collect_contract(args)["event_status"], "duplicate-idempotent")
            self.assertEqual(run.state()["procedure"]["revision"], revision)

    def test_invalid_result_and_wrong_task_or_owner_leave_manifest_unchanged(self):
        for mutation in ("owner", "ticket", "context", "verdict", "effort", "running", "failed-turn", "host", "missing-result"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                run, args, result = self.fixture(Path(tmp))
                original = run.path.read_bytes()
                if mutation == "owner":
                    args.owner = "not-owner"
                elif mutation == "ticket":
                    result["ticket_id"] = "another-ticket"
                elif mutation == "context":
                    result["checked_references"][0]["sha256"] = "0" * 64
                elif mutation == "verdict":
                    result["status"] = "approved-probably"
                elif mutation == "effort":
                    result["residual_implementation_complexity"] = "UNKNOWN"
                elif mutation == "missing-result":
                    args.result = Path(tmp) / "nonexistent.json"
                else:
                    raw = run_registry.load_json(args.snapshot)
                    poll = raw["polls"][0]
                    if mutation == "running":
                        poll["thread"]["status"]["type"] = "active"
                        poll["latestTurn"]["status"] = "inProgress"
                    elif mutation == "host":
                        poll["thread"]["hostId"] = "wrong-host"
                    else:
                        poll["latestTurn"]["status"] = "failed"
                    run_registry.save_json(args.snapshot, raw)
                if mutation != "missing-result":
                    run_registry.save_json(args.result, result)
                with self.assertRaises((ValueError, OSError)):
                    adapter.collect_contract(args)
                self.assertEqual(run.path.read_bytes(), original)

    def test_unapplied_journal_can_refresh_real_snapshot_after_interruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, args, _ = self.fixture(Path(tmp))
            before = run.path.read_bytes()
            with patch.object(adapter.controller, "apply_event", side_effect=OSError("before apply")):
                with self.assertRaises(OSError):
                    adapter.collect_contract(args)
            self.assertEqual(run.path.read_bytes(), before)
            fresh = args.snapshot.parent / "new-product-snapshot.json"
            raw = run_registry.load_json(args.snapshot)
            raw["polls"][0]["cursor"] = "new-capture"
            run_registry.save_json(fresh, raw)
            args.snapshot = fresh
            self.assertEqual(adapter.collect_contract(args)["event_status"], "applied")

    def test_changed_result_cannot_replace_a_collected_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, args, result = self.fixture(Path(tmp))
            adapter.collect_contract(args)
            before = run.path.read_bytes()
            result["status"] = "failed"
            run_registry.save_json(args.result, result)
            with self.assertRaisesRegex(ValueError, "collected result changed"):
                adapter.collect_contract(args)
            self.assertEqual(run.path.read_bytes(), before)

    def test_controller_advertises_collector_not_dispatch_for_completed_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, args, _ = self.fixture(Path(tmp))
            adapter.advance(argparse.Namespace(command="observe", state=run.path, owner=args.owner,
                                              expected_revision=args.expected_revision, snapshot=args.snapshot))
            action = controller.next_actions(run.state())[0]
            self.assertEqual(action["action"], "COLLECT_OBSERVED_PHASE_RESULTS")
            self.assertEqual(action["phases"][0]["collector"]["command"], "collect-contract")
            self.assertFalse(action["phases"][0]["collector"]["requires_collection_spec"])


if __name__ == "__main__":
    unittest.main()
