# Run Continuity, Ownership, and Human-Action Protocol

## Purpose

This protocol prevents duplicate trains, duplicate analysis, silent loss of
supervision, and hidden approval requests. It applies before any triage or
phase dispatch and after every interruption, handoff, context compaction, or
new main conversation.

## One canonical identity per train

Store every run under exactly one canonical root:

```text
$CODEX_HOME/ticket-train/runs/<run-id>/manifest.json
```

If `CODEX_HOME` is not defined, use:

```text
~/.codex/ticket-train/runs/<run-id>/manifest.json
```

Do not create alternative `ticket-trains`, project-local, date-only, or
conversation-specific manifest locations.

Compute the run fingerprint from these normalized values:

```text
canonical repository identity
train branch
ticket source locator
sorted selected ticket IDs
```

The conversation ID is not part of the fingerprint. A new main conversation
for the same repository, train branch, source, and ticket set is a resumption,
not a new train.

Before startup preflight, run `run_registry.py init`. An
`existing-active-run` result is a hard duplicate-run stop: load and reconcile
that manifest. Do not create another manifest, rerun triage, or redispatch a
phase.

When no canonical run exists, the registry also scans the deprecated
`$CODEX_HOME/ticket-trains` layout for the same train branch, source, and
ticket set. `legacy-run-found` is also a hard stop. Re-run initialization with
`--adopt-legacy` only to create the canonical reconciliation shell, then
inventory both old manifests, sessions, phase results, branches, and gates.
Select authoritative artifacts and reconcile them before dispatch. The
adoption opens a cost-anomaly checkpoint and does not authorize repeating
triage or analysis.

After discovery or adoption, bootstrap or load the procedural controller under
[controller-protocol.md](controller-protocol.md). Its event log and revision,
not the current conversation summary, determine which phases are complete and
which next actions are legal.

## Single orchestrator lease

Exactly one main conversation owns the run at a time. Persist an
`orchestrator_lease` with owner thread ID, heartbeat, and expiry.

When the matching run belongs to another unexpired conversation:

- direct the user to the owner conversation when it is usable; or
- request explicit takeover in the new conversation.

Never claim that the old conversation is unusable merely because it is not
currently producing messages. Reconcile its task and manifest state first.

An expired lease may be adopted after reconciliation. An unexpired lease may
be replaced only with explicit user authorization. Record every handoff and
mark the former owner superseded. Once superseded, the former orchestrator
must not dispatch new phases if it wakes later.

The exception is a budgeted controlled handoff authorized by the explicitly
invoked train workflow. It is not a takeover. The current owner prepares one
single-use token tied to a bounded decision packet, creates exactly one fresh
visible successor, and that successor atomically accepts the lease. The old
conversation becomes read-only immediately after acceptance. Follow
[control-plane-runner.md](control-plane-runner.md). A prepared transfer blocks
ordinary lifecycle actions and yielding until accepted or explicitly
cancelled after a conclusive launch failure.

Use:

```powershell
python scripts/run_registry.py init --repository <repo> --train-branch <branch> --source <source> --tickets <id,id> --orchestrator-thread <thread-id> --execution-mode <mode>
python scripts/run_registry.py init --repository <repo> --train-branch <branch> --source <source> --tickets <id,id> --orchestrator-thread <thread-id> --execution-mode <mode> --adopt-legacy
python scripts/run_registry.py claim --state <manifest> --orchestrator-thread <thread-id>
python scripts/run_registry.py claim --state <manifest> --orchestrator-thread <thread-id> --takeover --user-authorized-takeover
python scripts/run_registry.py prepare-handoff --state <manifest> --from-thread <thread-id> --reason budget --packet <decision-packet.json>
python scripts/run_registry.py accept-handoff --state <manifest> --to-thread <new-thread-id> --handoff-token <single-use-token>
```

## Durable evidence, not conversational memory

The canonical manifest must contain enough evidence to continue without
reconstructing technical work from chat history. Persist:

- run identity, configuration, source revision, train base and head;
- orchestrator lease and handoff history;
- every phase key, actual visible thread ID, visibility verification,
  completion envelope, and usage boundary;
- every phase-specific hidden-fallback authorization, actual hidden agent
  session ID, and explicit `visibility_verified = false` state;
- every compact context-packet descriptor and exact base/head;
- per-ticket analysis artifact and revision;
- dependency contract, consolidated digest, classification, and gate result;
- product decisions and proportionality-profile revision;
- branch, worktree, commit, pull-request, verification, review, and finding
  ledger references;
- active supervisor mode and watcher identity;
- the single pending human action, if any;
- duplicate sessions, failed attempts, and unmeasured usage.
- orchestrator usage plus every hidden session discovered from its activity
  log, including sessions not yet mapped to a procedural phase.
