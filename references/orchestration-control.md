# Ticket Train Orchestration Control

## Contents

- Control-plane invariants
- Procedural controller
- Canonical run and ownership
- Phase identity and launch state
- User-visible thread launch protocol
- Child completion envelope
- Active supervision loop
- Progress reporting
- Durable state updates
- Deterministic yield guard
- Yield and completion gates
- Restart recovery

## Control-plane invariants

Use the main conversation as the only orchestrator. Every triage, analysis,
implementation, independent acceptance-test authoring, review, follow-up
review, and final-train review phase must run
in a separate user-visible Codex thread when thread-management tools are
available.

Never use `fork_thread` for a train phase. A fork copies the orchestrator
history and produces a misleading duplicate conversation instead of a clean
phase context.

Never use hidden collaboration subagents as a fallback for a phase unless the
user explicitly authorized hidden execution for the affected phase after the
visible-thread failure was reported. General train authorization or an
approval bypass mode does not grant this permission.

Do not ask the user to advance an automatic transition. User input is required
only for a configured human gate, a material decision, a permission boundary,
or a reported blocker.

## Procedural controller

Use [controller-protocol.md](controller-protocol.md) and
`scripts/train_controller.py` as the transition authority. The main
conversation is a tool adapter: read the controller's next action, execute it,
and submit the observation as a revision-checked idempotent event.

Narrative state in the main conversation, a child report, a heartbeat, or a
GitHub status is evidence, not permission to advance. Feed that evidence to
the controller. Do not dispatch, retry, review, merge, yield, or complete when
the corresponding event is rejected.

`train_supervisor.py` collects deterministic thread, GitHub, test, and
verification observations. It does not own lifecycle transitions. Convert its
changed observations into controller events authorized by `next_actions`.

## Canonical run and ownership

Apply [run-continuity.md](run-continuity.md) before startup preflight and after
every interruption. Use only
`$CODEX_HOME/ticket-train/runs/<run-id>/manifest.json`. Discover the run by its
repository/train/source/ticket fingerprint and claim its single orchestrator
lease. A matching active run must be adopted, never duplicated.

Do not dispatch while another unexpired owner exists. Resume in that
conversation or obtain explicit user authorization for takeover. Persist the
handoff so a superseded orchestrator cannot continue later.

## Phase identity and launch state

Assign every phase attempt a stable, unique key before dispatch:

```text
phase_key = <run-id>:<ticket-or-run>:<phase>:<attempt>
```

Include the phase key, source orchestrator thread ID, ticket ID, and phase in
the child prompt and visible title. Persist the launch intent before calling a
thread-creation tool.

Track these launch states:

```text
INTENT_RECORDED
LAUNCH_REQUESTED
LAUNCH_UNKNOWN
QUEUED
RUNNING
COMPLETED
BLOCKED
FAILED
CANCELLED
```

Persist at least:

```text
phase_key
display_title
source_orchestrator_thread_id
requested_model
requested_reasoning_effort
client_thread_id
thread_id
host_id
wait_cursor
launch_state
launch_attempts
last_observed_at
final_report_captured
usage_captured
```

Never create a second attempt while the previous attempt is `LAUNCH_UNKNOWN`,
`QUEUED`, `RUNNING`, or otherwise not conclusively absent or terminal.

## User-visible thread launch protocol

Use the product's user-visible thread creation tool. Do not substitute a fork,
hidden subagent, shell process, or direct implementation in the orchestrator.

Apply this sequence for every phase:

1. Persist `INTENT_RECORDED` with the unique phase key.
2. Call the user-visible create-thread operation once with explicit model,
   reasoning effort, target branch or worktree, title, and phase key.
3. Persist every returned `clientThreadId` or `threadId` immediately.
4. When a client ID is returned without a thread ID, mark `QUEUED`, reconcile
   until the real thread ID appears, then register its host ID and initial
   cursor. Verify the materialized task through a user-visible list/read
   operation and persist `visibility_verified = true` with its timestamp.
5. When a call returns an error, times out, loses its response handler, or has
   an otherwise ambiguous outcome, mark `LAUNCH_UNKNOWN`. Do not assume that
   no side effect occurred.
6. Reconcile user-visible threads at least twice over a bounded 30-to-60-second
   window using the phase key, source thread ID, title, target, and creation
   time. A task that materializes after an error is the original attempt, not a
   reason to create another.
7. If the task is found, register it and continue. If it is conclusively absent
   after reconciliation, retry one visible creation with an incremented
   attempt key only when the operation is safe. If reliable reconciliation
   itself is unavailable, transition the phase to `BLOCKED`, preserve that its
   launch outcome remains uncertain, and report the thread-management platform
   blocker. Never switch silently to hidden execution.
