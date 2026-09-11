#!/usr/bin/env python3
"""Create, discover, and claim canonical ticket-train runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


ACTIVE_RUN_STATES = {"ACTIVE", "AWAITING_USER", "BLOCKED", "CHECKPOINT", "SPLIT"}
DEFAULT_LEASE_MINUTES = 30
DEFAULT_HANDOFF_MINUTES = 15


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now().isoformat()


def default_root() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home) / "ticket-train" / "runs"
    return Path.home() / ".codex" / "ticket-train" / "runs"


def default_legacy_root() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home) / "ticket-trains"
    return Path.home() / ".codex" / "ticket-trains"


def canonical_text(value: str) -> str:
    return value.strip().replace("\\", "/").rstrip("/").casefold()


def ticket_ids(value: str) -> list[str]:
    tickets = sorted({item.strip() for item in value.split(",") if item.strip()})
    if not tickets:
        raise ValueError("At least one ticket ID is required")
    return tickets


def run_fingerprint(repository: str, train_branch: str, source: str, tickets: list[str]) -> str:
    payload = {
        "repository": canonical_text(repository),
        "train_branch": canonical_text(train_branch),
        "source": canonical_text(source),
        "tickets": [canonical_text(ticket) for ticket in sorted(tickets)],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def slug(value: str) -> str:
    result = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-._").lower()
    return result[:48] or "train"


def load_json(path: Path) -> dict[str, Any]:
    with Path(path).expanduser().resolve().open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def save_json(path: Path, value: dict[str, Any]) -> None:
    if isinstance(value.get("procedure"), dict):
        project_control(value)
    resolved = Path(path).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_suffix(resolved.suffix + "." + secrets.token_hex(8) + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, resolved)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def file_lock(lock: Path, timeout_seconds: float = 10.0) -> Iterator[None]:
    """Process-owned lock. Keep the inode: unlinking it allows two owners."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    with lock.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        while True:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise ValueError(f"Resource is locked by a live process: {lock}") from error
                time.sleep(0.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def directory_lock(root: Path, timeout_seconds: float = 10.0):
    return file_lock(root / ".registry.lock", timeout_seconds)


def require_owner(state: dict[str, Any], owner: str | None, epoch: str | None = None) -> None:
    current = state.get("orchestrator_lease") or {}
    if not owner or owner != current.get("owner_thread_id"):
        raise ValueError("Only the canonical owner may write this run")
    if current.get("epoch") and epoch != current["epoch"]:
        raise ValueError("Ownership generation changed; reopen the run before writing")


def renew_lease(state: dict[str, Any]) -> None:
    current = state["orchestrator_lease"]
    current["heartbeat_at"] = now_iso()
    current["expires_at"] = (now() + timedelta(minutes=DEFAULT_LEASE_MINUTES)).isoformat()


def project_control(state: dict[str, Any]) -> None:
    """Compatibility view only; procedure is the sole writable workflow state."""
    proc = state["procedure"]
    old = state.get("control") or {}
    state["run_status"] = proc.get("run_status", state.get("run_status"))
    state["supervision"] = dict(proc.get("supervision", {}))
    state["control"] = {
        "derived_from": "procedure", "revision": proc.get("revision"),
        "manifest_updated_at": proc.get("updated_at"), "manifest_reconciled": True,
        "terminal_reason": "COMPLETED" if state["run_status"] == "COMPLETED" else None,
        "next_automatic_action": "derived-by-controller",
        "requested_ticket_states": {k: v.get("status") for k, v in proc.get("tickets", {}).items()},
        "phases": list(proc.get("phases", {}).values()),
        "pending_human_gates": [v for v in proc.get("human_gates", {}).values() if v.get("status", "").startswith("PENDING")],
        "train_size_budget": proc.get("train_size_budget", {}),
        "finalization": proc.get("finalization", {}),
        "launch_unknown_phase_keys": [k for k, v in proc.get("phases", {}).items() if v.get("launch_state") == "LAUNCH_UNKNOWN"],
        "blocking_conditions": [],
        "duplicate_session_inventory": old.get("duplicate_session_inventory", []),
        "cost_anomaly_status": old.get("cost_anomaly_status", "unknown"),
    }


def pin_release(run_directory: Path) -> dict[str, Any]:
    """Freeze the executable skill used by this run, outside the installed skill."""
    source = Path(__file__).resolve().parent.parent
    files = sorted(p for p in source.rglob("*") if p.is_file()
                   and p.suffix in {".py", ".js", ".md", ".yaml"}
                   and ".git" not in p.parts and "__pycache__" not in p.parts
                   and not p.name.startswith("test_"))
    hashes = {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    release_id = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    destination = run_directory / "runtime" / release_id
    if not destination.exists():
        staged = destination.with_name(".preparing-" + secrets.token_hex(12))
        for name in hashes:
            target = staged / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != hashes[name]:
                raise ValueError("Installed runtime changed during pinning; retry from a stable release")
        os.replace(staged, destination)
    return {"id": release_id, "path": str(destination), "files": hashes, "pinned_at": now_iso()}


def verify_release(state: dict[str, Any]) -> Path:
    release = state.get("runtime_release")
    if not isinstance(release, dict):
        raise ValueError("This legacy run has no pinned runtime; migrate it explicitly while idle")
    root = Path(release["path"]).resolve()
    for name, expected in release["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Pinned runtime was changed or is incomplete: " + name)
    return root


def manifest_paths(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.glob("*/manifest.json"))


def matching_runs(root: Path, fingerprint: str) -> list[tuple[Path, dict[str, Any]]]:
    matches: list[tuple[Path, dict[str, Any]]] = []
    for path in manifest_paths(root):
        try:
            state = load_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        identity = state.get("run_identity")
        if isinstance(identity, dict) and identity.get("fingerprint") == fingerprint:
            matches.append((path, state))
    return matches


def legacy_ticket_ids(state: dict[str, Any]) -> list[str]:
    raw = state.get("ticket_selection") or state.get("requested_tickets") or state.get("tickets")
    if not isinstance(raw, list):
        return []
    values: list[str] = []
    for item in raw:
        if isinstance(item, str):
            values.append(item.strip())
        elif isinstance(item, dict) and item.get("id"):
            values.append(str(item["id"]).strip())
    return sorted(value for value in values if value)


def legacy_source(state: dict[str, Any]) -> str:
    value = state.get("ticket_source") or state.get("source")
    if isinstance(value, dict):
        value = value.get("path") or value.get("locator") or value.get("url")
    return canonical_text(str(value or ""))


def legacy_train_branch(state: dict[str, Any]) -> str:
    value = state.get("train_branch")
    if not value:
        train = state.get("train")
        if isinstance(train, dict):
            value = train.get("branch") or train.get("name")
    if not value:
        candidate = state.get("base_branch")
        if isinstance(candidate, str) and "train" in candidate.casefold():
            value = candidate
    return canonical_text(str(value or ""))


def matching_legacy_runs(
    root: Path, train_branch: str, source: str, tickets: list[str]
) -> list[Path]:
    if not root.exists():
        return []
    expected_tickets = [canonical_text(ticket) for ticket in sorted(tickets)]
    expected_source = canonical_text(source)
    expected_branch = canonical_text(train_branch)
    candidates = set(root.rglob("manifest.json")) | set(root.rglob("run-manifest.json"))
    matches: list[Path] = []
    for path in sorted(candidates):
        try:
            state = load_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        found_tickets = [canonical_text(ticket) for ticket in legacy_ticket_ids(state)]
        if found_tickets != expected_tickets:
            continue
        if legacy_source(state) != expected_source:
            continue
        if legacy_train_branch(state) != expected_branch:
            continue
        matches.append(path.resolve())
    return matches


def lease_expired(lease: Any) -> bool:
    if not isinstance(lease, dict) or not lease.get("expires_at"):
        return True
    try:
        expires = datetime.fromisoformat(str(lease["expires_at"]).replace("Z", "+00:00"))
    except ValueError:
        return True
    return expires <= now()


def validate_visible_thread_id(owner_thread_id: str) -> str:
    value = str(owner_thread_id or "").strip()
    if not value:
        raise ValueError("orchestrator thread ID is required")
    if value.startswith(("/", "\\")) or value.casefold() in {"root", "agent", "orchestrator"}:
        raise ValueError(
            "orchestrator thread ID must identify the real user-visible task, not an agent path"
        )
    return value


def lease(owner_thread_id: str, lease_minutes: int) -> dict[str, Any]:
    owner_thread_id = validate_visible_thread_id(owner_thread_id)
    timestamp = now()
    return {
        "owner_thread_id": owner_thread_id,
        "epoch": secrets.token_hex(16),
        "claimed_at": timestamp.isoformat(),
        "heartbeat_at": timestamp.isoformat(),
        "expires_at": (timestamp + timedelta(minutes=lease_minutes)).isoformat(),
    }


def init_run(args: argparse.Namespace) -> int:
    root = args.root.expanduser().resolve()
    tickets = ticket_ids(args.tickets)
    fingerprint = run_fingerprint(args.repository, args.train_branch, args.source, tickets)
    with directory_lock(root):
        matches = matching_runs(root, fingerprint)
        active = [
            (path, state)
            for path, state in matches
            if state.get("run_status") in ACTIVE_RUN_STATES
        ]
        if active:
            path, state = active[-1]
            document = {
                "status": "existing-active-run",
                "manifest": str(path),
                "run_id": state.get("run_id"),
                "run_status": state.get("run_status"),
                "owner_thread_id": (state.get("orchestrator_lease") or {}).get("owner_thread_id"),
                "lease_expired": lease_expired(state.get("orchestrator_lease")),
                "required_action": "adopt-or-explicit-takeover; do not repeat completed phases",
            }
            sys.stdout.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
            return 3

        legacy = matching_legacy_runs(
            args.legacy_root.expanduser().resolve(), args.train_branch, args.source, tickets
        )
        if legacy and not args.adopt_legacy:
            sys.stdout.write(
                json.dumps(
                    {
                        "status": "legacy-run-found",
                        "legacy_manifests": [str(path) for path in legacy],
                        "required_action": "adopt and reconcile legacy manifests; do not repeat completed phases",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            )
            return 6

        stamp = now().strftime("%Y%m%dT%H%M%SZ")
        run_id = args.run_id or f"{stamp}-{slug(args.train_branch)}-{fingerprint[:8]}"
        path = root / run_id / "manifest.json"
        if path.exists():
            raise ValueError(f"Run manifest already exists: {path}")
        timestamp = now_iso()
        state = {
            "schema_version": 2,
            "run_id": run_id,
            "run_status": "ACTIVE",
            "execution_mode": args.execution_mode,
            "created_at": timestamp,
            "manifest_updated_at": timestamp,
            "run_identity": {
                "fingerprint": fingerprint,
                "repository": args.repository,
                "train_branch": args.train_branch,
                "source": args.source,
                "tickets": tickets,
            },
            "orchestrator_lease": lease(args.orchestrator_thread, args.lease_minutes),
            "pending_orchestrator_handoff": None,
            "orchestrator_rotation_policy": {
                "mode": "automatic-budgeted-handoff",
                "soft_total_tokens": 10_000_000,
                "hard_total_tokens": 25_000_000,
                "max_model_wakes": 50,
                "max_tool_calls": 500,
                "max_context_compactions": 1,
                "decision_packet_max_bytes": 16_384,
            },
            "supervision": {
                "mode": "UNRESOLVED",
                "status": "INACTIVE",
                "watcher_id": None,
                "last_check_at": None,
                "max_internal_poll_seconds": 300,
                "max_user_silence_seconds": 900,
            },
            "pending_human_action": None,
            "handoff_history": [],
            "analysis_artifacts": {},
            "legacy_manifest_inventory": [str(path) for path in legacy],
            "control": {
                "manifest_updated_at": timestamp,
                "manifest_reconciled": True,
                "terminal_reason": None,
                "next_automatic_action": (
                    "reconcile_legacy_manifests" if legacy else "await_orchestrator_confirmation"
                ),
                "launch_unknown_phase_keys": [],
                "pending_human_gates": [],
                "blocking_conditions": [],
                "requested_ticket_states": {ticket: "DISCOVERED" for ticket in tickets},
                "phases": [],
                "proportionality_profile_revision": None,
                "train_size_budget": {
                    "material_files": 0,
                    "schema_or_data_transformations": 0,
                    "structural_domains": [],
                    "checkpoint_crossed": False,
                },
                "review_pass_budgets": {},
                "verification_gates": {},
                "duplicate_session_inventory": [str(path) for path in legacy],
                "unmeasured_phase_inventory": [],
                "cost_anomaly_status": "checkpoint-open" if legacy else "clear",
                "finalization": {},
            },
        }
        state["runtime_release"] = pin_release(path.parent)
        save_json(path, state)
    sys.stdout.write(
        json.dumps(
            {"status": "created", "manifest": str(path), "run_id": run_id, "fingerprint": fingerprint},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return 0


def discover(args: argparse.Namespace) -> int:
    tickets = ticket_ids(args.tickets)
    fingerprint = run_fingerprint(args.repository, args.train_branch, args.source, tickets)
    matches = matching_runs(args.root.expanduser().resolve(), fingerprint)
    document = [
        {
            "manifest": str(path),
            "run_id": state.get("run_id"),
            "run_status": state.get("run_status"),
            "owner_thread_id": (state.get("orchestrator_lease") or {}).get("owner_thread_id"),
            "lease_expired": lease_expired(state.get("orchestrator_lease")),
        }
        for path, state in matches
    ]
    sys.stdout.write(json.dumps({"fingerprint": fingerprint, "matches": document}, indent=2) + "\n")
    return 0 if matches else 4


def claim(args: argparse.Namespace) -> int:
    path = args.state.expanduser().resolve()
    root = path.parent.parent
    with file_lock(path.parent / "driver" / "driver.lock", 0), directory_lock(path.parent):
        state = load_json(path)
        pending_handoff = state.get("pending_orchestrator_handoff")
        if isinstance(pending_handoff, dict) and pending_handoff.get("status") == "PREPARED":
            raise ValueError("a prepared handoff must be accepted with accept-handoff, not claim")
        current = state.get("orchestrator_lease")
        current_owner = current.get("owner_thread_id") if isinstance(current, dict) else None
        expired = lease_expired(current)
        if current_owner and current_owner != args.orchestrator_thread and not expired and not args.takeover:
            sys.stdout.write(
                json.dumps(
                    {
                        "status": "owned-by-another-orchestrator",
                        "owner_thread_id": current_owner,
                        "expires_at": current.get("expires_at"),
                        "required_action": "resume in the owner thread or request explicit takeover",
                    },
                    indent=2,
                )
                + "\n"
            )
            return 5
        if args.takeover and not args.user_authorized_takeover:
            raise ValueError("--takeover requires --user-authorized-takeover")
        history = state.setdefault("handoff_history", [])
        if not isinstance(history, list):
            raise ValueError("handoff_history must be a list")
        if current_owner and current_owner != args.orchestrator_thread:
            history.append(
                {
                    "from_thread_id": current_owner,
                    "to_thread_id": args.orchestrator_thread,
                    "at": now_iso(),
                    "reason": "explicit-takeover" if args.takeover else "expired-lease-adoption",
                }
            )
        state["orchestrator_lease"] = lease(args.orchestrator_thread, args.lease_minutes)
        state["manifest_updated_at"] = now_iso()
        save_json(path, state)
    sys.stdout.write(
        json.dumps(
            {
                "status": "claimed",
                "manifest": str(path),
                "owner_thread_id": args.orchestrator_thread,
                "takeover": bool(args.takeover),
            },
            indent=2,
        )
        + "\n"
    )
    return 0


def migrate_runtime(args: argparse.Namespace) -> int:
    import train_controller
    path = args.state.expanduser().resolve()
    with file_lock(path.parent / "driver" / "driver.lock", 0), directory_lock(path.parent):
        state = load_json(path)
        require_owner(state, args.owner_thread_id, args.owner_epoch)
        phases = (state.get("procedure") or {}).get("phases", {})
        active = [
            phase for phase in phases.values()
            if phase.get("launch_state") in train_controller.ACTIVE_PHASE_STATES
        ]
        terminal = {"completed", "failed", "interrupted", "needs_input"}

        def transport_is_idle(phase):
            observation = phase.get("runtime_observation") or {}
            key = phase.get("phase_key")
            digest = hashlib.sha256(
                json.dumps(key, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            journal = path.parent / "driver" / "effects" / digest[:24] / "effect.json"
            if not key or not journal.is_file():
                return False
            job = load_json(journal)
            return (
                observation.get("runtime_status") in terminal
                and observation.get("thread_id") == phase.get("thread_id") == job.get("thread_id")
                and observation.get("turn_id") == job.get("turn_id")
                and job.get("status") in {"blocked", "completed", "needs_input"}
            )

        if active and not all(transport_is_idle(phase) for phase in active):
            raise ValueError("Runtime migration requires all technical tasks to be idle")
        from contextlib import ExitStack
        with ExitStack() as probes:
            for lock in path.parent.rglob("*.runner.lock"):
                probes.enter_context(file_lock(lock, 0))
            previous = state.get("runtime_release")
            release = pin_release(path.parent)
            train_controller.migrate_procedure(state)
            state.setdefault("runtime_migrations", []).append({"from": previous, "to": release["id"], "at": now_iso(), "owner": args.owner_thread_id})
            state["runtime_release"] = release
            save_json(path, state)
    print(json.dumps({"status": "migrated", "release": release["id"], "state": str(path)}))
    return 0


def prepare_handoff(args: argparse.Namespace) -> int:
    """Prepare a single-use controlled handoff without creating a second run."""
    path = args.state.expanduser().resolve()
    root = path.parent.parent
    packet_path = args.packet.expanduser().resolve()
    if not packet_path.is_file():
        raise ValueError(f"handoff decision packet does not exist: {packet_path}")
    with file_lock(path.parent / "driver" / "driver.lock", timeout_seconds=0), directory_lock(path.parent):
        state = load_json(path)
        require_owner(state, args.from_thread, args.owner_epoch)
        current = state.get("orchestrator_lease")
        owner = current.get("owner_thread_id") if isinstance(current, dict) else None
        if owner != args.from_thread:
            raise ValueError("only the current orchestrator owner may prepare a handoff")
        pending = state.get("pending_orchestrator_handoff")
        if isinstance(pending, dict) and pending.get("status") == "PREPARED":
            raise ValueError("an orchestrator handoff is already prepared")
        token = secrets.token_urlsafe(24)
        token_sha256 = hashlib.sha256(token.encode("utf-8")).hexdigest()
        timestamp = now()
        handoff = {
            "status": "PREPARED",
            "from_thread_id": args.from_thread,
            "owner_epoch": args.owner_epoch,
            "reason": args.reason,
            "packet_reference": str(packet_path),
            "token_sha256": token_sha256,
            "prepared_at": timestamp.isoformat(),
            "expires_at": (timestamp + timedelta(minutes=args.handoff_minutes)).isoformat(),
        }
        state["pending_orchestrator_handoff"] = handoff
        if isinstance(current, dict):
            current["handoff_status"] = "PREPARED"
            current["handoff_expires_at"] = handoff["expires_at"]
        control_plane = state.get("control_plane")
        if isinstance(control_plane, dict):
            rotation = control_plane.setdefault("rotation", {})
            rotation["status"] = "PREPARED"
            rotation["handoff_packet_reference"] = handoff["packet_reference"]
        state["manifest_updated_at"] = now_iso()
        save_json(path, state)
    sys.stdout.write(
        json.dumps(
            {
                "status": "handoff-prepared",
                "manifest": str(path),
                "from_thread_id": args.from_thread,
                "handoff_token": token,
                "expires_at": handoff["expires_at"],
                "packet_reference": handoff["packet_reference"],
                "required_action": "create one visible successor and pass the packet plus single-use token",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return 0


def accept_handoff(args: argparse.Namespace) -> int:
    """Atomically transfer the lease to the prepared visible successor."""
    path = args.state.expanduser().resolve()
    root = path.parent.parent
    with file_lock(path.parent / "driver" / "driver.lock", timeout_seconds=0), directory_lock(path.parent):
        state = load_json(path)
        pending = state.get("pending_orchestrator_handoff")
        if not isinstance(pending, dict) or pending.get("status") != "PREPARED":
            raise ValueError("no prepared orchestrator handoff exists")
        if datetime.fromisoformat(str(pending["expires_at"]).replace("Z", "+00:00")) <= now():
            raise ValueError("prepared orchestrator handoff has expired")
        require_owner(state, pending["from_thread_id"], pending.get("owner_epoch"))
        supplied = hashlib.sha256(args.handoff_token.encode("utf-8")).hexdigest()
        if not secrets.compare_digest(supplied, str(pending.get("token_sha256") or "")):
            raise ValueError("invalid orchestrator handoff token")
        if pending.get("from_thread_id") == args.to_thread:
            raise ValueError("handoff successor must be a different thread")
        history = state.setdefault("handoff_history", [])
        if not isinstance(history, list):
            raise ValueError("handoff_history must be a list")
        accepted_at = now_iso()
        history.append(
            {
                "from_thread_id": pending["from_thread_id"],
                "to_thread_id": args.to_thread,
                "at": accepted_at,
                "reason": f"controlled-{pending['reason']}",
                "packet_reference": pending["packet_reference"],
            }
        )
        state["orchestrator_lease"] = lease(args.to_thread, args.lease_minutes)
        state["pending_orchestrator_handoff"] = None
        control_plane = state.get("control_plane")
        if isinstance(control_plane, dict):
            rotation = control_plane.setdefault("rotation", {})
            rotation.update({"status": "CLEAR", "reasons": [], "accepted_at": accepted_at})
        state["manifest_updated_at"] = accepted_at
        save_json(path, state)
    sys.stdout.write(
        json.dumps(
            {
                "status": "handoff-accepted",
                "manifest": str(path),
                "owner_thread_id": args.to_thread,
                "packet_reference": pending["packet_reference"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return 0


def cancel_handoff(args: argparse.Namespace) -> int:
    """Cancel a prepared handoff when visible successor creation failed."""
    path = args.state.expanduser().resolve()
    root = path.parent.parent
    with file_lock(path.parent / "driver" / "driver.lock", timeout_seconds=0), directory_lock(path.parent):
        state = load_json(path)
        require_owner(state, args.from_thread, args.owner_epoch)
        pending = state.get("pending_orchestrator_handoff")
        if not isinstance(pending, dict) or pending.get("status") != "PREPARED":
            raise ValueError("no prepared orchestrator handoff exists")
        if pending.get("from_thread_id") != args.from_thread:
            raise ValueError("only the preparing orchestrator may cancel the handoff")
        supplied = hashlib.sha256(args.handoff_token.encode("utf-8")).hexdigest()
        if not secrets.compare_digest(supplied, str(pending.get("token_sha256") or "")):
            raise ValueError("invalid orchestrator handoff token")
        state["pending_orchestrator_handoff"] = None
        current = state.get("orchestrator_lease")
        if isinstance(current, dict):
            current.pop("handoff_status", None)
            current.pop("handoff_expires_at", None)
        control_plane = state.get("control_plane")
        if isinstance(control_plane, dict):
            rotation = control_plane.setdefault("rotation", {})
            rotation.update({"status": "REQUIRED", "cancelled_at": now_iso()})
        state["manifest_updated_at"] = now_iso()
        save_json(path, state)
    sys.stdout.write(json.dumps({"status": "handoff-cancelled", "manifest": str(path)}, indent=2) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    migration = subparsers.add_parser("migrate-runtime")
    migration.add_argument("--state", type=Path, required=True)
    migration.add_argument("--owner-thread-id", required=True)
    migration.add_argument("--owner-epoch", required=True)
    migration.set_defaults(handler=migrate_runtime)

    initialize = subparsers.add_parser("init")
    initialize.add_argument("--root", type=Path, default=default_root())
    initialize.add_argument("--legacy-root", type=Path, default=default_legacy_root())
    initialize.add_argument("--repository", required=True)
    initialize.add_argument("--train-branch", required=True)
    initialize.add_argument("--source", required=True)
    initialize.add_argument("--tickets", required=True)
    initialize.add_argument("--orchestrator-thread", required=True)
    initialize.add_argument("--execution-mode", choices=("dry-run", "live"), required=True)
    initialize.add_argument("--run-id")
    initialize.add_argument("--adopt-legacy", action="store_true")
    initialize.add_argument("--lease-minutes", type=int, default=DEFAULT_LEASE_MINUTES)
    initialize.set_defaults(handler=init_run)

    find = subparsers.add_parser("discover")
    find.add_argument("--root", type=Path, default=default_root())
    find.add_argument("--repository", required=True)
    find.add_argument("--train-branch", required=True)
    find.add_argument("--source", required=True)
    find.add_argument("--tickets", required=True)
    find.set_defaults(handler=discover)

    acquire = subparsers.add_parser("claim")
    acquire.add_argument("--state", type=Path, required=True)
    acquire.add_argument("--orchestrator-thread", required=True)
    acquire.add_argument("--lease-minutes", type=int, default=DEFAULT_LEASE_MINUTES)
    acquire.add_argument("--takeover", action="store_true")
    acquire.add_argument("--user-authorized-takeover", action="store_true")
    acquire.set_defaults(handler=claim)

    prepare = subparsers.add_parser("prepare-handoff")
    prepare.add_argument("--state", type=Path, required=True)
    prepare.add_argument("--from-thread", required=True)
    prepare.add_argument("--owner-epoch", required=True)
    prepare.add_argument("--reason", choices=("budget", "compaction", "manual"), required=True)
    prepare.add_argument("--packet", type=Path, required=True)
    prepare.add_argument("--handoff-minutes", type=int, default=DEFAULT_HANDOFF_MINUTES)
    prepare.set_defaults(handler=prepare_handoff)

    accept = subparsers.add_parser("accept-handoff")
    accept.add_argument("--state", type=Path, required=True)
    accept.add_argument("--to-thread", required=True)
    accept.add_argument("--handoff-token", required=True)
    accept.add_argument("--lease-minutes", type=int, default=DEFAULT_LEASE_MINUTES)
    accept.set_defaults(handler=accept_handoff)

    cancel = subparsers.add_parser("cancel-handoff")
    cancel.add_argument("--state", type=Path, required=True)
    cancel.add_argument("--from-thread", required=True)
    cancel.add_argument("--owner-epoch", required=True)
    cancel.add_argument("--handoff-token", required=True)
    cancel.set_defaults(handler=cancel_handoff)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "lease_minutes", DEFAULT_LEASE_MINUTES) <= 0:
        parser.error("--lease-minutes must be positive")
    if getattr(args, "handoff_minutes", DEFAULT_HANDOFF_MINUTES) <= 0:
        parser.error("--handoff-minutes must be positive")
    try:
        return int(args.handler(args))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
