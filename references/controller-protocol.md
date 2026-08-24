# Procedural Controller Protocol

## Purpose

`scripts/train_controller.py` is the authoritative control plane for a ticket
train. Prompts describe technical work; they do not decide whether a phase may
start, whether a gate passed, or whether the run is complete.

The controller is deliberately strict. An event that violates ordering,
routing, visibility, review, merge, or finalization invariants is rejected
without changing the manifest.

## Responsibility boundary

The deterministic controller owns:

- phase order and concurrency;
- event idempotency and manifest revision;
- ticket and train lifecycle state;
- human-gate creation, announcement, and exact-revision resolution;
- model-routing matrix lookup and conformance;
- bounded hash-addressed context-packet validation;
- user-visible versus explicitly authorized hidden execution identity;
- atomic implementation/acceptance-test pair creation;
- verification-before-review ordering;
- full-review-before-follow-up ordering;
- review and remediation limits;
- pull-request base and exact-head relationships;
- final pull request, verification, review, feedback, usage, and report gates;
- deterministic zero-token command evidence and token-cost circuit breakers;
- explicit information requests, their main-thread announcement, and
  same-thread phase resumption after a user answer;
- the list of allowed next actions.

Models retain judgment for:

- triage evidence;
- technical analysis and dependency contracts;
- implementation and test authorship;
- failure adjudication;
- code review and finding disposition;
- remediation;
- concise reports and user-facing decision packets.

The main Codex conversation is a thin adapter. It asks the controller for the
next action, executes only that action through the available task, GitHub, and
shell tools, then records the observed result as a new event. It must not
invent a transition because a prompt says that a phase is probably finished.

The adapter reads controller state through
`scripts/control_plane_runner.py`, not by loading the full manifest into the
conversation. The runner emits a maximum 16 KiB decision packet, suppresses
unchanged waits, and requires a controlled orchestrator handoff at its context
budget. Read [control-plane-runner.md](control-plane-runner.md).

## Bootstrap

Create or adopt the canonical run with `run_registry.py`, then bootstrap the
procedure once:

```powershell
python scripts/train_controller.py bootstrap `
  --state <run-manifest.json> `
  --base-branch <main-or-master> `
  --approval-mode <standard|auto-analysis|auto-merge|full-auto>
```

Bootstrap is idempotent. It upgrades the canonical manifest with a versioned
`procedure` object and does not replay technical work.

For a legacy run, reconcile prior artifacts before emitting replacement
events. Preserve reusable analysis, branches, reviews, usage, and duplicate
attempts under the continuity protocol.

## Event application

Every transition is one immutable JSON event with a globally unique
`event_id`. Apply it with the manifest revision last returned by the
controller:

```powershell
python scripts/train_controller.py apply `
  --state <run-manifest.json> `
  --expected-revision <revision> `
  --event <event.json>
```

The controller applies updates under a filesystem lock and writes the
manifest atomically. A stale revision is rejected. Replaying the same event ID
and identical payload is a no-op even when the caller still has the old
revision. Reusing an event ID with another payload is rejected.

This contract prevents two orchestrators, delayed tool responses, retries, or
scheduled wake-ups from applying the same transition twice.

## Dispatch protocol

Before any tool call, record the applicable dispatch event. For ordinary
phases this creates an `INTENT_RECORDED` phase. Implementation and independent
test authorship are created by one `EXECUTION_PAIR_DISPATCHED` event, which
atomically reserves:

- the implementation phase;
- the acceptance-test phase;
- one common base commit;
- two distinct branches;
- the exact routed model and reasoning settings.

There is no supported standalone implementation dispatch. Consequently, the
acceptance-test phase cannot be forgotten or started after implementation
completion.

After the task tool responds, apply `PHASE_LAUNCH_OBSERVED`:

- `QUEUED` requires a client task ID;
- visible `RUNNING` requires the real task ID, `execution_visibility =
  user-visible`, and durable visibility evidence;