8. Report every launch anomaly and its resolution in the main conversation and
   durable manifest.

Treat duplicate phase keys or multiple live threads for one phase as an
orchestration incident. Stop dispatching that phase, select one authoritative
thread without discarding completed evidence, cancel or ignore the duplicate
only when safe, and disclose the disposition.

Never classify a task as blocked, failed, or inactive from silence, missing
incremental commentary, or an unchanged cursor. Reconcile authoritative task
state and the completion envelope first.

## Child completion envelope

Require every phase prompt to end with a compact, machine-readable completion
envelope in its final response:

```text
phase_key
phase_status = completed | blocked | failed | cancelled
actual_model
actual_reasoning_effort
result_summary
artifacts = thread | branch | commit | pull_request | reviewed_head
tests_and_checks
verification_contract_and_coverage
red_green_and_environment_evidence
findings_or_decisions
residual_risks
requested_or_recommended_next_action
files_modified = none | explicit list
usage = measurement plus exact counters or explicit unavailable status
```

The envelope is a result contract, not a notification mechanism. The
orchestrator must still wait for and collect it. Reject an incomplete envelope
through a targeted follow-up in the same visible thread instead of reconstructing
the technical result from raw logs.

## Active supervision loop

Resolve supervision before dispatch. Use foreground transition waits or create
and verify a run-scoped background watcher. Persist the mode, watcher ID,
`last_check_at`, and `next_check_at`. If neither is reliable, block before
launching children. The user is never responsible for creating this watcher.

After dispatch, keep the orchestrator turn active while any phase is queued,
running, launch-unknown, or ready for an automatic successor.

Use one event-driven `wait_threads` call for all active user-visible threads
with their latest host IDs and cursors. Prefer the longest product-approved
wait that still permits required user updates. On every wake-up:

1. Reconcile all active and launch-unknown phases.
2. Advance each cursor and persist the latest state.
3. Read a detailed child result only when the phase completed, needs input,
   reported a blocker, or requires a targeted amendment.
4. Capture the completed phase's token counters before starting its successor.
5. Validate routing conformance and phase output completeness.
6. Publish the compact state transition in the main conversation.
7. Dispatch every newly ready automatic successor.
8. Wait again while active work remains.

When no state, cursor, blocker, decision, or result changed, do not read a
thread, rewrite a narrative status, or create another model-visible report.
Reuse the deterministic snapshot and wait again. A model wake must correspond
to a transition, dispatch, failure, blocker, or technical decision whenever
the product supports transition-triggered waits.

A child completion does not by itself justify ending the orchestrator turn.
The orchestrator owns result collection, state advancement, and successor
dispatch. Do not rely on the user to notice a completed child or send a message
that wakes the main conversation.

If normal thread waits are temporarily unavailable, use a bounded monitor or
product-supported wake-up mechanism while continuing to reconcile the same
visible thread IDs. Do not create replacement workers. If reliable monitoring
cannot be restored, persist the state and report a blocker.

When the host requires the main turn to yield during long work, use
`SUPERVISED_ACTIVE` only after the deterministic guard verifies the watcher.
The watcher checks no less often than every five minutes and produces a
manifest-derived liveness update at least every 15 minutes while active work
remains. It wakes the model immediately for transitions, blockers, successor
dispatch, or human decisions.

## Progress reporting

Report immediately when a phase:

- is queued or starts;
- completes or fails;
- requests input or reaches a gate;
- produces findings or remediation;
- is merged into the train;
- changes the next automatic action;
- encounters or resolves a launch anomaly.

For long unchanged work, follow the product's mandatory communication cadence
without rereading child context. Publish ordinary train status only on a
material transition or at an explicit user-requested interval. If the platform
requires a heartbeat, keep it to one line derived from the durable snapshot.
Do not dump raw logs or repeat unchanged detailed reports.

While work is active, the default maximum user-visible silence is 15 minutes.
This liveness message is a one-line deterministic status and must not reread
child context. A pending human action is never unchanged noise: its reminder
begins with `ACTION REQUIRED` and cannot be replaced by `DONT_NOTIFY`.

Use the status template in [report-template.md](report-template.md).

## Durable state updates

Write the manifest after every launch intent, tool result, reconciliation,
cursor advance, phase completion, gate decision, branch or pull-request
change, integration, finding-ledger update, and usage capture.

Record globally:

