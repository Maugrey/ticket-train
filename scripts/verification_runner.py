#!/usr/bin/env python3
"""Run exact-head verification commands without an LLM supervision loop."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
import re
import time
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import run_registry


RUNNER_VERSION = "3"
_executor_job = None


def protect_executor_children():
    """Windows closes this non-inherited job handle when the executor dies.

    Join before spawning commands so even a crash at launch cannot orphan them.
    Only the dedicated CLI executor calls this, never the orchestration process.
    https://learn.microsoft.com/windows/win32/procthread/job-objects
    """
    global _executor_job
    if os.name != "nt" or _executor_job is not None:
        return
    import ctypes as c
    from ctypes import wintypes as w
    class Limits(c.Structure):
        _fields_ = [("process_time", c.c_int64), ("job_time", c.c_int64), ("flags", w.DWORD),
                    ("minimum", c.c_size_t), ("maximum", c.c_size_t), ("processes", w.DWORD),
                    ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
    class Extended(c.Structure):
        _fields_ = [("limits", Limits), ("io", c.c_uint64 * 6), ("process_memory", c.c_size_t),
                    ("job_memory", c.c_size_t), ("peak_process", c.c_size_t), ("peak_job", c.c_size_t)]
    api = c.WinDLL("kernel32", use_last_error=True)
    api.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]; api.CreateJobObjectW.restype = w.HANDLE
    api.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    api.GetCurrentProcess.restype = w.HANDLE
    api.CloseHandle.argtypes = [w.HANDLE]
    handle = api.CreateJobObjectW(None, None)
    if not handle:
        raise c.WinError(c.get_last_error())
    limits = Extended(); limits.limits.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not api.SetInformationJobObject(handle, 9, c.byref(limits), c.sizeof(limits)) or not api.AssignProcessToJobObject(handle, api.GetCurrentProcess()):
        error = c.get_last_error(); api.CloseHandle(handle)
        raise c.WinError(error)
    _executor_job = handle  # Retain until OS process teardown; closing it kills this executor too.


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
    with path.open("rb") as handle:
        handle.seek(max(0, path.stat().st_size - limit))
        data = handle.read(limit)
    return data[-limit:].decode("utf-8", errors="replace")


def present_result(result_path: Path, command_id: str, rtk_executable: str | None = None) -> dict:
    """Optional presentation only; never change command evidence or exit status."""
    document = run_registry.load_json(result_path)
    command = next((c for c in document["command_results"] if c["command_id"] == command_id), None)
    if command is None:
        raise ValueError("Unknown verification command: " + command_id)
    raw = (bounded_excerpt(Path(command["stderr_log"]), 8192) + "\n" + bounded_excerpt(Path(command["stdout_log"]), 8192)).strip()
    shown, filter_used = raw, "bounded-raw"
    if rtk_executable:
        env = dict(os.environ, RTK_DB_PATH=str(result_path.resolve().parent / "rtk-history.db"), RTK_TEE="0", DO_NOT_TRACK="1")
        try:
            compact = subprocess.run([rtk_executable, "log"], input=raw.encode("utf-8"), capture_output=True, timeout=10, env=env)
            if compact.returncode == 0 and 0 < len(compact.stdout) < len(raw.encode("utf-8")):
                shown, filter_used = compact.stdout.decode("utf-8", errors="replace"), "rtk-log"
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {"command_id": command_id, "status": command["status"], "exit_code": command["exit_code"],
            "presentation": shown, "filter": filter_used, "source_excerpt_bytes": len(raw.encode("utf-8")),
            "presentation_bytes": len(shown.encode("utf-8")), "raw_stdout": command["stdout_log"], "raw_stderr": command["stderr_log"]}


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
        if not isinstance(command_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", command_id) or command_id in seen:
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


def worktree_fingerprint(workdir: Path, excluded: tuple[Path, ...] = ()) -> str:
    """HEAD plus tracked diff and untracked source content, excluding own receipts."""
    digest = hashlib.sha256(git_head(workdir).encode())
    diff = subprocess.run(["git", "diff", "HEAD", "--binary", "--no-ext-diff"], cwd=workdir, capture_output=True, check=True)
    digest.update(diff.stdout)
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=workdir, capture_output=True, check=True)
    for name in sorted(untracked.stdout.split(b"\0")):
        if not name:
            continue
        path = (workdir / os.fsdecode(name)).resolve()
        if any(path == p or path.is_relative_to(p) for p in excluded) or not path.is_file():
            continue
        digest.update(name)
        with path.open("rb") as handle:
            while chunk := handle.read(65536):
                digest.update(chunk)
    return digest.hexdigest()


def run_plan(plan_path: Path, output_path: Path, logs_dir: Path) -> dict[str, Any]:
    output_path = output_path.expanduser().resolve()
    plan = load_json(plan_path)
    resources = plan.get("resources", [])
    if not isinstance(resources, list) or not all(isinstance(name, str) and name for name in resources):
        raise ValueError("Verification resources must be stable, nonempty names")
    with ExitStack() as locks:
        locks.enter_context(run_registry.file_lock(output_path.with_suffix(".runner.lock"), 0))
        if output_path.exists():
            result = run_registry.load_json(output_path)
            if result.get("plan_sha256") != hashlib.sha256(plan_path.read_bytes()).hexdigest():
                raise ValueError("Existing verification result belongs to another plan")
            excluded = (logs_dir.resolve(), output_path, output_path.with_suffix(".runner.lock"), output_path.with_suffix(output_path.suffix + ".execution.lock"))
            if result.get("worktree_fingerprint") != worktree_fingerprint(Path(result["workdir"]), excluded):
                raise ValueError("Verification result no longer covers the current worktree")
            return {"status": result["status"], "result": str(output_path), "model_tokens": 0,
                    "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(), "expected_head": result["expected_head"]}
        for resource in sorted(set(resources)):
            key = hashlib.sha256(resource.encode()).hexdigest()
            locks.enter_context(run_registry.file_lock(run_registry.default_root().parent / "resources" / (key + ".lock"), 0))
        return execute_plan(plan_path, output_path, logs_dir)


def run_detached_plan(plan_path: Path, output_path: Path, logs_dir: Path) -> dict[str, Any]:
    """Keep the resource-owning executor alive if its orchestration caller dies.

    An OS lock prevents duplicate execution even across the small process-start
    window. The child journals commands and writes its result itself. Restarted
    callers follow that result; they never infer success from a dead parent PID.
    """
    import time
    plan = load_json(plan_path)
    _, _, commands = validate_plan(plan)
    output_path = output_path.resolve()
    logs_dir = logs_dir.resolve()
    logs_dir.mkdir(parents=True, exist_ok=True)
    process = None
    busy = False
    try:
        with run_registry.file_lock(output_path.with_suffix(".runner.lock"), 0):
            pass
    except ValueError:
        busy = True
    if not busy and not output_path.exists():
        with (logs_dir / "executor.stdout.log").open("ab") as stdout, (logs_dir / "executor.stderr.log").open("ab") as stderr:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--plan", str(plan_path.resolve()),
                                        "--output", str(output_path), "--logs-dir", str(logs_dir)],
                                       stdout=stdout, stderr=stderr,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                                       start_new_session=os.name != "nt")
    deadline = time.monotonic() + sum(c.get("timeout_seconds", 1800) for c in commands) + 60
    while not output_path.exists():
        if time.monotonic() >= deadline:
            raise ValueError("Verification executor exceeded its bounded plan deadline; inspect the active lease")
        if process is not None and process.poll() is not None and process.returncode != 0:
            try:
                with run_registry.file_lock(output_path.with_suffix(".runner.lock"), 0):
                    raise RuntimeError(bounded_excerpt(logs_dir / "executor.stderr.log") or "Verification executor failed")
            except ValueError:
                process = None  # Another reconciled executor owns this plan.
        time.sleep(0.2)
    result = run_registry.load_json(output_path)
    if result.get("plan_sha256") != hashlib.sha256(plan_path.read_bytes()).hexdigest():
        raise ValueError("Verification executor returned another plan's result")
    if process is not None:
        process.wait(timeout=10)
    return {"status": result["status"], "result": str(output_path), "model_tokens": 0,
            "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(), "expected_head": result["expected_head"]}


def execute_plan(plan_path: Path, output_path: Path, logs_dir: Path) -> dict[str, Any]:
    plan = load_json(plan_path)
    workdir, expected_head, commands = validate_plan(plan)
    logs_dir = logs_dir.expanduser().resolve()
    logs_dir.mkdir(parents=True, exist_ok=True)
    initial_head = git_head(workdir)
    if initial_head != expected_head:
        raise ValueError(f"Expected head {expected_head}, found {initial_head}")

    excluded = (logs_dir, output_path, output_path.with_suffix(".runner.lock"), output_path.with_suffix(output_path.suffix + ".execution.lock"))
    fingerprint = worktree_fingerprint(workdir, excluded)
    plan_sha = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    journal_path = logs_dir / "verification-journal.json"
    journal = run_registry.load_json(journal_path) if journal_path.exists() else {
        "plan_sha256": plan_sha, "worktree_fingerprint": fingerprint,
        "started_at": now_iso(), "commands": {},
    }
    if journal["plan_sha256"] != plan_sha or journal["worktree_fingerprint"] != fingerprint:
        raise ValueError("Verification inputs changed; start a new explicit verification operation")

    started_at = journal["started_at"]
    results: list[dict[str, Any]] = []
    for command in commands:
        command_id = command["id"]
        prior = journal["commands"].get(command_id)
        if prior and prior.get("status") == "passed":
            results.append(prior)
            continue
        if prior and prior.get("status") == "running":
            pid = prior.get("pid")
            if pid and process_alive(pid):
                raise ValueError(f"Verification command {command_id} still owns process {pid}; follow it before retrying")
            raise ValueError(f"Verification command {command_id} has an unknown exit outcome; reconcile its logs before repeating it")
        stdout_path = logs_dir / f"{command_id}.stdout.log"
        stderr_path = logs_dir / f"{command_id}.stderr.log"
        command_started = datetime.now(timezone.utc)
        status = "failed"
        exit_code: int | None = None
        try:
            with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
                process = subprocess.Popen(
                    command["argv"],
                    cwd=workdir,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    start_new_session=os.name != "nt",
                )
                journal["commands"][command_id] = {"status": "running", "pid": process.pid, "started_at": command_started.isoformat(), "argv": command["argv"]}
                run_registry.save_json(journal_path, journal)
                try:
                    exit_code = process.wait(timeout=command.get("timeout_seconds", 1800))
                except subprocess.TimeoutExpired:
                    stop_process_tree(process)
                    raise
            status = "passed" if exit_code == 0 else "failed"
        except subprocess.TimeoutExpired:
            status = "timed_out"
        except OSError as error:
            # Missing executables and launch failures are durable failures too.
            stderr_path.write_text(str(error), encoding="utf-8")
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
                "error_excerpt": (
                    (bounded_excerpt(stderr_path, 8192) + "\n" + bounded_excerpt(stdout_path, 8192)).strip()
                    if status != "passed" else ""
                ),
            }
        )
        journal["commands"][command_id] = results[-1]
        run_registry.save_json(journal_path, journal)
        if status != "passed" and plan.get("continue_on_failure") is not True:
            break

    final_head = git_head(workdir)
    worktree_unchanged = worktree_fingerprint(workdir, excluded) == fingerprint
    head_unchanged = final_head == expected_head
    overall = "passed" if head_unchanged and worktree_unchanged and len(results) == len(commands) and all(
        result["status"] == "passed" for result in results
    ) else "failed"
    document = {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        "execution_mode": "deterministic",
        "model_tokens": 0,
        "started_at": started_at,
        "completed_at": now_iso(),
        "workdir": str(workdir),
        "expected_head": expected_head,
        "initial_head": initial_head,
        "final_head": final_head,
        "head_unchanged": head_unchanged,
        "worktree_fingerprint": fingerprint,
        "worktree_unchanged": worktree_unchanged,
        "status": overall,
        "command_results": results,
    }
    rendered = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rendered_bytes = rendered.encode("utf-8")
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_bytes(rendered_bytes)
    temporary.replace(output_path)
    return {
        "status": overall,
        "result": str(output_path),
        "sha256": hashlib.sha256(rendered_bytes).hexdigest(),
        "expected_head": expected_head,
        "model_tokens": 0,
    }


def process_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            # Access denied means it may still be alive; never authorize a retry.
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            return not api.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OSError):
        return False
    return True


def stop_process_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        import signal
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--logs-dir", type=Path)
    parser.add_argument("--show-result", type=Path)
    parser.add_argument("--command-id")
    parser.add_argument("--rtk-executable")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.show_result:
            if not args.command_id:
                raise ValueError("--show-result requires --command-id")
            print(json.dumps(present_result(args.show_result, args.command_id, args.rtk_executable), ensure_ascii=False))
            return 0
        if not all((args.plan, args.output, args.logs_dir)):
            raise ValueError("--plan, --output and --logs-dir are required for execution")
        protect_executor_children()
        result = run_plan(args.plan, args.output, args.logs_dir)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        sys.stderr.write(json.dumps({"status": "rejected", "error": str(error)}) + "\n")
        return 2
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