- hidden `RUNNING` requires a prior phase-specific
  `HIDDEN_FALLBACK_AUTHORIZED` event containing the user's decision reference,
  then records the actual agent session ID without claiming visibility;
- `LAUNCH_UNKNOWN` preserves an ambiguous outcome and prevents a retry;
- `BLOCKED` requires completed reconciliation evidence in the orchestration
  report.

A returned `clientThreadId` is only queue evidence. It cannot satisfy visible
launch, supervision, or progress reporting. Resolve and verify the real
`threadId` before treating the phase as running. Under `FOREGROUND_WAIT`, the
orchestrator must remain in the same turn and wait for transitions; the yield
guard rejects every queued, running, or launch-unknown phase in that mode.

Silence is not an event and never changes a phase to blocked. Only an
authoritative task transition or completion envelope changes phase state.

Every dispatch also carries a descriptor produced by `context_packet.py`.
The packet uses `fresh-compact-v1`, includes no inherited turns, names the
exact base/head and proportionality-profile revision, is SHA-256 addressed,
and is no larger than 64 KiB.

## Completion envelopes

Every technical phase ends with the machine-readable envelope defined in
[orchestration-control.md](orchestration-control.md). Record it through
`PHASE_COMPLETED`, or through the specialized review event where applicable.

Record `needs_input`, `blocked`, `failed`, or `cancelled` through
`PHASE_TERMINATED`; these outcomes must never be inferred from silence. A
`needs_input` envelope includes a complete `input_request`. The controller
creates an unannounced human-action gate, freezes only the affected ticket,
and keeps independent work eligible. After `INPUT_PROVIDED`, the next action
is `RESUME_VISIBLE_PHASE_WITH_INPUT`; record `PHASE_RESUMED` only after the
same visible thread has received the answer and is running again.

The controller rejects a completion when:

- the task never reached verified `RUNNING`;
- the phase key differs;
- actual model or effort differs from the routed request;
- required evidence is absent;
- phase token usage or an explicit unavailable measurement is absent;
- a non-completed outcome is mislabeled as completed.

Take actual model and effort from the explicit task-dispatch record. The
adapter injects those recorded values into the completion event; it must not
substitute a child's natural-language model name or UI label. A child's claim
is diagnostic text only and cannot create a routing gate or authorize a
rerun.

Store detailed logs outside the repository. Events contain concise evidence
and durable references, not full transcripts or secrets.

## Procedural gates

The following gates are enforced as code:

1. The orchestrator preflight is confirmed before supervision or dispatch.
2. Supervision is active before triage.
3. Every ticket is triaged before analysis.
4. Every analysis uses the matrix-selected setting.
5. Dependencies are consolidated only after all analyses are recorded.
   Consolidation includes file/domain/transformation inventories, an explicit
   schedule, a cumulative size budget, and rejects a parallel group with an
   unproven shared file or collision domain.
6. A matrix-required analysis approval is announced in the main conversation
   before it can be resolved.
7. Hard dependencies are merged before an execution pair starts.
8. Implementation and independent tests start as one atomic pair from the
   same base.
9. Both workers finish before functional verification.
10. Verification commands run through `verification_runner.py`, preserve the
    exact Git head, store complete logs outside the repository, and record
    `model_tokens = 0`. A failed run is recorded before technical failure
    adjudication.
11. Red, green, environment, and applicable Supabase/Auth evidence pass before
    review. Every verification explicitly classifies operational changes; a
    changed scheduler, workflow, provider, webhook, or runtime configuration
    requires a deterministic presence preflight and complete inventory before
    review.
12. A ticket pull request must target the train branch.
13. The first review is exhaustive and covers the verified ticket head.
14. The ticket PR is ready, then Codex, CI, Copilot, and available human
    findings are collected and reconciled into one exact-head ledger before
    remediation or merge. The ticket event records actual collection times,
    stable finding IDs, source counts matching that inventory, exactly one
    technical disposition per finding, CI and Copilot terminal states, and
    durable evidence.
    Copilot `unavailable` or `timed_out` is rejected before the recorded
    deadline, which is at least ten minutes after collection starts.
    A completed failed CI run may be recorded only when at least one CI
    finding is `accepted-deferred`, blocking, listed in `blocking_findings`,
    and has `pending` remediation. This transition enters remediation; it
    never makes the ticket mergeable.
