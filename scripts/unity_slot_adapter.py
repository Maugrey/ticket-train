#!/usr/bin/env python3
"""Execute one controller-authorized Unity slot transition deterministically.

This adapter removes prompt interpretation from Unity slot initialization,
acquisition, and release. It invokes ``unity_slot_manager.py``, translates the
result into an idempotent controller event, applies that event with the exact
manifest revision, and prints the newly authorized next actions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import run_registry
import train_controller
import unity_slot_manager


class AdapterError(ValueError):
    """Raised when the current controller action is not adapter-executable."""


SCRIPT_DIR = Path(__file__).resolve().parent


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AdapterError(message)


def run_json(argv: list[str]) -> dict[str, Any]:
    completed = subprocess.run(argv, capture_output=True, text=True, check=False)
    payload_text = completed.stdout.strip() or completed.stderr.strip()
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as error:
        raise AdapterError(f"command returned non-JSON output: {argv!r}: {payload_text[-2000:]}") from error
    require(completed.returncode == 0, f"command failed: {payload}")
    require(isinstance(payload, dict), "command result must be a JSON object")
    return payload


def event_id(prefix: str, material: str) -> str:
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
    return f"unity:{prefix}:{digest}"


def manager_command(*arguments: str) -> list[str]:
    return [sys.executable, str(SCRIPT_DIR / "unity_slot_manager.py"), *arguments]


def apply_event(state_path: Path, event: dict[str, Any]) -> dict[str, Any]:
    state = run_registry.load_json(state_path)
    return run_json(
        [
            sys.executable,
            str(SCRIPT_DIR / "train_controller.py"),
            "apply",
            "--state",
            str(state_path),
            "--expected-revision",
            str(state["procedure"]["revision"]),
            "--event-json",
            json.dumps(event, ensure_ascii=False, separators=(",", ":")),
        ]
    )


def initialize(state_path: Path, action: dict[str, Any]) -> dict[str, Any]:
    result = run_json(
        manager_command(
            "init",
            "--repository",
            str(action["repository"]),
            "--base-ref",
            str(action["base_ref"]),
            "--max-editors",
            str(action["max_editors"]),
        )
    )
    slots = [
        slot for slot in result.get("slots", [])
        if slot.get("status") in {"IDLE", "READY"}
    ]
    require(len(slots) >= int(action["max_editors"]), "Unity slot initialization returned insufficient enabled slots")
    require(
        all(isinstance(slot.get("config_profile_sha256"), str) for slot in slots),
        "Unity MCP configuration profile is missing after deterministic configure",
    )
    event = {
        "event_id": event_id("environment", f"{result['state']}:{result['max_editors']}"),
        "type": "UNITY_ENVIRONMENT_CONFIGURED",
        "registry_reference": result["state"],
        "repository": result["repository"],
        "slot_root": result["slot_root"],
        "max_editors": result["max_editors"],
        "mcp_mode": "local",
        "cli_package": f"unity-mcp-cli@{unity_slot_manager.plugin_version(Path(result['repository']))}",
        "plugin_package": f"com.ivanmurzak.unity.mcp@{unity_slot_manager.plugin_version(Path(result['repository']))}",
        "slots": slots,
    }
    apply_event(state_path, event)
    return {
        "operation": "initialized",
        "registry_reference": result["state"],
        "max_editors": result["max_editors"],
        "slot_ids": [slot["slot_id"] for slot in slots],
        "controller_event_id": event["event_id"],
    }


def acquire(state_path: Path, action: dict[str, Any]) -> dict[str, Any]:
    arguments = [
        "acquire",
        "--state",
        str(action["registry_reference"]),
        "--phase-key",
        str(action["owner_key"]),
        "--requirement",
        str(action["requirement"]),
        "--expected-head",
        str(action["expected_head"]),
        "--recovery-attempts",
        str(action.get("recovery_attempts", 2)),
    ]
    if action.get("branch"):
        arguments.extend(("--branch", str(action["branch"])))
    result = run_json(manager_command(*arguments))
    slot = result["slot"]
    lease = slot["lease"]
    event = {
        "event_id": event_id("acquire", str(lease["lease_id"])),
        "type": "UNITY_SLOT_ACQUIRED",
        "owner_key": action["owner_key"],
        "slot_id": slot["slot_id"],
        "lease_id": lease["lease_id"],
        "requirement": action["requirement"],
        "path": slot["path"],
        "branch": slot.get("branch"),
        "expected_head": action["expected_head"],
        "observed_head": slot["head"],
        "readiness_evidence_reference": f"{action['registry_reference']}#{slot['slot_id']}:readiness",
    }
    apply_event(state_path, event)
    return {
        "operation": "acquired",
        "slot_id": slot["slot_id"],
        "owner_key": action["owner_key"],
        "lease_id": lease["lease_id"],
        "controller_event_id": event["event_id"],
    }


def release(state_path: Path, action: dict[str, Any]) -> dict[str, Any]:
    result = run_json(
        manager_command(
            "release",
            "--state",
            str(action["registry_reference"]),
            "--phase-key",
            str(action["owner_key"]),
            "--lease-id",
            str(action["lease_id"]),
        )
    )
    slot = result["slot"]
    event = {
        "event_id": event_id("release", str(action["lease_id"])),
        "type": "UNITY_SLOT_RELEASED",
        "owner_key": action["owner_key"],
        "slot_id": action["slot_id"],
        "lease_id": action["lease_id"],
        "slot_status": slot["status"],
        "release_evidence_reference": f"{action['registry_reference']}#{action['slot_id']}:release",
    }
    apply_event(state_path, event)
    return {
        "operation": "released",
        "slot_id": action["slot_id"],
        "owner_key": action["owner_key"],
        "lease_id": action["lease_id"],
        "controller_event_id": event["event_id"],
    }


def step(args: argparse.Namespace) -> int:
    state_path = args.state.expanduser().resolve()
    state = run_registry.load_json(state_path)
    actions = train_controller.next_actions(state)
    require(actions, "controller returned no action")
    action = actions[0]
    action_name = action.get("action")
    if action_name == "INITIALIZE_UNITY_SLOTS_DETERMINISTICALLY":
        result = initialize(state_path, action)
    elif action_name == "ACQUIRE_UNITY_SLOT_DETERMINISTICALLY":
        result = acquire(state_path, action)
    elif action_name == "RELEASE_UNITY_SLOT_DETERMINISTICALLY":
        result = release(state_path, action)
    else:
        raise AdapterError(
            "next action is not a deterministic Unity slot transition: "
            f"{action_name}; execute that controller action first and invoke "
            "unity_slot_adapter.py only for INITIALIZE, ACQUIRE, or RELEASE Unity actions"
        )
    updated = run_registry.load_json(state_path)
    output = {
        "status": "applied",
        **result,
        "controller_revision": updated["procedure"]["revision"],
        "next_actions": train_controller.next_actions(updated),
    }
    sys.stdout.write(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    return parser


def main() -> int:
    try:
        return step(build_parser().parse_args())
    except (OSError, AdapterError, unity_slot_manager.SlotError, train_controller.ControllerError) as error:
        sys.stderr.write(json.dumps({"status": "blocked", "error": str(error)}, ensure_ascii=False) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
