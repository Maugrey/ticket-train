#!/usr/bin/env python3
"""Merge a ticket-train pull request only after controller and live GitHub gates pass."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import run_registry
import train_controller


PR_NUMBER = re.compile(r"/pull/(\d+)(?:$|[/?#])")
ACCEPTABLE_CHECK_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def run_json(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or "command failed: " + " ".join(command))
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object from GitHub CLI")
    return value


def pull_request_number(url: str) -> str:
    match = PR_NUMBER.search(url)
    if not match:
        raise ValueError(f"cannot extract pull-request number from {url!r}")
    return match.group(1)


def github_gate_issues(
    snapshot: dict[str, Any], *, expected_head: str, expected_base: str, expected_head_branch: str,
    ci_not_configured: bool,
) -> list[str]:
    issues: list[str] = []
    if snapshot.get("state") != "OPEN":
        issues.append("pull request is not open")
    if snapshot.get("isDraft") is not False:
        issues.append("pull request is still draft")
    if snapshot.get("headRefOid") != expected_head:
        issues.append("live GitHub head differs from the controller head")
    if snapshot.get("baseRefName") != expected_base:
        issues.append("live GitHub base branch differs from the controller base")
    if snapshot.get("headRefName") != expected_head_branch:
        issues.append("live GitHub head branch differs from the controller branch")

    checks = snapshot.get("statusCheckRollup")
    if not isinstance(checks, list):
        issues.append("GitHub check state is unavailable")
    elif not checks and not ci_not_configured:
        issues.append("no GitHub checks are present and CI was not proven unconfigured")
    else:
        for check in checks:
            if not isinstance(check, dict):
                issues.append("GitHub returned an invalid check record")
                continue
            name = check.get("name") or check.get("context") or "unnamed check"
            status = check.get("status") or check.get("state")
            conclusion = check.get("conclusion") or check.get("state")
            if status not in {None, "COMPLETED", *ACCEPTABLE_CHECK_CONCLUSIONS}:
                issues.append(f"GitHub check {name!r} is not complete ({status})")
            if conclusion not in ACCEPTABLE_CHECK_CONCLUSIONS:
                issues.append(f"GitHub check {name!r} is not successful ({conclusion})")
    return issues


def apply_controller_event(state_path: Path, event: dict[str, Any]) -> None:
    state = run_registry.load_json(state_path)
    revision = train_controller.procedure(state)["revision"]
    command = [
        sys.executable,
        str(Path(train_controller.__file__).resolve()),
        "apply",
        "--state",
        str(state_path.expanduser().resolve()),
        "--expected-revision",
        str(revision),
        "--event-json",
        json.dumps(event, ensure_ascii=False, separators=(",", ":")),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise ValueError(
            "GitHub merge succeeded but controller reconciliation failed; stop and reconcile: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )


def merge(args: argparse.Namespace) -> int:
    state_path = args.state.expanduser().resolve()
    state = run_registry.load_json(state_path)
    proc = train_controller.procedure(state)

    if args.action == "ticket":
        if not args.ticket_id:
            raise ValueError("--ticket-id is required for a ticket merge")
        item = train_controller.ticket(proc, args.ticket_id)
        pull_request = item.get("pull_request") or {}
        ledger = item.get("finding_ledger") or {}
        expected_base = state["run_identity"]["train_branch"]
        ci_not_configured = ledger.get("ci_status") == "not_configured"
    elif args.action == "final-remediation":
        final = proc.get("finalization", {})
        pull_request = final.get("remediation_pull_request") or {}
        expected_base = state["run_identity"]["train_branch"]
        ci_not_configured = False
    else:
        final = proc.get("finalization", {})
        pull_request = final.get("pull_request") or {}
        snapshot = final.get("feedback_snapshot") or {}
        expected_base = proc["base_branch"]
        ci_not_configured = snapshot.get("ci_status") == "not_configured"

    required = ("url", "head_commit", "head_branch")
    missing = [field for field in required if not pull_request.get(field)]
    if missing:
        raise ValueError("controller pull-request record is incomplete: " + ", ".join(missing))

    permit_issues = train_controller.merge_permit_issues(
        state,
        action=args.action,
        ticket_id=args.ticket_id,
        head_commit=pull_request["head_commit"],
    )
    number = pull_request_number(pull_request["url"])
    live = run_json([
        "gh", "pr", "view", number, "--repo", args.repo, "--json",
        "number,url,state,isDraft,baseRefName,headRefName,headRefOid,statusCheckRollup,mergeCommit,mergedAt",
    ])
    issues = permit_issues + github_gate_issues(
        live,
        expected_head=pull_request["head_commit"],
        expected_base=expected_base,
        expected_head_branch=pull_request["head_branch"],
        ci_not_configured=ci_not_configured,
    )
    if issues:
        sys.stdout.write(json.dumps({"status": "blocked", "issues": issues}, ensure_ascii=False, indent=2) + "\n")
        return 2

    if args.dry_run:
        sys.stdout.write(json.dumps({
            "status": "permitted",
            "action": args.action,
            "pull_request": pull_request["url"],
            "head_commit": pull_request["head_commit"],
        }, ensure_ascii=False, indent=2) + "\n")
        return 0

    method_flag = {"merge": "--merge", "squash": "--squash", "rebase": "--rebase"}[args.method]
    completed = subprocess.run(
        ["gh", "pr", "merge", number, "--repo", args.repo, method_flag],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or completed.stdout.strip() or "GitHub merge failed")

    merged = run_json([
        "gh", "pr", "view", number, "--repo", args.repo, "--json",
        "state,headRefOid,mergeCommit,mergedAt",
    ])
    if merged.get("state") != "MERGED" or not isinstance(merged.get("mergeCommit"), dict):
        raise ValueError("GitHub did not confirm the pull request as merged")
    merge_commit = merged["mergeCommit"].get("oid")
    if not merge_commit:
        raise ValueError("GitHub merge confirmation omitted the merge commit")

    event: dict[str, Any] = {
        "event_id": f"guarded-merge-{uuid.uuid4()}",
        "head_commit": pull_request["head_commit"],
        "merge_commit": merge_commit,
    }
    if args.action == "ticket":
        event.update({
            "type": "TICKET_MERGED",
            "ticket_id": args.ticket_id,
            "train_head": merge_commit,
        })
    elif args.action == "final-remediation":
        event.update({
            "type": "FINAL_REMEDIATION_MERGED",
            "train_head": merge_commit,
            "merged_at": merged.get("mergedAt") or datetime.now(timezone.utc).isoformat(),
        })
    else:
        event.update({
            "type": "FINAL_BASE_MERGED",
            "merged_at": merged.get("mergedAt") or datetime.now(timezone.utc).isoformat(),
        })
    apply_controller_event(state_path, event)
    sys.stdout.write(json.dumps({
        "status": "merged-and-recorded",
        "action": args.action,
        "pull_request": pull_request["url"],
        "head_commit": pull_request["head_commit"],
        "merge_commit": merge_commit,
    }, ensure_ascii=False, indent=2) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--action", choices=("ticket", "final-remediation", "final"), required=True)
    parser.add_argument("--ticket-id")
    parser.add_argument("--method", choices=("merge", "squash", "rebase"), default="merge")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return merge(args)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
