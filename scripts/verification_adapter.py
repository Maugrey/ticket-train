#!/usr/bin/env python3
"""Execute/reuse exact-head verification, record its outcome, return the successor.

Evidence is a prepared controller-event template, not inferred test coverage.
Existing results are never rerun or overwritten by this adapter.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import sys
from pathlib import Path

import control_plane_runner
import run_registry
import train_controller
import verification_runner


@contextlib.contextmanager
def execution_lock(path: Path):
    with run_registry.file_lock(path, timeout_seconds=0):
        yield


def execute(args: argparse.Namespace) -> dict:
    state_path = args.state.expanduser().resolve()
    output = args.output.expanduser().resolve()
    plan_path = args.plan.expanduser().resolve()
    evidence = run_registry.load_json(args.evidence)
    event_type = evidence.get("type")
    if event_type not in {"VERIFICATION_RECORDED", "FINAL_VERIFICATION_RECORDED", "VALIDATION_ONLY_RECORDED"}:
        raise ValueError("Evidence must be a ticket or final verification event template")
    plan = verification_runner.load_json(plan_path)
    workdir, expected_head, _ = verification_runner.validate_plan(plan)
    plan_hash = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    # This is a per-result lock, not the manifest lock. Other tasks may advance.
    output.parent.mkdir(parents=True, exist_ok=True)
    with execution_lock(output.with_suffix(output.suffix + ".execution.lock")):
        state = run_registry.load_json(state_path)
        owner = getattr(args, "owner", None) or os.environ.get("CODEX_THREAD_ID")
        epoch = getattr(args, "owner_epoch", None)
        run_registry.require_owner(state, owner, epoch)
        ticket_id = evidence.get("ticket_id")
        if not output.exists():
            required = (
                "RUN_VALIDATION_ONLY_VERIFICATION" if event_type == "VALIDATION_ONLY_RECORDED" else
                "RUN_DETERMINISTIC_TICKET_VERIFICATION"
                if event_type == "VERIFICATION_RECORDED"
                else "RUN_FINAL_EXACT_HEAD_VERIFICATION_DETERMINISTICALLY"
            )
            if not any(
                action.get("action") == required
                and (event_type == "FINAL_VERIFICATION_RECORDED" or action.get("ticket_id") == ticket_id)
                for action in train_controller.next_actions(state)
            ):
                raise ValueError("Verification is not currently authorized by the controller")
            verification_runner.run_detached_plan(plan_path, output, args.logs_dir)

        result = run_registry.load_json(output)
        if result.get("plan_sha256") != plan_hash:
            raise ValueError("Stored result belongs to another/legacy plan; do not rerun or overwrite it")
        if result.get("expected_head") != expected_head or result.get("workdir") != str(workdir):
            raise ValueError("Stored verification worktree/head differs from its plan")
        if verification_runner.git_head(workdir) != expected_head or not result.get("head_unchanged"):
            raise ValueError("Verification head changed; reconcile instead of recording stale evidence")
        excluded = (args.logs_dir.resolve(), output, output.with_suffix(".runner.lock"), output.with_suffix(output.suffix + ".execution.lock"))
        if result.get("worktree_fingerprint") and (
            not result.get("worktree_unchanged") or verification_runner.worktree_fingerprint(workdir, excluded) != result["worktree_fingerprint"]
        ):
            raise ValueError("Verification worktree changed; stored evidence no longer covers the current files")
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        event = {
            **evidence,
            "event_id": "verification-result-" + digest,
            "status": result["status"],
            "execution_mode": "deterministic",
            "model_tokens": 0,
            "runner_version": result["runner_version"],
            "runner_result_reference": str(output),
            "runner_result_sha256": digest,
            "logs_reference": str(args.logs_dir.expanduser().resolve()),
            "command_results": [{
                **{key: command[key] for key in ("command_id", "status", "exit_code", "duration_seconds")},
                "log_reference": command["stdout_log"],
            } for command in result["command_results"]],
        }
        if event_type == "VERIFICATION_RECORDED":
            event.update(ticket_head=expected_head, integrated_green_head=expected_head)
        else:
            event["head_commit"] = expected_head
        state = run_registry.load_json(state_path)
        with contextlib.redirect_stdout(io.StringIO()):
            train_controller.apply_event(argparse.Namespace(
                state=state_path, event=None, event_json=json.dumps(event),
                expected_revision=state["procedure"]["revision"],
                owner_thread_id=owner, owner_epoch=epoch,
            ))
        packet_output = io.StringIO()
        with contextlib.redirect_stdout(packet_output):
            control_plane_runner.step(argparse.Namespace(state=state_path, output_dir=None, owner_thread_id=owner, owner_epoch=epoch))
        return {
            "status": "recorded", "verification_status": result["status"],
            "result": str(output), "sha256": digest, "model_tokens": 0,
            "next": json.loads(packet_output.getvalue()),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("state", "evidence", "plan", "output", "logs-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--owner-epoch", required=True)
    args = parser.parse_args()
    try:
        result = execute(args)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        # The result stays on disk. Repeating the identical invocation attempts
        # only registration; it never repeats the expensive test commands.
        result = {"status": "reconciliation-required", "error": str(error), "result": str(args.output)}
        sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return 2
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