15. A follow-up review requires an exhaustive baseline, remediation, and a
    structured delta classification with its own verification complexity.
16. A focused follow-up cannot use a setting above the trustworthy initial
    review ceiling.
17. A ticket receives at most two automatic remediation cycles. One third
    cycle requires a completed root-cause checkpoint, an explicit user input
    gate, and a controller-recorded run-scoped, ticket-scoped, single-use
    exception. The exception grants exactly one cycle and is consumed at
    dispatch.
18. The ticket cannot merge until review is clean, live exact-head GitHub
    checks pass, Copilot is terminal and dispositioned, and the exact human
    pre-merge gate, when applicable, is approved. Agent merges use only
    `merge_pull_request.py`; direct GitHub merge commands are unsupported.
19. Finalization freezes active work.
20. The final train pull request exists and is no longer draft before final
    review.
21. Final verification and final review cover the same exact PR head.
22. After the final review, a feedback window of at least ten minutes covers
    Codex, CI, Copilot, and human sources on that same exact head. Every
    collected finding receives exactly one technical disposition. `timed_out`
    and `unavailable` are accepted only after the recorded deadline;
    `not_configured` and `unavailable` require evidence.
23. Blocking final-review findings enter at most two routed final-remediation
    cycles; every updated PR head invalidates prior verification and review,
    GitHub feedback snapshot, and ledger, then receives targeted follow-up
    review unless material scope changed.
24. CI, Copilot disposition, finding ledger, token ledger, manual validation,
    attention points, task inventory, and the completion report are recorded
    before `RUN_COMPLETED`.
25. A phase above 50 million tokens, more than one context compaction, or a
    focused re-review above twice its initial-review usage opens a blocking
    cost checkpoint. Only `COST_ANOMALY_RESOLVED` may advance the run until a
    quality-neutral restart/continuation or user-approved quality tradeoff is
    recorded.
26. Merging the final train into the base branch additionally requires a
    `FINAL_BASE_MERGE_AUTHORIZED` event tied to the exact final head and a
    direct user decision reference. Approval mode never supplies this event.
    This authorization may be recorded after `RUN_COMPLETED`, because normal
    delivery reports complete before the user decides whether Codex should
    perform the final merge.

Approval modes alter only the two human-validation matrices. They do not
bypass any other procedural gate.

## Human action lifecycle

When classification requires human validation, the controller creates a gate
in `PENDING_UNANNOUNCED`. Its next action is `ANNOUNCE_HUMAN_GATE`.

The orchestrator publishes the complete `ACTION REQUIRED` packet in the main
conversation, then applies `GATE_ANNOUNCED`. Only a matching
`GATE_RESOLVED` event with the same gate and revision can approve or reject it.

This prevents an approval from being hidden in heartbeat output, inferred from
an unrelated user reply, or applied to a newer analysis or code head.

Material product, legal, architecture, credential, or environment information
uses the same announcement lifecycle through `HUMAN_INPUT_REQUESTED`. It is
not a validation bypass and it is not represented by a narrative "waiting for
information" status. The gate stores the exact question, reason, blocked
scope, independently continuing scope, accepted reply formats, and revision.
Only one human action is announced at a time, and it is mirrored as
`pending_human_action` in the canonical manifest.

## Next-action loop

The controller still exposes raw compact status for diagnostics:

```powershell
python scripts/train_controller.py status --state <run-manifest.json>
```

For routine execution, reduce it to a bounded wake packet:

```powershell
python scripts/control_plane_runner.py step --state <run-manifest.json>
```

The adapter follows this loop:

