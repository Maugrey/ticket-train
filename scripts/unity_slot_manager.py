#!/usr/bin/env python3
"""Deterministic lifecycle manager for persistent local Unity MCP slots.

The manager owns a bounded pool of long-lived Git worktrees. Each worktree
keeps its Unity Library, UserSettings, local MCP server and firewall identity
between ticket-train phases. A phase must acquire an exclusive lease before it
may use an editor-backed Unity capability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import run_registry


SCHEMA_VERSION = 1
DEFAULT_MAX_EDITORS = 3
MAX_CONFIGURABLE_EDITORS = 16
UNITY_REQUIREMENTS = (
    "none",
    "editor-read",
    "editor-write",
    "playmode-ui",
    "build",
)
ACTIVE_SLOT_STATES = {"STARTING", "READY", "LEASED", "RECOVERING"}
STABLE_CONFIG_FIELDS = (
    "keepConnected",
    "keepServerRunning",
    "generateSkillFiles",
    "transportMethod",
    "logLevel",
    "timeoutMs",
    "tools",
    "prompts",
    "resources",
)
MANAGED_CODEX_CONFIG = ".codex/config.toml"


class SlotError(ValueError):
    """Raised when a slot operation would violate an invariant."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SlotError(message)


def emit(payload: dict[str, Any]) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 0


