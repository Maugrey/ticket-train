#!/usr/bin/env python3
"""Execute durable ticket trains; emit compact decisions only when needed."""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import run_registry
import train_controller
import orchestration_metrics
import token_usage


RUNNER_VERSION = "2.0"
PACKET_FORMAT = "ticket-train-decision-v1"
DEFAULT_PACKET_MAX_BYTES = 16_384
DEFAULT_SOFT_TOKEN_LIMIT = 10_000_000
DEFAULT_HARD_TOKEN_LIMIT = 25_000_000
DEFAULT_MODEL_WAKE_LIMIT = 50
DEFAULT_TOOL_CALL_LIMIT = 500
DEFAULT_CONTEXT_COMPACTION_LIMIT = 1

WAIT_ACTIONS = {"WAIT_FOR_PHASE_TRANSITION", "WAIT_FOR_UNITY_SLOT", "AWAIT_HUMAN_GATE"}
UNITY_SLOT_ACTIONS = {
    "INITIALIZE_UNITY_SLOTS_DETERMINISTICALLY",
    "ACQUIRE_UNITY_SLOT_DETERMINISTICALLY",
    "RELEASE_UNITY_SLOT_DETERMINISTICALLY",
}
VERIFICATION_ACTIONS = {
    "RUN_DETERMINISTIC_TICKET_VERIFICATION",
    "RUN_FINAL_EXACT_HEAD_VERIFICATION_DETERMINISTICALLY",
}


