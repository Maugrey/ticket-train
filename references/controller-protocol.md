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
- specification-alignment and scope-origin validation plus the non-bypassable
  per-deviation decision gate;
- model-routing matrix lookup and conformance;
- routing-policy version, phase-local route comparisons, mechanical fast-path
  proof, and scoped Max authorizations;
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
- the list of allowed next actions;
- for the `unity-mcp-local` profile, the global editor limit, environment
  readiness, exclusive phase/operation leases, and refusal to launch an
  editor-backed phase without a matching ready slot.

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
The canonical lease must contain the real user-visible Codex task ID. Agent
paths such as `/root` or `/root/review` are invalid orchestrator identities.

The adapter reads controller state through
`scripts/control_plane_runner.py`, not by loading the full manifest into the
conversation. The runner emits a maximum 16 KiB decision packet, suppresses
unchanged waits, and requires a controlled orchestrator handoff at its context
budget. Read [control-plane-runner.md](control-plane-runner.md).

Every emitted next action includes one authoritative `executor_kind`:
`deterministic`, `adapter`, or `technical-model`. The adapter must wrap the
action with `orchestration_metrics.py start-action` and `finish-action`, and
record every actual wake. The classifier fails closed when an action is absent
from or duplicated in the taxonomy; prompts must not override it.
The bounded decision packet also emits `authorized_handler`. Only actions whose
handler is `scripts/unity_slot_adapter.py` may invoke that resource adapter;
`main-thread-controller-adapter` means apply the named controller transition
first.

The response/runtime adapter is `scripts/continuation_adapter.py`; its command
contract is in [control-plane-runner.md](control-plane-runner.md). The
`RUNTIME_OBSERVED` event accepts `owner_thread_id`, `snapshot_reference`, and
`snapshot_sha256` alongside the standard event ID/revision. It reads raw
product polls and records only observed current phase identities. It never
marks a technical phase completed automatically. Runtime observations remain
permitted during a cost checkpoint because observing is not new execution.
Launch/resume receipts invalidate previous liveness evidence. A visible
`RUNNING` phase without fresh evidence cannot pass the yield guard; observed
terminal phases produce `COLLECT_OBSERVED_PHASE_RESULTS`, not a replacement
dispatch. `OBSERVE_ACTIVE_PHASES` is a bounded task-tool adapter action.

Events submitted through the continuation adapter check the caller's
`owner_thread_id` inside the controller write lock, without altering the event
payload or breaking replay of events previously applied by the CLI. Phase children
deliver artifacts and envelopes to this owner; they do not apply events or
silently take over orchestration. Existing scope, approval and route checks
still apply to every event, including resumed runs.

## Bootstrap

Create or adopt the canonical run with `run_registry.py`, then bootstrap the
procedure once:

```powershell
python scripts/train_controller.py bootstrap `
  --state <run-manifest.json> `
  --base-branch <main-or-master> `
  --approval-mode <standard|auto-analysis|auto-merge|full-auto>
```

For a Unity project using local AI Game Dev MCP, also pass:

```powershell
  --environment-profile unity-mcp-local `
  --unity-repository <absolute-unity-git-root> `
  --max-unity-editors <count>
