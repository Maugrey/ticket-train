"""Fault tests for native effects, automatic continuation and durable evidence."""
import argparse
import copy
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import control_plane_runner as runner
import phase_dispatch
import run_registry
import thread_runtime
import train_supervisor
import token_usage
import train_controller as controller
import verification_runner
from test_train_controller import Harness


class FakeServer:
    def __init__(self):
        self.threads = {}
        self.projects = []
        self.calls = []
        self.call_params = []
        self.crash_after_create = False
        self.active_writer_failures = 0
        self.event_sink = None
        self.host = self

    def call(self, method, params, receipt_path=None):
        self.calls.append(method)
        self.call_params.append((method, copy.deepcopy(params)))
        if method == "thread/start":
            task_id = "task-" + str(len(self.threads) + 1)
            self.threads[task_id] = {"id": task_id, "cwd": params["cwd"], "createdAt": time.time(), "turns": [], "status": {"type": "idle"},
                                     "projectId": params.get("projectId")}
            result = {"thread": self.threads[task_id], "model": params.get("model", "test-model")}
            if self.crash_after_create:
                self.crash_after_create = False
                raise thread_runtime.HostError("Connection lost after server created the task")
        elif method == "thread/list":
            result = {"data": list(self.threads.values()), "nextCursor": None}
        elif method == "project/list":
            result = {"data": self.projects, "nextCursor": None}
        elif method in {"thread/read", "thread/resume"}:
            if method == "thread/resume" and self.active_writer_failures:
                self.active_writer_failures -= 1
                if receipt_path:
                    run_registry.save_json(receipt_path, {
                        "error": {"code": -32600, "message": "already has an active writer"}
                    })
                raise thread_runtime.HostError("already has an active writer")
            result = {"thread": self.threads[params["threadId"]]}
        elif method == "thread/metadata/update":
            task = self.threads[params["threadId"]]
            task["projectId"] = params["projectId"]
            result = {"thread": task}
        elif method == "turn/start":
            task = self.threads[params["threadId"]]
            turn = {"id": "turn-" + str(len(task["turns"]) + 1), "status": "inProgress", "items": []}
            task["turns"].append(turn)
            task["status"] = {"type": "active"}
            result = {"turn": turn}
        elif method == "thread/name/set":
            result = {}
        else:
            raise AssertionError(method)
        if receipt_path:
            run_registry.save_json(receipt_path, {"result": result})
        return result

    def complete(self, task_id, result):
        task = self.threads[task_id]
        task["status"] = {"type": "idle"}
        task["turns"][-1].update(status="completed", items=[{"type": "agentMessage", "text": json.dumps(result)}])

    def wait(self, seconds):
        return []

    def close(self):
        pass

    def answer_request(self, request_id, result):
        self.calls.append("answer_request")


def effects(root, server):
    return train_supervisor.NativeEffects(root, __file__, host_factory=lambda *a, **kw: server)