class Driver:
    """One durable collect/apply/schedule loop. No model runs while idle.

    The controller remains the workflow authority. Effects and event receipts
    are separate from it so an interrupted external operation can be reconciled
    before another effect is allowed. Project hooks are argv vectors, not shell.
    """

    def __init__(self, state_path, owner, epoch, profile, host=None):
        import train_supervisor
        self.path = Path(state_path).resolve()
        self.owner, self.epoch, self.profile = owner, epoch, profile
        self.directory = self.path.parent / "driver"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state()
        self.host = host
        self.host_factory = train_supervisor.NativeEffects
        self.last_observation = 0
        self.children = {}
        self.child_locks = contextlib.ExitStack()
        self.blocked_actions = []

    def state(self):
        state = run_registry.load_json(self.path)
        run_registry.require_owner(state, self.owner, self.epoch)
        return state

    def effects(self):
        if self.host is None:
            self.host = self.host_factory(self.directory / "effects", self.profile.get("host_executable"))
        return self.host

    def apply(self, event):
        started_at, started = now_iso(), time.monotonic()
        event = copy.deepcopy(event)
        event.setdefault("event_id", "driver:" + sha256_json(event))
        before = self.state()
        if event["event_id"] in before["procedure"]["applied_events"]:
            # The controller still validates payload equality on this replay.
            with contextlib.redirect_stdout(io.StringIO()):
                train_controller.apply_event(argparse.Namespace(state=self.path, event=None, event_json=json.dumps(event),
                    expected_revision=before["procedure"]["revision"], owner_thread_id=self.owner, owner_epoch=self.epoch))
            return
        with contextlib.redirect_stdout(io.StringIO()):
            train_controller.apply_event(argparse.Namespace(
                state=self.path, event=None, event_json=json.dumps(event),
                expected_revision=self.state()["procedure"]["revision"],
                owner_thread_id=self.owner, owner_epoch=self.epoch))
        if event["type"] != "RUNTIME_OBSERVED":
            with run_registry.directory_lock(self.path.parent):
                state = self.state()
                metrics = orchestration_metrics.ensure_metrics(state)
                envelope = event.get("envelope") or {}
                usage = envelope.get("usage") or {}
                technical = event["type"] in {"PHASE_COMPLETED", "PHASE_TERMINATED"}
                kind = "technical-model" if technical else "deterministic"
                tokens = usage.get("total_tokens") if technical else 0
                phase = before["procedure"]["phases"].get(event.get("phase_key"), {})
                action_started = phase.get("created_at") if technical else started_at
                duration = (datetime.now(timezone.utc) - datetime.fromisoformat(action_started)).total_seconds() if technical and action_started else time.monotonic() - started
                metrics["actions"][event["event_id"]] = {
                    "action_id": event["event_id"], "action_name": event["type"], "expected_executor_kind": kind,
                    "actual_executor_kind": kind, "ticket_id": event.get("ticket_id"), "phase_key": event.get("phase_key"),
                    "controller_revision": state["procedure"]["revision"], "started_at": action_started, "ended_at": now_iso(),
                    "duration_seconds": round(duration, 6), "token_delta": tokens, "model_wake": technical,
                    "wake_issue": None, "outcome": "recorded", "status": "COMPLETED"}
                run_registry.save_json(self.path, state)

    def transaction(self, key, events):
        """Validate the whole result first; journal and replay immutable events."""
        events = copy.deepcopy(events)
        for index, event in enumerate(events):
            event.setdefault("event_id", "collected:" + sha256_json([key, index, event]))
        receipt = self.directory / "events" / (sha256_json(key) + ".json")
        if receipt.exists():
            prepared = run_registry.load_json(receipt)
            require(prepared["events"] == events, "An already collected result changed")
        else:
            simulated = copy.deepcopy(self.state())
            for event in events:
                # Validate remaining result data without discarding a completed
                # phase when its measured cost opens a checkpoint. Real replay
                # below still stops at that checkpoint before another event.
                for anomaly in train_controller.unresolved_cost_anomalies(simulated["procedure"]):
                    anomaly["status"] = "RESOLVED"
                train_controller.handle_event(simulated, copy.deepcopy(event))
            run_registry.save_json(receipt, {"key": key, "events": events})
        return self.apply_journal(events)

    def apply_journal(self, events):
        for event in events:
            state = self.state()
            if event["event_id"] in state["procedure"]["applied_events"]:
                continue
            if train_controller.unresolved_cost_anomalies(state["procedure"]):
                return False
            self.apply(event)
        return True

    def replay(self):
        self.__dict__.setdefault("replayed", set())
        for path in sorted((self.directory / "events").glob("*.json")):
            if str(path) in self.replayed:
                continue
            if self.apply_journal(run_registry.load_json(path)["events"]):
                self.replayed.add(str(path))

    def notify(self, kind, payload):
        value = {"kind": kind, "payload": payload}
        key = sha256_json(value)
        path = self.directory / "outbox" / (key + ".json")
        if not path.exists():
            run_registry.save_json(path, {**value, "created_at": now_iso()})
            print(json.dumps({"status": "attention", "kind": kind, "reference": str(path)}, ensure_ascii=False), flush=True)
        return False

    def command_hook(self, action):
        """One optional project adapter per nonstandard mechanical operation.

        It receives an immutable input path, uses its own result directory for
        receipts, and returns controller events. It must reconcile before replay.
        Raw stdout/stderr stay on disk; only errors/decisions reach the model.
        """
        hook = self.profile.get("commands", {}).get(action["action"])
        if not hook:
            return None
        require(isinstance(hook, dict) and hook.get("replay") == "reconcile", "Project effects must support reconciliation")
        argv = hook.get("argv")
        require(isinstance(argv, list) and argv and all(isinstance(x, str) for x in argv), "Project command must be an argv array")
        state = self.state()
        key = sha256_json({"action": action, "head": state["procedure"].get("train_head"),
                           "ticket": state["procedure"]["tickets"].get(action.get("ticket_id"), {})})
        directory = self.directory / "commands" / key
        directory.mkdir(parents=True, exist_ok=True)
        request = directory / "input.json"
        output = directory / "result.json"
        if not request.exists():
            run_registry.save_json(request, {"state": str(self.path), "action": action,
                                             "owner": self.owner, "owner_epoch": self.epoch,
                                             "result": str(output), "directory": str(directory)})
        if not output.exists():
            with (directory / "stdout.log").open("ab") as stdout, (directory / "stderr.log").open("ab") as stderr:
                completed = subprocess.run([*argv, str(request)], stdout=stdout, stderr=stderr,
                                           timeout=hook.get("timeout_seconds", 600), cwd=self.profile["repository"])
            if completed.returncode or not output.exists():
                raise ValueError("Project command failed; evidence: " + str(directory))
        result = run_registry.load_json(output)
        self.transaction(key, result["events"])
        return True

    def tick(self):
        import phase_dispatch
        state = self.state()
        with run_registry.directory_lock(self.path.parent):
            measured = self.state()
            refresh_activity(measured, ensure_control_plane(measured))
            run_registry.save_json(self.path, measured)
        # Human answers are events supplied by the owner. Merely observing an
        # unchanged pending question does not create another model turn.
        for path in sorted((self.directory / "inbox").glob("*.json")):
            event = run_registry.load_json(path)
            receipt = path.with_suffix(".accepted")
            if receipt.exists():
                continue
            if event.get("type") == "HOST_REQUEST_ANSWER":
                self.effects().answer(event)
            elif event.get("type") == "RETRY_ACTION":
                require(event.get("reason"), "Retry needs evidence that its cause was addressed")
                retry_path = self.directory / "retries" / (sha256_json(event["action"]) + ".json")
                run_registry.save_json(retry_path, {"attempts": 0, "reason": event["reason"]})
            else:
                self.apply(event)
            run_registry.save_json(receipt, {"answered_at": now_iso()})
        self.replay()
        if self.host and self.host.pending_requests:
            self.notify("host-input", {"server_instance": self.host.instance,
                                      "requests_reference": str(self.host.root / "requests")})
        try:
            if phase_dispatch.collect(self):
                return True
        except (ValueError, KeyError) as error:
            self.notify("result-invalid", {"error": str(error)})
        actions = train_controller.next_actions(self.state())
        progress = False
        self.blocked_actions = []
        for action in actions:
            if action["action"] in WAIT_ACTIONS:
                continue
            retry_path = self.directory / "retries" / (sha256_json(action) + ".json")
            retry = run_registry.load_json(retry_path) if retry_path.exists() else {}
            if retry.get("attempts", 0) >= 3:
                registry = action.get("registry_reference")
                available = False
                if action["action"] == "ACQUIRE_UNITY_SLOT_DETERMINISTICALLY" and registry and Path(registry).is_file():
                    slots = run_registry.load_json(Path(registry)).get("slots", [])
                    available = any(not slot.get("lease") and slot.get("status") in {"IDLE", "READY"} for slot in slots)
                if available:
                    retry = {"attempts": 0, "reason": "Unity slot registry now exposes an available slot"}
                    run_registry.save_json(retry_path, retry)
                else:
                    self.blocked_actions.append({"action": action, "error": retry.get("error")})
                    continue
            if retry.get("retry_at", 0) > time.time():
                continue
            try:
                result = self.command_hook(action)
                if result is None:
                    result = phase_dispatch.execute_action(self, action)
            except (ValueError, KeyError, subprocess.TimeoutExpired) as error:
                attempts = retry.get("attempts", 0) + 1
                run_registry.save_json(retry_path, {"attempts": attempts, "retry_at": time.time() + (15, 60, 300)[attempts - 1], "error": str(error)})
                if attempts == 3:
                    self.notify("action-blocked", {"action": action, "error": str(error), "attempts": attempts})
                result = False
            progress = progress or bool(result)
        return progress

    def run(self, max_seconds=None):
        import thread_runtime
        started = time.monotonic()
        reconnects = 0
        with run_registry.file_lock(self.directory / "driver.lock", timeout_seconds=0):
            while True:
                try:
                    progress = self.tick()
                    state = self.state()
                    if state["procedure"]["run_status"] == "COMPLETED":
                        return {"status": "completed", "revision": state["procedure"]["revision"]}
                    if max_seconds is not None and time.monotonic() - started >= max_seconds:
                        return {"status": "checkpointed", "state": str(self.path)}
                    with run_registry.directory_lock(self.path.parent):
                        current = self.state()
                        run_registry.renew_lease(current)
                        health_status = "blocked" if self.blocked_actions else ("working" if progress else "waiting")
                        current["driver_health"] = {"pid": os.getpid(), "observed_at": now_iso(),
                                                    "status": health_status,
                                                    "blocked_actions": self.blocked_actions}
                        run_registry.save_json(self.path, current)
                    if not progress:
                        if self.host:
                            self.host.host.wait(5)
                        else:
                            time.sleep(5)
                except thread_runtime.HostError as error:
                    reconnects += 1
                    if self.host:
                        self.host.close()
                        self.host = None
                    if reconnects <= 3:
                        time.sleep((5, 15, 30)[reconnects - 1])
                        continue
                    self.notify("host-unavailable", {"error": str(error), "attempts": reconnects})
                    return {"status": "blocked", "error": str(error), "state": str(self.path)}
                except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
                    self.notify("driver-blocked", {"error": str(error)})
                    # A stopped transport is restarted, but armed effects are
                    # reconciled from their receipts before any mutation.
                    return {"status": "blocked", "error": str(error), "state": str(self.path)}

    def close(self):
        for child in self.children.values():
            child.close()
        self.child_locks.close()
        if self.host:
            self.host.close()
            self.host = None


