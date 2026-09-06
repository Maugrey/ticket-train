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


def require_owner(state: dict, owner: str) -> None:
    controller.require((state.get("orchestrator_lease") or {}).get("owner_thread_id") == owner,
                       "only the canonical owner may advance this run")


def advance(args: argparse.Namespace) -> dict:
    state = run_registry.load_json(args.state)
    require_owner(state, args.owner)
    run_registry.require_owner(state, args.owner, getattr(args, "owner_epoch", None))
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
                event_json=json.dumps(event), event=None, owner_thread_id=args.owner,
                owner_epoch=args.owner_epoch))
        applied = json.loads(output.getvalue())["status"]
    else:
        controller.require(state["procedure"]["revision"] == args.expected_revision, "refresh the stale controller revision")
    # A crash between event application and packet write is recovered by an
    # idempotent replay, not a second user approval or duplicate worker.
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        runner.step(argparse.Namespace(state=args.state, output_dir=None, owner_thread_id=args.owner, owner_epoch=getattr(args, "owner_epoch", None)))
    receipt = json.loads(output.getvalue())
    packet = run_registry.load_json(Path(receipt["packet_reference"]))
    return {**receipt, "event_status": applied, "next_actions": packet["next_actions"],
            "instruction": "Execute the next allowed action now. Packet delivery is not execution; do not stop at an acknowledgement."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("advance", "observe"):
        sub = commands.add_parser(name)
        sub.add_argument("--state", type=Path, required=True)
        sub.add_argument("--owner", required=True)
        sub.add_argument("--owner-epoch", required=True)
        sub.add_argument("--expected-revision", type=int, required=True)
        if name == "advance":
            sub.add_argument("--event", type=Path)
        elif name == "observe":
            sub.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = advance(args)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "rejected", "error": str(error), "may_relaunch": False}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