def repository(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    for key, value in (("user.name", "Fixture"), ("user.email", "fixture@example.invalid")):
        subprocess.run(["git", "-C", str(path), "config", key, value], check=True)
    (path / "source.txt").write_text("before\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)
    return verification_runner.git_head(path)


class NativeRuntimeTests(unittest.TestCase):
    def test_run_level_input_request_becomes_a_blocked_phase(self):
        envelope = {
            "phase_status": "needs_input",
            "result_summary": "ticket analyses are missing",
            "input_request": {"question": "supply analyses"},
        }
        normalized = phase_dispatch.normalize_termination_envelope(
            {"phase_key": "run:decision:1", "ticket_id": None}, envelope
        )
        self.assertEqual(normalized["phase_status"], "blocked")
        self.assertNotIn("input_request", normalized)
        self.assertEqual(envelope["phase_status"], "needs_input")

    def test_active_writer_contention_does_not_exhaust_host_reconnects(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Harness(Path(tmp))
            driver = runner.Driver(
                run.path,
                "thread-main",
                run.state()["orchestrator_lease"]["epoch"],
                {},
            )
            attempts = 0

            def tick():
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise thread_runtime.HostError("thread worker already has an active writer")
                state = run.state()
                state["procedure"]["run_status"] = "COMPLETED"
                run_registry.save_json(run.path, state)
                return True

            with patch.object(driver, "tick", side_effect=tick), \
                    patch.object(driver, "sync_owner_attention", return_value=False), \
                    patch.object(runner.time, "sleep") as sleep:
                result = driver.run()
            self.assertEqual(result["status"], "completed")
            sleep.assert_called_once_with(5)

    def test_driver_materializes_human_gate_for_owner_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Harness(Path(tmp))
            run.confirm()
            run.analyze(scope_proposals=[Harness.scope_expansion_proposal()])
            state = run.state()
            action = controller.next_actions(state)[0]
            self.assertEqual(action["action"], "ANNOUNCE_HUMAN_GATE")
            action["gate"].update({
                "question": "Choose the ticket scope.",
                "blocked_scope": ["Ticket implementation"],
                "continuing_scope": ["Independent tickets"],
                "accepted_replies": ["Use the minimal scope.", "Approve the expanded scope."],
            })
            driver = runner.Driver(
                run.path,
                "thread-main",
                state["orchestrator_lease"]["epoch"],
                {},
            )
            try:
                self.assertTrue(phase_dispatch.execute_action(driver, action))
            finally:
                driver.close()

            pending = run.state()["pending_human_action"]
            self.assertEqual(pending["gate_id"], action["gate"]["gate_id"])
            self.assertEqual(pending["question"], action["gate"]["question"])
            self.assertEqual(pending["notification_status"], "ANNOUNCED")

    def test_driver_wakes_owner_conversation_once_and_never_polls_unchanged_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Harness(Path(tmp))
            run.confirm()
            run.analyze(scope_proposals=[Harness.scope_expansion_proposal()])
            state = run.state()
            action = controller.next_actions(state)[0]
            action["gate"].update({
                "question": "Choose the ticket scope.",
                "blocked_scope": ["Ticket implementation"],
                "continuing_scope": ["Independent tickets"],
                "accepted_replies": ["Use the minimal scope.", "Approve the expanded scope."],
            })
            server = FakeServer()
            repository = str(Path(tmp) / "project")
            server.threads["thread-main"] = {
                "id": "thread-main", "cwd": repository, "createdAt": time.time(),
                "turns": [], "status": {"type": "idle"}, "projectId": None,
            }
            server.projects = [{"id": "project-1", "name": "Project", "roots": [{"path": repository}]}]
            runtime = train_supervisor.NativeEffects(
                Path(tmp) / "driver" / "effects",
                __file__,
                host_factory=lambda *a, **kw: server,
                source_thread_id="thread-main",
                repository=repository,
            )
            driver = runner.Driver(
                run.path,
                "thread-main",
                state["orchestrator_lease"]["epoch"],
                {"repository": repository},
                host=runtime,
            )
            try:
                self.assertTrue(phase_dispatch.execute_action(driver, action))
                self.assertTrue(driver.sync_owner_attention())
                self.assertFalse(driver.sync_owner_attention())
            finally:
                driver.close()

            self.assertEqual(server.calls.count("turn/start"), 1)
            request = next(params for method, params in server.call_params if method == "turn/start")
            prompt = request["input"][0]["text"]
            self.assertIn("Do not create a scheduled automation", prompt)
            self.assertIn("continuous supervision", prompt)
            self.assertIn('"question": "Choose the ticket scope."', prompt)
            self.assertIn("Do not omit or summarize those fields", prompt)
            self.assertEqual(request["threadId"], "thread-main")
            self.assertNotIn("effort", request)
            self.assertEqual(server.calls.count("thread/start"), 0)

    def test_legacy_gate_replies_are_enriched_from_collected_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Harness(Path(tmp))
            result_path = run.path.parent / "reports" / "result.json"
            run_registry.save_json(result_path, {
                "events": [{
                    "scope_assessment": {
                        "specification_deviations": [{
                            "id": "D-1",
                            "recommendation": "OPTION-A",
                            "options": [
                                {"id": "OPTION-A", "meaning": "Complete A", "consequences": "Effect A"},
                                {"id": "OPTION-B", "meaning": "Complete B", "consequences": "Effect B"},
                            ],
                        }],
                    },
                }],
            })
            state = run.state()
            state["procedure"]["phases"]["T-1:analysis:1"] = {
                "completion_envelope": {
                    "input_request": {"gate_id": "G-1"},
                    "artifacts": {"complete_result_reference": str(result_path)},
                },
            }
            driver = runner.Driver(
                run.path,
                "thread-main",
                state["orchestrator_lease"]["epoch"],
                {},
            )
            payload = {
                "gate_id": "G-1",
                "accepted_replies": [{"deviation_id": "D-1", "option_ids": ["OPTION-A", "OPTION-B"]}],
            }

            enriched = driver.enrich_gate_payload(state, payload)

            reply = enriched["accepted_replies"][0]
            self.assertEqual(reply["recommendation"], "OPTION-A")
            self.assertEqual(reply["options"][0]["meaning"], "Complete A")
            self.assertNotIn("options", payload["accepted_replies"][0])

    def test_interrupted_owner_relay_reuses_its_recorded_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Harness(Path(tmp))
            server = FakeServer()
            server.threads["thread-main"] = {
                "id": "thread-main", "cwd": str(Path(tmp)), "createdAt": time.time(),
                "turns": [], "status": {"type": "idle"}, "projectId": None,
            }
            runtime = effects(Path(tmp) / "driver" / "effects", server)
            runtime.source_thread_id = "thread-main"
            driver = runner.Driver(
                run.path,
                "thread-main",
                run.state()["orchestrator_lease"]["epoch"],
                {},
                host=runtime,
            )
            try:
                reference = driver.queue_owner_attention("human-gate", {"gate_id": "G-1"})
                self.assertTrue(driver.sync_owner_attention())
                notification = run_registry.load_json(reference)
                task = server.threads[notification["thread_id"]]
                task["turns"][-1]["status"] = "interrupted"
                task["status"] = {"type": "idle"}
                self.assertFalse(driver.sync_owner_attention())
                job = runtime.read(notification["job_key"])
                job["retry_at"] = time.time() - 1
                runtime.save(job)
                self.assertFalse(driver.sync_owner_attention())
                self.assertEqual(server.calls.count("turn/start"), 2)
                server.complete(notification["thread_id"], {"relay": "presented"})
                self.assertTrue(driver.sync_owner_attention())
                presented = run_registry.load_json(reference)
            finally:
                driver.close()

            self.assertEqual(presented["status"], "presented")
            self.assertEqual(presented["thread_id"], "thread-main")
            self.assertEqual(server.calls.count("thread/start"), 0)

    def test_busy_owner_is_retried_without_creating_an_attention_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Harness(Path(tmp))
            server = FakeServer()
            server.threads["thread-main"] = {
                "id": "thread-main", "cwd": str(Path(tmp)), "createdAt": time.time(),
                "turns": [], "status": {"type": "active"}, "projectId": None,
            }
            server.active_writer_failures = 1
            runtime = effects(Path(tmp) / "driver" / "effects", server)
            runtime.source_thread_id = "thread-main"
            driver = runner.Driver(
                run.path,
                "thread-main",
                run.state()["orchestrator_lease"]["epoch"],
                {},
                host=runtime,
            )
            try:
                reference = driver.queue_owner_attention("human-gate", {"gate_id": "G-1"})
                self.assertFalse(driver.sync_owner_attention())
                notification = run_registry.load_json(reference)
                self.assertEqual(notification["status"], "pending")
                job = runtime.read("owner-attention:" + reference.parent.name)
                job["retry_at"] = time.time() - 1
                runtime.save(job)
                self.assertTrue(driver.sync_owner_attention())
                delivered = run_registry.load_json(reference)
            finally:
                driver.close()

            self.assertEqual(delivered["thread_id"], "thread-main")
            self.assertEqual(server.calls.count("thread/start"), 0)
            self.assertEqual(server.calls.count("turn/start"), 1)

    def test_worker_inherits_orchestrator_project_while_keeping_its_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = FakeServer()
            repository = str(Path(tmp) / "project")
            server.threads["parent"] = {"id": "parent", "cwd": repository, "createdAt": time.time(),
                                        "turns": [], "status": {"type": "idle"}, "projectId": None}
            server.projects = [{"id": "project-1", "name": "Project", "roots": [{"path": repository}]}]
            runtime = train_supervisor.NativeEffects(
                Path(tmp), __file__, host_factory=lambda *a, **kw: server,
                source_thread_id="parent", repository=repository
            )
            spec = {"key": "T-1:analysis:1", "cwd": str(Path(tmp) / "worktree"), "model": "test-model",
                    "effort": "low", "prompt": "Analyze", "title": "Train worker"}

            job = runtime.submit(spec)

            start = next(params for method, params in server.call_params if method == "thread/start")
            self.assertEqual(start["projectId"], "project-1")
            self.assertEqual(start["cwd"], spec["cwd"])
            self.assertEqual(server.threads[job["thread_id"]]["projectId"], "project-1")
            self.assertEqual(server.calls.count("thread/read"), 1)
            self.assertEqual(server.calls.count("project/list"), 1)

    def test_recorded_unassigned_creation_is_repaired_without_a_second_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = FakeServer()
            repository = str(Path(tmp) / "project")
            server.projects = [{"id": "project-1", "name": "Project", "roots": [{"path": repository}]}]
            server.threads["worker"] = {"id": "worker", "cwd": str(Path(tmp) / "worktree"),
                                        "createdAt": time.time(), "turns": [], "status": {"type": "idle"},
                                        "projectId": None}
            runtime = train_supervisor.NativeEffects(
                Path(tmp) / "effects", __file__, host_factory=lambda *a, **kw: server, repository=repository
            )
            spec = {"key": "T-1:analysis:1", "cwd": server.threads["worker"]["cwd"], "model": "test-model",
                    "effort": "low", "prompt": "Analyze", "title": "Train worker"}
            runtime.prepare(spec)
            directory = runtime.directory(spec["key"])
            run_registry.save_json(directory / "create-response.json", {
                "result": {"thread": copy.deepcopy(server.threads["worker"]), "model": "test-model"}
            })

            job = runtime.submit(spec)

            self.assertEqual(job["thread_id"], "worker")
            self.assertNotIn("thread/start", server.calls)
            self.assertIn("thread/metadata/update", server.calls)
            receipt = run_registry.load_json(directory / "create-response.json")
            self.assertEqual(receipt["result"]["thread"]["projectId"], "project-1")

    def test_scope_decision_reuse_preserves_choice_and_risk_but_new_source_reopens_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Harness(Path(tmp)); run.confirm(); run.analyze(scope_proposals=[Harness.scope_expansion_proposal()])
            original = copy.deepcopy(run.state()["procedure"]["tickets"]["T-1"]["analysis"])
            gate = run.state()["procedure"]["tickets"]["T-1"]["specification_gate_id"]
            self.assertEqual(run.apply("GATE_ANNOUNCED", gate_id=gate, revision="scope-1", decision_summary="Choose scope", evidence_summary="Source lacks migration",
                blocked_scope="T-1", continuing_scope="Other analysis", accepted_replies=["Minimal MVP", "Approve migration"]), 0)
            self.assertEqual(run.apply("SCOPE_EXPANSION_DECIDED", ticket_id="T-1", gate_id=gate, revision="scope-1",
                decisions=[{"proposal_id": "legacy-save-migration", "decision": "approved", "selected_variant": "expanded"}], specification_decisions=[],
                user_decision_reference="user:approved", active_scope_revision="active-2", implementation_contract_revision="implementation-2", verification_contract_revision="verification-2",
                criticality="NORMAL", complexity="MEDIUM", criticality_evidence="Persisted state is affected", complexity_evidence="Versioned loader",
                residual_implementation_complexity="MEDIUM", verification_complexity="MEDIUM", complexity_reduction_evidence="Oracle resolved", unresolved_implementation_difficulty=[],
                classification_scope_item_ids=["source-criterion-1", "proposed-legacy-save-migration"]), 0)
            original.pop("event_id", None); original.pop("type", None)
            # A later analysis receipt may use the same source and options.
            for new_source in (False, True):
                state = run.state(); state["procedure"]["tickets"]["T-1"]["status"] = "TRIAGED"; run_registry.save_json(run.path, state)
                if new_source:
                    original["source_revision"] = "changed-source"
                self.assertEqual(run.apply("ANALYSIS_RECORDED", **original), 0)
                item = run.state()["procedure"]["tickets"]["T-1"]
                if not new_source:
                    self.assertEqual(item["analysis"]["scope_assessment"]["user_decision_reference"], "user:approved")
                    self.assertEqual(item["analysis"]["criticality"], "NORMAL")
                    self.assertEqual(len(run.state()["procedure"]["human_gates"]), 1)
                else:
                    self.assertEqual(item["status"], "AWAITING_SPECIFICATION_DECISION")
                    self.assertNotEqual(item["specification_gate_id"], gate)

    @unittest.skipUnless(os.name == "nt", "Windows executor containment")
    def test_killing_executor_also_stops_its_live_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); repo = root / "repo"; head = repository(repo)
            plan, output, logs = root / "plan.json", root / "result.json", root / "logs"
            run_registry.save_json(plan, {"schema_version": 1, "workdir": str(repo), "expected_head": head,
                "commands": [{"id": "slow", "argv": [sys.executable, "-c", "import time;time.sleep(40)"], "timeout_seconds": 50}]})
            process = subprocess.Popen([sys.executable, str(Path(verification_runner.__file__)), "--plan", str(plan), "--output", str(output), "--logs-dir", str(logs)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            pid = None
            try:
                deadline = time.monotonic() + 10
                journal = logs / "verification-journal.json"
                while not journal.exists() or not run_registry.load_json(journal)["commands"]:
                    self.assertIsNone(process.poll(), process.stderr.read() if process.poll() is not None else "")
                    self.assertLess(time.monotonic(), deadline); time.sleep(0.05)
                pid = run_registry.load_json(journal)["commands"]["slow"]["pid"]
                self.assertTrue(verification_runner.process_alive(pid))
                process.kill(); process.wait()
                while verification_runner.process_alive(pid) and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse(verification_runner.process_alive(pid))
                self.assertFalse(output.exists())
                with run_registry.file_lock(output.with_suffix(".runner.lock"), 0):
                    pass
            finally:
                if process.poll() is None:
                    process.kill(); process.wait()
                process.stderr.close()
                if pid and verification_runner.process_alive(pid):
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)

    def test_preflight_pins_profile_and_rejects_changed_host_or_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); repo = root / "repo"; repository(repo)
            host = root / "codex.exe"; host.write_bytes(b"fixture executable")
            proof_path = root / "capability.json"
            run_registry.save_json(proof_path, {"format": "ticket-train-native-capability-v1", "desktop_read_verified": True,
                "host_executable": str(host), "host_sha256": hashlib.sha256(host.read_bytes()).hexdigest(),
                "first": {"thread": {"id": "one"}}, "second": {"thread": {"id": "two"}}})
            profile = {"revision": "one", "repository": str(repo), "tickets": {"T-1": {"source_reference": "source", "source_revision": "one"}},
                "host_executable": str(host), "native_visibility_evidence": str(proof_path)}
            state = {"execution_mode": "dry-run", "procedure": {"tickets": {"T-1": {}}}}
            runner.preflight(state, profile, root / "driver")
            changed = copy.deepcopy(profile); changed["revision"] = "two"
            with self.assertRaisesRegex(ValueError, "profile changed"):
                runner.preflight(state, changed, root / "driver")
            host.write_bytes(b"another version")
            with self.assertRaisesRegex(ValueError, "executable changed"):
                runner.preflight(state, profile, root / "driver")

    def test_pinned_release_rejects_tampering_and_migration_requires_idle_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run = Harness(root); state = run.state()
            release = run_registry.verify_release(state)
            script = release / "scripts" / "train_controller.py"
            original = script.read_bytes(); script.write_bytes(original + b"\n# changed\n")
            with self.assertRaisesRegex(ValueError, "changed|hash|mismatch"):
                run_registry.verify_release(state)
            script.write_bytes(original)
            args = argparse.Namespace(state=run.path, owner_thread_id="thread-main", owner_epoch=state["orchestrator_lease"]["epoch"])
            with run_registry.file_lock(run.path.parent / "driver" / "driver.lock", 0), self.assertRaises(ValueError):
                run_registry.migrate_runtime(args)
            state["procedure"]["phases"]["busy"] = {"phase_key": "busy", "launch_state": "RUNNING"}
            run_registry.save_json(run.path, state)
            with self.assertRaisesRegex(ValueError, "idle"):
                run_registry.migrate_runtime(args)
            state["procedure"]["phases"].clear(); run_registry.save_json(run.path, state)
            with contextlib.redirect_stdout(io.StringIO()):
                run_registry.migrate_runtime(args)
            self.assertEqual(len(run.state()["runtime_migrations"]), 1)
            self.assertEqual(run_registry.verify_release(run.state()), release)

    def test_action_error_budget_prevents_endless_retries_and_idle_wakes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run = Harness(root); run.confirm()
            driver = runner.Driver(run.path, "thread-main", run.state()["orchestrator_lease"]["epoch"], {})
            action = {"action": "fixture-operation"}
            with patch.object(phase_dispatch, "collect", return_value=False), patch.object(controller, "next_actions", return_value=[action]), patch.object(phase_dispatch, "execute_action", side_effect=ValueError("service unavailable")) as execute:
                for stamp in (1000, 2000, 3000, 4000, 5000):
                    with patch.object(runner.time, "time", return_value=stamp), contextlib.redirect_stdout(io.StringIO()):
                        driver.tick()
                self.assertEqual(execute.call_count, 3)
                self.assertEqual(driver.blocked_actions[0]["action"], action)
            self.assertIsNone(driver.host)
            self.assertFalse(run.state()["procedure"]["phases"])

    def test_available_unity_slot_rearms_an_exhausted_acquisition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run = Harness(root); run.confirm()
            registry = root / "slots.json"
            run_registry.save_json(registry, {"slots": [{"slot_id": "one", "status": "IDLE", "lease": None}]})
            action = {"action": "ACQUIRE_UNITY_SLOT_DETERMINISTICALLY", "registry_reference": str(registry)}
            driver = runner.Driver(run.path, "thread-main", run.state()["orchestrator_lease"]["epoch"], {})
            retry = driver.directory / "retries" / (runner.sha256_json(action) + ".json")
            run_registry.save_json(retry, {"attempts": 3, "error": "slots unavailable"})
            with patch.object(phase_dispatch, "collect", return_value=False), \
                    patch.object(controller, "next_actions", return_value=[action]), \
                    patch.object(phase_dispatch, "execute_action", return_value=True) as execute:
                self.assertTrue(driver.tick())
            execute.assert_called_once_with(driver, action)
            self.assertEqual(run_registry.load_json(retry)["attempts"], 0)
            self.assertEqual(driver.blocked_actions, [])

    def test_approval_answer_unblocks_same_native_task_and_rejects_stale_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = FakeServer()
            runtime = effects(Path(tmp), server)
            job = runtime.submit({"key": "approval", "cwd": tmp, "prompt": "work"})
            job["status"] = "needs_input"; runtime.save(job)
            runtime.notification({"id": 12, "method": "request", "params": {"threadId": job["thread_id"]}})
            answer = {"server_instance": "old", "request_id": 12, "result": {"decision": "accept"}, "user_decision_reference": "user:yes"}
            with self.assertRaises(ValueError):
                runtime.answer(answer)
            answer["server_instance"] = runtime.instance
            runtime.answer(answer)
            self.assertEqual(runtime.read("approval")["status"], "running")
            self.assertEqual(server.calls.count("turn/start"), 1)
            with self.assertRaises(ValueError):
                runtime.answer(answer)

    def test_pending_user_prompt_survives_before_turn_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, server = Path(tmp), FakeServer()
            runtime = effects(root, server)
            spec = {"key": "input", "cwd": tmp, "prompt": "original"}
            job = runtime.submit(spec)
            job.update(attempt=1, pending_prompt="approved choice", status="starting_turn")
            runtime.save(job)
            restarted = effects(root, server)
            restarted.submit(spec)
            request = run_registry.load_json(restarted.directory("input") / "turn-1-request.json")
            self.assertEqual(request["input"][0]["text"], "approved choice")
            restarted.submit(spec)
            self.assertEqual(server.calls.count("turn/start"), 2)

    def test_active_writer_retry_reuses_one_turn_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, server = Path(tmp), FakeServer()
            runtime = effects(root, server)
            spec = {"key": "input", "cwd": tmp, "prompt": "original"}
            job = runtime.submit(spec)
            job.update(attempt=1, pending_prompt="approved choice", status="starting_turn")
            runtime.save(job)
            restarted = effects(root, server)
            server.active_writer_failures = 1

            self.assertFalse(restarted.start_turn(restarted.read("input"), "approved choice"))
            pending = restarted.read("input")
            self.assertEqual(pending["attempt"], 1)
            self.assertEqual(pending["pending_prompt"], "approved choice")
            self.assertFalse((restarted.directory("input") / "turn-1-resume-response.json").exists())

            self.assertTrue(restarted.start_turn(pending, "approved choice"))
            self.assertEqual(restarted.read("input")["attempt"], 1)

    def test_result_journal_survives_cost_checkpoint_and_rejects_changed_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run = Harness(root); run.confirm()
            run.apply("PHASE_DISPATCHED", kind="triage", phase_key="triage", base_commit="base-sha", model="gpt-5.6-terra",
                reasoning_effort="medium", routing_conformance="conformant", triage_profile="standard", context_packet=run.context_packet("base-sha", "base-sha"))
            run.materialize("triage", "thread-triage")
            envelope = {"phase_key": "triage", "phase_status": "completed", "actual_model": "gpt-5.6-terra", "actual_reasoning_effort": "medium",
                "result_summary": "triaged", "artifacts": {"report": "fixture"}, "tests_and_checks": ["read"], "residual_risks": "none",
                "files_modified": [], "requested_or_recommended_next_action": "analyze", "usage": {"measurement": "complete", "total_tokens": 50000001}}
            events = [{"type": "PHASE_COMPLETED", "phase_key": "triage", "envelope": envelope}, {"type": "TICKET_TRIAGED", "ticket_id": "T-1", "phase_key": "triage",
                "criticality": "LOW", "complexity": "LOW", "confidence": "high", "triage_model": "gpt-5.6-terra", "triage_reasoning_effort": "medium",
                "analysis_model": "gpt-5.6-terra", "analysis_reasoning_effort": "medium", "analysis_routing_conformance": "conformant"}]
            driver = runner.Driver(run.path, "thread-main", run.state()["orchestrator_lease"]["epoch"], {})
            self.assertFalse(driver.transaction("result", events))
            self.assertEqual(run.state()["procedure"]["phases"]["triage"]["launch_state"], "COMPLETED")
            anomaly = controller.unresolved_cost_anomalies(run.state()["procedure"])[0]
            run_registry.save_json(driver.directory / "inbox" / "cost.json", {"type": "COST_ANOMALY_RESOLVED", "anomaly_id": anomaly["anomaly_id"],
                "resolution": "restart-fresh-compact", "resolution_evidence": "Actual measured usage reviewed"})
            with patch.object(phase_dispatch, "execute_action", return_value=False), patch.object(phase_dispatch, "collect", return_value=False):
                driver.tick()
            self.assertEqual(run.state()["procedure"]["tickets"]["T-1"]["status"], "TRIAGED")
            changed = copy.deepcopy(events); changed[1]["confidence"] = "low"
            with self.assertRaisesRegex(ValueError, "changed"):
                driver.transaction("result", changed)

    def test_guardian_restarts_crashed_worker_without_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); marker = root / "attempts.txt"
            code = "from pathlib import Path;import sys;p=Path(sys.argv[1]);n=int(p.read_text()) if p.exists() else 0;p.write_text(str(n+1));sys.exit(1 if n==0 else 0)"
            with patch.object(runner.time, "sleep"):
                self.assertEqual(runner.supervise_worker([sys.executable, "-c", code, str(marker)], root), 0)
            self.assertEqual(marker.read_text(), "2")

    def test_validation_only_finishes_with_real_evidence_and_no_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); repo = root / "repo"; head = repository(repo)
            run = Harness(root); run.confirm(); run.analyze()
            state = run.state(); item = state["procedure"]["tickets"]["T-1"]
            item["status"] = "READY_FOR_IMPLEMENTATION"
            run_registry.save_json(run.path, state)
            plan_path, evidence_path = root / "plan.json", root / "evidence.json"
            run_registry.save_json(plan_path, {"schema_version": 1, "workdir": str(repo), "expected_head": head,
                "commands": [{"id": "criterion", "argv": [sys.executable, "-c", "print('criterion verified')"], "timeout_seconds": 10}]})
            run_registry.save_json(evidence_path, {"type": "VALIDATION_ONLY_RECORDED", "ticket_id": "T-1", "acceptance_coverage_status": "complete"})
            self.assertEqual(run.apply("VALIDATION_ONLY_DISPATCHED", ticket_id="T-1", base_commit=head, plan_reference=str(plan_path), evidence_reference=str(evidence_path),
                source_reference="source", scope_assessment_revision="scope-1", scope_conformance="within-authorized-scope"), 0)
            driver = runner.Driver(run.path, "thread-main", state["orchestrator_lease"]["epoch"], {"repository": str(repo)})
            phase_dispatch.verify(driver, {"action": "RUN_VALIDATION_ONLY_VERIFICATION", "ticket_id": "T-1"})
            self.assertEqual(run.state()["procedure"]["tickets"]["T-1"]["status"], "VALIDATED")
            phase_dispatch.reports(driver, {"action": "RECORD_NO_DELIVERY_REPORT"})
            driver.apply({"type": "RUN_COMPLETED"})
            self.assertEqual(controller.completion_issues(run.state()), [])
            self.assertNotIn("pull_request", run.state()["procedure"]["finalization"])

    def test_rtk_presentation_fallback_preserves_failure_and_raw_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); stdout, stderr = root / "out.log", root / "err.log"
            stdout.write_text("same line\n" * 300); stderr.write_text("Assertion failed\n")
            result = root / "result.json"
            run_registry.save_json(result, {"command_results": [{"command_id": "failed", "status": "failed", "exit_code": 7, "stdout_log": str(stdout), "stderr_log": str(stderr)}]})
            before = hashlib.sha256(stdout.read_bytes()).hexdigest()
            with patch.object(verification_runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"short\n", b"")):
                compact = verification_runner.present_result(result, "failed", "rtk")
            fallback = verification_runner.present_result(result, "failed", str(root / "missing.exe"))
            self.assertEqual((compact["status"], compact["exit_code"], compact["filter"]), ("failed", 7, "rtk-log"))
            self.assertEqual(fallback["filter"], "bounded-raw")
            self.assertEqual(hashlib.sha256(stdout.read_bytes()).hexdigest(), before)
            with self.assertRaises(ValueError):
                verification_runner.present_result(result, "unknown")

    def test_unknown_create_reconciles_one_task_without_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = FakeServer()
            server.crash_after_create = True
            spec = {"key": "phase-1", "cwd": str(root / "isolated"), "prompt": "original"}
            first = effects(root / "effects", server)
            with self.assertRaises(thread_runtime.HostError):
                first.submit(spec)
            restarted = effects(root / "effects", server)
            job = restarted.submit(spec)
            self.assertEqual(job["thread_id"], "task-1")
            self.assertEqual(server.calls.count("thread/start"), 1)
            self.assertEqual(server.calls.count("turn/start"), 1)
            restarted.submit(spec)
            self.assertEqual(server.calls.count("turn/start"), 1)

    def test_completed_result_collected_after_restart_without_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, server = Path(tmp), FakeServer()
            spec = {"key": "phase", "cwd": str(root), "prompt": "return data"}
            job = effects(root / "effects", server).submit(spec)
            server.complete(job["thread_id"], {"value": 42})
            restarted = effects(root / "effects", server)
            result = restarted.observe(restarted.submit(spec))
            self.assertEqual(result["status"], "completed")
            self.assertEqual(json.loads(run_registry.load_json(result["result_reference"])["text"]), {"value": 42})
            self.assertEqual(server.calls.count("thread/start"), 1)

    def test_automatic_collection_applies_triage_and_schedules_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, server = Path(tmp), FakeServer()
            repo = root / "repo"
            head = repository(repo)
            run = Harness(root)
            run.confirm()
            state = run.state(); state["procedure"]["base_branch"] = head
            run_registry.save_json(run.path, state)
            profile = {"repository": str(repo), "revision": "fixture", "native_visibility_evidence": "fixture",
                       "tickets": {"T-1": {"source_reference": "fixture"}}}
            driver = runner.Driver(run.path, "thread-main", state["orchestrator_lease"]["epoch"], profile, effects(root / "effects", server))
            self.assertTrue(driver.tick())
            self.assertTrue(driver.tick())
            value = next(iter(run.state()["procedure"]["phases"].values()))
            server.complete(value["thread_id"], {"envelope": {
                "phase_status": "completed", "result_summary": "Classified one ticket", "artifacts": {"report": "fixture"},
                "tests_and_checks": ["Read-only triage"], "residual_risks": "none identified", "files_modified": [],
                "requested_or_recommended_next_action": "Analyze"},
                "events": [{"type": "TICKET_TRIAGED", "ticket_id": "T-1", "criticality": "LOW", "complexity": "LOW", "confidence": "high"}]})
            driver.last_observation = 0
            self.assertTrue(driver.tick(), [run_registry.load_json(p) for p in (driver.directory / "outbox").glob("*.json")])
            self.assertEqual(run.state()["procedure"]["tickets"]["T-1"]["status"], "TRIAGED")
            self.assertTrue(driver.tick())
            self.assertTrue(any(p["kind"] == "analysis" for p in run.state()["procedure"]["phases"].values()))
            count = server.calls.count("thread/start")
            driver.replay()
            self.assertEqual(server.calls.count("thread/start"), count)
            driver.close()

    def test_collection_normalizes_legacy_event_type_and_ignores_worker_authorization(self):
        class DriverStub:
            profile = {"revision": "fixture"}

        value = {
            "kind": "triage", "phase_key": "run:triage:1",
            "requested_model": "gpt-6-astra", "requested_reasoning_effort": "high",
        }
        events = phase_dispatch.decorate_events(DriverStub(), value, {"events": [{
            "event_type": "TICKET_TRIAGED", "event_id": "worker-controlled",
            "ticket_id": "T-1", "criticality": "CRITICAL", "complexity": "MAXIMUM",
            "confidence": "high", "reasoning_authorized": True,
            "reasoning_authorization_id": "worker-controlled",
        }]})
        event = events[0]
        self.assertEqual(event["type"], "TICKET_TRIAGED")
        self.assertNotIn("event_type", event)
        self.assertNotIn("event_id", event)
        self.assertNotIn("reasoning_authorized", event)
        self.assertNotIn("reasoning_authorization_id", event)
        self.assertEqual(
            (event["analysis_model"], event["analysis_reasoning_effort"], event["analysis_routing_conformance"]),
            ("gpt-6-astra", "xhigh", "documented-fallback"),
        )

    def test_collection_rejects_conflicting_event_type_fields(self):
        class DriverStub:
            profile = {"revision": "fixture"}

        value = {
            "kind": "triage", "phase_key": "run:triage:1",
            "requested_model": "gpt-6-astra", "requested_reasoning_effort": "high",
        }
        with self.assertRaisesRegex(ValueError, "type fields disagree"):
            phase_dispatch.decorate_events(DriverStub(), value, {"events": [{
                "type": "TICKET_TRIAGED", "event_type": "ANALYSIS_RECORDED",
            }]})

    def test_missing_environment_field_is_a_repairable_result_error(self):
        error = ValueError("analysis_unity_requirement is required by the unity-mcp-local environment profile")
        self.assertTrue(phase_dispatch.repairable_result_error(error))

    def test_invalid_enum_is_repairable_with_exact_contract_guidance(self):
        error = ValueError("invalid product lifecycle stage")
        self.assertTrue(phase_dispatch.repairable_result_error(error))
        prompt = phase_dispatch.repair_prompt(error, "Original task")
        self.assertIn("exact JSON field names", prompt)
        self.assertIn("do not paraphrase enum values", prompt)

    def test_old_generation_cannot_write_even_with_same_owner(self):
        state = {"orchestrator_lease": {"owner_thread_id": "same", "epoch": "new"}}
        with self.assertRaisesRegex(ValueError, "generation"):
            run_registry.require_owner(state, "same", "old")
        with self.assertRaises(ValueError):
            run_registry.require_owner(state, "same")

    def test_dead_process_releases_os_lock_without_deleting_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock"
            code = "import sys,time;from pathlib import Path;import run_registry;ctx=run_registry.file_lock(Path(sys.argv[1]));ctx.__enter__();print('locked',flush=True);time.sleep(60)"
            process = subprocess.Popen([sys.executable, "-c", code, str(path)], cwd=Path(__file__).parent, stdout=subprocess.PIPE, text=True)
            self.assertEqual(process.stdout.readline().strip(), "locked")
            with self.assertRaises(ValueError):
                with run_registry.file_lock(path, 0):
                    pass
            process.kill(); process.wait(); process.stdout.close()
            self.assertTrue(path.exists())
            with run_registry.file_lock(path, 0):
                pass

    def test_incremental_counter_measurement_handles_reset_and_partial_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            def count(n):
                return {"timestamp": "2026-09-06T12:00:00Z", "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"total_tokens": n}}}}
            records = [{"type": "session_meta", "payload": {"id": "session"}}, count(100), count(150)]
            path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
            first = token_usage.measure_session(path, "session")
            with path.open("a", encoding="utf-8") as out:
                out.write(json.dumps(count(20)) + "\n" + json.dumps(count(30)))
            second = token_usage.measure_session(path, "session", cursor=first["cursor"])
            self.assertEqual(second["usage"]["total_tokens"], 170)
            with path.open("a") as out:
                out.write("\n")
            third = token_usage.measure_session(path, "session", cursor=second["cursor"])
            self.assertEqual(third["usage"]["total_tokens"], 180)
            self.assertEqual(third["counter_resets"], 1)

    def test_fingerprint_rejects_source_edit_without_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repository(repo)
            before = verification_runner.worktree_fingerprint(repo)
            (repo / "source.txt").write_text("changed\n")
            self.assertNotEqual(verification_runner.worktree_fingerprint(repo), before)

    def test_service_retries_are_bounded_and_reuse_one_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = FakeServer()
            runtime = effects(Path(tmp), server)
            spec = {"key": "retry", "cwd": tmp, "prompt": "work"}
            job = runtime.submit(spec)
            for attempt in range(3):
                server.threads[job["thread_id"]]["turns"][-1]["status"] = "failed"
                job = runtime.observe(job)
                if attempt < 2:
                    job["retry_at"] = time.time() - 1
                    runtime.save(job)
                    job = runtime.observe(job)
            self.assertEqual(job["status"], "blocked")
            self.assertEqual(server.calls.count("thread/start"), 1)
            self.assertEqual(server.calls.count("turn/start"), 3)

    def test_verification_executor_survives_caller_and_does_not_repeat_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            head = repository(repo)
            marker = root / "calls.txt"
            output, logs, plan_path = root / "result.json", root / "logs", root / "plan.json"
            commands = []
            for name, delay in (("first", 0), ("second", 3)):
                code = "from pathlib import Path;import time;p=Path(" + repr(str(marker)) + ");p.open('a').write('" + name + "\\n');time.sleep(" + str(delay) + ")"
                commands.append({"id": name, "argv": [sys.executable, "-c", code], "timeout_seconds": 15})
            run_registry.save_json(plan_path, {"schema_version": 1, "workdir": str(repo), "expected_head": head, "commands": commands, "resources": ["isolated-fixture-" + root.name]})
            code = "from pathlib import Path;import sys;import verification_runner;verification_runner.run_detached_plan(*map(Path,sys.argv[1:]))"
            caller = subprocess.Popen([sys.executable, "-c", code, str(plan_path), str(output), str(logs)], cwd=Path(__file__).parent)
            try:
                deadline = time.monotonic() + 15
                while not marker.exists() or "second" not in marker.read_text():
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.05)
                caller.kill(); caller.wait()
                with self.assertRaises(ValueError):
                    with run_registry.file_lock(output.with_suffix(".runner.lock"), 0):
                        pass
                result = verification_runner.run_detached_plan(plan_path, output, logs)
                self.assertEqual(result["status"], "passed")
                self.assertEqual(marker.read_text().splitlines(), ["first", "second"])
            finally:
                if caller.poll() is None:
                    caller.kill(); caller.wait()

    def test_split_materializes_distinct_deliveries_and_rejects_dependency_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Harness(Path(tmp), tickets="T-1,T-2")
            run.confirm()
            batches = [{"batch_id": "one", "tickets": ["T-1"], "train_branch": "codex/lot-one"},
                       {"batch_id": "two", "tickets": ["T-2"], "train_branch": "codex/lot-two"}]
            state = run.state()
            state["procedure"]["tickets"]["T-2"]["hard_dependencies"] = ["T-1"]
            run_registry.save_json(run.path, state)
            self.assertEqual(run.apply("TRAIN_SPLIT_RECORDED", batches=batches, user_decision_reference="user:split"), 2)
            state["procedure"]["tickets"]["T-2"]["hard_dependencies"] = []
            run_registry.save_json(run.path, state)
            self.assertEqual(run.apply("TRAIN_SPLIT_RECORDED", batches=batches, user_decision_reference="user:split"), 0)
            driver = runner.Driver(run.path, "thread-main", state["orchestrator_lease"]["epoch"], {"revision": "fixture"})
            phase_dispatch.execute_action(driver, {"action": "MATERIALIZE_SPLIT_RUNS"})
            paths = run.state()["procedure"]["split_plan"]["manifests"]
            self.assertEqual(len(paths), 2)
            children = [run_registry.load_json(p) for p in paths.values()]
            self.assertEqual({c["run_identity"]["train_branch"] for c in children}, {"codex/lot-one", "codex/lot-two"})
            self.assertEqual([len(c["procedure"]["tickets"]) for c in children], [1, 1])

    def test_artifact_hash_and_sum_are_verified_from_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            usage = token_usage.empty_usage(); usage["total_tokens"] = 20
            matrix = {"aggregate_usage": usage, "coverage_status": "partial", "ticket_rows": {},
                      "transverse_rows": {"run:unallocated": {"accounting_mode": "independent", "usage": usage}}}
            ledger = {"usage_matrix": matrix, "aggregate": {"usage": usage, "status": "partial", "authoritative_phase_count": 0, "measured_phase_count": 0}}
            event = {"authoritative_phase_count": 0, "measured_phase_count": 0, "token_reporting_status": "partial",
                     "usage_matrix_status": "partial", "orchestration_metrics_status": "unavailable"}
            for name, value in (("ledger", ledger), ("usage_matrix", matrix), ("orchestration_metrics", {"status": "unavailable"})):
                path = root / (name + ".json")
                run_registry.save_json(path, value)
                event[name + "_reference"] = str(path)
                event[name + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            controller.validate_usage_artifacts({"phases": {}}, event)
            matrix_path = Path(event["usage_matrix_reference"])
            matrix_path.write_text(matrix_path.read_text() + " ")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                controller.validate_usage_artifacts({"phases": {}}, event)
            matrix["transverse_rows"]["run:unallocated"]["usage"] = token_usage.empty_usage()
            ledger["usage_matrix"] = matrix
            for name, value in (("ledger", ledger), ("usage_matrix", matrix)):
                path = Path(event[name + "_reference"]); run_registry.save_json(path, value)
                event[name + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, "sum"):
                controller.validate_usage_artifacts({"phases": {}}, event)


if __name__ == "__main__":
    unittest.main()
