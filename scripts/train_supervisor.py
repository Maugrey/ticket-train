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
                 source_thread_id=None, repository=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.instance = str(uuid.uuid4())
        self.pending_requests = {}
        self.host = host_factory(thread_runtime.app_server_executable(executable), event_sink=self.notification)
        self.loaded = set()
        self.source_thread_id = source_thread_id
        self.repository = repository
        self.source_project_id = None
        self.source_project_resolved = False

    def project_id(self):
        """Resolve the orchestrator's app project once for all worker tasks."""
        if not self.source_project_resolved:
            self.source_project_resolved = True
            if self.source_thread_id:
                source = self.host.call(
                    "thread/read", {"threadId": self.source_thread_id, "includeTurns": False}
                )["thread"]
                value = source.get("projectId")
                self.source_project_id = value if isinstance(value, str) and value else None
            if not self.source_project_id and self.repository:
                expected = os.path.normcase(os.path.abspath(self.repository))
                projects = self.host.call("project/list", {}).get("data", [])
                matches = [
                    project for project in projects
                    if any(
                        os.path.normcase(os.path.abspath(root.get("path", ""))) == expected
                        for root in project.get("roots", [])
                    )
                ]
                if len(matches) > 1:
                    raise thread_runtime.HostError(
                        "Repository belongs to multiple Codex projects: "
                        + ", ".join(project["id"] for project in matches)
                    )
                if matches:
                    self.source_project_id = matches[0]["id"]
        return self.source_project_id

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

    def start_owner_turn(self, notification):
        """Deliver one durable, event-driven turn to the owning task."""
        if not self.source_thread_id:
            raise ValueError("Owner notification needs the source task ID")
        directory = Path(notification["directory"])
        directory.mkdir(parents=True, exist_ok=True)
        response_path = directory / "turn-response.json"
        request_path = directory / "turn-request.json"
        if notification.get("status") == "delivered":
            return True

        if response_path.exists():
            receipt = self.receipt(directory, "turn-response")
            notification.update(
                status="delivered",
                turn_id=receipt["turn"]["id"],
                delivered_at=utcnow(),
            )
            notification.pop("retry_at", None)
            notification.pop("retry_reason", None)
            save_json(Path(notification["reference"]), notification)
            return True
        if notification.get("status") == "ambiguous":
            return False
        if notification.get("retry_at", 0) > time.time():
            return False
        if request_path.exists() and notification.get("retry_reason") != "active-writer":
            notification.update(
                status="ambiguous",
                error="Owner turn intent exists without a response; do not repeat the mutation",
                ambiguous_at=utcnow(),
            )
            save_json(Path(notification["reference"]), notification)
            return False

        try:
            if self.source_thread_id not in self.loaded:
                self.host.call(
                    "thread/resume",
                    {"threadId": self.source_thread_id, "excludeTurns": True},
                )
                self.loaded.add(self.source_thread_id)
            request = {
                "threadId": self.source_thread_id,
                "clientUserMessageId": notification["client_message_id"],
                "input": [{"type": "text", "text": notification["prompt"], "text_elements": []}],
            }
            save_json(request_path, request)
            receipt = self.host.call("turn/start", request, receipt_path=response_path)
        except thread_runtime.HostError as error:
            if "already has an active writer" in str(error):
                response_path.unlink(missing_ok=True)
                notification["retry_at"] = time.time() + 30
                notification["retry_reason"] = "active-writer"
                save_json(Path(notification["reference"]), notification)
                return False
            # The host may have accepted a mutation before a timeout or
            # transport loss. Preserve the intent and never repeat blindly.
            notification.update(status="ambiguous", error=str(error), ambiguous_at=utcnow())
            save_json(Path(notification["reference"]), notification)
            raise

        notification.update(
            status="delivered",
            turn_id=receipt["turn"]["id"],
            delivered_at=utcnow(),
        )
        notification.pop("retry_at", None)
        notification.pop("retry_reason", None)
        save_json(Path(notification["reference"]), notification)
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
                project_id = self.project_id()
                if project_id:
                    request["projectId"] = project_id
                if spec.get("model"):
                    request["model"] = spec["model"]
                save_json(armed, request)
                creation = self.host.call("thread/start", request, receipt_path=directory / "create-response.json")
                self.loaded.add(creation["thread"]["id"])
        project_id = self.project_id()
        if project_id and creation["thread"].get("projectId") != project_id:
            updated = self.host.call(
                "thread/metadata/update", {"threadId": creation["thread"]["id"], "projectId": project_id}
            )
            creation["thread"] = updated["thread"]
            save_json(directory / "create-response.json", {"result": creation, "project_reconciled": True})
        job["thread_id"] = creation["thread"]["id"]
        job["actual_model"] = creation.get("model") or job.get("actual_model")
        self.save(job)
        if not job.get("named"):
            self.host.call("thread/name/set", {"threadId": job["thread_id"], "name": spec.get("title", "Ticket Train — " + spec["key"])})
            job["named"] = True
            self.save(job)
        if job.get("pending_prompt") or job["status"] == "creating":
            self.start_turn(job, job.get("pending_prompt") or spec["prompt"])
        return self.read(spec["key"])

    def start_turn(self, job, prompt):
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
        if job.get("pending_prompt"):
            self.start_turn(job, job["pending_prompt"])
            return self.read(job["key"])
        if job["status"] in {"completed", "blocked"}:
            return job
        if job["status"] == "creating":
            return self.submit(job["spec"])
        if job.get("retry_at", 0) > time.time():
            return job
        task_id = job["thread_id"]
        if task_id not in self.loaded:
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
            job["error"] = turn.get("error") or {"message": turn["status"]}
            if job["attempt"] >= 2:
                job["status"] = "blocked"
            elif job.get("retry_at"):
                job["attempt"] += 1
                job.pop("retry_at", None)
                job["client_message_id"] = str(uuid.uuid4())
                self.save(job)
                self.start_turn(job, "Resume this same authorized phase after the host interruption. Reconcile existing files and results before repeating any work.\n" + job["spec"]["prompt"])
                return self.read(job["key"])
            else:
                job["retry_at"] = time.time() + (15, 60)[job["attempt"]]
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