```

The editor count defaults to three and may be overridden by the user's launch
prompt. The controller supports only `mcp_mode = local` for this profile.

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
`EVENT_CALLBACK` requires a verified callback target equal to the current
orchestrator owner and injects that callback contract into each visible child
dispatch. Record `callback_target_thread_id` and `callback_contract_reference`
on each running phase's `PHASE_LAUNCH_OBSERVED`, citing the actual launch or
follow-up message that carried the contract. A global `callback_verified`
flag alone cannot prove delivery to a child. Missing or stale per-child proof
returns `RECONFIGURE_EVENT_CALLBACKS_FOR_CURRENT_OWNER`; deliver the contract
and update the same phase observation, never create a replacement task.
`BACKGROUND_WATCHER` requires both a watcher ID and deterministic
evidence that it consumes zero model tokens. A recurring Codex automation that
opens a model turn cannot satisfy this requirement.
Legacy or resumed supervision without that evidence yields
`REPLACE_MODEL_WAKING_WATCHER` before any technical action.
An orchestrator handoff invalidates the old callback target; the controller
blocks other actions until every active visible child and the supervision
record target the new owner.

Silence is not an event and never changes a phase to blocked. Only an
authoritative task transition or completion envelope changes phase state.

Every dispatch also carries a descriptor produced by `context_packet.py`.
The packet uses `fresh-compact-v1`, includes no inherited turns, names the
exact base/head and proportionality-profile revision, is SHA-256 addressed,
and is no larger than 64 KiB.

Under `unity-mcp-local`, every technical dispatch event also records one
`unity_requirement`: `none`, `editor-read`, `editor-write`, `playmode-ui`, or
`build`. `TICKET_TRIAGED` records the requirement for full analysis;
`EXECUTION_PAIR_DISPATCHED` records independent implementation, acceptance,
and deterministic-verification requirements. The controller emits
initialization, acquisition, wait, and release actions and rejects `QUEUED`,
`RUNNING`, or resumed editor-backed phases without an active matching lease.
Execute these resource transitions with `unity_slot_adapter.py`, never by
prompt-authored editor lifecycle logic. See
[unity-mcp-local.md](unity-mcp-local.md).

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

An environment failure before any visible task exists is different. A
`HUMAN_INPUT_REQUESTED` event may attach to a `BLOCKED` phase only when that
phase has neither a client task ID nor a real task ID. The gate records
`resume_mode = prelaunch-retry`; after `INPUT_PROVIDED`, the controller restores
the prior ticket state and rearms the same phase as `INTENT_RECORDED`. It never
emits `RESUME_VISIBLE_PHASE_WITH_INPUT` for a task that was never created and
never duplicates the technical phase solely to apply the answer.

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

For bounded contract validations, `PLAN_CONTRACT_RESULT_COLLECTED` is the
atomic collector transaction emitted by `continuation_adapter.py collect-contract`.
It verifies the actual result hash and observed completed task, then applies
`PHASE_COMPLETED` and `PLAN_CONTRACT_VALIDATION_RECORDED` in a single revision.
Both transitions retain their existing checks. An invalid verdict cannot leave
a partially completed phase. Other phases retain their specialized events.

Store detailed logs outside the repository. Events contain concise evidence
and durable references, not full transcripts or secrets.

## Procedural gates

The following gates are enforced as code:

1. The orchestrator preflight is confirmed before supervision or dispatch.
2. Supervision is active before triage.
3. Every ticket is triaged before analysis.
4. Every analysis uses the matrix-selected setting. A confirmed classification
   that is not covered by the dispatched route triggers one targeted route
   validation, never a second complete analysis.
   When that targeted validation fails because its compact packet or a bounded
   source input was missing, record `ANALYSIS_ROUTE_VALIDATION_RECONCILED` with
   the corrected compact packet. Preserve the failed attempt as superseded and
   dispatch a new targeted validation; do not rerun the complete analysis.
5. Dependencies are consolidated only after all analyses and any targeted
   route validations are recorded.
   Consolidation includes file/domain/transformation inventories, an explicit
   schedule, a cumulative size budget, and rejects a parallel group with an
   unproven shared file or collision domain.
6. Every complete analysis records exact specification alignment or a complete
   deviation inventory, plus a scope assessment whose active classification is
   based on authorized scope only. Any missing precision, interpretation,
   reduction, behavior change, or expansion opens a `specification_deviation`
   gate before route validation, dependency consolidation, contract
   validation, implementation, or acceptance-test authoring. The specialized
   `SPECIFICATION_DECISIONS_RECORDED` event records one explicit selected
   option per deviation and one decision per expansion. No approval mode
   bypasses this gate.
7. A matrix-required analysis approval is announced in the main conversation
   before it can be resolved.
8. Hard dependencies are merged before an execution pair starts. `HIGH` and
   `MAXIMUM` analysis complexity also require a routed one-turn plan-contract
   validation. Implementation uses the validated residual implementation
   complexity; acceptance tests use their dedicated verification matrix.
9. Implementation and independent tests start as one atomic pair from the
   same base.
10. Both workers finish before functional verification. The controller then
   requires `EXECUTION_PAIR_INTEGRATED`, with exact implementation/test commits,
   combined implementation-branch head, and deterministic integration evidence.
11. Verification commands run through `verification_runner.py`, preserve the
    exact Git head, store complete logs outside the repository, and record
    `model_tokens = 0`. A failed run is recorded before technical failure
    adjudication.
12. Red, green, environment, and applicable Supabase/Auth evidence pass before
    review. Every verification explicitly classifies operational changes; a
    changed scheduler, workflow, provider, webhook, or runtime configuration
    requires a deterministic presence preflight and complete inventory before
    review.
13. A ticket pull request must target the train branch.
14. The first review is exhaustive and covers the verified ticket head.
15. The ticket PR is ready, then Codex, CI, Copilot, and available human
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
16. A follow-up review requires the latest trustworthy exhaustive baseline,
    remediation, and a
    structured delta classification with its own verification complexity.
17. A focused follow-up cannot use a setting outside the phase-local
    compatibility ceiling of the trustworthy full
    review ceiling.
18. A ticket receives at most two automatic remediation cycles. One third
    cycle requires a completed root-cause checkpoint, an explicit user input
    gate, and a controller-recorded run-scoped, ticket-scoped, single-use
    exception. The exception grants exactly one cycle and is consumed at
    dispatch.
    Final-train remediation has one separate deterministic exception: after
    two cycles, a third cycle is allowed only for an exact-head `test-defect`
    routed `LOW/LOW` whose dispatch declares `test_only_remediation: true` and
    `production_files_modified: false`. It cannot change production files and
    cannot be repeated.
19. The ticket cannot merge until review is clean, live exact-head GitHub
    checks pass, Copilot is terminal and dispositioned, and the exact human
    pre-merge gate, when applicable, is approved. Agent merges use only
    `merge_pull_request.py`; direct GitHub merge commands are unsupported.
20. Finalization freezes active work.
21. The final train pull request exists and is no longer draft before final
    review.
22. Final verification and final review cover the same exact PR head. Ticket
    review settings become final-review floors only with explicit evidence
    that integration invalidated the review or affected its protected surface.
23. After the final review, a feedback window of at least ten minutes covers
    Codex, CI, Copilot, and human sources on that same exact head. Every
    collected finding receives exactly one technical disposition. `timed_out`
    and `unavailable` are accepted only after the recorded deadline;
    `not_configured` and `unavailable` require evidence.
24. Blocking final-review findings enter at most two routed final-remediation
    cycles; every updated PR head invalidates prior verification and review,
    GitHub feedback snapshot, and ledger, then receives targeted follow-up
    review unless material scope changed. A final-remediation phase that
    changes only the final PR's metadata may use
    `FINAL_PR_METADATA_REMEDIATION_RECORDED` instead of fabricating an empty
    repository commit: it preserves the exact-head code verification, records
    a no-repository-files proof, and invalidates final review, feedback, and
    ledger for a focused same-head follow-up review.
25. CI, Copilot disposition, finding ledger, token ledger, manual validation,
    attention points, task inventory, and the completion report are recorded
    before `RUN_COMPLETED`.
26. A phase above 50 million tokens, more than one context compaction, or a
    focused re-review above twice its initial-review usage opens a blocking
    cost checkpoint. Only `COST_ANOMALY_RESOLVED` may advance the run until a
    quality-neutral restart/continuation or user-approved quality tradeoff is
    recorded.
27. Merging the final train into the base branch additionally requires a
    `FINAL_BASE_MERGE_AUTHORIZED` event tied to the exact final head and a
    direct user decision reference. Approval mode never supplies this event.
    This authorization may be recorded after `RUN_COMPLETED`, because normal
    delivery reports complete before the user decides whether Codex should
    perform the final merge.

Approval modes alter only the two human-validation matrices. They do not
bypass any other procedural gate, including specification-deviation approval.

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

Specification decisions use this announcement visibility but not generic
`GATE_RESOLVED`. Apply `SPECIFICATION_DECISIONS_RECORDED` with the assessment
revision, user-decision reference, selected option for every deviation,
selected variant for every expansion, active-scope revision, and amended
contract revisions. The resulting specification may raise classification and
trigger one targeted route validation. Rejection or deferral of optional scope
preserves the minimal classification and records the expansion as inactive.

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
3. On `unchanged-suppressed`, resume the wait; do not end the current turn
   unless `turn_control.may_end_turn` is true.
4. Read a newly generated decision packet or reuse an `action-pending` packet.
5. Read its bounded `next_actions`.
6. Execute only the listed deterministic, adapter, or model task.
7. Record the outcome as one event using the returned revision.
8. Publish a user-visible transition when state materially changes.
9. Repeat through automatic successors. A wait is not a final response:
   foreground waiting continues inside the current turn. Only the yield guard
   authorizes ending the turn.

The persisted consolidation order and collision-free parallel groups govern
execution-pair dispatch in both `next_actions` and event validation. Sequential
tickets wait for the predecessor's entire validation/merge lifecycle, not
merely its coding phase. An unrelated active phase or announced human gate
must not hide another already-authorized ticket's verification, remediation,
or merge. Existing execution pairs are preserved when upgrading a run.

### Controlled continuation around a blocked predecessor

`BLOCKED_TICKET_CONTINUATION_ISOLATED` is the sole exception to the sequential
barrier for a predecessor that remains `BLOCKED`. It is a recovery transition,
not a completion, retry, remediation, merge, or approval of the blocked
ticket. It preserves its failure evidence, remediation counter, execution
history, branches, and terminal status exactly as recorded.

The event is accepted only when all of the following are recorded and verified
by the controller:

- the blocked ticket has a preserved `VERIFICATION_FAILURE_CLASSIFIED` event
  with `next_step = block`;
- exactly one released ticket is already `READY_FOR_IMPLEMENTATION`, has no
  hard dependency on the blocked ticket, and all of its own hard dependencies
  are `MERGED_INTO_TRAIN`;
- the supplied train head equals the controller's current train head;
- no technical phase is active;
- the collision inventory matches both tickets exactly, and every shared
  resource has a distinct evidence reference proving it is `quiescent`; and
- an explicit user-decision reference authorizes this procedural recovery.

The recorded exception releases only that named successor. Other sequential
tickets remain blocked. It does not authorize an additional diagnostic or a
ninth remediation cycle; reopening a blocked ticket requires a separate,
future controller transition backed by its own explicit user decision.

Use `verification_adapter.py` for the test-command-to-event transition. A
timeout or process-launch failure records `exit_code: null`, never an invented
exit code; it is valid failed evidence, not a passing test or a controller
dead end. Every passed command still requires exit code zero.

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
`may_pause_or_delete_watcher` is true. The projection distinguishes:

- `WAIT_FOR_VISIBLE_TASK_TRANSITION`: verified visible child tasks are active;
  keep deterministic supervision and emit no periodic model status;
- `WAIT_FOR_HUMAN_WITHOUT_WATCHER`: the complete gate was already announced,
  no technical phase or automatic action remains, and the watcher must pause
  or delete itself before the orchestrator yields;
- `ESCALATE_VISIBILITY_GAP`: controller state claims active work but no
  verified user-visible task identifies it;
- `CONTINUE_AUTOMATICALLY`: execute the listed transition before yielding.

After successfully pausing or deleting the watcher for a pure human wait,
apply `SUPERVISION_PAUSED_FOR_HUMAN_GATE`. While that state is active the only
allowed action is `AWAIT_HUMAN_GATE`. Resolving the gate makes
`CONFIGURE_SUPERVISION_BEFORE_DISPATCH` mandatory before work resumes. Never
schedule a recurring model heartbeat whose only purpose is to repeat an
approval request. This prevents a watcher from deciding conversationally that
a train is finished while the controller still requires verification, review,
integration, or finalization.

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

If an analysis or routed-analysis validation finishes after dependency
consolidation, it must become `READY_FOR_IMPLEMENTATION` immediately. For a
legacy manifest stranded at `ANALYZED`, the controller returns
`RECORD_ANALYSIS_READINESS_RECONCILIATION`; apply
`ANALYSIS_READINESS_RECONCILED` for that exact analysis revision. Do not repeat
dependency consolidation or analysis to repair this ordering condition.

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
