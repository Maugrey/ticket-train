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
- atomic implementation/acceptance-test pair creation;
- verification-before-review ordering;
- full-review-before-follow-up ordering;
- review and remediation limits;
- pull-request base and exact-head relationships;
- final pull request, verification, review, feedback, usage, and report gates;
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
- `RUNNING` requires the real task ID and verified user visibility;
- `LAUNCH_UNKNOWN` preserves an ambiguous outcome and prevents a retry;
- `BLOCKED` requires completed reconciliation evidence in the orchestration
  report.

Silence is not an event and never changes a phase to blocked. Only an
authoritative task transition or completion envelope changes phase state.

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

Store detailed logs outside the repository. Events contain concise evidence
and durable references, not full transcripts or secrets.

## Procedural gates

The following gates are enforced as code:

1. The orchestrator preflight is confirmed before supervision or dispatch.
2. Supervision is active before triage.
3. Every ticket is triaged before analysis.
4. Every analysis uses the matrix-selected setting.
5. Dependencies are consolidated only after all analyses are recorded.
6. A matrix-required analysis approval is announced in the main conversation
   before it can be resolved.
7. Hard dependencies are merged before an execution pair starts.
8. Implementation and independent tests start as one atomic pair from the
   same base.
9. Both workers finish before functional verification.
10. Red, green, environment, and applicable Supabase/Auth evidence pass before
    review.
11. A ticket pull request must target the train branch.
12. The first review is exhaustive and covers the verified ticket head.
13. Codex, CI, Copilot, and available human findings are reconciled into one
    exact-head ledger before remediation or merge.
14. A follow-up review requires an exhaustive baseline and remediation.
15. A focused follow-up cannot use a setting above the trustworthy initial
    review ceiling.
16. The ticket cannot merge until review is clean and the exact human
    pre-merge gate, when applicable, is approved.
17. Finalization freezes active work.
18. The final train pull request exists before final review.
19. Final verification and final review cover the same exact PR head.
20. After the final review, a bounded GitHub feedback collection covers
    Codex, CI, Copilot, and human sources on that same exact head. Every
    collected finding receives exactly one technical disposition. `timed_out`
    is accepted only after the recorded deadline; `not_configured` and
    `unavailable` require evidence.
21. Blocking final-review findings enter at most two routed final-remediation
    cycles; every updated PR head invalidates prior verification and review,
    GitHub feedback snapshot, and ledger, then receives targeted follow-up
    review unless material scope changed.
22. CI, Copilot disposition, finding ledger, token ledger, manual validation,
    attention points, task inventory, and the completion report are recorded
    before `RUN_COMPLETED`.

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

Read compact state and the allowed next actions with:

```powershell
python scripts/train_controller.py status --state <run-manifest.json>
```

The adapter follows this loop:

1. Read `next_actions`.
2. Execute only the listed deterministic or model task.
3. Record the outcome as one event using the returned revision.
4. Publish a user-visible transition when state materially changes.
5. Repeat until the controller returns a wait, human gate, blocker, checkpoint,
   or completion action.

An unchanged wait snapshot does not produce an event, manifest revision,
detailed thread read, or model-written status. A non-LLM host should perform
task polling where the product exposes a stable API. Wake the model only for a
transition, technical decision, failure, blocker, or report.

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

## Adapter limitation

The bundled Python controller cannot itself create Codex tasks because the
desktop task API is supplied to the active Codex conversation rather than as a
stable local process API. Until such an API is available, the main conversation
must execute the controller's dispatch actions. This remaining adapter is
intentionally narrow: it does not decide order, retries, gates, or completion.