- control-plane decision packets, suppressed unchanged observations,
  orchestrator activity segments, and controlled handoffs.

A launch/client ID is not a visible thread ID. A phase may become `RUNNING`
only after the real thread ID has been resolved and a read/list operation has
verified that the user-visible task exists. Persist
`visibility_verified = true` and its timestamp.

When only a client ID exists under `FOREGROUND_WAIT`, keep the orchestrator
turn alive until the task materializes or launch reconciliation reaches a real
blocker. Never end the turn on a queued client ID: there is no durable wake-up
mechanism behind that state.

A hidden fallback may become `RUNNING` only after the user authorizes that
exact phase and the controller consumes `HIDDEN_FALLBACK_AUTHORIZED`. Persist
the actual agent session ID, do not set `visibility_verified = true`, and do
not reuse the authorization.

Do not infer `BLOCKED`, `FAILED`, or `RUNNING` from absence of new text. Use
the authoritative thread state, completion envelope, Git/GitHub evidence, and
manifest transition. Silence means only that there is no new observed event.

## Analysis reuse on resume

A completed analysis is a durable read-only artifact. Store its ticket ID,
revision, base commit, source revision, proportionality-profile revision,
thread ID, compact digest, validity conditions, completion status, and token
boundary.

On resume, classify each analysis:

- `REUSABLE`: source, base, profile, ticket scope, and relevant project rules
  are unchanged;
- `RECONCILE`: a predecessor, source revision, train head, or project rule
  changed but stable findings remain usable;
- `INVALID`: the ticket meaning, protected boundary, architecture constraint,
  or evidence is missing or materially obsolete.

For `REUSABLE`, do not call an analyzer.

For `RECONCILE`, send only changed evidence to the original analysis thread
or a compact replacement when that thread is unavailable. Preserve stable
sections and usage history.

For `INVALID`, run one scoped replacement analysis and record why the prior
artifact could not be reused. A new conversation, changed run ID, or missing
orchestrator summary is never by itself a reason to repeat analysis.

Before any expensive repeat, mark a cost anomaly and report the proposed
quality-neutral recovery. Duplicate analysis is prohibited until the registry
and prior artifact inventory have been reconciled.

## Supervision must exist before child dispatch

Resolve supervision after startup confirmation and before creating the first
child task. Select one mode:

1. `FOREGROUND_WAIT`: the orchestrator remains active and uses transition-aware
   task waits.
2. `EVENT_CALLBACK`: every visible child sends its terminal or input-required
   envelope to the verified current orchestrator task.
3. `BACKGROUND_WATCHER`: an external run-scoped process consumes zero model
   tokens, observes real task transitions, and uses the canonical manifest.

Do not launch a child while supervision is `INACTIVE`, `UNRESOLVED`, or has no
verified callback or zero-model watcher in background mode. A recurring Codex
heartbeat automation does not qualify because each run wakes a model. The user
must never need to create a scheduled task to make the train advance.

The deterministic watcher:

- polls task state with stored host IDs and cursors;
- reconciles GitHub and test artifacts;
- renews the orchestrator lease;
- wakes the model only for a transition, completion, failure, blocker,
  successor dispatch, or decision;
- writes `last_check_at` and `next_check_at` after every check;
- never launches a replacement merely because a task is silent.

At every changed wake, it first executes `train_controller.py heartbeat`, then
`control_plane_runner.py step`, and obeys the bounded decision packet. An
unchanged observation is suppressed without a model wake. It may pause or delete itself only when
`may_pause_or_delete_watcher` is true. It cannot convert "no currently running
child" into completion while verification, integration, final pull-request,
final review, GitHub feedback collection, reporting, or another automatic
controller action remains.

Verified visible child tasks are the normal proof that processing is active.
The watcher does not wake the orchestrator to repeat their running status. If
an active controller phase lacks a verified visible task ID, the projection is
`ACTIVE_WITH_VISIBILITY_GAP` and the adapter must repair or report it once.

Defaults:

```text
maximum internal supervision interval = 5 minutes
model-authored unchanged liveness = prohibited
unchanged liveness = deterministic product notification when required
human-action reminders = disabled unless the user explicitly opts in
```

Any required unchanged liveness update is derived from the manifest and costs
no child-thread reread or model wake. It states active tasks, last confirmed
transition, next check, and whether user action is required. Transition reports
remain immediate.

If background automation cannot be created or verified, stay in foreground
wait mode. If neither mode is reliable, stop before dispatch and report a
platform blocker. Never promise future follow-up without an active, recorded
mechanism.

