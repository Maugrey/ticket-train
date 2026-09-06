#!/usr/bin/env python3
"""Guard one visible phase launch across the desktop tool boundary.

Used by phase_dispatch.js in the canonical owner's active turn. Does not call
Codex, select a model, infer approvals, or execute a technical phase itself.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path

import continuation_adapter
import run_registry
import thread_runtime
import train_controller as controller


INTENT_ACTIONS = {
    "plan_contract_validation": "RECORD_PLAN_CONTRACT_VALIDATION_DISPATCH_INTENT",
}


def load(path):
    return run_registry.load_json(Path(path))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def context(spec_path):
    spec = load(spec_path)
    controller.require(spec.get("schema_version") == 1, "unsupported dispatch specification")
    controller.require_fields(spec, ("state", "owner", "phase_key", "target", "title"), "dispatch specification")
    path = Path(spec["state"]).resolve()
    state = load(path)
    continuation_adapter.require_owner(state, spec["owner"])
    key = hashlib.sha256(spec["phase_key"].encode()).hexdigest()[:24]
    directory = path.parent / "dispatches" / key
    directory.mkdir(parents=True, exist_ok=True)
    return spec, path, state, directory


def apply(path, owner, event):
    state = load(path)
    continuation_adapter.require_owner(state, owner)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        controller.apply_event(argparse.Namespace(
            state=path, expected_revision=state["procedure"]["revision"],
            event=None, event_json=json.dumps(event), owner_thread_id=owner))
    return json.loads(output.getvalue())


def unwrap(raw):
    controller.require(isinstance(raw, dict) and not raw.get("isError"), "task tool returned an error; reconcile, do not retry creation")
    if "threadId" in raw or "clientThreadId" in raw:
        return raw
    if isinstance(raw.get("structuredContent"), dict):
        return unwrap(raw["structuredContent"])
    blocks = [b for b in raw.get("content", []) if b.get("type") == "text"]
    controller.require(len(blocks) == 1, "unrecognized creation receipt; reconcile, do not retry")
    return unwrap(json.loads(blocks[0]["text"]))


def begin(spec_path):
    spec, path, state, directory = context(spec_path)
    journal_path = directory / "launch.json"
    receipt_path = directory / "creation-receipt.json"
    with run_registry.directory_lock(directory):
        state = load(path)
        continuation_adapter.require_owner(state, spec["owner"])
        phase = state["procedure"]["phases"].get(spec["phase_key"])
        if journal_path.exists():
            journal = load(journal_path)
            controller.require(journal["spec_sha256"] == digest(spec), "dispatch specification changed; reconcile the existing attempt")
            if journal.get("status") == "RECORDED":
                return {"status": "already-recorded", "may_create": False,
                        "phase_key": spec["phase_key"], "thread_id": (phase or {}).get("thread_id"),
                        "next_actions": controller.next_actions(state)}
            if receipt_path.exists():
                return {"status": "recover-receipt", "may_create": False,
                        "receipt_path": str(receipt_path), "raw_receipt": load(receipt_path),
                        "attempt_directory": str(directory)}
            raise ValueError("launch outcome unknown: attempt already armed without a durable receipt; reconcile product task identity, NEVER create again")
        if phase is None:
            event = spec.get("intent_event")
            controller.require(isinstance(event, dict) and event.get("type") == "PHASE_DISPATCHED", "an existing phase or supported dispatch intent is required")
            controller.require(event.get("phase_key") == spec["phase_key"], "intent phase mismatch")
            required_action = INTENT_ACTIONS.get(event.get("kind"))
            controller.require(required_action is not None, "unsupported new intent; use the controller's existing phase-specific adapter")
            controller.require(any(a["action"] == required_action and a.get("ticket_id") == event.get("ticket_id")
                                   for a in controller.next_actions(state)), "dispatch intent is not a current authorized action")
            apply(path, spec["owner"], event)
            state = load(path)
        handoff = continuation_adapter.handoff(argparse.Namespace(
            state=path, owner=spec["owner"], expected_revision=state["procedure"]["revision"], phase_key=spec["phase_key"]))
        controller.require(handoff["tool"] == "create_thread", "this adapter only creates authorized new phases; use the same-thread resume adapter")
        request = {"target": spec["target"], "title": spec["title"], "prompt": handoff["prompt"],
                   "model": handoff["model"], "thinking": handoff["thinking"]}
        attempt = {"status": "ARMED", "spec_sha256": digest(spec), "phase_key": spec["phase_key"],
                   "owner": spec["owner"], "request": request,
                   "callback_contract_reference": handoff["callback_contract_reference"], "created_at": controller.now_iso()}
        # Fail closed BEFORE crossing the non-transactional external boundary.
        # A crash in either of the following writes must not authorize a repeat.
        apply(path, spec["owner"], {"event_id": "launch-armed:" + digest(spec), "type": "PHASE_LAUNCH_OBSERVED",
                                  "phase_key": spec["phase_key"], "launch_state": "LAUNCH_UNKNOWN"})
        run_registry.save_json(journal_path, attempt)
        return {"status": "launch-armed", "may_create": True, "tool_request": request,
                "receipt_path": str(receipt_path), "attempt_directory": str(directory)}


def record(spec_path, observation_path=None):
    spec, path, state, directory = context(spec_path)
    with run_registry.directory_lock(directory):
        journal = load(directory / "launch.json")
        controller.require(journal["spec_sha256"] == digest(spec), "dispatch specification changed")
        state = load(path)
        continuation_adapter.require_owner(state, spec["owner"])
        value = state["procedure"]["phases"][spec["phase_key"]]
        raw_path = directory / "creation-receipt.json"
        raw = load(raw_path)
        receipt = unwrap(raw)
        task_id = receipt.get("threadId")
        if journal.get("status") == "RECORDED":
            return {"status": "already-recorded", "thread_id": value.get("thread_id"), "next_actions": controller.next_actions(state)}
        controller.require(value["launch_state"] not in {"COMPLETED", "FAILED", "BLOCKED", "NEEDS_INPUT"}, "phase already terminal; do not overwrite its result with a launch")
        event = {"event_id": "launch-receipt:" + digest(raw), "type": "PHASE_LAUNCH_OBSERVED",
                 "phase_key": spec["phase_key"], "reconciled": True,
                 "callback_target_thread_id": spec["owner"],
                 "callback_contract_reference": journal["callback_contract_reference"]}
        if task_id:
            controller.require(observation_path is not None, "real task ID requires a product visibility observation")
            observation_path = Path(observation_path).resolve()
            controller.require(observation_path.parent == directory, "observation must belong to this launch attempt")
            observations = thread_runtime.parse_wait_result(load(observation_path))
            matching = [o for o in observations if o["thread_id"] == task_id]
            controller.require(len(matching) == 1, "product observation did not verify the created task ID")
            controller.require(matching[0]["host_id"] == receipt.get("hostId", "local"), "creation/observation host mismatch")
            event.update(launch_state="RUNNING", thread_id=task_id, host_id=matching[0]["host_id"],
                         execution_visibility="user-visible", visibility_verified=True,
                         visibility_verified_at=controller.now_iso(), visibility_evidence_reference=str(observation_path))
        else:
            controller.require(bool(receipt.get("clientThreadId")), "creation receipt has no task or queued client ID")
            event.update(launch_state="QUEUED", client_thread_id=receipt["clientThreadId"])
        if journal.get("receipt_event"):
            controller.require(journal.get("receipt_sha256") == digest(raw), "durable creation receipt changed")
            event = journal["receipt_event"]
        else:
            journal.update(receipt_event=event, receipt_sha256=digest(raw))
            run_registry.save_json(directory / "launch.json", journal)
        apply(path, spec["owner"], event)
        journal.update(status="RECORDED", receipt_sha256=digest(raw), recorded_at=controller.now_iso())
        run_registry.save_json(directory / "launch.json", journal)
        state = load(path)
        return {"status": "recorded", "phase_key": spec["phase_key"], "thread_id": task_id,
                "client_thread_id": receipt.get("clientThreadId"), "controller_revision": state["procedure"]["revision"],
                "next_actions": controller.next_actions(state), "turn_control": controller.turn_control(state)}


def observe(spec_path, observation_path):
    spec, path, state, directory = context(spec_path)
    controller.require(Path(observation_path).resolve().parent == directory, "observation must belong to this attempt")
    return continuation_adapter.advance(argparse.Namespace(
        command="observe", state=path, owner=spec["owner"],
        expected_revision=state["procedure"]["revision"], snapshot=Path(observation_path)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("begin", "record", "observe"))
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--observation", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "begin":
            result = begin(args.spec)
        elif args.command == "record":
            result = record(args.spec, args.observation)
        else:
            result = observe(args.spec, args.observation)
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"status": "rejected", "may_create": False, "error": str(error),
                          "supported_use": "visible phase creation/receipt only",
                          "existing_contract_result_handler": "continuation_adapter.py collect-contract --state MANIFEST --owner OWNER --expected-revision REVISION --phase-key PHASE --result EXISTING_RESULT --snapshot RAW_WAIT_RESULT; no collection specification is required"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