def preflight(state, profile, directory):
    """Check concrete host capability and freeze the actual project inputs."""
    import thread_runtime
    require(profile.get("revision"), "Project profile needs a source/environment revision")
    repository = Path(profile.get("repository", "")).resolve()
    require(repository.is_dir(), "Project repository is missing")
    check = subprocess.run(["git", "-C", str(repository), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    require(check.returncode == 0 and Path(check.stdout.strip()).resolve() == repository, "Profile must target the repository root")
    require(set(state["procedure"]["tickets"]).issubset(profile.get("tickets", {})), "Profile omits requested tickets")
    for key in state["procedure"]["tickets"]:
        source = profile["tickets"][key]
        require(source.get("source_reference") and source.get("source_revision"), "Ticket source/reference revision is missing: " + key)
    executable = Path(thread_runtime.app_server_executable(profile.get("host_executable"))).resolve()
    proof = run_registry.load_json(Path(profile.get("native_visibility_evidence", "")))
    require(proof.get("format") == "ticket-train-native-capability-v1" and proof.get("desktop_read_verified") is True,
            "Native visibility needs an actual desktop read receipt")
    require(Path(proof.get("host_executable", "")).resolve() == executable, "Visibility proof targets another executable")
    require(proof.get("host_sha256") == hashlib.sha256(executable.read_bytes()).hexdigest(), "Native executable changed; verify its capability before dispatch")
    ids = {(proof.get(k, {}).get("thread") or {}).get("id") for k in ("first", "second")}
    require(None not in ids and len(ids) == 2, "Capability proof omits the two observed native test tasks")
    if state.get("execution_mode") != "dry-run" and not all(profile["tickets"][key].get("mode") == "validation-only" for key in state["procedure"]["tickets"]):
        require(profile.get("github_repository"), "Delivery requires a GitHub repository")
        require(all(profile.get("final_verification", {}).get(k) for k in ("verification_plan_reference", "verification_evidence_reference")),
                "Prepare the final verification contract before starting a live train")
    target = directory / "profile.json"
    if target.exists():
        saved = run_registry.load_json(target)
        require(saved["sha256"] == sha256_json(profile), "Pinned project profile changed; restore the recorded profile for this run")
    else:
        run_registry.save_json(target, {"profile": profile, "sha256": sha256_json(profile), "verified_at": now_iso()})
        run_registry.save_json(directory / "native-capability.json", proof)


def supervise_worker(argv, directory, attempts=3):
    """A small OS parent restarts a crashed driver without waking a model."""
    with run_registry.file_lock(directory / "guardian.lock", timeout_seconds=0):
        for attempt in range(attempts):
            process = subprocess.Popen(argv)
            run_registry.save_json(directory / "guardian.json", {"pid": os.getpid(), "worker_pid": process.pid,
                "attempt": attempt + 1, "started_at": now_iso()})
            result = process.wait()
            if result == 0:
                return 0
            if attempt + 1 < attempts:
                time.sleep((5, 15)[min(attempt, 1)])
        value = {"status": "blocked", "reason": "Driver restart budget exhausted", "exit_code": result}
        run_registry.save_json(directory / "guardian-result.json", value)
        print(json.dumps(value), flush=True)
        return 2


def drive(args):
    profile = run_registry.load_json(args.profile)
    state = run_registry.load_json(args.state)
    run_registry.require_owner(state, args.owner_thread_id, args.owner_epoch)
    release = run_registry.verify_release(state)
    if Path(__file__).resolve().parent.parent != release:
        return subprocess.call([sys.executable, str(release / "scripts" / "control_plane_runner.py"), *sys.argv[1:]])
    directory = Path(args.state).resolve().parent / "driver"
    preflight(state, profile, directory)
    if args.preflight_only:
        print(json.dumps({"status": "ready", "release": str(release), "profile": str(directory / "profile.json")}))
        return 0
    if not args.worker:
        return supervise_worker([sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], "--worker"], directory)
    driver = Driver(args.state, args.owner_thread_id, args.owner_epoch, profile)
    try:
        result = driver.run(args.max_seconds)
    finally:
        driver.close()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] != "blocked" else 2