`SUPERVISED_ACTIVE` is the only allowed yield while work is still queued or
running. It requires an active supervisor, verified schedule, no hidden human
gate, and a reconciled manifest. Run `control_guard.py check-yield` before
using it. Also require `control_plane_runner.py step` to return
`unchanged-suppressed` or `NO_MODEL_WAKE`; otherwise execute or hand off its
action first.

The procedural `train_controller.py check --mode yield` is authoritative for
new runs and rejects active work under `FOREGROUND_WAIT`. Only verified
`EVENT_CALLBACK` supervision or a zero-model `BACKGROUND_WATCHER` with a
persisted watcher ID can make `SUPERVISED_ACTIVE` survive the end of the
current turn.

## Human actions are first-class state

Human approval is requested only in the main orchestrator conversation. Child
threads may report evidence or questions, but the orchestrator owns the
decision surface and durable gate state.

The same rule applies to missing information. Product choices, legal data,
architecture decisions, credentials, and environment inputs must be recorded
through `HUMAN_INPUT_REQUESTED`; a free-form note or child `needs_input`
conclusion is not enough. The controller pauses only the affected ticket and
keeps unrelated eligible work moving.

Before yielding for a human decision:

1. Finish safe unrelated automatic work.
2. Publish a normal main-conversation message headed `ACTION REQUIRED`.
3. Persist the exact gate with `notification_status = ANNOUNCED`.
4. State explicitly whether verified visible tasks remain active.
5. If unrelated work remains, keep its supervisor active and run the yield
   guard.
6. If the gate is the only remaining action, pause or delete the watcher,
   apply `SUPERVISION_PAUSED_FOR_HUMAN_GATE`, run the yield guard, and stop.

The message must include:

- ticket or train and exact revision/head;
- gate type and why it applies;
- concise implementation or analysis summary;
- material functional, architecture, data, contract, security, and operations
  impacts, with explicit no-impact statements where applicable;
- automated evidence and review result;
- residual risk and proportionality decision;
- pull-request or durable diff link when code exists;
- what is blocked and what continues;
- the exact accepted replies.

Never hide an approval request inside an automation transcript, nested status,
child report, or `DONT_NOTIFY` heartbeat. A user must know what to decide and
have enough evidence to decide without opening a child conversation.

Persist one `pending_human_action` object with:

```text
gate_id
gate_type
ticket_id or train
revision
reason
decision_summary
evidence_summary
blocked_scope
continuing_scope
accepted_replies
notification_status = ANNOUNCED
announced_at
last_reminder_at
```

When no technical work remains, the one `ACTION REQUIRED` message must say
`Active visible tasks: none` and `Waiting only for your response`; no recurring
watcher or reminder runs by default. If the user explicitly requests a
reminder, it begins with `ACTION REQUIRED`, repeats the compact decision, and
uses a deterministic product notification. Never wake the full orchestrator
merely to reproduce unchanged gate text. When the user responds, resolve the
exact gate revision, clear pending state, reconfigure supervision, and continue
automatically.

For an information request, also repeat the exact question and accepted answer
formats. Record the answer through `INPUT_PROVIDED`. If the request came from
a phase, send the answer to that same visible thread and record
`PHASE_RESUMED`; if it was orchestrator-originated, restore the ticket's prior
eligible status. Never require the user to discover which child asked or to
answer in that child conversation.

## Resume report

After adoption or restart, publish this before continuing:

```markdown
## Train resumed

- Canonical run:
- Previous orchestrator:
- Current owner:
- State reconciled at:
- Reused completed phases:
- Targeted reconciliation required:
- Duplicate or ambiguous attempts:
- Active visible tasks:
- Pending human action: none | exact gate and accepted replies
- Next automatic action:
- Supervision mode and next check:
- Token/cost impact of the interruption:
```

If a prior phase was duplicated, select one authoritative artifact, retain all
usage in the ledger, quarantine write branches from unauthorized or invisible
attempts, and report the disposition. Do not discard trustworthy read-only
evidence solely because its session became non-authoritative.

## Non-bypassable continuity stops

Stop before dispatch when any of these is true:

- matching active run discovery was not performed;
- more than one active run matches and no authoritative manifest is selected;
- another unexpired orchestrator lease exists without explicit takeover;
- the canonical manifest is missing or stale;
- a visible task has only a client/launch ID and no verified thread ID;
- active work has no verified foreground or background supervision;
- an approval is required but its complete main-thread notification is not
  durably recorded;
- a repeat analysis is proposed without reuse classification and cost-anomaly
  review.

Approval bypass modes bypass only the configured human gate. They do not
bypass run discovery, ownership, visibility, supervision, evidence reuse, or
the deterministic guards.
