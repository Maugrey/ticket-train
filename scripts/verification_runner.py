#!/usr/bin/env python3
"""Run exact-head verification commands without an LLM supervision loop."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUNNER_VERSION = "1"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Verification plan must be a JSON object")
    return value


def git_head(workdir: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workdir,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("Verification workdir is not a readable Git checkout")
    return result.stdout.strip()


def bounded_excerpt(path: Path, limit: int = 16_384) -> str:
    if not path.exists():
        return ""
    data = path.read_bytes()
    return data[-limit:].decode("utf-8", errors="replace")


def validate_plan(plan: dict[str, Any]) -> tuple[Path, str, list[dict[str, Any]]]:
    if plan.get("schema_version") != 1:
        raise ValueError("Unsupported verification plan schema_version")
    workdir_value = plan.get("workdir")
    expected_head = plan.get("expected_head")
    commands = plan.get("commands")
    if not isinstance(workdir_value, str) or not workdir_value:
        raise ValueError("Verification plan requires workdir")
    if not isinstance(expected_head, str) or not expected_head:
        raise ValueError("Verification plan requires expected_head")
    if not isinstance(commands, list) or not commands:
        raise ValueError("Verification plan requires at least one command")
    seen: set[str] = set()
    for command in commands:
        if not isinstance(command, dict):
            raise ValueError("Every verification command must be an object")
        command_id = command.get("id")
        argv = command.get("argv")
        if not isinstance(command_id, str) or not command_id or command_id in seen:
            raise ValueError("Verification command IDs must be unique non-empty strings")
        if not isinstance(argv, list) or not argv or not all(isinstance(value, str) and value for value in argv):
            raise ValueError(f"Verification command {command_id} requires a non-empty argv list")
        timeout = command.get("timeout_seconds", 1800)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError(f"Verification command {command_id} has an invalid timeout")
        rendered_argv = " ".join(argv).lower()
        if (
            "unity-mcp-cli" in rendered_argv
            and "run-tool" in rendered_argv
            and "tests-run" in rendered_argv
            and "--timeout" not in rendered_argv
        ):
            raise ValueError(
                f"Verification command {command_id} must set the Unity MCP client --timeout explicitly"
            )
        seen.add(command_id)
    return Path(workdir_value).expanduser().resolve(), expected_head, commands


def run_plan(plan_path: Path, output_path: Path, logs_dir: Path) -> dict[str, Any]:
    plan = load_json(plan_path)
    workdir, expected_head, commands = validate_plan(plan)
    logs_dir = logs_dir.expanduser().resolve()
    logs_dir.mkdir(parents=True, exist_ok=True)
    initial_head = git_head(workdir)
    if initial_head != expected_head:
        raise ValueError(f"Expected head {expected_head}, found {initial_head}")

    started_at = now_iso()
    results: list[dict[str, Any]] = []
    for command in commands:
        command_id = command["id"]
        stdout_path = logs_dir / f"{command_id}.stdout.log"
        stderr_path = logs_dir / f"{command_id}.stderr.log"
        command_started = datetime.now(timezone.utc)
        status = "failed"
        exit_code: int | None = None
        try:
            with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
                completed = subprocess.run(
                    command["argv"],
                    cwd=workdir,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    timeout=command.get("timeout_seconds", 1800),
                    check=False,
                )
            exit_code = completed.returncode
            status = "passed" if completed.returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            status = "timed_out"
        duration = (datetime.now(timezone.utc) - command_started).total_seconds()
        results.append(
            {
                "command_id": command_id,
                "argv": command["argv"],
                "status": status,
                "exit_code": exit_code,
                "duration_seconds": round(duration, 3),
                "stdout_log": str(stdout_path),
                "stderr_log": str(stderr_path),
                "error_excerpt": bounded_excerpt(stderr_path),
            }
        )
        if status != "passed" and plan.get("continue_on_failure") is not True:
            break

    final_head = git_head(workdir)
    head_unchanged = final_head == expected_head
    overall = "passed" if head_unchanged and len(results) == len(commands) and all(
        result["status"] == "passed" for result in results
    ) else "failed"
    document = {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "execution_mode": "deterministic",
        "model_tokens": 0,
        "started_at": started_at,
        "completed_at": now_iso(),
        "workdir": str(workdir),
        "expected_head": expected_head,
        "initial_head": initial_head,
        "final_head": final_head,
        "head_unchanged": head_unchanged,
        "status": overall,
        "command_results": results,
    }
    rendered = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rendered_bytes = rendered.encode("utf-8")
    output_path.write_bytes(rendered_bytes)
    return {
        "status": overall,
        "result": str(output_path),
        "sha256": hashlib.sha256(rendered_bytes).hexdigest(),
        "expected_head": expected_head,
        "model_tokens": 0,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--logs-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run_plan(args.plan, args.output, args.logs_dir)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        sys.stderr.write(json.dumps({"status": "rejected", "error": str(error)}) + "\n")
        return 2
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