```text
manifest_updated_at
run_status
run_identity
orchestrator_lease
handoff_history
supervision
last_state_transition
next_automatic_action
active_phase_keys
launch_unknown_phase_keys
pending_human_gates
blocking_conditions
visible_thread_inventory
proportionality_profile_revision
train_size_budget
review_pass_budgets
verification_gates
session_usage_ledger
duplicate_session_inventory
unmeasured_phase_inventory
cost_anomaly_status
pending_human_action
analysis_artifacts
```

Do not leave the manifest at a stale high-level state while child work
continues. Before every user-facing status or final report, reconcile the
manifest with live threads, Git branches, pull requests, gates, and usage
snapshots.

Use `scripts/train_supervisor.py` for deterministic reconciliation:

```powershell
python scripts/train_supervisor.py thread-event --state <manifest> --event-json <changed-wait-snapshot-json>
python scripts/train_supervisor.py github-snapshot --state <manifest> --repo <owner/name> --pr <number>
python scripts/train_supervisor.py test-result --state <manifest> --phase-key <key> --command <command> --exit-code <code> --head <sha> --duration-seconds <seconds> --log <log-file>
python scripts/train_supervisor.py verification-event --state <manifest> --ticket <ticket-id> --event-json <changed-verification-evidence-json>
python scripts/train_supervisor.py supervision-event --state <manifest> --event-json <supervision-json>
python scripts/train_supervisor.py human-gate --state <manifest> --event-json <complete-announced-gate-json>
python scripts/train_supervisor.py clear-human-gate --state <manifest> --gate-id <id> --decision <decision> --next-action <action>
python scripts/train_supervisor.py analysis-artifact --state <manifest> --event-json <analysis-evidence-json>
python scripts/train_supervisor.py status --state <manifest>
```

The thread connector remains responsible for waiting on user-visible tasks;
feed only changed snapshots to the controller. The controller records task
transitions, reads GitHub status through deterministic `gh --json` output,
extracts bounded test errors, hashes full external logs, and updates the
manifest and verification gate atomically. Use the model only after the controller reports a
transition, failure, blocker, or decision.

After each changed observation, apply the corresponding controller event with
the expected revision. Do not update `next_automatic_action` by hand. Derive it
from `train_controller.py status`.

## Deterministic yield guard

Maintain a `control` object in the run manifest for the read-only guard script:

```json
{
  "run_identity": {
    "fingerprint": "<sha256>",
    "repository": "<canonical repository>",
    "train_branch": "<branch>",
    "source": "<source locator>",
    "tickets": ["TICKET-1"]
  },
  "orchestrator_lease": {
    "owner_thread_id": "<main thread>",
    "heartbeat_at": "<ISO timestamp>",
    "expires_at": "<ISO timestamp>"
  },
  "supervision": {
    "mode": "FOREGROUND_WAIT | BACKGROUND_WATCHER",
    "status": "ACTIVE",
    "watcher_id": "<required in background mode>",
    "last_check_at": "<ISO timestamp>",
    "next_check_at": "<ISO timestamp>"
  },
  "pending_human_action": null,
  "execution_mode": "live",
  "integrated_ticket_count": 0,
  "control": {
    "manifest_updated_at": "<ISO timestamp>",
    "manifest_reconciled": true,
    "terminal_reason": null,
    "next_automatic_action": "<action or none>",
    "launch_unknown_phase_keys": [],
    "pending_human_gates": [],
    "blocking_conditions": [],
    "proportionality_profile_revision": "profile-1",
    "train_size_budget": {
      "material_files": 0,
      "schema_or_data_transformations": 0,
      "structural_domains": [],
      "checkpoint_crossed": false
    },
    "review_pass_budgets": {},
    "verification_gates": {},
    "duplicate_session_inventory": [],
    "unmeasured_phase_inventory": [],
    "cost_anomaly_status": "clear",
    "requested_ticket_states": {
      "TICKET-1": "READY_FOR_IMPLEMENTATION"
    },
    "phases": [
      {
        "phase_key": "<stable key>",
        "launch_state": "RUNNING",
        "visibility": "user-visible",
        "hidden_authorized": false,
        "client_thread_id": "<queued client ID or null>",
        "thread_id": "<materialized thread ID or null>",
        "visibility_verified": true,
        "final_report_captured": false,
        "usage_captured": false
      }
    ],
    "finalization": {
      "integrated_ticket_count": 0,
      "final_pull_request_url": null,
      "final_pull_request_head": null,
      "final_pr_created_before_review": false,
      "full_verification_status": null,
      "final_review_status": null,
      "final_review_routing_conformance": null,
      "final_reviewed_head": null,
      "ci_status": null,
      "copilot_status": null,
      "finding_ledger_status": null,
      "token_reporting_status": null,
      "session_usage_ledger_ready": false,
      "verification_summary_ready": false,
      "manual_validation_summary_ready": false,
      "attention_points_summary_ready": false,
      "task_inventory_ready": false,
      "completion_report_ready": false
    }
  }
}
```

