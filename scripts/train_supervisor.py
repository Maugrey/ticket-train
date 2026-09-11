#!/usr/bin/env python3
"""Native, receipt-driven effects for the canonical ticket-train controller.

Changes suggested by AI model: GPT-6 (Codex).
"""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

import run_registry
import thread_runtime

load_json = run_registry.load_json
save_json = run_registry.save_json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class NativeEffects:
    """Own worker tasks and durable RPC receipts, never workflow decisions.

    One runner holds the run's driver lock for this object's lifetime. Its
    filesystem inbox is also usable after a lost model callback or host restart.
    Only tasks with a recorded creation receipt may be resumed by this server.
    """

    def __init__(self, root, executable=None, host_factory=thread_runtime.AppServer,
                 source_thread_id=None, owner_relay=None,
                 sidebar_section_name=None, sidebar_relay=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.instance = str(uuid.uuid4())
        self.pending_requests = {}
        self.host = host_factory(thread_runtime.app_server_executable(executable), event_sink=self.notification)
        self.loaded = set()
        self.source_thread_id = source_thread_id
        self.owner_relay = owner_relay or thread_runtime.send_message_to_thread
        self.sidebar_section_name = sidebar_section_name
        self.sidebar_relay = sidebar_relay or thread_runtime.call_app_tool

    @staticmethod
    def app_tool_payload(result):
        """Extract the JSON object returned by a bundled Codex app tool."""
        for item in result.get("content", []):
            if item.get("type") != "text":
                continue
            try:
                value = json.loads(item.get("text", ""))
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise thread_runtime.HostError("Codex sidebar tool returned no JSON object")

    def ensure_sidebar_section(self):
        """Create or reconcile this run's user-visible worker section once."""
        if not self.source_thread_id or not self.sidebar_section_name:
            return None
        reference = self.root / "sidebar-section.json"
        if reference.exists():
            record = load_json(reference)
            if record.get("status") == "completed":
                return record["section_id"]
        else:
            record = {
                "status": "armed", "name": self.sidebar_section_name,
                "armed_at": utcnow(),
            }
            save_json(reference, record)

        call_key = digest([self.source_thread_id, self.sidebar_section_name])
        listing = self.app_tool_payload(self.sidebar_relay(
            self.source_thread_id, "list_threads", {"limit": 1},
            "ticket-train-sidebar-list:" + call_key, 30,
        ))
        matches = [
            section for section in listing.get("sections", [])
            if section.get("name") == self.sidebar_section_name
            and section.get("sectionId") not in {"pinned", "threads", "chats"}
        ]
        if len(matches) > 1:
            raise thread_runtime.HostError(
                "Multiple Codex sidebar sections have the run's exact name"
            )
        if matches:
            section = matches[0]
        else:
            section = self.app_tool_payload(self.sidebar_relay(
                self.source_thread_id, "create_sidebar_section",
                {"name": self.sidebar_section_name},
                "ticket-train-sidebar-create:" + call_key, 30,
            ))
        section_id = section.get("sectionId")
        if not isinstance(section_id, str) or not section_id:
            raise thread_runtime.HostError("Codex sidebar section has no ID")
        record.update(status="completed", section_id=section_id, completed_at=utcnow())
        save_json(reference, record)
        return section_id

    def organize_worker(self, job):
        """Place a real worker task in its visible section without a model wake."""
        if not self.source_thread_id or not self.sidebar_section_name or job.get("sidebar_section_id"):
            return
        if int(job.get("sidebar_attempts", 0)) >= 3:
            return
        if job.get("sidebar_retry_at", 0) > time.time():
            return
        try:
            section_id = self.ensure_sidebar_section()
            call_id = "ticket-train-sidebar-move:" + digest([section_id, job["thread_id"]])
            payload = self.app_tool_payload(self.sidebar_relay(
                self.source_thread_id, "move_thread_to_sidebar_section",
                {"threadId": job["thread_id"], "hostId": "local", "sectionId": section_id},
                call_id, 30,
            ))
            if payload.get("threadId") != job["thread_id"] or payload.get("sectionId") != section_id:
                raise thread_runtime.HostError("Codex sidebar move returned another task or section")
            job.update(
                sidebar_status="completed", sidebar_section_id=section_id,
                sidebar_section_name=self.sidebar_section_name,
                sidebar_organized_at=utcnow(),
            )
            job.pop("sidebar_retry_at", None)
            job.pop("sidebar_error", None)
        except thread_runtime.HostError as error:
            attempts = int(job.get("sidebar_attempts", 0)) + 1
            job.update(
                sidebar_status="deferred", sidebar_attempts=attempts,
                sidebar_error=str(error),
            )
            if attempts < 3:
                job["sidebar_retry_at"] = time.time() + 15
            else:
                job.pop("sidebar_retry_at", None)
        self.save(job)

    def notification(self, event):
        # Server-initiated approval/input requests are durable, never approved
        # by this transport. The supervising conversation handles the request.
        if "id" in event:
            key = str(event["id"])
            self.pending_requests[key] = event
            save_json(self.root / "requests" / (digest([self.instance, key]) + ".json"),
                      {"status": "pending", "request": event, "server_instance": self.instance, "received_at": utcnow()})

    def answer(self, event):
        if event.get("server_instance") != self.instance or not event.get("user_decision_reference"):
            raise ValueError("Native input response needs its current server instance and actual user answer")
        key = str(event["request_id"])
        request = self.pending_requests.get(key)
        if request is None:
            raise ValueError("Native request is no longer pending; do not replay approval on another request")
        self.host.answer_request(request["id"], event["result"])
        del self.pending_requests[key]
        save_json(self.root / "requests" / (digest([self.instance, key]) + ".json"), {"status": "answered", "response": event})
        task_id = (request.get("params") or {}).get("threadId")
        for path in self.root.glob("*/effect.json"):
            job = load_json(path)
            if job.get("thread_id") == task_id and job.get("status") == "needs_input":
                job["status"] = "running"
                self.save(job)

    def directory(self, key):
        return self.root / digest(key)[:24]

    def read(self, key):
        path = self.directory(key) / "effect.json"
        return load_json(path) if path.exists() else None

    def save(self, job):
        save_json(self.directory(job["key"]) / "effect.json", job)

    def start_owner_turn(self, notification, spec, owner_thread_id):
        """Start one durable turn in the existing owner conversation."""
        if notification.get("status") == "delivered":
            return True
        job = self.prepare(spec)
        if job.get("thread_id") and job["thread_id"] != owner_thread_id:
            raise ValueError("Owner attention target changed after it was armed")
        job["thread_id"] = owner_thread_id
        self.save(job)
        if job.get("retry_at", 0) > time.time():
            return False
        if job.get("pending_prompt") or job["status"] in {"creating", "starting_turn"}:
            if not self.relay_owner_turn(job, job.get("pending_prompt") or spec["prompt"]):
                return False
            job = self.read(spec["key"])
        if not job.get("turn_id"):
            return False
        notification.update(
            status="delivered",
            job_key=job["key"],
            thread_id=owner_thread_id,
            turn_id=job["turn_id"],
            delivered_at=utcnow(),
        )
        save_json(Path(notification["reference"]), notification)
        return True

    @staticmethod
    def relayed_turn(task, prompt):
        """Find the exact desktop-relayed turn in a task snapshot."""
        for turn in reversed(task.get("turns", [])):
            for item in turn.get("items", []):
                texts = [item.get("text", ""), item.get("output", "")]
                texts.extend(
                    content.get("text", "") for content in item.get("content", [])
                    if isinstance(content, dict)
                )
                if any(prompt in text for text in texts if isinstance(text, str)):
                    return turn
        return None

    def relay_owner_turn(self, job, prompt):
        """Wake the desktop-owned conversation without acquiring its writer."""
        job.update(status="starting_turn", pending_prompt=prompt, transport="codex-app-tools")
        self.save(job)
        directory = self.directory(job["key"])
        name = "owner-relay-" + str(job["attempt"])
        request_path = directory / (name + "-request.json")
        response_path = directory / (name + "-response.json")

        observed = self.host.call("thread/read", {"threadId": job["thread_id"], "includeTurns": True})
        turn = self.relayed_turn(observed["thread"], prompt)
        if not turn and not response_path.exists():
            request = {
                "source_thread_id": self.source_thread_id or job["thread_id"],
                "target_thread_id": job["thread_id"],
                "prompt": prompt,
                "call_id": job["client_message_id"],
            }
            if not request_path.exists():
                save_json(request_path, request)
            elif load_json(request_path) != request:
                raise ValueError("Owner relay request changed after it was armed")
            result = self.owner_relay(
                request["source_thread_id"], request["target_thread_id"],
                request["prompt"], request["call_id"],
            )
            save_json(response_path, {"result": result})
            observed = self.host.call("thread/read", {"threadId": job["thread_id"], "includeTurns": True})
            turn = self.relayed_turn(observed["thread"], prompt)
        if not turn and response_path.exists():
            job.update(
                turn_id="app-relay:" + job["client_message_id"],
                status="completed",
                result_reference=str(response_path),
                completed_at=utcnow(),
            )
            job.pop("pending_prompt", None)
            job.pop("retry_at", None)
            self.save(job)
            return True
        if not turn:
            job["retry_at"] = time.time() + 2
            self.save(job)
            return False
        job.update(turn_id=turn["id"], status="running", started_at=utcnow())
        job.pop("pending_prompt", None)
        job.pop("retry_at", None)
        self.save(job)
        return True

    def relay_worker_turn(self, job, prompt):
        """Start a worker turn through Codex Desktop so its activity is live."""
        job.update(status="starting_turn", pending_prompt=prompt, transport="codex-app-tools")
        self.save(job)
        directory = self.directory(job["key"])
        name = "worker-relay-" + str(job["attempt"])
        request_path = directory / (name + "-request.json")
        response_path = directory / (name + "-response.json")

        observed = self.host.call("thread/read", {"threadId": job["thread_id"], "includeTurns": True})
        turn = self.relayed_turn(observed["thread"], prompt)
        if not turn and not response_path.exists():
            request = {
                "source_thread_id": self.source_thread_id,
                "target_thread_id": job["thread_id"],
                "prompt": prompt,
                "call_id": job["client_message_id"],
            }
            if not request_path.exists():
                save_json(request_path, request)
            elif load_json(request_path) != request:
                raise ValueError("Worker relay request changed after it was armed")
            result = self.owner_relay(
                request["source_thread_id"], request["target_thread_id"],
                request["prompt"], request["call_id"],
            )
            save_json(response_path, {"result": result})
            observed = self.host.call("thread/read", {"threadId": job["thread_id"], "includeTurns": True})
            turn = self.relayed_turn(observed["thread"], prompt)
        if not turn:
            job["retry_at"] = time.time() + 2
            self.save(job)
            return False
        job.update(turn_id=turn["id"], status="running", started_at=utcnow())
        job.pop("pending_prompt", None)
        job.pop("retry_at", None)
        self.save(job)
        return True

    def receipt(self, directory, name):
        path = directory / (name + ".json")
        if not path.exists():
            return None
        value = load_json(path)
        if "error" in value:
            raise thread_runtime.HostError(json.dumps(value["error"]))
        return value["result"]

    def prepare(self, spec):
        directory = self.directory(spec["key"])
        directory.mkdir(parents=True, exist_ok=True)
        job = self.read(spec["key"])
        if job:
            if job["spec_sha256"] != digest(spec):
                raise ValueError("Worker specification changed after dispatch")
        else:
            job = {"key": spec["key"], "spec": spec, "spec_sha256": digest(spec),
                   "created_at": utcnow(), "status": "creating", "attempt": 0,
                   "client_message_id": str(uuid.uuid4())}
            self.save(job)
        return job

    def submit(self, spec):
        job = self.prepare(spec)
        directory = self.directory(spec["key"])
        creation = self.receipt(directory, "create-response")
        if not creation:
            armed = directory / "create-request.json"
            if armed.exists():
                # The cwd is exclusive to this operation, also for read-only
                # workers. Reconcile an actual persisted task, never absence.
                found, cursor = [], None
                for _ in range(20):
                    listing = self.host.call("thread/list", {"cwd": spec["cwd"], "limit": 100, "cursor": cursor})
                    earliest = datetime.fromisoformat(job["created_at"]).timestamp() - 5
                    found.extend(x for x in listing.get("data", []) if x.get("cwd") == spec["cwd"] and x.get("createdAt", 0) >= earliest)
                    cursor = listing.get("nextCursor")
                    if not cursor:
                        break
                if cursor or len(found) != 1:
                    raise thread_runtime.HostError("Creation outcome unknown: exact operation cwd did not identify one task; no duplicate was started")
                creation = self.host.call("thread/read", {"threadId": found[0]["id"], "includeTurns": False})
                save_json(directory / "create-response.json", {"result": creation, "reconciled": True})
            else:
                request = {"cwd": spec["cwd"], "ephemeral": False}
                if spec.get("model"):
                    request["model"] = spec["model"]
                save_json(armed, request)
                creation = self.host.call("thread/start", request, receipt_path=directory / "create-response.json")
                self.loaded.add(creation["thread"]["id"])
        job["thread_id"] = creation["thread"]["id"]
        job["actual_model"] = creation.get("model") or job.get("actual_model")
        self.save(job)
        if not job.get("named"):
            self.host.call("thread/name/set", {"threadId": job["thread_id"], "name": spec.get("title", "Ticket Train — " + spec["key"])})
            job["named"] = True
            self.save(job)
        self.organize_worker(job)
        if job.get("pending_prompt") or job["status"] == "creating":
            self.start_turn(job, job.get("pending_prompt") or spec["prompt"])
        return self.read(spec["key"])

    def start_turn(self, job, prompt):
        if self.source_thread_id:
            return self.relay_worker_turn(job, prompt)
        job.update(status="starting_turn", pending_prompt=prompt)
        self.save(job)
        directory = self.directory(job["key"])
        name = "turn-" + str(job["attempt"])
        receipt = self.receipt(directory, name + "-response")
        if not receipt:
            if job["thread_id"] not in self.loaded:
                resume_receipt = directory / (name + "-resume-response.json")
                try:
                    resumed = self.host.call(
                        "thread/resume", {"threadId": job["thread_id"], "excludeTurns": True},
                        receipt_path=resume_receipt,
                    )
                except thread_runtime.HostError as error:
                    if "already has an active writer" not in str(error):
                        raise
                    resume_receipt.unlink(missing_ok=True)
                    job["retry_at"] = time.time() + 30
                    self.save(job)
                    return False
                job["actual_model"] = resumed.get("model") or job.get("actual_model")
                self.save(job)
                self.loaded.add(job["thread_id"])
            request_path = directory / (name + "-request.json")
            if request_path.exists():
                observed = self.host.call("thread/read", {"threadId": job["thread_id"], "includeTurns": True})
                turns = observed["thread"].get("turns", [])
                # On this run-owned task only this runner starts turns. A
                # count mismatch is ambiguous and must never trigger a repeat.
                if len(turns) != job["attempt"] + 1:
                    raise thread_runtime.HostError("Turn outcome unknown; no duplicate prompt was submitted")
                receipt = {"turn": turns[-1]}
                save_json(directory / (name + "-response.json"), {"result": receipt, "reconciled": True})
            else:
                request = {"threadId": job["thread_id"], "clientUserMessageId": job["client_message_id"],
                           "input": [{"type": "text", "text": prompt, "text_elements": []}]}
                if job["spec"].get("effort"):
                    request["effort"] = job["spec"]["effort"]
                save_json(request_path, request)
                receipt = self.host.call("turn/start", request, receipt_path=directory / (name + "-response.json"))
        job.update(turn_id=receipt["turn"]["id"], status="running", started_at=utcnow())
        job.pop("pending_prompt", None)
        job.pop("retry_at", None)
        self.save(job)
        return True

    def observe(self, job):
        self.organize_worker(job)
        job = self.read(job["key"])
        if job.get("pending_prompt"):
            self.start_turn(job, job["pending_prompt"])
            return self.read(job["key"])
        if job["status"] == "completed":
            return job
        if job["status"] == "blocked":
            terminal = job.get("terminal_turn_status") or (job.get("error") or {}).get("message")
            if terminal not in {"failed", "interrupted"}:
                return job
            if terminal == "failed" and int(job.get("service_retry_count", 0)) >= 2:
                return job
            # Older releases used the total turn number as the service retry
            # budget. Result repairs and user-input resumes could therefore
            # exhaust recovery before the first actual host interruption.
            job["status"] = "running"
        if job["status"] == "creating":
            return self.submit(job["spec"])
        if job.get("retry_at", 0) > time.time():
            return job
        task_id = job["thread_id"]
        if job.get("transport") != "codex-app-tools" and task_id not in self.loaded:
            self.host.call("thread/resume", {"threadId": task_id, "excludeTurns": True})
            self.loaded.add(task_id)
        observation = self.host.call("thread/read", {"threadId": task_id, "includeTurns": True})
        directory = self.directory(job["key"])
        save_json(directory / "observation.json", {"format": "ticket-train-native-observation-v1", "captured_at": utcnow(), "responses": [observation]})
        task = observation["thread"]
        turn = next((x for x in task.get("turns", []) if x["id"] == job.get("turn_id")), None)
        if not turn:
            raise thread_runtime.HostError("Recorded turn is absent; preserve task identity")
        flags = (task.get("status") or {}).get("activeFlags", [])
        if any(x in flags for x in ("waitingOnApproval", "waitingOnUserInput")):
            job["status"] = "needs_input"
        elif turn["status"] == "completed":
            messages = [x.get("text", "") for x in turn.get("items", []) if x.get("type") == "agentMessage"]
            text = messages[-1] if messages else ""
            save_json(directory / "result.json", {"thread_id": task_id, "turn_id": turn["id"],
                                                   "text": text, "completed_at": utcnow()})
            job.update(status="completed", result_reference=str(directory / "result.json"), completed_at=utcnow())
        elif turn["status"] in {"failed", "interrupted"}:
            job["terminal_turn_status"] = turn["status"]
            job["error"] = turn.get("error") or {"message": turn["status"]}
            retry_count = int(job.get("service_retry_count", 0))
            made_progress = turn["status"] == "interrupted" and any(
                item.get("type") == "fileChange"
                or (item.get("type") == "commandExecution" and item.get("status") == "completed")
                for item in turn.get("items", [])
            )
            if made_progress:
                retry_count = 0
                job["service_progress_turn_id"] = turn["id"]
            if retry_count >= 2:
                job["status"] = "blocked"
            elif job.get("retry_at"):
                job["service_retry_count"] = retry_count + 1
                job["attempt"] += 1
                job.pop("retry_at", None)
                job["client_message_id"] = str(uuid.uuid4())
                self.save(job)
                retry_prompt = "Resume this same authorized phase after the host interruption. Reconcile existing files and results before repeating any work.\n" + job["spec"]["prompt"]
                if job.get("transport") == "codex-app-tools":
                    self.relay_owner_turn(job, retry_prompt)
                else:
                    self.start_turn(job, retry_prompt)
                return self.read(job["key"])
            else:
                job["retry_at"] = time.time() + (15, 60)[retry_count]
        self.save(job)
        return job

    def close(self):
        self.host.close()


def verification_event(args):
    raise ValueError("Legacy state writers were retired; use the canonical verification adapter")


def human_gate_event(args):
    raise ValueError("Legacy state writers were retired; use a canonical human-gate event")


def phase_candidates(args: argparse.Namespace) -> None:
    """Bounded read-only fallback when the task list omits a new worktree task.

    Local session records only suggest IDs. The product read_task operation
    must still verify identity/visibility; absence never authorizes a retry.
    """
    state = load_json(args.state)
    phase = state["procedure"]["phases"][args.phase_key]
    created = datetime.fromisoformat(phase["created_at"].replace("Z", "+00:00"))
    owner = (state.get("orchestrator_lease") or {}).get("owner_thread_id")
    root = args.sessions_root
    if root is None:
        root = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")) / "sessions"
    # Include the preceding local-calendar day to tolerate timezone boundaries.
    start = created.astimezone(timezone.utc).date() - timedelta(days=1)
    end = datetime.now(timezone.utc).date()
    days = min((end - start).days + 1, 8)
    paths = []
    for offset in range(max(0, days)):
        day = start + timedelta(days=offset)
        paths.extend((root / day.strftime("%Y/%m/%d")).glob("rollout-*.jsonl"))
    candidates = []
    scanned = 0
    incomplete = (end - start).days + 1 > 8 or len(paths) > 500
    token = re.compile(r"(?<![\w-])" + re.escape(args.phase_key) + r"(?![\w-])")
    for path in sorted(paths)[:500]:
        scanned += 1
        try:
            with path.open("rb") as handle:
                # Never read a task's accumulated transcript or reasoning.
                header = handle.read(262144)
            lines = header.splitlines()
            meta = json.loads(lines[0])
            if meta.get("type") != "session_meta":
                continue
            info = meta["payload"]
            task_id = info.get("id") or info.get("session_id")
            if task_id == owner or not task_id:
                continue
            stamp = datetime.fromisoformat(info["timestamp"].replace("Z", "+00:00"))
            if stamp < created - timedelta(seconds=5):
                continue
            for line in lines[1:]:
                record = json.loads(line)
                payload = record.get("payload", {})
                if record.get("type") != "response_item":
                    continue
                if payload.get("role") == "assistant":
                    break
                prompt = ""
                if payload.get("role") == "user":
                    prompt = "\n".join(block.get("text", "") for block in payload.get("content", []) if isinstance(block, dict))
                elif payload.get("type") == "function_call_output" and payload.get("name") == "create_thread":
                    # Desktop materialization injects its initial request here.
                    output = payload.get("output")
                    if isinstance(output, str) and "<codex_delegation>" in output:
                        prompt = output
                if token.search(prompt) and str(state["run_id"]) in prompt:
                    candidates.append({"thread_id": task_id, "cwd": info.get("cwd"), "session_reference": str(path), "visibility_verified": False})
                    break
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            incomplete = True
    sys.stdout.write(json.dumps({
        "status": "candidates-found" if candidates else "unresolved",
        "phase_key": args.phase_key, "candidates": candidates[:8],
        "scanned_headers": scanned, "scan_incomplete": incomplete or len(candidates) > 8,
        "visibility_requires_product_read": True, "may_create_replacement": False,
    }, ensure_ascii=False, indent=2) + "\n")
