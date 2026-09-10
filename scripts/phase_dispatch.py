#!/usr/bin/env python3
"""Execute and reconcile native phase effects under the canonical controller."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import ast
import subprocess
import time
from pathlib import Path

import continuation_adapter
import run_registry
import thread_runtime
import train_controller as controller


def load(path):
    return run_registry.load_json(Path(path))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# Phase output is technical data. The adapter supplies identity, routing,
# completion receipts and deterministic successor transitions.
RESULT_EVENTS = {
    "triage": {"TICKET_TRIAGED"}, "analysis": {"ANALYSIS_RECORDED"},
    "analysis_route_validation": {"ANALYSIS_ROUTE_VALIDATION_RECORDED"},
    "analysis_reconciliation": {"ANALYSIS_ROUTE_VALIDATION_RECONCILED"},
    "plan_contract_validation": {"PLAN_CONTRACT_VALIDATION_RECORDED"},
    "implementation": set(), "acceptance_tests": set(), "remediation": set(),
    "review": {"REVIEW_RECORDED"}, "final_review": {"FINAL_REVIEW_RECORDED"},
    "final_remediation": set(),
}
DECISION_EVENTS = {
    "BLOCKED_OR_INCONSISTENT_STATE": {"ANALYSIS_ROUTE_VALIDATION_RECONCILED", "PLAN_CONTRACT_AMENDMENT_RECORDED"},
    "CONSOLIDATE_DEPENDENCIES": {"DEPENDENCIES_CONSOLIDATED"},
    "CLASSIFY_VERIFICATION_FAILURE": {"VERIFICATION_FAILURE_CLASSIFIED", "BLOCKED_TICKET_CONTINUATION_ISOLATED"},
    "CLASSIFY_FINAL_VERIFICATION_FAILURE": {"FINAL_VERIFICATION_FAILURE_CLASSIFIED"},
    "RECONCILE_CODEX_CI_COPILOT_FINDINGS": {"TICKET_FINDINGS_RECONCILED"},
    "RECONCILE_FINAL_CODEX_CI_COPILOT_FINDINGS": {"FINAL_FINDINGS_RECONCILED"},
    "ROOT_CAUSE_CHECKPOINT_REQUIRED": {"REMEDIATION_LIMIT_EXCEPTION_GRANTED"},
    "RESOLVE_COST_ANOMALY_CHECKPOINT": {"COST_ANOMALY_RESOLVED"},
    # These preparations contain real judgment about findings, changed scope,
    # complexity or applicable review floors. They use bounded fresh context.
    "DISPATCH_FRESH_BATCHED_REMEDIATION": {"REMEDIATION_DISPATCHED"},
    "RECORD_FINAL_REMEDIATION_DISPATCH_INTENT": {"FINAL_REMEDIATION_DISPATCHED"},
    "RECORD_FINAL_REVIEW_DISPATCH_INTENT": {"FINAL_REVIEW_DISPATCHED"},
    "DISPATCH_FOCUSED_FOLLOWUP_REVIEW": {"REVIEW_DISPATCHED"},
}


def git(repository, *args):
    result = subprocess.run(["git", "-C", str(repository), *args], capture_output=True, text=True, encoding="utf-8", timeout=120)
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return result.stdout.strip()


def private_context(driver, key, payload, base, head):
    import context_packet
    directory = driver.directory / "contexts" / digest(key)[:24]
    directory.mkdir(parents=True, exist_ok=True)
    source, target = directory / "input.json", directory / "context.json"
    run_registry.save_json(source, payload)
    return context_packet.build_packet(source, target, driver.profile["revision"], base, head)


def compact_inputs(driver, action):
    state = driver.state()
    proc = state["procedure"]
    ticket_id = action.get("ticket_id")
    # Full per-ticket artifacts remain on disk, outside the model's startup.
    payload = {"ticket": proc["tickets"].get(ticket_id) if ticket_id else proc["tickets"],
               "finalization": proc["finalization"] if not ticket_id else None,
               "source": driver.profile.get("tickets", {}).get(ticket_id) if ticket_id else {k: v for k, v in driver.profile.get("tickets", {}).items() if k in proc["tickets"]},
               "decisions": proc.get("decisions", {}), "action": action}
    path = driver.directory / "inputs" / (digest(payload) + ".json")
    run_registry.save_json(path, payload)
    return {"action": action, "inputs_reference": str(path), "inputs_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "repository": driver.profile["repository"], "source_revision": driver.profile["revision"]}


def event_contracts(events):
    """Expose only the relevant authoritative validators, without duplicating schemas."""
    source = Path(controller.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "handle_event")
    sections = []
    for node in function.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            names = {x.value for x in ast.walk(node.test) if isinstance(x, ast.Constant) and isinstance(x.value, str)}
            if names.intersection(events):
                sections.append(ast.get_source_segment(source, node))
    return "\n\n".join(sections)


def phase_prompt(driver, value, allowed):
    packet = value["context_packet"]
    directory = driver.directory / "contracts"
    directory.mkdir(parents=True, exist_ok=True)
    contract = directory / (digest(sorted(allowed)) + ".txt")
    if not contract.exists():
        contract.write_text(event_contracts(allowed), encoding="utf-8")
    role = value["kind"]
    refs = {
        "triage": "criticality.md", "analysis": "analysis-policy.md",
        "analysis_route_validation": "analysis-policy.md", "analysis_reconciliation": "analysis-policy.md",
        "plan_contract_validation": "analysis-policy.md", "implementation": "workflow.md",
        "acceptance_tests": "verification-policy.md", "review": "review-policy.md",
        "final_review": "review-policy.md", "remediation": "review-policy.md", "final_remediation": "review-policy.md",
    }
    reference = Path(__file__).resolve().parent.parent / "references" / refs.get(role, "workflow.md")
    return (
        f"Ticket Train {role}. Phase {value['phase_key']}. Read the exact compact context {packet['reference']} "
        f"(SHA256 {packet['sha256']}) and the relevant role instructions {reference}. "
        "Read the repository's AGENTS.md before work. Execute only the authorized scope. "
        "Triage, analysis, contract validation and reviews are read-only. Do not edit the train manifest, "
        "launch other tasks, approve decisions, or message another task. Persist reports/tests as required. "
        "Implementation, acceptance and remediation must commit their own changes. "
        f"Return a JSON object with envelope and events. The allowed event types are {sorted(allowed)}. "
        "Each event must identify its event type with the exact JSON field `type`; do not use `event_type`. "
        f"Their authoritative validation contract is {contract}; read only that contract. "
        "The envelope must contain phase_status (completed, failed, blocked or needs_input), result_summary, "
        "artifacts (including commit for code changes), tests_and_checks, residual_risks, "
        "requested_or_recommended_next_action and files_modified. Supply real evidence, never assumed success. "
        "For needs_input include input_request with gate_id, revision, question, reason, blocked_scope, "
        "continuing_scope and accepted_replies. Events contain technical findings; the runner supplies "
        "phase identity, event identity, model routing and reasoning authorization. Never claim or override "
        "those controller-owned fields. Never resolve an unspecified product behavior yourself. "
        "For acceptance, include artifacts.verification_plan_reference and verification_evidence_reference, "
        "prepared for verification_runner.py and the VERIFICATION_RECORDED event; assertions need real coverage. "
        "The native collector reads your final result automatically; no completion callback is needed."
    )


def worktree(driver, key, head, branch=None):
    repository = driver.profile["repository"]
    if branch:
        for block in git(repository, "worktree", "list", "--porcelain").split("\n\n"):
            lines = block.splitlines()
            if "branch refs/heads/" + branch in lines:
                existing = next(x.removeprefix("worktree ") for x in lines if x.startswith("worktree "))
                controller.require(Path(existing).resolve() != Path(repository).resolve(), "A worker branch is checked out in the user's project; isolate it first")
                return Path(existing)
    path = driver.directory / "worktrees" / digest(key)[:16]
    if path.exists():
        observed = git(path, "rev-parse", "HEAD")
        require_branch = git(path, "branch", "--show-current")
        controller.require(branch is None or require_branch == branch, "Existing worker worktree has another branch")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    if branch:
        exists = subprocess.run(["git", "-C", repository, "show-ref", "--verify", "--quiet", "refs/heads/" + branch]).returncode == 0
        args = ["worktree", "add", str(path), branch] if exists else ["worktree", "add", "-b", branch, str(path), head]
    else:
        args = ["worktree", "add", "--detach", str(path), head]
    git(repository, *args)
    return path


def launch(driver, action):
    state = driver.state()
    value = state["procedure"]["phases"][action["phase_key"]]
    existing = driver.effects().read(value["phase_key"])
    if existing:
        spec = existing["spec"]
    else:
        allowed = DECISION_EVENTS[value["decision_action"]] if value["kind"] == "technical_decision" else RESULT_EVENTS[value["kind"]]
        unity = controller.unity_slot_payload(state["procedure"], value["phase_key"])
        directory = Path(unity["path"]) if unity else worktree(driver, value["phase_key"], value.get("resume_head") or value["context_packet"]["exact_head"], value.get("branch"))
        prompt = phase_prompt(driver, value, allowed)
        if unity:
            prompt += " Use only this acquired local Unity slot: " + json.dumps(unity) + ". Never open another editor or change its lease."
        spec = {"key": value["phase_key"], "cwd": str(directory), "model": value["requested_model"],
                "effort": value["requested_reasoning_effort"], "prompt": prompt,
                "title": state["run_id"] + " — " + value["phase_key"]}
    # Claim the native transport only for a new, run-owned phase. It must never
    # resume an unrelated task already running in the desktop process.
    driver.effects().prepare(spec)
    if value["launch_state"] == "INTENT_RECORDED":
        driver.apply({"type": "PHASE_LAUNCH_OBSERVED", "phase_key": value["phase_key"], "launch_state": "LAUNCH_UNKNOWN"})
    job = driver.effects().submit(spec)
    if value["launch_state"] != "RUNNING":
        driver.apply({"type": "PHASE_LAUNCH_OBSERVED", "phase_key": value["phase_key"], "launch_state": "RUNNING",
                      "thread_id": job["thread_id"], "host_id": "local", "execution_visibility": "user-visible",
                      "visibility_verified": True, "visibility_evidence_reference": driver.profile["native_visibility_evidence"],
                      "reconciled": True})
    return True


def parse_result(job):
    raw = load(job["result_reference"])["text"].strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    result = json.loads(raw)
    controller.require(isinstance(result, dict), "Worker result must be a JSON object")
    return result


def repairable_result_error(error):
    if isinstance(error, (json.JSONDecodeError, KeyError)):
        return True
    message = str(error)
    return ("is missing:" in message or " is required by the " in message
            or message.startswith("invalid "))


def repair_prompt(error, original):
    guidance = (
        " Re-read the authoritative contract and use its exact JSON field names, "
        "enum spelling, casing and object shapes; do not paraphrase enum values."
    )
    return ("Your existing result could not be registered: " + str(error) + "." + guidance
            + " Return the required JSON envelope and technical events from your existing findings. "
            "Do not repeat technical work or alter scope. " + original)


def decorate_events(driver, value, result):
    allowed = DECISION_EVENTS[value["decision_action"]] if value["kind"] == "technical_decision" else RESULT_EVENTS[value["kind"]]
    events = result.get("events", [])
    controller.require(isinstance(events, list), "Worker events must be a list")
    controller.require(not allowed or events, "Worker result is missing: technical events")
    for event in events:
        controller.require(isinstance(event, dict), "Worker event must be a JSON object")
        legacy_type = event.pop("event_type", None)
        if legacy_type is not None:
            controller.require(event.get("type") in {None, legacy_type}, "Worker event type fields disagree")
            event["type"] = legacy_type
        # A technical worker reports findings. It cannot forge controller
        # identity or authorize its own route. Accepting the legacy event_type
        # spelling keeps already-completed work collectible without another
        # model turn; all persisted events use the canonical type field.
        event.pop("event_id", None)
        event.pop("reasoning_authorized", None)
        event.pop("reasoning_authorization_id", None)
        controller.require(event.get("type") in allowed, "Worker attempted an event outside its technical role")
        if value["kind"] != "technical_decision":
            event["phase_key"] = value["phase_key"]
        if value.get("ticket_id"):
            event["ticket_id"] = value["ticket_id"]
        if value["kind"] != "technical_decision":
            event.update(model=value["requested_model"], reasoning_effort=value["requested_reasoning_effort"])
        if event["type"] == "TICKET_TRIAGED":
            model, effort, conformance = controller.routed_setting(controller.ANALYSIS_MATRIX, event["criticality"], event["complexity"], False)
            event.update(analysis_model=model, analysis_reasoning_effort=effort, analysis_routing_conformance=conformance,
                         triage_model=value["requested_model"], triage_reasoning_effort=value["requested_reasoning_effort"])
        elif event["type"] == "ANALYSIS_RECORDED":
            event.update(report_thread_id=value["thread_id"], analysis_base_commit=value["base"], profile_revision=driver.profile["revision"])
    return events


def collect(driver):
    if time.monotonic() - driver.last_observation < 15:
        return False
    driver.last_observation = time.monotonic()
    progress = False
    for value in driver.state()["procedure"]["phases"].values():
        if value.get("launch_state") != "RUNNING":
            continue
        try:
            progress = collect_one(driver, value) or progress
        except (ValueError, KeyError, OSError) as error:
            job = driver.effects().read(value["phase_key"])
            format_error = repairable_result_error(error)
            if (job and job["status"] == "completed" and format_error
                    and int(job.get("result_repair_count", 0)) < 2):
                import uuid
                job.update(result_repair_count=int(job.get("result_repair_count", 0)) + 1,
                           attempt=job["attempt"] + 1, client_message_id=str(uuid.uuid4()))
                driver.effects().start_turn(job, repair_prompt(error, job["spec"]["prompt"]))
                progress = True
            else:
                driver.notify("result-invalid", {"phase_key": value["phase_key"], "error": str(error)})
    return progress


def collect_one(driver, value):
    if value.get("launch_state") != "RUNNING":
        return False
    job = driver.effects().read(value["phase_key"])
    if not job:
        driver.notify("external-task", {"phase_key": value["phase_key"], "thread_id": value.get("thread_id")})
        return False
    job = driver.effects().observe(job)
    observation = driver.effects().directory(job["key"]) / "observation.json"
    if observation.exists() and time.time() - observation.stat().st_mtime < thread_runtime.MAX_AGE_SECONDS:
        driver.apply({"type": "RUNTIME_OBSERVED", "owner_thread_id": driver.owner,
                      "snapshot_reference": str(observation), "snapshot_sha256": hashlib.sha256(observation.read_bytes()).hexdigest()})
    if job["status"] in {"needs_input", "blocked"}:
        driver.notify(job["status"], job)
        return False
    if job["status"] != "completed":
        return False
    descriptor = value["context_packet"]
    data = Path(descriptor["reference"]).read_bytes()
    controller.require(hashlib.sha256(data).hexdigest() == descriptor["sha256"], "Dispatched context changed before collection")
    inputs = json.loads(data)["payload"]
    if inputs.get("inputs_reference"):
        controller.require(hashlib.sha256(Path(inputs["inputs_reference"]).read_bytes()).hexdigest() == inputs["inputs_sha256"], "Worker inputs changed before collection")
    result = parse_result(job)
    controller.require(job.get("actual_model") == value["requested_model"], "Native model receipt differs from the routed phase")
    envelope = result["envelope"]
    envelope.update(phase_key=value["phase_key"], actual_model=job["actual_model"],
                    actual_reasoning_effort=value["requested_reasoning_effort"], usage={"measurement": "unavailable"})
    import token_usage
    paths = token_usage.candidate_session_files(token_usage.resolve_codex_home(None), job["thread_id"])
    if paths:
        measured = token_usage.measure_session(paths[0], job["thread_id"], job["created_at"], job.get("completed_at"))
        if measured:
            envelope["usage"] = {"measurement": measured["measurement_status"], **measured["usage"],
                                 "context_compactions": measured["context_compactions"]}
    events = [{"type": "PHASE_COMPLETED" if envelope["phase_status"] == "completed" else "PHASE_TERMINATED",
               "phase_key": value["phase_key"], "envelope": envelope}]
    if envelope["phase_status"] == "completed":
        events += decorate_events(driver, value, result)
    driver.transaction(value["phase_key"] + ":" + job["turn_id"], events)
    return True



def dispatch_intent(driver, action):
    proc = driver.state()["procedure"]
    ticket_id = action.get("ticket_id")
    item = proc["tickets"].get(ticket_id, {})
    names = {"RECORD_BATCH_TRIAGE_DISPATCH_INTENT": "triage", "RECORD_ANALYSIS_DISPATCH_INTENT": "analysis",
             "RECORD_ANALYSIS_ROUTE_VALIDATION_DISPATCH_INTENT": "analysis_route_validation",
             "RECORD_PLAN_CONTRACT_VALIDATION_DISPATCH_INTENT": "plan_contract_validation"}
    kind = names[action["action"]]
    key = f"{ticket_id or 'run'}:{kind}:{len(proc['phases']) + 1}"
    head = proc.get("train_head") or git(driver.profile["repository"], "rev-parse", proc["base_branch"])
    event = {"type": "PHASE_DISPATCHED", "kind": kind, "phase_key": key, "ticket_id": ticket_id,
             "base_commit": head, "unity_requirement": action.get("unity_requirement", "none")}
    if kind == "triage":
        event["triage_profile"] = "standard"
        route = controller.triage_setting(event)
    elif kind == "plan_contract_validation":
        event["contract_validation_profile"] = "standard"
        route = controller.plan_contract_setting(event)
    else:
        classification = item["triage"] if kind == "analysis" else item["analysis_route_validation_required"]
        route = controller.routed_setting(controller.ANALYSIS_MATRIX, classification["criticality"], classification["complexity"], False)
    event.update(model=route[0], reasoning_effort=route[1], routing_conformance=route[2],
                 context_packet=private_context(driver, key, compact_inputs(driver, action), head, head))
    driver.apply(event)
    return True


def execution_pair(driver, action):
    state = driver.state()
    ticket_id = action["ticket_id"]
    item = state["procedure"]["tickets"][ticket_id]
    analysis = item["analysis"]
    base = state["procedure"].get("train_head") or analysis["analysis_base_commit"]
    source = driver.profile.get("tickets", {}).get(ticket_id, {})
    if source.get("mode") == "validation-only":
        driver.apply({"type": "VALIDATION_ONLY_DISPATCHED", "ticket_id": ticket_id, "base_commit": base,
                      "source_reference": source["source_reference"], "plan_reference": analysis["verification_plan_reference"],
                      "evidence_reference": analysis["verification_evidence_reference"],
                      "scope_assessment_revision": analysis["scope_assessment"]["assessment_revision"],
                      "scope_conformance": "within-authorized-scope"})
        return True
    prefix = f"{ticket_id}:execution:{len(state['procedure']['phases']) + 1}"
    event = {"type": "EXECUTION_PAIR_DISPATCHED", "ticket_id": ticket_id, "base_commit": base,
             "verification_complexity": analysis["verification_complexity"],
             "scope_assessment_revision": analysis["scope_assessment"]["assessment_revision"],
             "scope_conformance": "within-authorized-scope"}
    for role, matrix, complexity in (("implementation", controller.IMPLEMENTATION_MATRIX, analysis["residual_implementation_complexity"]),
                                     ("acceptance", controller.ACCEPTANCE_MATRIX, analysis["verification_complexity"])):
        key = prefix + ":" + role
        route = controller.routed_setting(matrix, analysis["criticality"], complexity, False)
        event.update({role + "_phase_key": key, role + "_branch": "codex/train-" + digest(key)[:16],
                      role + "_model": route[0], role + "_reasoning_effort": route[1], role + "_routing_conformance": route[2],
                      role + "_unity_requirement": analysis.get(role + "_unity_requirement", "none"),
                      role + "_context_packet": private_context(driver, key, compact_inputs(driver, action), base, base)})
    event["verification_unity_requirement"] = analysis.get("verification_unity_requirement", "none")
    driver.apply(event)
    return True


def integrate(driver, action):
    proc = driver.state()["procedure"]
    execution = proc["tickets"][action["ticket_id"]]["execution"]
    impl = proc["phases"][execution["implementation_phase_key"]]
    tests = proc["phases"][execution["acceptance_phase_key"]]
    impl_commit = impl["completion_envelope"]["artifacts"]["commit"]
    test_commit = tests["completion_envelope"]["artifacts"]["commit"]
    directory = worktree(driver, impl["phase_key"], impl_commit, execution["implementation_branch"])
    # A completed merge is detected through ancestry even if the controller
    # receipt was lost. A conflict stays in this isolated worktree for judgment.
    merged = subprocess.run(["git", "-C", str(directory), "merge-base", "--is-ancestor", test_commit, "HEAD"]).returncode == 0
    if not merged:
        controller.require(not git(directory, "status", "--porcelain"), "Integration worktree is dirty; reconcile its conflict before retrying")
        git(directory, "merge", "--no-edit", "--no-ff", test_commit)
    head = git(directory, "rev-parse", "HEAD")
    evidence = driver.directory / "integrations" / (digest(action["ticket_id"] + head) + ".json")
    run_registry.save_json(evidence, {"implementation_commit": impl_commit, "acceptance_commit": test_commit, "combined_head": head})
    driver.apply({"type": "EXECUTION_PAIR_INTEGRATED", "ticket_id": action["ticket_id"],
                  "implementation_branch": execution["implementation_branch"], "implementation_commit": impl_commit,
                  "acceptance_commit": test_commit, "combined_head": head, "integration_evidence_reference": str(evidence)})
    return True


def verify(driver, action):
    import verification_adapter
    proc = driver.state()["procedure"]
    ticket_id = action.get("ticket_id")
    if action["action"] == "RUN_VALIDATION_ONLY_VERIFICATION":
        operation = proc["tickets"][ticket_id]["validation_operation"]
        config = {"verification_plan_reference": operation["plan_reference"], "verification_evidence_reference": operation["evidence_reference"]}
        head = operation["base_commit"]
        directory = worktree(driver, ticket_id + ":validation:" + head, head)
    elif ticket_id:
        item = proc["tickets"][ticket_id]
        acceptance = proc["phases"][item["execution"]["acceptance_phase_key"]]["completion_envelope"]["artifacts"]
        config = acceptance
        head = item["execution"].get("integrated_head")
        remediations = [p for p in proc["phases"].values() if p.get("ticket_id") == ticket_id and p["kind"] == "remediation" and p["launch_state"] == "COMPLETED"]
        if remediations:
            head = remediations[-1]["completion_envelope"]["artifacts"]["commit"]
        directory = worktree(driver, item["execution"]["implementation_phase_key"], head, item["execution"]["implementation_branch"])
    else:
        config = driver.profile.get("final_verification", {})
        head = proc["finalization"]["pull_request"]["head_commit"]
        directory = worktree(driver, "final-verification:" + head, head)
    if not all(config.get(k) for k in ("verification_plan_reference", "verification_evidence_reference")):
        return driver.notify("verification-contract-required", action)
    unity = controller.unity_slot_payload(proc, f"ticket:{ticket_id}:verification" if ticket_id else "run:final-verification")
    if unity:
        directory = Path(unity["path"])
        controller.require(git(directory, "rev-parse", "HEAD") == head, "Leased Unity slot has the wrong verification commit")
    # A classified retry creates a new attempt; reconnecting the same attempt
    # keeps its command journal and never repeats completed checks.
    retries = [e for e in proc.get("event_log", []) if e.get("type") in {"VERIFICATION_FAILURE_CLASSIFIED", "FINAL_VERIFICATION_FAILURE_CLASSIFIED"} and e.get("ticket_id") == ticket_id]
    destination = driver.directory / "verification" / digest([ticket_id or "run", head, retries])[:24]
    destination.mkdir(parents=True, exist_ok=True)
    plan = load(config["verification_plan_reference"])
    plan.update(workdir=str(directory), expected_head=head)
    run_registry.save_json(destination / "plan.json", plan)
    with contextlib.redirect_stdout(io.StringIO()):
        verification_adapter.execute(argparse.Namespace(
            state=driver.path, plan=destination / "plan.json", evidence=Path(config["verification_evidence_reference"]),
            output=destination / "result.json", logs_dir=destination / "logs", owner=driver.owner, owner_epoch=driver.epoch))
    return True


def gh(driver, *args):
    result = subprocess.run(["gh", *args, "--repo", driver.profile["github_repository"]], capture_output=True, text=True, encoding="utf-8", timeout=120)
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return result.stdout.strip()


def pull_request(driver, action):
    state = driver.state()
    proc, name = state["procedure"], action["action"]
    ticket_id = action.get("ticket_id")
    final = ticket_id is None
    remediation = name == "RECORD_FINAL_REMEDIATION_PR"
    item = proc["tickets"].get(ticket_id, proc["finalization"])
    base = proc["base_branch"] if final and not remediation else state["run_identity"]["train_branch"]
    if remediation:
        value = proc["phases"][proc["finalization"]["remediation_phase_key"]]
        branch, head = value["branch"], value["completion_envelope"]["artifacts"]["commit"]
    elif final:
        branch, head = state["run_identity"]["train_branch"], proc["train_head"]
    else:
        branch, head = item["execution"]["implementation_branch"], item["verification"]["ticket_head"]
    repo = driver.profile["repository"]
    # Push exact commits with ordinary fast-forward protection. Never overwrite
    # a remote change or use the user's current working tree as staging space.
    git(repo, "push", "origin", head + ":refs/heads/" + branch)
    records = json.loads(gh(driver, "pr", "list", "--state", "open", "--head", branch, "--base", base,
                            "--json", "url,headRefOid,baseRefName,headRefName,isDraft"))
    controller.require(len(records) <= 1, "More than one open PR matches this branch/base")
    if not records:
        armed = driver.directory / "pull-requests" / (digest([branch, base]) + ".json")
        controller.require(not armed.exists(), "PR creation outcome is uncertain; reconcile the armed GitHub request before another create")
        if base == state["run_identity"]["train_branch"]:
            base_head = proc.get("train_head") or git(repo, "rev-parse", proc["base_branch"])
            git(repo, "push", "origin", base_head + ":refs/heads/" + base)
        body = driver.directory / "pull-requests" / (digest(branch) + ".md")
        body.parent.mkdir(parents=True, exist_ok=True)
        title = ("Ticket " + ticket_id) if ticket_id else ("Train remediation" if remediation else "Ticket train integration")
        summary = item.get("analysis", {}).get("summary") or driver.profile.get("summary", title)
        body.write_text(f"{summary}\n\nValidation and technical evidence: {driver.path.parent}\n\nChanges suggested by AI model: GPT-6 (Codex)\n", encoding="utf-8")
        run_registry.save_json(armed, {"branch": branch, "base": base, "head": head, "status": "creating"})
        url = gh(driver, "pr", "create", "--head", branch, "--base", base, "--title", title, "--body-file", str(body), "--draft")
        run_registry.save_json(armed, {"branch": branch, "base": base, "head": head, "status": "created", "url": url})
    else:
        url = records[0]["url"]
    record = json.loads(gh(driver, "pr", "view", url, "--json", "url,headRefOid,baseRefOid,baseRefName,headRefName,isDraft"))
    controller.require(record["headRefOid"] == head, "Live PR head differs from verified commit")
    event = {"type": "FINAL_REMEDIATION_PR_RECORDED" if remediation else "FINAL_PR_RECORDED" if final else "TICKET_PR_RECORDED",
             "url": record["url"], "base_branch": record["baseRefName"], "base_commit": record["baseRefOid"],
             "head_branch": record["headRefName"], "head_commit": head, "is_draft": record["isDraft"]}
    if ticket_id:
        event["ticket_id"] = ticket_id
    driver.apply(event)
    return True


def initial_review(driver, action):
    item = driver.state()["procedure"]["tickets"][action["ticket_id"]]
    classification = item.get("effective_classification") or item["analysis"]
    route = controller.routed_setting(controller.INITIAL_REVIEW_MATRIX, classification["criticality"], classification["complexity"], False)
    pr = item["pull_request"]
    key = action["ticket_id"] + ":review:" + pr["head_commit"]
    event = {"type": "REVIEW_DISPATCHED", "ticket_id": action["ticket_id"], "phase_key": key, "scope": "initial",
             "review_kind": "full", "base_commit": pr["base_commit"], "head_commit": pr["head_commit"],
             "criticality": classification["criticality"], "complexity": classification["complexity"],
             "classification_evidence": "Durable analysis and contract classification", "model": route[0],
             "reasoning_effort": route[1], "routing_conformance": route[2], "unity_requirement": item["analysis"].get("review_unity_requirement", "none"),
             "context_packet": private_context(driver, key, compact_inputs(driver, action), pr["base_commit"], pr["head_commit"])}
    driver.apply(event)
    return True


def feedback(driver, action):
    """Bounded GitHub collection; full raw records stay outside model context."""
    from datetime import datetime, timedelta, timezone
    proc = driver.state()["procedure"]
    ticket_id = action.get("ticket_id")
    item = proc["tickets"][ticket_id] if ticket_id else proc["finalization"]
    pr = item["pull_request"]
    directory = driver.directory / "feedback" / digest(pr["url"] + pr["head_commit"])[:24]
    directory.mkdir(parents=True, exist_ok=True)
    clock_path = directory / "collection.json"
    if not clock_path.exists():
        started = datetime.now(timezone.utc)
        run_registry.save_json(clock_path, {"collection_id": digest(pr["url"] + pr["head_commit"]),
                                           "started_at": started.isoformat(), "deadline_at": (started + timedelta(minutes=10)).isoformat()})
    clock = load(clock_path)
    if not ticket_id and action["action"] == "START_FINAL_GITHUB_FEEDBACK_COLLECTION":
        driver.apply({"type": "FINAL_FEEDBACK_COLLECTION_STARTED", **clock, "head_commit": pr["head_commit"],
                      "expected_sources": ["codex", "ci", "copilot", "human"]})
        return False
    snapshot = directory / "github.json"
    if snapshot.exists() and time.time() - snapshot.stat().st_mtime < 30:
        return False
    raw = json.loads(gh(driver, "pr", "view", pr["url"], "--json", "headRefOid,reviews,comments,statusCheckRollup"))
    controller.require(raw["headRefOid"] == pr["head_commit"], "GitHub head changed; reconcile the reviewed commit")
    owner, repository = driver.profile["github_repository"].split("/", 1)
    number = int(pr["url"].rstrip("/").rsplit("/", 1)[-1])
    threads, cursor = [], None
    query = "query($owner:String!,$repo:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$repo){pullRequest(number:$number){reviewThreads(first:100,after:$cursor){nodes{id isResolved comments(first:100){nodes{id body url author{login} originalCommit{oid}} pageInfo{hasNextPage}}} pageInfo{hasNextPage endCursor}}}}}"
    while True:
        args = ["gh", "api", "graphql", "-f", "query=" + query, "-f", "owner=" + owner, "-f", "repo=" + repository, "-F", "number=" + str(number)]
        if cursor:
            args += ["-f", "cursor=" + cursor]
        response = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=120)
        controller.require(response.returncode == 0, response.stderr.strip())
        data = json.loads(response.stdout)
        controller.require(not data.get("errors"), "GitHub returned incomplete feedback")
        page = data["data"]["repository"]["pullRequest"]["reviewThreads"]
        controller.require(not any(n["comments"]["pageInfo"]["hasNextPage"] for n in page["nodes"]), "Large review thread requires a paginated project feedback adapter")
        threads.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    raw["review_threads"] = threads
    run_registry.save_json(snapshot, raw)
    if datetime.now(timezone.utc) < datetime.fromisoformat(clock["deadline_at"]):
        return False
    # Technical disposition needs the raw inventory and exact deadline. Store
    # them in the next decision's compact input, never ask the owner to collect.
    driver.__dict__.setdefault("feedback_inputs", {})[ticket_id or "run"] = {
        **clock, "evidence_reference": str(snapshot), "head_commit": pr["head_commit"], "collected_at": controller.now_iso()}
    if ticket_id:
        return True
    findings = []
    for thread in threads:
        for comment in thread["comments"]["nodes"]:
            login = (comment.get("author") or {}).get("login", "")
            source = "copilot" if "copilot" in login.lower() else "codex" if "codex" in login.lower() else "human"
            findings.append({"finding_id": comment["id"], "source": source, "reference": comment["url"]})
    for comment in [*raw.get("reviews", []), *raw.get("comments", [])]:
        if not (comment.get("body") or "").strip():
            continue
        login = (comment.get("author") or {}).get("login", "").lower()
        source = "copilot" if "copilot" in login else "codex" if "codex" in login else "human"
        identity = comment.get("id") or digest(comment)
        findings.append({"finding_id": str(identity), "source": source, "reference": comment.get("url") or str(snapshot) + "#" + str(identity)})
    checks = raw["statusCheckRollup"]
    if checks and not all((c.get("conclusion") or c.get("state")) in {"SUCCESS", "NEUTRAL", "SKIPPED"} for c in checks):
        return driver.notify("ci-not-passed", {"evidence": str(snapshot)})
    copilot_received = any("copilot" in (r.get("author") or {}).get("login", "").lower() for r in raw["reviews"]) or any(f["source"] == "copilot" for f in findings)
    driver.apply({"type": "FINAL_FEEDBACK_SNAPSHOT_RECORDED", **clock, "snapshot_id": digest(raw), "head_commit": pr["head_commit"],
                  "collected_at": controller.now_iso(), "ci_status": "passed" if checks else "not_configured",
                  "copilot_status": "received" if copilot_received else "timed_out",
                  "source_findings": findings, "source_counts": {k: sum(f["source"] == k for f in findings) for k in ("codex", "ci", "copilot", "human")},
                  "unresolved_thread_ids": [t["id"] for t in threads if not t["isResolved"]], "evidence_reference": str(snapshot)})
    return True


def reports(driver, action):
    import token_usage
    import orchestration_metrics
    proc = driver.state()["procedure"]
    directory = driver.directory / "reports"
    directory.mkdir(parents=True, exist_ok=True)
    ledger_path, matrix_path, metrics_path = (directory / n for n in ("ledger.json", "usage.md", "metrics.json"))
    with contextlib.redirect_stdout(io.StringIO()):
        token_usage.ledger_command(argparse.Namespace(manifest=driver.path, thread=None, codex_home=None,
                                                      output=ledger_path, matrix_output=matrix_path, report_language="en"))
    ledger = load(ledger_path)
    metrics = orchestration_metrics.build_report(driver.state())
    run_registry.save_json(metrics_path, metrics)
    matrix = ledger["usage_matrix"]
    matrix_json = directory / "usage.json"
    run_registry.save_json(matrix_json, matrix)
    report = directory / "report.md"
    rows = ["# Ticket train result", "", "| Ticket | Result | Evidence |", "|---|---|---|"]
    for ticket_id, item in proc["tickets"].items():
        rows.append(f"| {ticket_id} | {item['status']} | {(item.get('pull_request') or {}).get('url') or (item.get('analysis') or {}).get('report_thread_id', 'See analysis artifacts')} |")
    for ticket_id, item in proc["tickets"].items():
        rows += ["", "## " + ticket_id, "", "Result: " + item["status"]]
        for value in proc["phases"].values():
            if value.get("ticket_id") != ticket_id:
                continue
            result = value.get("completion_envelope") or {}
            rows += ["", value["kind"] + ": " + str(result.get("result_summary", "No completed result")),
                     "", "Checks: " + json.dumps(result.get("tests_and_checks", "Not performed"), ensure_ascii=False),
                     "", "Attention points: " + str(result.get("residual_risks", "Not assessed")),
                     "", "Evidence: " + json.dumps(result.get("artifacts", {}), ensure_ascii=False)]
        validation = item.get("validation_result") or item.get("verification")
        rows += ["", "Deterministic verification: " + (json.dumps(validation, ensure_ascii=False) if validation else "Not performed; see ticket status."),
                 "", "Manual validation: " + json.dumps(item.get("manual_validation", "Not recorded; do not infer approval from automated checks."), ensure_ascii=False)]
    if driver.state().get("orchestration_accounting_reference"):
        rows += ["", "Shared orchestration and reused analysis are counted in the parent run: " + driver.state()["orchestration_accounting_reference"]]
    rows += ["", f"Usage: [{matrix_path.name}]({matrix_path})", "", "Changes suggested by AI model: GPT-6 (Codex)", ""]
    report.write_text("\n".join(rows), encoding="utf-8")
    aggregate = ledger["aggregate"]
    event = {"type": "NO_DELIVERY_EVIDENCE_RECORDED" if action["action"] == "RECORD_NO_DELIVERY_REPORT" else "DRY_RUN_EVIDENCE_RECORDED" if action["action"] == "RECORD_DRY_RUN_REPORT_AND_USAGE_EVIDENCE" else "FINAL_EVIDENCE_RECORDED",
             "token_reporting_status": aggregate["status"], "ledger_reference": str(ledger_path),
             "ledger_sha256": hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
             "orchestration_metrics_status": metrics["status"], "orchestration_metrics_reference": str(metrics_path),
             "orchestration_metrics_sha256": hashlib.sha256(metrics_path.read_bytes()).hexdigest(),
             "authoritative_phase_count": aggregate["authoritative_phase_count"], "measured_phase_count": aggregate["measured_phase_count"],
             "task_inventory_requested_count": len(proc["tickets"]), "task_inventory_terminal_count": len(proc["tickets"]),
             "usage_matrix_reference": str(matrix_json), "usage_matrix_sha256": hashlib.sha256(matrix_json.read_bytes()).hexdigest(),
             "usage_matrix_status": matrix["coverage_status"], "usage_matrix_ticket_ids": matrix["expected_ticket_ids"],
             "usage_matrix_ticket_phase_columns": matrix["ticket_phase_columns"], "usage_matrix_transverse_task_ids": matrix["expected_transverse_task_ids"],
             "usage_matrix_unreported_cell_count": matrix["unreported_cell_count"], "orchestrator_session_included": ledger["orchestrator_session_included"],
             "hidden_sessions_reconciled": not ledger["unmapped_hidden_sessions"],
             "unmeasured_phase_keys": [v["phase_key"] for v in ledger["unmeasured_phases"]],
             "unmeasured_session_ids": [k for k, v in ledger["sessions"].items() if v["status"] != "available"],
             "completion_report_reference": str(report)}
    for field in ("usage_matrix_ready", "session_usage_ledger_ready", "analysis_reports_ready", "task_inventory_ready", "completion_report_ready",
                  "orchestration_metrics_ready", "verification_summary_ready", "manual_validation_summary_ready", "attention_points_summary_ready"):
        event[field] = True
    if event["type"] == "FINAL_EVIDENCE_RECORDED":
        snapshot = proc["finalization"]["feedback_snapshot"]
        event.update(feedback_snapshot_id=snapshot["snapshot_id"], ci_status=snapshot["ci_status"], copilot_status=snapshot["copilot_status"], finding_ledger_status="complete")
    driver.apply(event)
    return True


def execute_action(driver, action):
    name = action["action"]
    if name == "ANNOUNCE_HUMAN_GATE":
        gate = action["gate"]
        presentation = (
            "question", "reason", "blocked_scope", "continuing_scope", "accepted_replies"
        )
        if not all(key in gate for key in presentation):
            return driver.notify("human-gate-presentation-required", action)
        driver.apply({
            "type": "GATE_ANNOUNCED",
            "gate_id": gate["gate_id"],
            "revision": gate["revision"],
            "decision_summary": gate["question"],
            "evidence_summary": gate["reason"],
            "blocked_scope": gate["blocked_scope"],
            "continuing_scope": gate["continuing_scope"],
            "accepted_replies": gate["accepted_replies"],
        })
        return True
    if name in {"CONFIGURE_SUPERVISION_BEFORE_DISPATCH", "RECONFIGURE_EVENT_CALLBACKS_FOR_CURRENT_OWNER", "REPLACE_MODEL_WAKING_WATCHER"}:
        import os
        driver.apply({"type": "SUPERVISION_CONFIGURED", "mode": "BACKGROUND_WATCHER",
                      "watcher_id": "native-driver:" + str(os.getpid()), "watcher_consumes_model_tokens": False})
        return True
    if name in {"RECORD_DRY_RUN_REPORT_AND_USAGE_EVIDENCE", "COLLECT_FINAL_TOKENS_AND_REPORTS", "RECORD_NO_DELIVERY_REPORT"}:
        return reports(driver, action)
    if name in {"START_FINAL_GITHUB_FEEDBACK_COLLECTION", "POLL_FINAL_FEEDBACK_DETERMINISTICALLY"}:
        return feedback(driver, action)
    if name == "MATERIALIZE_SPLIT_RUNS":
        import copy
        state = driver.state()
        manifests = {}
        for batch in state["procedure"]["split_plan"]["batches"]:
            directory = driver.path.parent / "batches" / digest(batch["batch_id"])[:16]
            destination = directory / "manifest.json"
            if not destination.exists():
                child = copy.deepcopy(state)
                child["run_id"] = state["run_id"] + ":" + batch["batch_id"]
                child["parent_run_reference"] = str(driver.path)
                child["created_at"] = controller.now_iso()
                child["orchestration_accounting_reference"] = str(driver.path)
                child.pop("control_plane", None)
                child.pop("orchestration_metrics", None)
                child.pop("handoff_history", None)
                child.pop("control", None)
                child["run_identity"].update(tickets=batch["tickets"], train_branch=batch["train_branch"])
                child["run_identity"]["fingerprint"] = run_registry.run_fingerprint(**{
                    k: child["run_identity"][k] for k in ("repository", "train_branch", "source", "tickets")})
                proc = child["procedure"]
                proc.pop("split_plan")
                proc.update(tickets={k: v for k, v in proc["tickets"].items() if k in batch["tickets"]},
                            phases={}, revision=0, applied_events={}, event_log=[], train_head=None,
                            finalization={"status": "NOT_STARTED"}, run_status="ACTIVE")
                proc["execution_order"] = [k for k in proc.get("execution_order", batch["tickets"]) if k in batch["tickets"]]
                child["reused_analysis_reference"] = str(driver.path)
                # Historical analysis cost belongs to the parent, once. Child
                # reports link that ledger rather than counting sessions again.
                child.pop("usage_sessions", None)
                run_registry.save_json(destination, child)
            manifests[batch["batch_id"]] = str(destination)
        driver.apply({"type": "SPLIT_RUNS_MATERIALIZED", "manifests": manifests})
        return True
    if name == "DRIVE_SPLIT_RUNS":
        import control_plane_runner
        progress = False
        for path in action["manifests"].values():
            child = driver.children.get(path)
            if child is None:
                child = control_plane_runner.Driver(path, driver.owner, driver.epoch, driver.profile)
                driver.child_locks.enter_context(run_registry.file_lock(child.directory / "driver.lock", timeout_seconds=0))
                driver.children[path] = child
            progress = child.tick() or progress
        if all(c.state()["procedure"]["run_status"] == "COMPLETED" for c in driver.children.values()):
            import token_usage
            directory = driver.directory / "reports"
            directory.mkdir(parents=True, exist_ok=True)
            ledger = directory / "parent-ledger.json"
            with contextlib.redirect_stdout(io.StringIO()):
                token_usage.ledger_command(argparse.Namespace(manifest=driver.path, thread=None, codex_home=None,
                    output=ledger, matrix_output=directory / "parent-usage.md", report_language="en"))
            rows = ["# Split train result", "", "Each batch has its own delivery and evidence. Shared orchestration and reused analysis are counted once in the parent ledger.",
                    "", "| Batch | Branch | Delivery | Report |", "|---|---|---|---|"]
            for child in driver.children.values():
                state = child.state()
                final = state["procedure"]["finalization"]
                report = child.directory / "reports" / "report.md"
                rows.append(f"| {state['run_id']} | {state['run_identity']['train_branch']} | {(final.get('pull_request') or {}).get('url', 'No code delivery')} | [{report.name}]({report}) |")
            rows += ["", f"[Parent usage ledger]({ledger}). Partial measurements remain partial; linked batch ledgers cover their workers.", "", "Changes suggested by AI model: GPT-6 (Codex)."]
            report = directory / "split-report.md"
            report.write_text("\n".join(rows) + "\n", encoding="utf-8")
            driver.apply({"type": "SPLIT_RUNS_COMPLETED", "completion_report_reference": str(report),
                          "completion_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest()})
            return True
        return progress
    if name in {"DISPATCH_VISIBLE_PHASE", "RECONCILE_AMBIGUOUS_LAUNCH"}:
        if name == "RECONCILE_AMBIGUOUS_LAUNCH":
            for key in action["phase_keys"]:
                # An absent effect journal is an external/legacy task, never a
                # permission to create a replacement.
                if not driver.effects().read(key):
                    return driver.notify("unknown-creation", action)
                launch(driver, {"phase_key": key})
            return True
        return launch(driver, action)
    if name in {"COLLECT_OBSERVED_PHASE_RESULTS", "OBSERVE_ACTIVE_PHASES"}:
        return collect(driver)
    if name in {"RECORD_BATCH_TRIAGE_DISPATCH_INTENT", "RECORD_ANALYSIS_DISPATCH_INTENT", "RECORD_ANALYSIS_ROUTE_VALIDATION_DISPATCH_INTENT", "RECORD_PLAN_CONTRACT_VALIDATION_DISPATCH_INTENT"}:
        return dispatch_intent(driver, action)
    if name == "DISPATCH_EXECUTION_PAIR_ATOMICALLY":
        return execution_pair(driver, action)
    if name == "INTEGRATE_EXECUTION_PAIR_DETERMINISTICALLY":
        return integrate(driver, action)
    if name in {"RUN_DETERMINISTIC_TICKET_VERIFICATION", "RUN_FINAL_EXACT_HEAD_VERIFICATION_DETERMINISTICALLY", "RUN_VALIDATION_ONLY_VERIFICATION"}:
        return verify(driver, action)
    if name in {"INITIALIZE_UNITY_SLOTS_DETERMINISTICALLY", "ACQUIRE_UNITY_SLOT_DETERMINISTICALLY", "RELEASE_UNITY_SLOT_DETERMINISTICALLY"}:
        import unity_slot_adapter
        with contextlib.redirect_stdout(io.StringIO()):
            unity_slot_adapter.step(argparse.Namespace(state=driver.path, owner=driver.owner, owner_epoch=driver.epoch))
        return True
    if name in {"CREATE_OR_UPDATE_TICKET_PR_TARGETING_TRAIN", "CREATE_FINAL_TRAIN_PR", "RECORD_FINAL_REMEDIATION_PR", "UPDATE_FINAL_TRAIN_PR_HEAD"}:
        return pull_request(driver, action)
    if name in {"MARK_TICKET_PR_READY", "MARK_FINAL_TRAIN_PR_READY"}:
        ticket_id = action.get("ticket_id")
        proc = driver.state()["procedure"]
        item = proc["tickets"][ticket_id] if ticket_id else proc["finalization"]
        pr = item["pull_request"]
        live = json.loads(gh(driver, "pr", "view", pr["url"], "--json", "isDraft,headRefOid"))
        controller.require(live["headRefOid"] == pr["head_commit"], "PR head changed before ready transition")
        if live["isDraft"]:
            gh(driver, "pr", "ready", pr["url"])
        driver.apply({"type": "TICKET_PR_READY_RECORDED" if ticket_id else "FINAL_PR_READY_RECORDED",
                      "ticket_id": ticket_id, "head_commit": pr["head_commit"], "url": pr["url"], "is_draft": False})
        return True
    if name in {"MERGE_TICKET_PR_INTO_TRAIN", "MERGE_FINAL_REMEDIATION_PR_INTO_TRAIN"}:
        import merge_pull_request
        with contextlib.redirect_stdout(io.StringIO()):
            result = merge_pull_request.merge(argparse.Namespace(
                state=driver.path, repo=driver.profile["github_repository"], owner=driver.owner, owner_epoch=driver.epoch,
                action="ticket" if action.get("ticket_id") else "final-remediation", ticket_id=action.get("ticket_id"),
                method="merge", dry_run=False))
        if result:
            return driver.notify("merge-blocked", action)
        git(driver.profile["repository"], "fetch", "origin", driver.state()["run_identity"]["train_branch"])
        return True
    if name == "DISPATCH_EXHAUSTIVE_INITIAL_REVIEW":
        return initial_review(driver, action)
    if name == "START_FINALIZATION":
        driver.apply({"type": "FINALIZATION_STARTED", "final_verification_unity_requirement": driver.profile.get("final_unity_requirement", "none")})
        return True
    if name == "RESUME_VISIBLE_PHASE_WITH_INPUT":
        value = driver.state()["procedure"]["phases"][action["phase_key"]]
        job = driver.effects().read(value["phase_key"])
        if not job:
            return driver.notify("external-resume-required", action)
        job["attempt"] += 1
        import uuid
        job["client_message_id"] = str(uuid.uuid4())
        driver.effects().save(job)
        driver.effects().start_turn(job, "The user answered the phase's pending question: " + json.dumps(action["provided_input"], ensure_ascii=False))
        driver.apply({"type": "PHASE_RESUMED", "phase_key": value["phase_key"], "thread_id": job["thread_id"], "visibility_verified": True})
        return True
    if name == "RECORD_ANALYSIS_READINESS_RECONCILIATION":
        driver.apply({"type": "ANALYSIS_READINESS_RECONCILED", "ticket_id": action["ticket_id"],
                      "analysis_revision": action["analysis_revision"], "reason": action["reason"]})
        return True
    if name in DECISION_EVENTS and name not in {"ROOT_CAUSE_CHECKPOINT_REQUIRED", "RESOLVE_COST_ANOMALY_CHECKPOINT"}:
        proc = driver.state()["procedure"]
        if name == "BLOCKED_OR_INCONSISTENT_STATE" and not any(t.get("status") in {"ANALYSIS_RECONCILIATION_REQUIRED", "NEEDS_CONTRACT_AMENDMENT"} for t in proc["tickets"].values()):
            return driver.notify("configuration-required", action)
        matching = [p for p in proc["phases"].values() if p.get("decision_action") == name
                    and p.get("ticket_id") == action.get("ticket_id") and p["launch_state"] in controller.ACTIVE_PHASE_STATES]
        if matching:
            return False
        if name == "RECONCILE_CODEX_CI_COPILOT_FINDINGS" and not feedback(driver, action):
            return False
        key = f"{action.get('ticket_id') or 'run'}:decision:{len(proc['phases']) + 1}"
        head = proc.get("train_head") or git(driver.profile["repository"], "rev-parse", proc["base_branch"])
        payload = compact_inputs(driver, action)
        if name == "RECONCILE_CODEX_CI_COPILOT_FINDINGS":
            payload["feedback"] = driver.feedback_inputs[action["ticket_id"]]
        payload["dispatch_context_recipe"] = {
            "script": str(Path(__file__).parent / "context_packet.py"),
            "output_root": str(driver.directory / "contexts"), "profile_revision": driver.profile["revision"],
            "instruction": "For a dispatch event, prepare a small phase context for the selected exact base/head with this deterministic script. Select the routed setting from train_controller.py. Do not start the phase yourself."}
        driver.apply({"type": "TECHNICAL_DECISION_DISPATCHED", "phase_key": key, "action": name,
                      "ticket_id": action.get("ticket_id"), "base_commit": head,
                      "context_packet": private_context(driver, key, payload, head, head)})
        return True
    if name == "COMPLETE_RUN":
        driver.apply({"type": "RUN_COMPLETED"})
        return True
    return driver.notify("decision" if name in DECISION_EVENTS else "configuration-required", action)