class RunnerError(ValueError):
    """Raised when runner state or inputs are invalid."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RunnerError(message)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def ensure_control_plane(state: dict[str, Any]) -> dict[str, Any]:
    value = state.get("control_plane")
    if not isinstance(value, dict):
        value = {
            "schema_version": 1,
            "runner_version": RUNNER_VERSION,
            "packet_sequence": 0,
            "last_semantic_hash": None,
            "last_packet_reference": None,
            "suppressed_unchanged_observations": 0,
            "thresholds": {
                "soft_total_tokens": DEFAULT_SOFT_TOKEN_LIMIT,
                "hard_total_tokens": DEFAULT_HARD_TOKEN_LIMIT,
                "max_model_wakes": DEFAULT_MODEL_WAKE_LIMIT,
                "max_tool_calls": DEFAULT_TOOL_CALL_LIMIT,
                "max_context_compactions": DEFAULT_CONTEXT_COMPACTION_LIMIT,
                "packet_max_bytes": DEFAULT_PACKET_MAX_BYTES,
            },
            "segments": [],
            "rotation": {"status": "CLEAR", "reasons": []},
        }
        state["control_plane"] = value
    value.setdefault("schema_version", 1)
    value["runner_version"] = RUNNER_VERSION
    value.setdefault("packet_sequence", 0)
    value.setdefault("last_semantic_hash", None)
    value.setdefault("last_packet_reference", None)
    value.setdefault("suppressed_unchanged_observations", 0)
    value.setdefault("segments", [])
    value.setdefault("rotation", {"status": "CLEAR", "reasons": []})
    thresholds = value.setdefault("thresholds", {})
    thresholds.setdefault("soft_total_tokens", DEFAULT_SOFT_TOKEN_LIMIT)
    thresholds.setdefault("hard_total_tokens", DEFAULT_HARD_TOKEN_LIMIT)
    thresholds.setdefault("max_model_wakes", DEFAULT_MODEL_WAKE_LIMIT)
    thresholds.setdefault("max_tool_calls", DEFAULT_TOOL_CALL_LIMIT)
    thresholds.setdefault("max_context_compactions", DEFAULT_CONTEXT_COMPACTION_LIMIT)
    thresholds.setdefault("packet_max_bytes", DEFAULT_PACKET_MAX_BYTES)
    policy = state.get("orchestrator_rotation_policy")
    if isinstance(policy, dict):
        mapping = {
            "soft_total_tokens": "soft_total_tokens",
            "hard_total_tokens": "hard_total_tokens",
            "max_model_wakes": "max_model_wakes",
            "max_tool_calls": "max_tool_calls",
            "max_context_compactions": "max_context_compactions",
            "decision_packet_max_bytes": "packet_max_bytes",
        }
        for source, destination in mapping.items():
            if policy.get(source) is not None:
                thresholds[destination] = int(policy[source])
    return value


def current_owner(state: dict[str, Any]) -> str | None:
    lease = state.get("orchestrator_lease")
    return str(lease.get("owner_thread_id")) if isinstance(lease, dict) and lease.get("owner_thread_id") else None


def current_segment(control_plane: dict[str, Any], owner: str) -> dict[str, Any]:
    segments = control_plane.setdefault("segments", [])
    require(isinstance(segments, list), "control_plane.segments must be a list")
    if segments and isinstance(segments[-1], dict) and segments[-1].get("thread_id") == owner and segments[-1].get("status") == "ACTIVE":
        return segments[-1]
    segment = {
        "thread_id": owner,
        "status": "ACTIVE",
        "started_at": now_iso(),
        "baseline_total_tokens": None,
        "latest_total_tokens": None,
        "token_delta": None,
        "model_wakes": None,
        "tool_calls": None,
        "context_compactions": None,
        "measurement_status": "unavailable",
    }
    if segments and isinstance(segments[-1], dict) and segments[-1].get("status") == "ACTIVE":
        segments[-1]["status"] = "SUPERSEDED"
        segments[-1]["ended_at"] = now_iso()
    segments.append(segment)
    return segment


def rotation_reasons(segment: dict[str, Any], thresholds: dict[str, Any]) -> tuple[list[str], list[str]]:
    soft: list[str] = []
    hard: list[str] = []
    token_delta = int(segment.get("token_delta") or 0)
    model_wakes = int(segment.get("model_wakes") or 0)
    tool_calls = int(segment.get("tool_calls") or 0)
    compactions = int(segment.get("context_compactions") or 0)
    if token_delta >= int(thresholds["soft_total_tokens"]):
        soft.append("orchestrator token soft limit reached")
    if token_delta >= int(thresholds["hard_total_tokens"]):
        hard.append("orchestrator token hard limit reached")
    if model_wakes >= int(thresholds["max_model_wakes"]):
        hard.append("orchestrator model-wake limit reached")
    if tool_calls >= int(thresholds["max_tool_calls"]):
        hard.append("orchestrator tool-call limit reached")
    if compactions >= int(thresholds["max_context_compactions"]):
        hard.append("orchestrator context-compaction limit reached")
    return soft, hard


def refresh_rotation(control_plane: dict[str, Any], owner: str) -> dict[str, Any]:
    segment = current_segment(control_plane, owner)
    soft, hard = rotation_reasons(segment, control_plane["thresholds"])
    pending = control_plane.get("rotation") if isinstance(control_plane.get("rotation"), dict) else {}
    if pending.get("status") == "PREPARED":
        status = "PREPARED"
    elif hard:
        status = "REQUIRED"
    elif soft:
        status = "SOFT_WARNING"
    else:
        status = "CLEAR" if segment.get("token_delta") is not None else "UNMEASURED"
    rotation = {
        "status": status,
        "reasons": hard or soft,
        "evaluated_at": now_iso(),
        "thread_id": owner,
        "token_delta": segment.get("token_delta"),
        "model_wakes": segment.get("model_wakes"),
        "tool_calls": segment.get("tool_calls"),
        "context_compactions": segment.get("context_compactions"),
    }
    control_plane["rotation"] = rotation
    return rotation


def compact_context_packet(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        key: value.get(key)
        for key in ("format", "reference", "sha256", "byte_count", "profile_revision", "exact_base", "exact_head")
        if value.get(key) is not None
    }


def compact_action(action: dict[str, Any]) -> dict[str, Any]:
    # Actions are executable contracts, not narrative summaries. A whitelist
    # silently dropped Unity leases, input answers and reconciliation routes.
    # Preserve all controller fields; the packet byte limit still fails closed.
    result = dict(action)
    action_name = str(action.get("action"))
    executor_kind = orchestration_metrics.classify_action(action_name)
    result["executor_kind"] = executor_kind
    result["authorized_handler"] = (
        "scripts/unity_slot_adapter.py"
        if action_name in UNITY_SLOT_ACTIONS
        else "scripts/verification_adapter.py"
        if action_name in VERIFICATION_ACTIONS
        else "main-thread-controller-adapter"
        if executor_kind == "adapter"
        else "named-deterministic-command"
        if executor_kind == "deterministic"
        else "fresh-technical-model-task"
    )
    if "context_packet" in action:
        result["context_packet"] = compact_context_packet(action.get("context_packet"))
    if "anomalies" in action and isinstance(action["anomalies"], list):
        result["anomalies"] = [
            {
                key: item.get(key)
                for key in ("anomaly_id", "phase_key", "ticket_id", "reason", "status")
                if isinstance(item, dict) and item.get(key) is not None
            }
            for item in action["anomalies"]
        ]
    return result


def wake_kind(
    actions: list[dict[str, Any]], rotation: dict[str, Any], pending_handoff: Any,
    supervision: dict[str, Any],
) -> str:
    if isinstance(pending_handoff, dict) and pending_handoff.get("status") == "PREPARED":
        return "COMPLETE_CONTROLLED_HANDOFF"
    names = {str(item.get("action")) for item in actions}
    if (not names or names.issubset(WAIT_ACTIONS)) and not supervision.get("orchestrator_status_required"):
        return "NO_MODEL_WAKE"
    if rotation.get("status") == "REQUIRED":
        return "ROTATE_ORCHESTRATOR"
    if supervision.get("orchestrator_status_required"):
        return "WAKE_ADAPTER"
    executor_kinds = {orchestration_metrics.classify_action(name) for name in names}
    if "technical-model" in executor_kinds:
        return "WAKE_TECHNICAL_DECISION"
    if "adapter" in executor_kinds:
        return "WAKE_ADAPTER"
    if executor_kinds == {"deterministic"}:
        return "RUN_DETERMINISTIC"
    raise RunnerError("action executor taxonomy did not produce a wake class")


def build_packet(state: dict[str, Any], control_plane: dict[str, Any]) -> dict[str, Any]:
    proc = train_controller.procedure(state)
    owner = current_owner(state)
    require(owner is not None, "orchestrator lease has no owner")
    actions = [compact_action(item) for item in train_controller.next_actions(state)]
    supervision = train_controller.supervision_projection(state, actions)
    rotation = refresh_rotation(control_plane, owner)
    pending_action = state.get("pending_human_action")
    if isinstance(pending_action, dict):
        pending_action = {
            key: pending_action.get(key)
            for key in (
                "gate_id", "gate_type", "ticket_id", "revision", "reason",
                "decision_summary", "blocked_scope", "continuing_scope",
                "accepted_replies", "notification_status", "question",
            )
            if pending_action.get(key) is not None
        }
    packet = {
        "format": PACKET_FORMAT,
        "run_id": state.get("run_id"),
        "run_fingerprint": (state.get("run_identity") or {}).get("fingerprint"),
        "controller_revision": proc.get("revision"),
        "controller_updated_at": proc.get("updated_at"),
        "owner_thread_id": owner,
        "wake_kind": wake_kind(
            actions, rotation, state.get("pending_orchestrator_handoff"), supervision
        ),
        "ticket_states": {
            ticket_id: value.get("status")
            for ticket_id, value in proc.get("tickets", {}).items()
            if isinstance(value, dict)
        },
        "active_phases": train_controller.active_phase_inventory(state),
        "supervision_projection": supervision,
        "pending_human_action": pending_action,
        "finalization_status": (proc.get("finalization") or {}).get("status"),
        "cost_anomaly_count": len(train_controller.unresolved_cost_anomalies(proc)),
        "orchestrator_budget": {
            key: rotation.get(key)
            for key in (
                "status", "reasons", "thread_id", "token_delta", "model_wakes",
                "tool_calls", "context_compactions",
            )
        },
        "pending_orchestrator_handoff": state.get("pending_orchestrator_handoff"),
        "next_actions": actions,
        "turn_control": train_controller.turn_control(state),
    }
    return packet


def write_packet(state_path: Path, control_plane: dict[str, Any], packet: dict[str, Any], output_dir: Path | None) -> dict[str, Any]:
    semantic_hash = sha256_json(packet)
    if semantic_hash == control_plane.get("last_semantic_hash"):
        if packet["wake_kind"] != "NO_MODEL_WAKE":
            # Delivery is not execution. Reuse the same packet until the
            # controller observes an outcome; never launch a duplicate blindly.
            return {
                "status": "action-pending",
                "wake_kind": packet["wake_kind"],
                "semantic_hash": semantic_hash,
                "packet_reference": control_plane.get("last_packet_reference"),
                "controller_revision": packet["controller_revision"],
                "turn_control": packet["turn_control"],
            }
        control_plane["suppressed_unchanged_observations"] = int(control_plane.get("suppressed_unchanged_observations", 0)) + 1
        return {
            "status": "unchanged-suppressed",
            "wake_kind": "NO_MODEL_WAKE",
            "semantic_hash": semantic_hash,
            "packet_reference": control_plane.get("last_packet_reference"),
            "turn_control": packet["turn_control"],
        }
    sequence = int(control_plane.get("packet_sequence", 0)) + 1
    stamped = {**packet, "packet_sequence": sequence, "generated_at": now_iso(), "semantic_hash": semantic_hash}
    encoded = json.dumps(stamped, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    maximum = int(control_plane["thresholds"]["packet_max_bytes"])
    require(len(encoded) <= maximum, f"decision packet exceeds {maximum} bytes")
    root = output_dir.expanduser().resolve() if output_dir else state_path.parent / "control-plane" / "wake-packets"
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"{sequence:06d}-{semantic_hash[:12]}.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_bytes(encoded)
    temporary.replace(destination)
    control_plane["packet_sequence"] = sequence
    control_plane["last_semantic_hash"] = semantic_hash
    control_plane["last_packet_reference"] = str(destination)
    control_plane["last_packet_bytes"] = len(encoded)
    control_plane["last_packet_created_at"] = stamped["generated_at"]
    return {
        "status": "packet-written",
        "wake_kind": packet["wake_kind"],
        "semantic_hash": semantic_hash,
        "packet_reference": str(destination),
        "packet_bytes": len(encoded),
        "controller_revision": packet["controller_revision"],
        "turn_control": packet["turn_control"],
    }


def step(args: argparse.Namespace) -> int:
    path = args.state.expanduser().resolve()
    with run_registry.directory_lock(path.parent):
        state = run_registry.load_json(path)
        run_registry.require_owner(state, getattr(args, "owner_thread_id", None), getattr(args, "owner_epoch", None))
        control_plane = ensure_control_plane(state)
        refresh_activity(state, control_plane)
        packet = build_packet(state, control_plane)
        result = write_packet(path, control_plane, packet, args.output_dir)
        metrics = orchestration_metrics.ensure_metrics(state)
        if result["status"] == "packet-written":
            wake_counts = metrics["decision_packets_by_wake_kind"]
            wake_kind = str(result["wake_kind"])
            wake_counts[wake_kind] = int(wake_counts.get(wake_kind, 0)) + 1
            metrics["updated_at"] = now_iso()
        state["manifest_updated_at"] = now_iso()
        run_registry.save_json(path, state)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0


def refresh_activity(state: dict[str, Any], control_plane: dict[str, Any]) -> None:
    owner = current_owner(state)
    if not owner:
        return
    segment = current_segment(control_plane, owner)
    paths = token_usage.candidate_session_files(token_usage.resolve_codex_home(None), owner)
    if not paths:
        segment["measurement_status"] = "unavailable"
        return
    source = paths[0]
    stamp = [source.stat().st_size, source.stat().st_mtime_ns]
    if segment.get("source_stamp") == stamp:
        return
    measured = token_usage.measure_session(source, owner, segment["started_at"], cursor=segment.get("usage_cursor"))
    if measured:
        segment.update({key: measured[key] for key in ("model_wakes", "tool_calls", "context_compactions", "measurement_status", "counter_resets")})
        segment.update(token_delta=measured["usage"]["total_tokens"],
                       latest_total_tokens=measured["usage"]["total_tokens"], baseline_total_tokens=0,
                       measured_at=now_iso(), source_stamp=stamp, source_reference=str(source), usage_cursor=measured["cursor"])


def record_activity(args: argparse.Namespace) -> int:
    path = args.state.expanduser().resolve()
    with run_registry.directory_lock(path.parent):
        state = run_registry.load_json(path)
        run_registry.require_owner(state, args.thread_id, getattr(args, "owner_epoch", None))
        owner = current_owner(state)
        require(owner == args.thread_id, "activity thread must own the orchestrator lease")
        control_plane = ensure_control_plane(state)
        segment = current_segment(control_plane, args.thread_id)
        baseline = int(args.baseline_total_tokens)
        latest = int(args.latest_total_tokens)
        require(latest >= baseline >= 0, "token counters must satisfy latest >= baseline >= 0")
        prior_baseline = int(segment.get("baseline_total_tokens") or 0)
        if segment.get("measured_at") is not None:
            require(baseline == prior_baseline, "orchestrator segment baseline cannot change")
        for label, value in (
            ("model_wakes", args.model_wakes),
            ("tool_calls", args.tool_calls),
            ("context_compactions", args.context_compactions),
        ):
            require(int(value) >= int(segment.get(label) or 0), f"{label} cannot decrease")
        segment.update({
            "baseline_total_tokens": baseline,
            "latest_total_tokens": latest,
            "token_delta": latest - baseline,
            "model_wakes": int(args.model_wakes),
            "tool_calls": int(args.tool_calls),
            "context_compactions": int(args.context_compactions),
            "measured_at": now_iso(),
        })
        rotation = refresh_rotation(control_plane, args.thread_id)
        state["manifest_updated_at"] = now_iso()
        run_registry.save_json(path, state)
    output = {"status": "recorded", "thread_id": args.thread_id, "segment": segment, "rotation": rotation}
    sys.stdout.write(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    execution = commands.add_parser("drive", help="Collect results and execute successors without idle model wakes")
    execution.add_argument("--state", type=Path, required=True)
    execution.add_argument("--profile", type=Path, required=True)
    execution.add_argument("--owner-thread-id", required=True)
    execution.add_argument("--owner-epoch", required=True)
    execution.add_argument("--max-seconds", type=float)
    execution.add_argument("--preflight-only", action="store_true")
    execution.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    execution.set_defaults(handler=drive)

    run_step = commands.add_parser("step")
    run_step.add_argument("--state", type=Path, required=True)
    run_step.add_argument("--output-dir", type=Path)
    run_step.add_argument("--owner-thread-id", required=True)
    run_step.add_argument("--owner-epoch", required=True)
    run_step.set_defaults(handler=step)

    activity = commands.add_parser("record-activity")
    activity.add_argument("--state", type=Path, required=True)
    activity.add_argument("--thread-id", required=True)
    activity.add_argument("--owner-epoch", required=True)
    activity.add_argument("--baseline-total-tokens", type=int, required=True)
    activity.add_argument("--latest-total-tokens", type=int, required=True)
    activity.add_argument("--model-wakes", type=int, required=True)
    activity.add_argument("--tool-calls", type=int, required=True)
    activity.add_argument("--context-compactions", type=int, required=True)
    activity.set_defaults(handler=record_activity)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.handler(args))
    except (OSError, json.JSONDecodeError, RunnerError, ValueError) as error:
        sys.stderr.write(json.dumps({"status": "rejected", "error": str(error)}, ensure_ascii=False) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