At completion or checkpoint, give every requested ticket one terminal state:
`ANALYSIS_REPORTED`, `REPORTED`, `MERGED_INTO_TRAIN`, `BLOCKED`, `FAILED`, or
`CANCELLED`. Use `ANALYSIS_REPORTED` for a completed dry-run ticket and report
why any live ticket did not reach the train.

Before yielding or publishing a completion/checkpoint report, run:

```powershell
python scripts/train_controller.py check --state <run-manifest.json> --mode yield
python scripts/train_controller.py check --state <run-manifest.json> --mode completion
```

Then run the migration-period independent guards:

```powershell
python scripts/control_guard.py check-yield --state <run-manifest.json>
python scripts/control_guard.py check-completion --state <run-manifest.json>
python scripts/control_guard.py check-verification --state <run-manifest.json> --ticket <ticket-id>
```

Use the skill's absolute script path when outside the skill directory. Treat a
nonzero exit as a guard failure: keep supervising or complete the missing
evidence. The script is read-only and does not replace live-state
reconciliation.

The guard also enforces user-visible execution, proportionality and size state,
one complete review per stable scope, at most two automatic remediation cycles,
independent functional readiness before review, and explicit token-ledger
coverage. A failed cost guard never authorizes lower
quality; it triggers the root-cause checkpoint in
[efficiency-policy.md](efficiency-policy.md).

## Yield and completion gates

Before ending an orchestrator turn, evaluate one terminal reason:

```text
SUPERVISED_ACTIVE
AWAITING_REQUIRED_USER_INPUT
BLOCKED
COMPLETED
CHECKPOINT
```

Do not send an unsupervised final response when:

- any phase is `LAUNCH_REQUESTED`, `LAUNCH_UNKNOWN`, `QUEUED`, or `RUNNING`;
- an automatic successor is ready to dispatch;
- a completed child report, token counter, review finding, or integration
  result has not been captured;
- the durable manifest is stale;
- finalization requirements remain executable.

`SUPERVISED_ACTIVE` is the narrow exception for queued/running phases. It
requires verified supervision and no hidden human gate. It is not a completion
state and must include the next check time in the user-visible status.

For `AWAITING_REQUIRED_USER_INPUT`, identify the exact ticket, revision, gate,
and decision needed. Continue unrelated safe work before yielding. Publish the
complete `ACTION REQUIRED` decision packet in the main conversation and
persist it with `notification_status = ANNOUNCED` before running the guard.

For `BLOCKED`, state the blocking condition, completed reconciliation attempts,
preserved uncertain outcome, and precise resume action. A transient or
ambiguous task-tool response is not a blocker until the launch protocol has
been exhausted. A blocked uncertain phase must be reconciled again before any
retry on resume.

For `COMPLETED` or `CHECKPOINT`, run the finalization checklist from
[workflow.md](workflow.md) and the completion template from
[report-template.md](report-template.md). Do not close the train with a short
status message in place of the required report.

## Restart recovery

On restart, context compaction, new main conversation, or user resumption:

1. Discover the canonical manifest by fingerprint and claim or explicitly
   take over its orchestrator lease before dispatching work.
2. If it predates the `control` schema, migrate it by reconciling live threads,
   branches, pull requests, gates, and usage; never infer terminal values merely
   to make the guard pass.
   For manifests created before the cost-control schema, also create the
   proportionality profile, derive current size counts from the actual diff,
   inventory complete reviews and remediation cycles by stable scope, run the
   session ledger, and record duplicates and unmeasured phases. Preserve old
   work and classify it honestly; do not rerun it solely to populate new fields.
   For manifests created before independent verification, derive contracts and
   coverage from existing evidence where trustworthy. Mark missing red,
   environment, or Supabase/Auth evidence honestly and run only the missing
   gate before further review or merge; do not fabricate historical proof.
3. Reconcile every recorded client ID, thread ID, branch, worktree, pull
   request, commit, gate, usage snapshot, and launch-unknown phase.
4. Capture completed child results that were not yet recorded.
5. Recompute `next_automatic_action` from authoritative live state.
6. Report a compact recovered status and continue automatically unless a human
   gate or blocker is active.

Classify completed analysis artifacts as `REUSABLE`, `RECONCILE`, or `INVALID`
before launching analysis. Reuse stable evidence and perform only targeted
reconciliation when inputs changed. Record an unavoidable repeat as a cost
anomaly before dispatch.

Never replay a completed technical phase merely because the orchestrator lost
its conversational summary.