1. Apply only changed observations.
2. Run the control-plane step.
3. Stop immediately on `unchanged-suppressed`; this consumes no model wake.
4. Read only the newly generated decision packet.
5. Read its bounded `next_actions`.
6. Execute only the listed deterministic, adapter, or model task.
7. Record the outcome as one event using the returned revision.
8. Publish a user-visible transition when state materially changes.
9. Repeat until the controller returns a wait, human gate, blocker, checkpoint,
   or completion action.

An unchanged wait snapshot does not produce an event, manifest revision,
decision packet, detailed thread read, or model-written status. A non-LLM host
should perform task polling where the product exposes a stable API. Wake the
model only for a transition, task dispatch, technical decision, failure,
blocker, gate announcement, or report.

For a background wake-up, use the deterministic heartbeat projection:

```powershell
python scripts/train_controller.py heartbeat --state <run-manifest.json>
```

The watcher may pause or delete itself only when
`may_pause_or_delete_watcher` is true. `NOTIFY_ACTION_REQUIRED` must be
published as an ordinary main-conversation message. `CONTINUE_AUTOMATICALLY`
means the listed transition must execute before yielding. This prevents a
watcher from deciding conversationally that a train is finished while the
controller still requires verification, review, integration, or finalization.

## Final checks

Before yielding or completing, run:

```powershell
python scripts/train_controller.py check --state <run-manifest.json> --mode yield
python scripts/train_controller.py check --state <run-manifest.json> --mode completion
```

Continue to run the existing `control_guard.py` checks during the migration
period only when diagnosing a legacy manifest. Its duplicated `control`
projection is not a second authority and must not override the versioned
`procedure` state. New runs use the controller check as the sole lifecycle
gate; do not maintain two independently advancing state machines.

Do not declare success when the procedural controller rejects completion.

For merges, first run the controller permit and then the live guarded merger:

```powershell
python scripts/train_controller.py permit-merge --state <run-manifest.json> --action ticket --ticket-id <ticket> --head-commit <sha>
python scripts/merge_pull_request.py --state <run-manifest.json> --repo <owner/name> --action ticket --ticket-id <ticket>
python scripts/merge_pull_request.py --state <run-manifest.json> --repo <owner/name> --action final
```

The merge script verifies the live PR state, draft state, base/head branches,
exact head, and completed successful checks immediately before merging, then
records the resulting merge event. Do not replace it with `gh pr merge`.

In dry-run mode, dependency consolidation and every applicable human analysis
gate still apply, but the controller never offers an execution-pair action.
`DRY_RUN_EVIDENCE_RECORDED` closes the analyzed tickets only after analysis
reports, task inventory, session ledger, token status, and the consolidated
dry-run report are recorded. No branch, pull request, implementation, review,
or merge event is permitted or required.

## Recovery

On restart or main-conversation handoff:

1. Discover the canonical run by fingerprint.
2. Claim or explicitly take over its lease.
3. Read controller status and reconcile only active, queued, launch-unknown,
   or externally changed artifacts.
4. Apply new events for recovered observations.
5. Continue from `next_actions`.

Never rerun triage, analysis, implementation, or review merely to rebuild
conversation context. The event log and durable artifacts are the recovery
source of truth.

When the control-plane runner requests rotation, prepare a single-use handoff
through `run_registry.py prepare-handoff`, create exactly one fresh visible
successor with the bounded packet, and transfer ownership through
`accept-handoff`. The controller returns only
`COMPLETE_CONTROLLED_ORCHESTRATOR_HANDOFF` while a transfer is prepared. A
prepared but unaccepted handoff fails the yield guard.

## Adapter limitation

The bundled Python controller and runner cannot themselves create Codex tasks because the
desktop task API is supplied to the active Codex conversation rather than as a
stable local process API. Until such an API is available, the main conversation
must execute the controller's dispatch actions and transition-aware waits.
This remaining adapter is intentionally narrow: it does not poll unchanged
state, decide order, retries, gates, or completion, and it is rotated before
its conversation context becomes a dominant cost center.