def run_command(
    argv: Iterable[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: int = 600,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [str(value) for value in argv]
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "no diagnostic output").strip()
        raise SlotError(f"command failed ({completed.returncode}): {command!r}: {detail}")
    return completed


def git(repository: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run_command(("git", "-C", str(repository), *args), check=check)


def canonical_repository(path: Path) -> Path:
    repository = path.expanduser().resolve()
    require(repository.is_dir(), f"Unity repository does not exist: {repository}")
    root = git(repository, "rev-parse", "--show-toplevel").stdout.strip()
    resolved_root = Path(root).resolve()
    require(resolved_root == repository, f"repository must be its Git root: {resolved_root}")
    require((repository / "Assets").is_dir(), "Unity repository is missing Assets/")
    require((repository / "Packages" / "manifest.json").is_file(), "Unity repository is missing Packages/manifest.json")
    require((repository / "ProjectSettings" / "ProjectVersion.txt").is_file(), "Unity repository is missing ProjectSettings/ProjectVersion.txt")
    return repository


def repository_id(repository: Path) -> str:
    common = git(repository, "rev-parse", "--git-common-dir").stdout.strip()
    common_path = (repository / common).resolve() if not Path(common).is_absolute() else Path(common).resolve()
    material = f"{repository.as_posix().lower()}\n{common_path.as_posix().lower()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def default_codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser().resolve() if configured else (Path.home() / ".codex").resolve()


def default_state_path(repository: Path) -> Path:
    return default_codex_home() / "ticket-train" / "unity-slots" / repository_id(repository) / "slots.json"


def default_slot_root(repository: Path) -> Path:
    return repository.parent / f"{repository.name}-unity-slots"


def validate_max_editors(value: int) -> int:
    require(
        isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_CONFIGURABLE_EDITORS,
        f"max editors must be between 1 and {MAX_CONFIGURABLE_EDITORS}",
    )
    return value


def plugin_version(repository: Path) -> str:
    manifest = json.loads((repository / "Packages" / "manifest.json").read_text(encoding="utf-8"))
    version = manifest.get("dependencies", {}).get("com.ivanmurzak.unity.mcp")
    require(isinstance(version, str) and version, "com.ivanmurzak.unity.mcp is not installed")
    require(not any(marker in version for marker in ("git+", "http://", "https://", "file:")), "Unity MCP package must use a pinned registry version")
    return version


def cli_prefix(repository: Path) -> list[str]:
    # On Windows, CreateProcess does not resolve the extensionless npm shim
    # that PowerShell accepts as `npx`; invoke the executable batch shim.
    executable = "npx.cmd" if os.name == "nt" else "npx"
    return [executable, "--yes", f"unity-mcp-cli@{plugin_version(repository)}"]


def stable_config_profile(config_path: Path) -> tuple[dict[str, Any], str] | tuple[None, None]:
    if not config_path.is_file():
        return None, None
    raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    profile = {field: raw.get(field) for field in STABLE_CONFIG_FIELDS if field in raw}
    digest = hashlib.sha256(
        json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return profile, digest


def same_git_repository(repository: Path, slot_path: Path) -> bool:
    if not (slot_path / ".git").exists():
        return False
    source_common = git(repository, "rev-parse", "--git-common-dir").stdout.strip()
    slot_common = git(slot_path, "rev-parse", "--git-common-dir").stdout.strip()
    source_path = (repository / source_common).resolve() if not Path(source_common).is_absolute() else Path(source_common).resolve()
    slot_common_path = (slot_path / slot_common).resolve() if not Path(slot_common).is_absolute() else Path(slot_common).resolve()
    return source_path == slot_common_path


def ensure_safe_slot_root(repository: Path, slot_root: Path) -> Path:
    root = slot_root.expanduser().resolve()
    require(root != repository, "slot root cannot be the repository root")
    require(repository not in root.parents, "slot root cannot be inside the Unity repository")
    require(root.parent != Path(root.anchor), "slot root is too broad")
    return root


def provision_slot(repository: Path, slot_path: Path, *, skip_cli: bool) -> tuple[str | None, str | None]:
    if not skip_cli:
        run_command(
            (
                *cli_prefix(repository),
                "configure",
                str(slot_path),
                "--enable-all-tools",
                "--enable-all-prompts",
                "--enable-all-resources",
            ),
            timeout_seconds=300,
        )
        # Each persistent worktree has a distinct local MCP endpoint.  The
        # repository's tracked Codex config is only a template, so regenerate
        # its worktree-local endpoint after configuring Unity-MCP.  Mark that
        # local override skip-worktree to keep an environment-specific port
        # out of ticket diffs and to preserve a clean slot lease.
        run_command(
            (*cli_prefix(repository), "setup-mcp", "codex", str(slot_path)),
            timeout_seconds=300,
        )
        run_command(
            ("git", "-C", str(slot_path), "update-index", "--skip-worktree", ".codex/config.toml"),
            timeout_seconds=120,
        )
    _, digest = stable_config_profile(slot_path / "UserSettings" / "AI-Game-Developer-Config.json")
    return str(slot_path / "UserSettings" / "AI-Game-Developer-Config.json"), digest


def fresh_state(repository: Path, slot_root: Path, max_editors: int) -> dict[str, Any]:
    version = plugin_version(repository)
    return {
        "schema_version": SCHEMA_VERSION,
        "repository": str(repository),
        "repository_id": repository_id(repository),
        "slot_root": str(slot_root),
        "max_editors": max_editors,
        "mcp_mode": "local",
        "cli_package": f"unity-mcp-cli@{version}",
        "plugin_package": f"com.ivanmurzak.unity.mcp@{version}",
        "slots": [],
        "updated_at": now_iso(),
    }


def load_state(path: Path) -> dict[str, Any]:
    state = run_registry.load_json(path)
    require(state.get("schema_version") == SCHEMA_VERSION, "unsupported Unity slot state version")
    require(state.get("mcp_mode") == "local", "ticket-train Unity slots require local MCP mode")
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    run_registry.save_json(path, state)


def slot_summary(slot: dict[str, Any]) -> dict[str, Any]:
    return {
        key: slot.get(key)
        for key in (
            "slot_id",
            "path",
            "status",
            "branch",
            "head",
            "config_profile_sha256",
            "lease",
            "last_ready_at",
            "last_error",
        )
    }


def init_slots(args: argparse.Namespace) -> int:
    repository = canonical_repository(args.repository)
    max_editors = validate_max_editors(args.max_editors)
    state_path = (args.state or default_state_path(repository)).expanduser().resolve()
    slot_root = ensure_safe_slot_root(repository, args.slot_root or default_slot_root(repository))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    slot_root.mkdir(parents=True, exist_ok=True)

    with run_registry.directory_lock(state_path.parent):
        if state_path.exists():
            state = load_state(state_path)
            require(Path(state["repository"]).resolve() == repository, "slot registry belongs to another repository")
            require(Path(state["slot_root"]).resolve() == slot_root, "slot registry uses another slot root")
        else:
            state = fresh_state(repository, slot_root, max_editors)

        existing_by_id = {slot["slot_id"]: slot for slot in state.get("slots", [])}
        for index in range(1, max_editors + 1):
            slot_id = f"unity-slot-{index}"
            slot_path = slot_root / slot_id
            slot = existing_by_id.get(slot_id)
            if not slot_path.exists():
                git(repository, "worktree", "add", "--detach", str(slot_path), args.base_ref)
            require(same_git_repository(repository, slot_path), f"slot path is not a worktree of the repository: {slot_path}")
            config_reference, config_digest = provision_slot(repository, slot_path, skip_cli=args.skip_cli)
            head = git(slot_path, "rev-parse", "HEAD").stdout.strip()
            branch = git(slot_path, "branch", "--show-current").stdout.strip() or None
            if slot is None:
                slot = {"slot_id": slot_id, "lease": None, "recovery_attempts": 0}
                state.setdefault("slots", []).append(slot)
            slot.update(
                {
                    "path": str(slot_path),
                    "status": "IDLE" if not slot.get("lease") else slot.get("status", "LEASED"),
                    "branch": branch,
                    "head": head,
                    "config_reference": config_reference,
                    "config_profile_sha256": config_digest,
                    "last_error": None,
                }
            )

        for slot in state.get("slots", []):
            index = int(str(slot["slot_id"]).rsplit("-", 1)[-1])
            if index <= max_editors:
                continue
            require(not slot.get("lease"), f"cannot lower editor limit while {slot['slot_id']} is leased")
            if not args.skip_cli:
                run_command((*cli_prefix(repository), "close", slot["path"]), timeout_seconds=120, check=False)
            slot["status"] = "DISABLED"

        state["max_editors"] = max_editors
        state["cli_package"] = f"unity-mcp-cli@{plugin_version(repository)}"
        state["plugin_package"] = f"com.ivanmurzak.unity.mcp@{plugin_version(repository)}"
        save_state(state_path, state)

    return emit(
        {
            "status": "initialized",
            "state": str(state_path),
            "repository": str(repository),
            "slot_root": str(slot_root),
            "max_editors": max_editors,
            "mcp_mode": "local",
            "slots": [slot_summary(slot) for slot in state["slots"]],
        }
    )


def ensure_clean(slot_path: Path) -> None:
    dirty = git(slot_path, "status", "--porcelain").stdout.strip()
    require(not dirty, f"Unity slot has uncommitted changes: {slot_path}")


def switch_preserving_managed_config(
    slot_path: Path, *, branch: str | None, expected_head: str
) -> None:
    config_path = slot_path / MANAGED_CODEX_CONFIG
    tracked = git(slot_path, "ls-files", "--error-unmatch", MANAGED_CODEX_CONFIG, check=False)
    if tracked.returncode != 0 or not config_path.is_file():
        if branch:
            git(slot_path, "switch", branch)
        else:
            git(slot_path, "switch", "--detach", expected_head)
        return

    target_has_config = git(
        slot_path,
        "cat-file",
        "-e",
        f"{expected_head}:{MANAGED_CODEX_CONFIG}",
        check=False,
    )
    require(
        target_has_config.returncode == 0,
        f"target revision does not track the managed Unity MCP config: {expected_head}",
    )
    preserved = config_path.read_bytes()
    git(slot_path, "update-index", "--no-skip-worktree", MANAGED_CODEX_CONFIG)
    git(slot_path, "restore", "--source=HEAD", "--staged", "--worktree", "--", MANAGED_CODEX_CONFIG)
    try:
        if branch:
            git(slot_path, "switch", branch)
        else:
            git(slot_path, "switch", "--detach", expected_head)
    finally:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_bytes(preserved)
        git(slot_path, "update-index", "--skip-worktree", MANAGED_CODEX_CONFIG)


def position_slot(slot_path: Path, *, branch: str | None, expected_head: str) -> tuple[str | None, str]:
    ensure_clean(slot_path)
    if branch:
        git(slot_path, "show-ref", "--verify", f"refs/heads/{branch}")
    switch_preserving_managed_config(slot_path, branch=branch, expected_head=expected_head)
    head = git(slot_path, "rev-parse", "HEAD").stdout.strip()
    require(head == expected_head, f"Unity slot head mismatch: expected {expected_head}, got {head}")
    actual_branch = git(slot_path, "branch", "--show-current").stdout.strip() or None
    return actual_branch, head


def ready_slot(repository: Path, slot: dict[str, Any], *, skip_cli: bool, attempts: int) -> dict[str, Any]:
    if skip_cli:
        return {"status": "skipped", "reason": "--skip-cli"}
    path = slot["path"]
    prefix = cli_prefix(repository)
    diagnostics: list[dict[str, Any]] = []
    for attempt in range(1, attempts + 1):
        try:
            # A configured worktree only tells the plugin where its local
            # endpoint lives.  The CLI must also start that endpoint for a
            # freshly opened editor; otherwise Unity keeps retrying a dead
            # /hub/mcp-server connection indefinitely.
            run_command(
                (*prefix, "open", path, "--start-server", "true", "--keep-connected"),
                timeout_seconds=180,
            )
            wait = run_command((*prefix, "wait-for-ready", path), timeout_seconds=900)
            status = run_command((*prefix, "status", path), timeout_seconds=120)
            return {
                "status": "ready",
                "attempt": attempt,
                "wait_output": wait.stdout.strip()[-4000:],
                "status_output": status.stdout.strip()[-4000:],
            }
        except (SlotError, subprocess.TimeoutExpired) as error:
            diagnostics.append({"attempt": attempt, "error": str(error)})
            run_command((*prefix, "close", path), timeout_seconds=120, check=False)
    raise SlotError(f"Unity MCP did not become ready after {attempts} attempts: {diagnostics}")


def acquire_slot(args: argparse.Namespace) -> int:
    require(args.requirement in UNITY_REQUIREMENTS and args.requirement != "none", "acquire requires an editor-backed Unity requirement")
    state_path = args.state.expanduser().resolve()
    with run_registry.directory_lock(state_path.parent):
        state = load_state(state_path)
        repository = canonical_repository(Path(state["repository"]))
        owned_slot = next(
            (
                slot for slot in state["slots"]
                if isinstance(slot.get("lease"), dict)
                and slot["lease"].get("phase_key") == args.phase_key
            ),
            None,
        )
        if isinstance(owned_slot, dict) and owned_slot.get("status") == "LEASED":
            return emit({"status": "already-acquired", "state": str(state_path), "slot": slot_summary(owned_slot)})
        if isinstance(owned_slot, dict):
            candidates = [owned_slot]
        else:
            candidates = [
                slot
                for slot in state["slots"][: int(state["max_editors"])]
                if slot.get("status") in {"IDLE", "READY"} and not slot.get("lease")
            ]
            require(candidates, "no Unity editor slot is currently available")
            if args.branch:
                candidates.sort(key=lambda candidate: candidate.get("branch") != args.branch)
        preparation_failures: list[dict[str, str]] = []
        prepared: tuple[dict[str, Any], str | None, str, str | None, str | None] | None = None
        for candidate in candidates:
            slot_path = Path(candidate["path"]).resolve()
            try:
                branch, head = position_slot(
                    slot_path, branch=args.branch, expected_head=args.expected_head
                )
                config_reference, config_digest = provision_slot(
                    repository, slot_path, skip_cli=args.skip_cli
                )
                prepared = (candidate, branch, head, config_reference, config_digest)
                break
            except (SlotError, subprocess.TimeoutExpired) as error:
                candidate["status"] = "BLOCKED_HUMAN"
                candidate["last_error"] = str(error)
                candidate["recovery_attempts"] = 0
                preparation_failures.append({
                    "slot_id": str(candidate["slot_id"]),
                    "error": str(error),
                })
                save_state(state_path, state)
        require(
            prepared is not None,
            f"no Unity slot could prepare the requested revision: {preparation_failures}",
        )
        slot, branch, head, config_reference, config_digest = prepared
        lease_id = (
            slot["lease"]["lease_id"]
            if isinstance(slot.get("lease"), dict)
            else uuid.uuid4().hex
        )
        slot["status"] = "STARTING"
        slot["branch"] = branch
        slot["head"] = head
        slot["config_reference"] = config_reference
        slot["config_profile_sha256"] = config_digest
        slot["last_error"] = None
        slot["preparation_failures_before_acquire"] = preparation_failures
        slot["lease"] = {
            "lease_id": lease_id,
            "phase_key": args.phase_key,
            "requirement": args.requirement,
            "acquired_at": now_iso(),
        }
        save_state(state_path, state)
        try:
            evidence = ready_slot(
                repository,
                slot,
                skip_cli=args.skip_cli,
                attempts=args.recovery_attempts + 1,
            )
        except (SlotError, subprocess.TimeoutExpired) as error:
            slot["status"] = "BLOCKED_HUMAN"
            slot["last_error"] = str(error)
            slot["recovery_attempts"] = args.recovery_attempts
            save_state(state_path, state)
            raise
        slot["status"] = "LEASED"
        slot["last_ready_at"] = now_iso()
        slot["readiness_evidence"] = evidence
        slot["recovery_attempts"] = evidence.get("attempt", 1) - 1 if isinstance(evidence, dict) else 0
        save_state(state_path, state)

    return emit(
        {
            "status": "acquired",
            "state": str(state_path),
            "slot": slot_summary(slot),
            "readiness_evidence": evidence,
        }
    )


def release_slot(args: argparse.Namespace) -> int:
    state_path = args.state.expanduser().resolve()
    with run_registry.directory_lock(state_path.parent):
        state = load_state(state_path)
        repository = canonical_repository(Path(state["repository"]))
        slot = next(
            (
                item
                for item in state["slots"]
                if isinstance(item.get("lease"), dict)
                and item["lease"].get("phase_key") == args.phase_key
            ),
            None,
        )
        if slot is None:
            prior = next(
                (
                    item for item in state["slots"]
                    if isinstance(item.get("last_released_lease"), dict)
                    and item["last_released_lease"].get("phase_key") == args.phase_key
                    and (
                        not getattr(args, "lease_id", None)
                        or item["last_released_lease"].get("lease_id") == args.lease_id
                    )
                ),
                None,
            )
            require(isinstance(prior, dict), f"phase does not own a Unity slot: {args.phase_key}")
            return emit(
                {
                    "status": "already-released",
                    "state": str(state_path),
                    "lease": prior["last_released_lease"],
                    "slot": slot_summary(prior),
                }
            )
        if getattr(args, "lease_id", None):
            require(slot["lease"].get("lease_id") == args.lease_id, "Unity slot lease ID mismatch")
        ensure_clean(Path(slot["path"]))
        if args.close_editor and not args.skip_cli:
            run_command((*cli_prefix(repository), "close", slot["path"]), timeout_seconds=120)
            slot["status"] = "IDLE"
        else:
            slot["status"] = "READY"
        released_lease = slot["lease"]
        slot["last_released_lease"] = dict(released_lease)
        slot["lease"] = None
        slot["released_at"] = now_iso()
        save_state(state_path, state)
    return emit(
        {
            "status": "released",
            "state": str(state_path),
            "lease": released_lease,
            "slot": slot_summary(slot),
        }
    )


def status_slots(args: argparse.Namespace) -> int:
    state_path = args.state.expanduser().resolve()
    state = load_state(state_path)
    repository = canonical_repository(Path(state["repository"]))
    slots: list[dict[str, Any]] = []
    for slot in state["slots"]:
        summary = slot_summary(slot)
        slot_path = Path(slot["path"])
        if slot_path.exists() and (slot_path / ".git").exists():
            summary["git_clean"] = not bool(git(slot_path, "status", "--porcelain").stdout.strip())
            summary["observed_head"] = git(slot_path, "rev-parse", "HEAD").stdout.strip()
        if args.live and slot.get("status") != "DISABLED":
            observed = run_command(
                (*cli_prefix(repository), "status", slot["path"]),
                timeout_seconds=120,
                check=False,
            )
            summary["mcp_status_exit_code"] = observed.returncode
            summary["mcp_status_output"] = (observed.stdout or observed.stderr).strip()[-4000:]
        slots.append(summary)
    return emit(
        {
            "status": "observed",
            "state": str(state_path),
            "max_editors": state["max_editors"],
            "active_managed_slots": sum(1 for slot in state["slots"] if slot.get("status") in ACTIVE_SLOT_STATES),
            "slots": slots,
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser("init")
    initialize.add_argument("--repository", type=Path, required=True)
    initialize.add_argument("--state", type=Path)
    initialize.add_argument("--slot-root", type=Path)
    initialize.add_argument("--base-ref", default="HEAD")
    initialize.add_argument("--max-editors", type=int, default=DEFAULT_MAX_EDITORS)
    initialize.add_argument("--skip-cli", action="store_true", help=argparse.SUPPRESS)
    initialize.set_defaults(handler=init_slots)

    acquire = commands.add_parser("acquire")
    acquire.add_argument("--state", type=Path, required=True)
    acquire.add_argument("--phase-key", required=True)
    acquire.add_argument("--requirement", choices=UNITY_REQUIREMENTS, required=True)
    acquire.add_argument("--branch")
    acquire.add_argument("--expected-head", required=True)
    acquire.add_argument("--recovery-attempts", type=int, choices=range(0, 4), default=2)
    acquire.add_argument("--skip-cli", action="store_true", help=argparse.SUPPRESS)
    acquire.set_defaults(handler=acquire_slot)

    release = commands.add_parser("release")
    release.add_argument("--state", type=Path, required=True)
    release.add_argument("--phase-key", required=True)
    release.add_argument("--lease-id")
    release.add_argument("--close-editor", action="store_true")
    release.add_argument("--skip-cli", action="store_true", help=argparse.SUPPRESS)
    release.set_defaults(handler=release_slot)

    status = commands.add_parser("status")
    status.add_argument("--state", type=Path, required=True)
    status.add_argument("--live", action="store_true")
    status.set_defaults(handler=status_slots)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.handler(args))
    except (OSError, json.JSONDecodeError, SlotError, subprocess.TimeoutExpired) as error:
        sys.stderr.write(json.dumps({"status": "blocked", "error": str(error)}, ensure_ascii=False) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
