"""Parse product task snapshots; a manifest RUNNING flag is not liveness proof."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
from typing import Any

MAX_AGE_SECONDS = 120


class HostError(RuntimeError):
    """An observed host error, never evidence that creation did not happen."""


class _JsonLineProcess:
    """Small JSON-line RPC transport shared by the two native Codex bridges."""

    def __init__(self, command: list[str], event_sink=None, timeout: float = 30):
        self.timeout = timeout
        self.events = queue.Queue()
        self.responses = queue.Queue()
        self.event_sink = event_sink or (lambda event: None)
        self.serial = 0
        self.receipts = {}
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", creationflags=flags,
        )
        self.stderr_tail = []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_errors, daemon=True).start()

    def _read_errors(self):
        for line in self.process.stderr:
            self.stderr_tail.append(line.rstrip())
            del self.stderr_tail[:-20]

    def _read(self):
        try:
            for line in self.process.stdout:
                event = json.loads(line)
                if "method" in event:
                    self.events.put(event)
                else:
                    receipt = self.receipts.pop(event.get("id"), None)
                    if receipt:
                        # Persist the actual response before exposing it to the
                        # scheduler. A restart can replay this external receipt.
                        import run_registry
                        run_registry.save_json(receipt, event)
                    self.responses.put(event)
        except (OSError, ValueError) as error:
            self.responses.put({"transport_error": str(error)})
        finally:
            self.responses.put({"transport_error": "Native RPC connection closed"})

    def _send(self, value):
        if self.process.poll() is not None:
            raise HostError("Native RPC process has exited")
        self.process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def request(self, message: dict, receipt_path: Path | None = None) -> dict:
        self.serial += 1
        serial = self.serial
        if receipt_path:
            self.receipts[serial] = receipt_path
        self._send({**message, "id": serial})
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            self.drain()
            try:
                response = self.responses.get(timeout=min(0.1, max(0.001, deadline-time.monotonic())))
            except queue.Empty:
                continue
            if response.get("transport_error"):
                raise HostError(response["transport_error"])
            if response.get("id") != serial:
                raise HostError("Unexpected RPC response; reconcile the pending operation")
            if "error" in response:
                raise HostError(json.dumps(response["error"], ensure_ascii=False))
            return response.get("result", {})
        method = message.get("method", "RPC response")
        raise HostError(f"Timed out awaiting {method}; outcome is unknown, do not repeat a mutation")

    def drain(self):
        events = []
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            events.append(event)
            self.event_sink(event)
        return events

    def wait(self, seconds: float = 1):
        """Wait on the actual event queue. No model turn, status fiction or sleep loop."""
        try:
            event = self.events.get(timeout=seconds)
        except queue.Empty:
            if self.process.poll() is not None:
                raise HostError("App Server stopped while waiting")
            return []
        self.event_sink(event)
        return [event, *self.drain()]

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)
        for handle in (self.process.stdout, self.process.stderr):
            handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class AppServer(_JsonLineProcess):
    """One worker App Server connection; notifications consume no model.

    Existing desktop-owned active tasks must not be resumed through this server.
    Only tasks recorded as managed by this run may be started or resumed here.
    """

    def __init__(self, executable: str, event_sink=None, timeout: float = 30):
        super().__init__([executable, "app-server", "--stdio"], event_sink, timeout)
        try:
            self.identity = self.call("initialize", {
                "clientInfo": {"name": "ticket_train", "version": "2"},
                "capabilities": {"experimentalApi": True},
            })
            self._send({"method": "initialized", "params": {}})
        except Exception:
            self.close()
            raise

    def call(self, method: str, params: dict, receipt_path: Path | None = None) -> dict:
        return self.request({"method": method, "params": params}, receipt_path)

    def answer_request(self, request_id, result):
        self._send({"id": request_id, "result": result})


def app_server_executable(explicit: str | None = None) -> str:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise ValueError("Configured App Server executable is missing")
        return str(path)
    # Desktop supplies its actual native binary next to the code-mode host.
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
        candidates = list(root.glob("*/codex.exe")) if root.is_dir() else []
        if candidates:
            return str(max(candidates, key=lambda p: p.stat().st_mtime))
    command = shutil.which("codex")
    if command and Path(command).suffix.lower() not in {".cmd", ".ps1", ".bat"}:
        return command
    raise ValueError("Provide the native Codex executable with --host-executable")


def send_message_to_thread(source_thread_id: str, target_thread_id: str, prompt: str,
                           call_id: str, timeout: float = 90) -> dict:
    """Use the desktop's own task relay instead of taking its writer lease.

    The app tool starts the target turn inside the already-running desktop host.
    A stable call ID lets a restarted runner replay an uncertain RPC safely.
    """
    pipe = os.environ.get("CODEX_APP_TOOLS_PIPE_PATH")
    node = os.environ.get("CODEX_MCP_NODE_PATH") or shutil.which("node")
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    server = codex_home / ".tmp" / "bundled-marketplaces" / "openai-bundled" / "plugins" / "codex-app-tools" / "server.mjs"
    if not pipe or not node or not Path(node).is_file() or not server.is_file():
        raise HostError("Codex desktop task relay is unavailable; preserve the pending owner notification")

    with _JsonLineProcess(
        [node, str(server), "--interaction-client-id", source_thread_id], timeout=timeout,
    ) as host:
        host.request({"jsonrpc": "2.0", "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "ticket_train", "version": "2"},
        }})
        host._send({
            "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
        })
        result = host.request({"jsonrpc": "2.0", "method": "tools/call", "params": {
            "name": "send_message_to_thread",
            "arguments": {"threadId": target_thread_id, "prompt": prompt},
            "_meta": {
                "openai/threadId": source_thread_id,
                "openai/toolCallId": call_id,
            },
        }})
        if result.get("isError"):
            raise HostError(json.dumps(result, ensure_ascii=False))
        return result


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("runtime observation time requires a timezone")
    return parsed


def parse_wait_result(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Accept raw wait_threads output or its MCP text/structured wrapper.

    A multi-target wait may return only one poll. Omitted targets are NOT
    finished, missing, or safe to restart. Never infer them from the wake item.
    """
    if raw.get("format") == "ticket-train-native-observation-v1":
        result = []
        for response in raw.get("responses", []):
            task = response["thread"]
            turn = (task.get("turns") or [{}])[-1]
            status = turn.get("status", "unknown")
            flags = (task.get("status") or {}).get("activeFlags", [])
            if any(x in flags for x in ("waitingOnApproval", "waitingOnUserInput")):
                status = "needs_input"
            elif status == "inProgress":
                status = "running" if (task.get("status") or {}).get("type") != "notLoaded" else "unknown"
            result.append({"thread_id": task["id"], "host_id": "local", "runtime_status": status,
                           "turn_id": turn.get("id"), "turn_status": turn.get("status"),
                           "product_status": (task.get("status") or {}).get("type"), "cursor": None})
        if not result:
            raise ValueError("Native observation contains no actual task responses")
        return result
    if raw.get("isError"):
        raise ValueError("product task observation failed; do not relaunch")
    if "polls" not in raw:
        if isinstance(raw.get("structuredContent"), dict):
            raw = raw["structuredContent"]
        else:
            blocks = [x for x in raw.get("content", []) if x.get("type") == "text"]
            if len(blocks) != 1:
                raise ValueError("expected one raw wait_threads result")
            raw = json.loads(blocks[0]["text"])
    polls = raw.get("polls")
    if not isinstance(polls, list) or not polls:
        raise ValueError("no observed tasks; preserve existing phase identities")
    result = []
    seen = set()
    for poll in polls:
        task = poll.get("thread") or {}
        turn = poll.get("latestTurn") or {}
        task_id = task.get("id")
        if not task_id:
            raise ValueError("invalid product snapshot: expected polls[].thread.id and polls[].latestTurn; save the raw wait_threads return without flattening, summarizing or inventing fields")
        if task_id in seen:
            raise ValueError("duplicate observed task ID; capture each target once")
        seen.add(task_id)
        task_status = (task.get("status") or {}).get("type")
        flags = (task.get("status") or {}).get("activeFlags") or []
        turn_status = turn.get("status")
        status = "unknown"
        if task_status in {"waitingOnApproval", "waitingOnUserInput"} or any(x in flags for x in ("waitingOnApproval", "waitingOnUserInput")):
            status = "needs_input"
        elif task_status in {"active", "running", "busy"} and turn_status == "inProgress":
            status = "running"
        elif task_status == "idle" and turn_status in {"completed", "failed", "interrupted"}:
            status = turn_status
        result.append({
            "thread_id": task_id, "host_id": task.get("hostId"),
            "runtime_status": status, "product_status": task_status,
            "turn_id": turn.get("id"), "turn_status": turn_status,
            "cursor": poll.get("cursor"),
        })
    return result


def phase_observation(value: dict[str, Any], owner: str | None) -> tuple[str, str | None]:
    observation = value.get("runtime_observation") or {}
    if observation.get("thread_id") != value.get("thread_id") or observation.get("owner_thread_id") != owner:
        return "unobserved", "no runtime observation for the current task and owner"
    status = observation.get("runtime_status", "unknown")
    # Terminal evidence stays actionable until its envelope is consumed. Time
    # cannot transform a finished child back into a running one.
    if status in {"completed", "failed", "interrupted", "needs_input"}:
        return status, "observed task outcome has not been reconciled"
    try:
        age = (datetime.now(timezone.utc) - timestamp(observation["observed_at"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        return "unobserved", "invalid runtime observation timestamp"
    if not 0 <= age <= MAX_AGE_SECONDS:
        return "stale", "runtime observation is stale"
    if status != "running":
        return "unknown", "product task status is unknown; inspect, never blindly relaunch"
    return "running", None
