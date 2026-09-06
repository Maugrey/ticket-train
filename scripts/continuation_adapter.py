#!/usr/bin/env python3
"""Apply a response/observation and expose its successor, or build a phase prompt.

No task is started here. The current owner executes the emitted tool request
and records its real outcome. Approval semantics stay in the controller.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sys

import control_plane_runner as runner
import run_registry
import train_controller as controller


CONTRACT_FIELDS = (
    "status", "ticket_id", "phase_key", "analysis_complexity",
    "residual_implementation_complexity", "verification_complexity",
    "complexity_reduction_evidence", "unresolved_implementation_difficulty",
)


def collect_contract(args: argparse.Namespace) -> dict:
    """Collect an existing visible result atomically; never dispatch a task.

    The generated immutable event is a replay journal, not a caller-authored
    collection specification. Keep the exact event after interruption so an
    already-applied observation does not need a fabricated fresh timestamp.
    """
    state = run_registry.load_json(args.state)
    require_owner(state, args.owner)
    value = controller.phase(state["procedure"], args.phase_key)
    controller.require(value["kind"] == "plan_contract_validation", "collect-contract only accepts contract validation phases")
    result_path = args.result.expanduser().resolve()
    controller.require(result_path.is_relative_to(args.state.resolve().parent), "result must be a private artifact in this canonical run")
    data = result_path.read_bytes()
    result_sha = hashlib.sha256(data).hexdigest()
    result = json.loads(data)
    controller.require_fields(result, CONTRACT_FIELDS + ("checked_references", "tests_and_checks", "residual_risks", "files_modified", "usage"), "contract result")
    controller.require(result["phase_key"] == args.phase_key and str(result["ticket_id"]) == value["ticket_id"], "contract result phase/ticket mismatch")
    controller.require(result.get("run_id", state["run_id"]) == state["run_id"], "contract result run mismatch")
    source = Path(value["context_packet"]["reference"])
    if not source.is_absolute():
        source = args.state.resolve().parent / source
    context_data = source.read_bytes()
    controller.require(hashlib.sha256(context_data).hexdigest() == value["context_packet"]["sha256"], "dispatched contract context changed")
    context = json.loads(context_data)
    expected_result = context["payload"].get("result_requirements", {}).get("json_path")
    if expected_result:
        controller.require(Path(expected_result).resolve() == result_path, "result differs from dispatched output path")
    checked = {Path(x["reference"]).resolve(): x["sha256"] for x in result["checked_references"]}
    controller.require(checked.get(source.resolve()) == value["context_packet"]["sha256"], "result does not cover the dispatched context hash")
    for item in context["payload"].get("required_inputs", []):
        input_path = Path(item["reference"]).resolve()
        controller.require(checked.get(input_path) == item["sha256"], "contract result omitted or mismatched a required input")
        controller.require(hashlib.sha256(input_path.read_bytes()).hexdigest() == item["sha256"], "required contract input changed after validation")
    controller.require(result.get("analysis_revision", state["procedure"]["tickets"][value["ticket_id"]]["analysis"]["analysis_revision"])
                       == state["procedure"]["tickets"][value["ticket_id"]]["analysis"]["analysis_revision"], "result analysis revision mismatch")
    directory = args.state.resolve().parent / "collections"
    directory.mkdir(exist_ok=True)
    journal = directory / (hashlib.sha256(args.phase_key.encode()).hexdigest()[:24] + ".json")
    with run_registry.directory_lock(directory):
        if journal.exists():
            event = run_registry.load_json(journal)
            controller.require(event["result_sha256"] == result_sha and event["result_reference"] == str(result_path), "collected result changed; reconcile instead of overwriting the prior verdict")
            if event["event_id"] not in state["procedure"].get("applied_events", {}) and args.snapshot is not None:
                # No transition was committed: refresh a real expired snapshot,
                # not its timestamp. Applied event payloads remain immutable.
                snapshot = args.snapshot.expanduser().resolve()
                controller.require(snapshot.is_relative_to(args.state.resolve().parent), "snapshot must belong to this canonical run")
                event.update(snapshot_reference=str(snapshot), snapshot_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest())
                controller.handle_event(copy.deepcopy(state), event)
                run_registry.save_json(journal, event)
        else:
            controller.require(value["launch_state"] == "RUNNING", "contract phase must be visible RUNNING before its first collection")
            controller.require(args.snapshot is not None, "capture a raw product wait_threads snapshot for this existing task")
            snapshot = args.snapshot.expanduser().resolve()
            controller.require(snapshot.is_relative_to(args.state.resolve().parent), "snapshot must belong to this canonical run")
            next_action = result.get("requested_or_recommended_next_action") or result.get("recommended_next_action")
            controller.require(bool(next_action), "contract result needs a recommended next action")
            envelope = {
                "phase_key": args.phase_key, "phase_status": "completed",
                "actual_model": value["requested_model"], "actual_reasoning_effort": value["requested_reasoning_effort"],
                "routing_evidence": "recorded explicit desktop dispatch; child self-identification is diagnostic only",
                "result_summary": result.get("result_summary") or f"Contract validation: {result['status']}; findings retained in the result artifact.",
                "artifacts": {"result_reference": str(result_path), "result_sha256": result_sha},
                "tests_and_checks": result["tests_and_checks"], "residual_risks": result["residual_risks"],
                "requested_or_recommended_next_action": next_action,
                "files_modified": result["files_modified"], "usage": result["usage"],
            }
            event = {
                "event_id": "contract-collected:" + hashlib.sha256((args.phase_key + result_sha).encode()).hexdigest(),
                "type": "PLAN_CONTRACT_RESULT_COLLECTED", "phase_key": args.phase_key, "ticket_id": value["ticket_id"],
                "owner_thread_id": args.owner, "result_reference": str(result_path), "result_sha256": result_sha,
                "snapshot_reference": str(snapshot), "snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                "envelope": envelope,
                "validation": {**{key: result[key] for key in CONTRACT_FIELDS}, "validation_reference": str(result_path)},
            }
            # Validate the complete transaction before writing even its journal.
            controller.handle_event(copy.deepcopy(state), event)
            run_registry.save_json(journal, event)
        return advance(argparse.Namespace(command="advance", state=args.state, owner=args.owner,
                                          expected_revision=args.expected_revision, event=journal))


def require_owner(state: dict, owner: str) -> None:
    controller.require((state.get("orchestrator_lease") or {}).get("owner_thread_id") == owner,
                       "only the canonical owner may advance this run")


def advance(args: argparse.Namespace) -> dict:
    state = run_registry.load_json(args.state)
    require_owner(state, args.owner)
    event = run_registry.load_json(args.event) if getattr(args, "event", None) else None
    if args.command == "observe":
        source = args.snapshot.expanduser().resolve()
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        event = {"event_id": f"runtime:{args.owner}:{digest}", "type": "RUNTIME_OBSERVED",
                 "owner_thread_id": args.owner, "snapshot_reference": str(source), "snapshot_sha256": digest}
    applied = None
    if event:
        # Check ownership inside the write lock without changing the event's
        # identity: events previously applied through the CLI remain replayable.
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            controller.apply_event(argparse.Namespace(
                state=args.state, expected_revision=args.expected_revision,
                event_json=json.dumps(event), event=None, owner_thread_id=args.owner))
        applied = json.loads(output.getvalue())["status"]
    else:
        controller.require(state["procedure"]["revision"] == args.expected_revision, "refresh the stale controller revision")
    # A crash between event application and packet write is recovered by an
    # idempotent replay, not a second user approval or duplicate worker.
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        runner.step(argparse.Namespace(state=args.state, output_dir=None, owner_thread_id=args.owner))
    receipt = json.loads(output.getvalue())
    packet = run_registry.load_json(Path(receipt["packet_reference"]))
    return {**receipt, "event_status": applied, "next_actions": packet["next_actions"],
            "instruction": "Execute the next allowed action now. Packet delivery is not execution; do not stop at an acknowledgement."}


def handoff(args: argparse.Namespace) -> dict:
    state = run_registry.load_json(args.state)
    require_owner(state, args.owner)
    controller.require(state["procedure"]["revision"] == args.expected_revision, "refresh the stale controller revision")
    matches = [x for x in controller.next_actions(state) if x.get("phase_key") == args.phase_key
               and x["action"] in {"DISPATCH_VISIBLE_PHASE", "RESUME_VISIBLE_PHASE_WITH_INPUT"}]
    controller.require(len(matches) == 1, "phase has no authorized dispatch/resume action; reconcile instead of creating another task")
    action = matches[0]
    value = state["procedure"]["phases"][args.phase_key]
    descriptor = value.get("context_packet") or {}
    source = Path(descriptor.get("reference", ""))
    if not source.is_absolute():
        source = args.state.resolve().parent / source
    data = source.read_bytes()
    controller.require(hashlib.sha256(data).hexdigest() == descriptor.get("sha256"), "context packet hash mismatch")
    controller.require(len(data) <= 65_536, "context packet exceeds 64 KiB")
    context = json.loads(data)
    controller.require(context.get("format") == "fresh-compact-v1" and context.get("history_turns_included") == 0,
                       "handoff requires a compact, history-free context")
    for key in ("exact_base", "exact_head", "profile_revision"):
        controller.require(context.get(key) == descriptor.get(key) and bool(context.get(key)), f"context {key} mismatch")
    identity = {"run_id": state["run_id"], "phase_key": args.phase_key,
                "owner_thread_id": args.owner, "manifest_reference": str(args.state.resolve()),
                "context_reference": str(source.resolve()), "context_sha256": descriptor["sha256"],
                "kind": value["kind"], "ticket_id": value.get("ticket_id"),
                "exact_base": descriptor["exact_base"], "exact_head": value.get("resume_head") or descriptor["exact_head"],
                "branch": value.get("branch"), "unity_slot": action.get("unity_slot"),
                "provided_input": action.get("provided_input"),
                "model": value["requested_model"], "reasoning_effort": value["requested_reasoning_effort"]}
    envelope = {"phase_key": args.phase_key, "phase_status": "completed|failed|blocked|needs_input",
                "actual_model": "observed model, never guessed", "actual_reasoning_effort": "observed effort",
                "result_summary": "concise result", "artifacts": {"result_reference": "exact result artifact"},
                "tests_and_checks": ["actual checks, or explicitly not run with reason"],
                "residual_risks": "actual risks or explicitly none identified",
                "requested_or_recommended_next_action": "recommendation only",
                "files_modified": "exact file list or explicitly none (read-only)",
                "usage": {"measurement": "complete|partial|unavailable"},
                "input_request_only_if_needed": {"gate_id": "unique ID", "revision": "exact revision",
                                                "question": "specific unresolved question", "reason": "why it blocks",
                                                "blocked_scope": "exact scope", "continuing_scope": "independent work",
                                                "accepted_replies": ["concrete answer formats"]}}
    prompt = (
        "Ticket Train phase handoff (canonical identity follows). Read the exact compact context file; "
        "do not search other conversations for the task definition.\n"
        + json.dumps(identity, ensure_ascii=False, indent=2)
        + "\nPerform only this phase's authorized scope. Analyses, triage, contract validation and reviews "
        "are read-only. Do not modify the canonical manifest/controller, approve any gate, create another "
        "orchestrator, or launch unregistered tasks. No scope/specification deviation or missing functional "
        "precision may be resolved without the user's explicit decision. Report such a need to the owner. "
        "Preserve the routed model, phase ownership and Unity slot limit.\n"
        "Before ending on completed, failed, blocked or needs_input: persist the result artifact and send "
        "one compact notification using send_message_to_thread to owner_thread_id above. Include run_id, "
        "phase_key, your real thread_id, result_reference and this structured phase envelope:\n"
        + json.dumps(envelope, ensure_ascii=False, indent=2)
        + "\nReport exact usage when measurable, otherwise partial/unavailable, never invented zero. "
        "For needs_input include the conditional object under the actual key input_request; otherwise omit it. "
        "The owner validates and applies the event. If notification fails, retain the artifact and state "
        "the delivery failure in your final message; do not claim the owner has resumed."
    )
    resume = action["action"] == "RESUME_VISIBLE_PHASE_WITH_INPUT"
    result = {"run_id": state["run_id"], "controller_revision": args.expected_revision,
              "phase_key": args.phase_key, "action": action["action"],
              "tool": "send_message_to_thread" if resume else "create_thread",
              "model": value["requested_model"], "thinking": value["requested_reasoning_effort"],
              "prompt": prompt, "existing_thread_id": action.get("thread_id"),
              "target_constraint": "Use the approved project/worktree or leased Unity slot; do not create an extra editor.",
              "required_receipt_event": "PHASE_RESUMED" if resume else "PHASE_LAUNCH_OBSERVED"}
    if value["kind"] == "plan_contract_validation":
        result["result_collection"] = {
            "script": "scripts/continuation_adapter.py", "command": "collect-contract",
            "arguments": ["--state", str(args.state.resolve()), "--owner", args.owner,
                          "--expected-revision", "CURRENT_REVISION", "--phase-key", args.phase_key,
                          "--result", "ACTUAL_RESULT_REFERENCE", "--snapshot", "RAW_PRODUCT_SNAPSHOT"],
            "requires_collection_spec": False, "may_create_task": False,
        }
    filename = hashlib.sha256(f"{args.phase_key}:{args.expected_revision}".encode()).hexdigest()[:20]
    destination = args.state.resolve().parent / "handoffs" / f"{filename}.json"
    run_registry.save_json(destination, result)
    return {**result, "callback_contract_reference": str(destination)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("advance", "observe", "handoff", "collect-contract"):
        sub = commands.add_parser(name)
        sub.add_argument("--state", type=Path, required=True)
        sub.add_argument("--owner", required=True)
        sub.add_argument("--expected-revision", type=int, required=True)
        if name == "advance":
            sub.add_argument("--event", type=Path)
        elif name == "observe":
            sub.add_argument("--snapshot", type=Path, required=True)
        elif name == "collect-contract":
            sub.add_argument("--phase-key", required=True)
            sub.add_argument("--result", type=Path, required=True)
            sub.add_argument("--snapshot", type=Path)
        else:
            sub.add_argument("--phase-key", required=True)
    args = parser.parse_args()
    try:
        result = collect_contract(args) if args.command == "collect-contract" else handoff(args) if args.command == "handoff" else advance(args)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "rejected", "error": str(error), "may_relaunch": False}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
