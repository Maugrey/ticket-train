---
name: ticket-train
description: Explicitly orchestrate bounded, cost-controlled trains of implementation tickets from a user-provided source. Use when the user invokes $ticket-train to triage tickets in visible threads, apply a product proportionality profile, reconcile plans, run implementation and independent acceptance-test authoring in parallel branches and worktrees, enforce red/green functional and environment gates including Supabase/Auth/RLS when applicable, route model effort strictly, run one exhaustive review plus grouped remediation and targeted re-review, validate the final train pull request, report manual tests and attention points, measure per-session and per-phase token usage, enforce human gates, and stop at ticket-count or review-surface checkpoints. Do not use for ordinary one-off coding tasks or without explicit invocation.
---

# Ticket Train

Orchestrate a bounded, reviewable implementation train while keeping ticket analysis, implementation, and review contexts separate.

## Load the workflow

Read these references before starting:

- [controller-protocol.md](references/controller-protocol.md) for the
  authoritative procedural state machine, event contract, next-action loop,
  and adapter boundary. Apply it before interpreting the narrative workflow.
- [control-plane-runner.md](references/control-plane-runner.md) for bounded
  decision packets, unchanged-wait suppression, orchestrator activity budgets,
  and controlled handoffs to fresh visible orchestrator tasks.
- [workflow.md](references/workflow.md) for lifecycle, concurrency, branches, gates, and stopping rules.
- [orchestration-control.md](references/orchestration-control.md) for visible-thread launch, ambiguous outcomes, active supervision, durable recovery, and yield gates.
- [run-continuity.md](references/run-continuity.md) for canonical run identity,
  single-orchestrator ownership, restart adoption, analysis reuse, mandatory
  supervision, and visible human-action requests.
- [ticket-sources.md](references/ticket-sources.md) for source resolution and ticket normalization.
- [criticality.md](references/criticality.md) for intrinsic criticality, complexity, and human validation matrices.
- [analysis-policy.md](references/analysis-policy.md) for parallel conditional analysis, dependency consolidation, and reconciliation.
- [model-routing.md](references/model-routing.md) for fast triage and model/reasoning matrices.
- [usage-reporting.md](references/usage-reporting.md) for exact-if-available token accounting.
- [report-template.md](references/report-template.md) for required main-thread reports.
- [efficiency-policy.md](references/efficiency-policy.md) for proportionality,
  scope budgets, compact contracts, review-pass limits, deterministic
  supervision, log handling, and token-cost anomaly controls.
- [verification-policy.md](references/verification-policy.md) for independent
  parallel acceptance-test authoring, red/green evidence, functional readiness,
  environment parity, Supabase/Auth/RLS validation, and manual-test boundaries.

Before any write-enabled implementation or pull-request review, also read:

- [review-policy.md](references/review-policy.md) for independent review, GitHub comments, Copilot findings, remediation, and merge checks.

Do not partially read a selected reference.

## Resolve the run before acting

Treat explicit invocation of this skill as authorization to create the separate
Codex threads, ticket pull requests, and final train pull request required by
the requested live train, but not as authorization to merge the train into
`main` or `master`.

Invoke explicitly with `$ticket-train`. Accept natural-language configuration around that invocation, for example:

```text
Use $ticket-train in dry-run mode for the first three tickets in docs/backlog.md
using standard approvals.
```

Before triage, resolve and echo:

1. Ticket source.
2. Ticket selection and order.
3. Approval mode.
4. Dry-run or live run.
5. Repository and base branch.
6. Train branch name.
7. Reasoning cap, defaulting to `xhigh`.
8. Analysis policy, defaulting to `parallel-conditional`.
9. Token usage reporting, fixed to `exact-if-available` unless the user explicitly disables it.
10. Routing enforcement, fixed to `strict`.
11. Coordination policy, fixed to `compact-control-plane`.
12. Control-plane runner, fixed to `deterministic-decision-packets`.
13. Orchestrator rotation, fixed to `automatic-budgeted-handoff` with a 10M
    soft warning, 25M hard token limit, 50 model wakes, 500 tool calls, one
    context compaction, and 16 KiB decision packets.
14. Actual orchestrator model and reasoning effort, when observable.
15. Recommended orchestrator model and effort under the startup preflight.
16. Proportionality profile revision and its material assumptions.
17. Train size budget and current estimated review surface.
18. Review-pass budget, fixed to one complete pass and at most two grouped
    remediation/follow-up cycles per stable scope.
19. Cost-control policy, fixed to `strict-quality-preserving`.
20. Verification policy, fixed to `parallel-independent-red-green`.
21. Environment tiers required by the selected tickets.

Before publishing the startup preflight, use
[run_registry.py](scripts/run_registry.py) to discover or create the canonical
run under `$CODEX_HOME/ticket-train/runs`. The repository, train branch,
source, and ticket set define the run fingerprint; the conversation ID does
not. If a matching active run exists, adopt it under
[run-continuity.md](references/run-continuity.md) instead of starting another
run. Never repeat triage or analysis merely because orchestration moved to a
new conversation. If the registry reports a deprecated-layout manifest, adopt
it into the canonical reconciliation shell and inventory all prior artifacts
before dispatching anything.

Immediately after creating or adopting the canonical manifest, bootstrap
[train_controller.py](scripts/train_controller.py) with the resolved base
branch and approval mode. From that point onward, the controller is the
authoritative lifecycle. Before every dispatch or transition, read its
`next_actions`; execute only an allowed action; then record the observed result
as an idempotent, revision-checked event. A prompt, child-thread conclusion, or
orchestrator inference cannot authorize a transition that the controller
rejects.

Build every phase handoff with [context_packet.py](scripts/context_packet.py).
Pass the returned descriptor to the dispatch event. The controller rejects a
packet above 64 KiB, a packet without an exact base/head and profile revision,
or any packet that includes conversation history. Do not manually recreate a
full prior conversation in a fresh task.

Require the user to provide or confirm the ticket source and selection. Never invent a source by scanning TODOs, comments, or arbitrary repository files.

If the user did not specify an approval mode, pause before analysis and ask for one:

- `standard`
- `auto-analysis`
- `auto-merge`
- `full-auto`

Interpret these modes only as human-gate configuration. Never bypass automated analysis, automated tests, independent automated review, remediation of blocking findings, the five-ticket checkpoint, repository guidance, tool permissions, or the prohibition on autonomous train-to-base merge.

If the user did not specify dry-run versus live execution, default to live only when they clearly asked to implement. Otherwise ask.

Keep the resolved configuration fixed for the run. Apply a later change only after an explicit user instruction, then report the change in the main thread.

Treat authorization for `max` or `ultra` as separate from approval mode. Ask only when routing requires it, unless the user already granted an explicit scope.

Before triage, publish the orchestrator startup preflight required by
[model-routing.md](references/model-routing.md). Show the recommended and
current settings, the orchestration criteria that drove the recommendation,
and the cost/quality implication. Ask whether to continue with the current
conversation. This confirmation is mandatory in every approval mode.

## Distinguish dry-run from live execution

In dry-run mode:

- resolve and normalize tickets;
- perform fast routing triage;
- create read-only analysis threads for every selected ticket using the parallel-conditional rolling window;
- produce analysis, dependency, collision, scheduling, validation-gate, and model-routing reports;
- produce the proportionality profile, scope-budget assessment, compact
  implementation-contract simulation, and review-pass simulation;
- produce a verification-contract, test-branch, red/green, environment-parity,
  and functional-readiness simulation;
- report exact-if-available token usage for orchestration and analysis;
- simulate branch, pull-request, and train ordering.

Do not modify files, create or switch branches or worktrees, commit, push, create pull requests, post review comments, merge, update ticket sources, or start implementation workers. There is no implementation diff to review.

In live mode, execute the complete workflow subject to all gates and permissions.

## Respect project truth

Before delegating work:

1. Read all applicable `AGENTS.md` files and mandatory project documentation.
2. Treat project architecture, conventions, tests, security rules, and language rules as authoritative.
3. Keep the ticket source read-only unless the user separately authorizes source updates.
4. Write user-facing reports in the user's language.
5. Follow repository language and attribution rules for code, documentation, branches, commits, and pull requests.

## Orchestrate with separate threads

Use separate user-visible Codex threads when the thread-management tools are available:

- one read-only batch triage thread with an explicit `Terra/H` override;
- one analysis thread per ticket;
- one implementation thread per ticket branch/worktree;
- one independent acceptance-test thread per active implementation, using a
  separate branch and worktree from the same train commit;
- one independent read-only review thread per ticket pull request.
- one fresh compact remediation thread per grouped correction cycle, retaining
  the ticket branch and worktree;
- one fresh focused re-review thread per remediation cycle;
- one independent read-only final-train review thread at live finalization;
- one final-train remediation thread only when integrated findings require it.

Keep the main thread as a compact control plane. Persist the run mapping and
usage snapshots outside the repository, and retain only decisions, gates,
state transitions, compact reports, and durable references in the main
conversation. Do not copy raw logs, full child reports, or unchanged progress
snapshots into the orchestrator context.

After every changed observation, run
`scripts/control_plane_runner.py step`. Read only a newly written bounded
decision packet and execute its declared wake class. A result of
`unchanged-suppressed` means no model wake, no detailed task read, and no
user-facing status. Never run a prompt-authored polling or heartbeat loop.

The main thread is an adapter, not the scheduler. It must not decide the next
phase from conversational memory. Use `train_controller.py status`, execute
the returned action through the available task/Git/GitHub tool, and feed the
result back through `train_controller.py apply`. Keep technical judgment in
the routed visible phase threads and procedural judgment in the controller.
Include the controller revision and update time in material status reports;
reconcile immediately if tasks, Git, or GitHub advanced beyond that state.

Maintain exactly one orchestrator lease and one canonical manifest. Resolve
and verify foreground transition waiting or a run-scoped deterministic
background watcher before the first child dispatch. A background watcher is
part of the workflow, not a recovery action the user must request. Do not wake
a model for periodic liveness. Publish transitions immediately; when the host
requires unchanged liveness, emit it from a deterministic product notification
without replaying the orchestrator context. At every changed watcher
observation, run `control_plane_runner.py step` and wake a model only when its
packet requires one.

`FOREGROUND_WAIT` is valid only inside the current orchestrator turn. It never
survives a final response. When any phase is queued, running, or launch-unknown
under this mode, keep calling transition-aware task waits and advancing the
controller until the phase is captured or an actual gate/blocker exists. A
nonzero foreground yield check is a hard stop, not a status that may be
explained away in prose.

Follow [orchestration-control.md](references/orchestration-control.md) for
every phase dispatch. Record a unique phase key and launch intent before
creation, reconcile asynchronous or ambiguous outcomes before retrying, and
keep the run supervised until it reaches an allowed human gate, blocker,
completion, or checkpoint. The same orchestrator conversation need not remain
active after durable supervision is verified.

When task creation returns only `clientThreadId`, record `QUEUED`: this is not
a task launch and not a user-visible thread. Resolve the real `threadId`, read
or list it to prove visibility, record `RUNNING`, and start a wait in the same
turn. Never send a final response merely saying that queued work was launched.
For read-only analysis and review, prefer a visible local-environment task that
inspects exact Git refs; do not request a new worktree when it adds only a
materialization queue and no isolation benefit.

Treat model/effort routing as controller evidence from the explicit task-tool
dispatch, not as a natural-language self-identification by the child. Inject
the routed values into the completion event from the recorded dispatch. A
child UI label such as `standard` or `GPT-5 (Codex)` is not evidence of a
routing mismatch and must never create a human gate or duplicate execution.

Use [train_supervisor.py](scripts/train_supervisor.py) to reconcile changed
thread snapshots, GitHub status, and test artifacts into the manifest. Do not
use LLM turns for unchanged polling or raw-log status extraction.
Treat supervisor observations as inputs to controller events; they do not
advance lifecycle state by themselves.

Record the current orchestrator segment with `control_plane_runner.py
record-activity` at material checkpoints. Before another ordinary model
action, rotate to one fresh visible orchestrator when any hard budget is
reached. Prepare and accept that single-owner transfer through
`run_registry.py prepare-handoff` and `accept-handoff`; pass only the bounded
decision packet and single-use token. Never fork or replay the old
conversation. Count every segment in orchestration usage.

Keep a mapping from ticket to source locator, analysis base commit, intrinsic criticality, complexity, conditional assumptions, validity conditions, reconciliation state, routing decisions, routing conformance, dependencies, worker thread, reviewer thread, branch, worktree, pull request, usage snapshots, gate state, and train commit. Also track the final train pull request, reviewed head, CI and Copilot state, final finding ledger, manual validation plan, and code/application attention points.

Also track the proportionality profile revision, ticket and cumulative train
size budgets, compact implementation contract, plan-contract validation,
internal slices, worker self-review, complete-review count, remediation-cycle
count, log artifacts, all authoritative and duplicate sessions, unmeasured
phases, and cost-anomaly checkpoints.

Also track the run fingerprint, orchestrator lease and handoffs, supervision
mode and watcher ID, verified visible thread IDs, durable analysis artifacts,
reuse/reconciliation decisions, and the single pending human action.

Track the verification-contract revision, execution-pair base, independent
test thread/branch/worktree/commit/pull request, acceptance-coverage map,
baseline-red and integrated-green evidence, environment fingerprints,
Supabase/Auth gate when applicable, validation failures, regression tests, and
manual-only automation justifications.

Never use `fork_thread` for a train phase. Do not replace user-visible ticket
threads with hidden subagents unless the user explicitly permits that fallback
for the affected phase after the visible-thread failure is reported. Approval
modes do not imply this permission.

Limit active work:

- maximum five simultaneous analysis threads;
- maximum two simultaneous implementation threads;
- maximum one independent acceptance-test thread per active implementation;
- maximum two simultaneous implementation/test execution pairs;
- maximum one integration into the train at a time;
- maximum five tickets integrated into a train before a mandatory checkpoint.

The five-ticket limit is not the only checkpoint. Apply the cumulative file,
migration/data-transformation, structural-domain, and epic thresholds from
[efficiency-policy.md](references/efficiency-policy.md). A `MAXIMUM` ticket
does not automatically require its own train; it requires a size-and-risk
checkpoint and decomposition only when the work is an epic or the cumulative
review surface is no longer coherent.

## Run the train

Follow the detailed state machine in [workflow.md](references/workflow.md).
Enforce it through [train_controller.py](scripts/train_controller.py) and
[controller-protocol.md](references/controller-protocol.md); the numbered list
below explains the procedure but does not replace controller authorization.

At a high level:

1. Normalize the selected tickets.
2. Capture the initial orchestrator usage baseline when available.
3. Discover or create the canonical run, claim its orchestrator lease, and
   reconcile prior state before any technical phase.
4. Bootstrap the procedural controller and obtain its first allowed action.
5. Recommend `Terra/H` or `Sol/H` for the orchestrator, report the current
   setting, and obtain explicit confirmation to continue.
6. Record that confirmation as an idempotent controller event. Create and
   version the mandatory proportionality profile and transmit it
   to every technical phase.
7. Resolve and verify foreground or background deterministic supervision,
   bootstrap the control-plane runner, and stop before child dispatch if no
   transition-aware mechanism is reliable.
8. Perform a short routing triage in a dedicated read-only thread with an
   explicit `Terra/H` override, without producing a technical plan.
9. Record one common analysis base commit and queue every selected ticket for analysis immediately.
10. Resolve every routed matrix cell mechanically, dispatch every phase with
   explicit model and reasoning settings, and record requested, actual, and
   conformance values.
   Apply the visible launch and active supervision protocol to every dispatch;
   never interpret an ambiguous creation response as proof that no task was
   created.
   Record `execution_visibility = user-visible` and durable visibility evidence.
   If visible launch is unavailable, stop and obtain a phase-specific user
   authorization before recording `HIDDEN_FALLBACK_AUTHORIZED`; never label a
   hidden session as visible.
   After each changed observation, generate one bounded decision packet. Do
   not expose raw controller state or full child history to the orchestrator.
11. Route and run one full analysis per ticket with at most five active threads, without dependency or human-gate waits. Reuse a valid durable analysis on resume.
12. Consolidate returned dependency contracts and route targeted amendments to original analysis threads.
13. Require every recommendation to separate the minimum required correction,
    optional hardening, and explicitly deferred post-MVP work.
14. Return every consolidated analysis to the main thread through the compact,
   self-sufficient structural-impact digest required by the report template,
   together with its measured token usage.
15. Confirm intrinsic criticality and complexity.
16. Apply the matrix-based human analysis gate independently to each ticket.
17. Build dependency and collision maps and apply the cumulative train-size
    checkpoint.
18. Before each implementation, reconcile its conditional analysis against the current train and merged predecessors.
19. Materialize compact implementation and verification contracts. For `HIGH`
    or `MAXIMUM` complexity, run the bounded plan-contract validation.
20. Schedule only proven-independent ticket execution pairs in parallel.
21. Route the implementation and independent acceptance-test workers from the
    confirmed classification and verification complexity.
22. Record one atomic `EXECUTION_PAIR_DISPATCHED` event before either task-tool
    call. Create implementation and acceptance-test branches/worktrees from
    the same exact train commit. There is no permitted standalone
    implementation launch. Keep test authorship independent until its first
    commit and baseline-red evidence are durable.
23. Use internal implementation slices for large coherent tickets and require
    the worker's same-phase self-review.
24. Push both branches, open the ticket pull request as draft, and integrate
    the independent test commit through a test pull request into the ticket
    branch or an equivalent durable merge.
25. Run baseline-red, exact-head integrated-green, environment-parity, and
    applicable Supabase/Auth/RLS checks with
    [verification_runner.py](scripts/verification_runner.py). This command
    execution is deterministic and records zero model tokens. Wake a model
    only to classify a failure that the structured result cannot resolve.
26. Record functional evidence through the controller, run
    `control_guard.py check-verification`, and mark the ticket pull request
    ready for automated code review only when functional readiness passes.
27. Reassess both dimensions from the final production and test diff.
28. Route one exhaustive independent automated review through the
    initial-review matrix and require its complete finding inventory.
29. Consolidate Codex, Copilot, CI, and human findings into one deduplicated
    disposition ledger, then send one grouped remediation packet to a fresh
    compact remediation thread on the same branch and worktree.
    A completed failed CI run is admissible in this ledger only as an
    accepted-deferred blocking CI finding with pending remediation. It must
    transition the ticket to remediation and never relax a merge permit.
30. Require a reproducing regression test for every confirmed behavioral
    defect, then classify the remediation delta as `mechanical`,
    `bounded-behavioral`, `cross-cutting`, or `material-scope` and route a
    targeted re-review from its actual verification complexity. Permit another full review only for
    the material scope changes listed in the efficiency policy, never merely
    because the train base advanced.
31. Stop automatic remediation after two cycles and perform a root-cause and
    cost-anomaly checkpoint instead of looping. A third cycle is allowed only
    when the user explicitly authorizes one additional cycle after that
    checkpoint and the controller records a run-scoped, ticket-scoped,
    single-use exception. Never edit the manifest directly or raise the
    default two-cycle budget.
32. Measure non-overlapping triage, analysis, consolidation, reconciliation,
    plan-contract validation, implementation, acceptance-test authoring,
    red/green/environment validation, initial review, remediation, follow-up review, final train
    validation, and orchestration usage. Record deterministic verification,
    CI polling, and GitHub polling as zero-model-token phases rather than
    assigning them to a review session.
33. Mark the ticket PR ready, collect exact-head CI and GitHub review state,
    and reconcile Codex, CI, and Copilot findings. If Copilot responds and CI
    completes, collection may close immediately; otherwise retain a deadline
    at least ten minutes after collection starts and permit unavailable or
    timed-out status only after that deadline. A failed terminal CI result is
    collected immediately as a blocking remediation finding, not mislabeled
    as unavailable and not ignored.
34. Apply the matrix-based human pre-merge gate selected by the run mode.
35. Serialize merges into the train exclusively through
    `scripts/merge_pull_request.py`. Direct `gh pr merge`, API merge, or web
    merge by an agent is prohibited because it bypasses the controller and
    live exact-head CI guard.
36. Report each integrated ticket and its ticket total in the main thread.
37. When the requested live queue finishes or a ticket-count or size
    checkpoint is reached, freeze finalization, push the train, and create or update one
    final train pull request against the already resolved base branch. This is
    an automatic successor; do not wait for the user to request the PR or ask
    them to reconfirm `main` versus `master`.
38. Run integrated cross-ticket acceptance and applicable clean-reset
    Supabase/Auth/RLS checks on the exact train head through the deterministic
    verification runner.
39. Review cross-ticket interactions and unreviewed integration surfaces in
    the current final pull request, reuse trustworthy unchanged ticket-review
    evidence, consolidate all feedback, and apply the same grouped-remediation
    and targeted-re-review limits.
40. After that review, start a bounded exact-head GitHub feedback collection
    of at least ten minutes,
    snapshot Codex, CI, Copilot, and human findings, and give every collected
    finding one technical disposition. Record final PR, validation, review,
    feedback snapshot, ledger, token ledger, reports, and attention summaries
    as separate controller events. `RUN_COMPLETED` must be rejected until all
    are present at the same final head.
41. Report the final pull request, its exact reviewed head, readiness, a
    deduplicated manual validation plan, and separate code and application
    attention points.
42. Report the measured run total with baseline/final/delta for every known
    session, including the orchestrator, sessions discovered from hidden-agent
    activity, duplicate attempts, and explicitly unmeasured phases. A
    sequential phase family is not a duplicate merely because its attempt
    suffix changed. Report every controlled orchestrator segment separately
    and sum them under orchestration; a valid budget handoff is not a
    duplicate.
43. Keep the train frozen after five integrated tickets or a cumulative-size
    checkpoint until the user merges it or explicitly authorizes more tickets.

For sequential or dependent tickets, complete the predecessor's implementation, automated validation, required human validation, and train merge before starting the dependent implementation. Do not delay the dependent analysis.

For parallel tickets, allow implementation in parallel only after analysis proves that they have no shared files, central registries, data migrations, contracts, business invariants, or ordering dependency. Rebase or update every still-open ticket branch onto the latest train and rerun affected tests and review before its merge.

## Enforce non-bypassable stops

Pause and report a blocker when:

- the ticket source or selection is unresolved;
- the orchestrator startup preflight has not been explicitly confirmed;
- a material product or architecture decision is missing;
- required credentials, connectors, permissions, or environments are unavailable;
- mandatory automated tests fail after reasonable remediation;
- independent acceptance coverage, red/green evidence, environment parity, or
  the functional-readiness gate is incomplete;
- a required Supabase/Auth/RLS check is environment-blocked or used privileged
  credentials to prove an ordinary user path;
- automated review still has blocking findings;
- the final train pull request cannot be created or updated during live
  finalization;
- the final pull request head differs from the head covered by required tests,
  Codex review, or unresolved-comment collection;
- the final pull request still has blocking Codex, Copilot, CI, or human
  findings;
- an operation would be destructive or exceed granted authority;
- routing requires `max` or `ultra` and user authorization has not yet been resolved;
- a routed phase cannot be dispatched with its exact matrix-selected model and
  effort and no documented fallback applies;
- post-dispatch verification reports an unexplained routing mismatch;
- a visible phase launch or monitoring operation remains unresolved after the
  bounded reconciliation protocol, with no user-authorized visible fallback;
- five tickets are already integrated into the train.
- a cumulative size checkpoint was crossed and no continuation or
  finalization decision is recorded;
- the automatic review/remediation pass budget is exhausted;
- a token-cost anomaly requires an expensive repeat and no quality-neutral
  continuation is established.
- a phase exceeds 50 million tokens, performs more than one context
  compaction, or a focused re-review exceeds twice its initial-review token
  total and the controller's cost checkpoint is unresolved;
- a fresh phase lacks a valid bounded context-packet descriptor;
- a supposedly visible phase lacks durable visibility evidence or a hidden
  fallback lacks phase-specific user authorization;
- the current orchestrator reached a hard activity budget and its controlled
  handoff has not been accepted;
- a decision packet exceeds 16 KiB or an unchanged observation would require
  replaying the main conversation;

When information rather than approval is missing, apply
`HUMAN_INPUT_REQUESTED` or a child `PHASE_TERMINATED` with `needs_input`.
Publish the controller-generated `ACTION REQUIRED` packet with the exact
question, accepted reply formats, blocked scope, and work continuing
independently. Never expose only a generic "waiting for information" status.
After `INPUT_PROVIDED`, resume the same visible child thread when applicable.

When a human analysis or pre-merge gate is required, pause only the affected ticket and implementations that depend on it. Continue every selected analysis and any independent implementation whose gates are satisfied.

Publish every required decision in the main conversation using the `ACTION
REQUIRED` report from [report-template.md](references/report-template.md), then
persist the complete `pending_human_action`. Never rely on a child thread,
automation transcript, or silent status field to tell the user that approval
is waiting. Keep verified supervision active. When reminders are configured,
emit them through deterministic product notifications from the persisted gate;
do not wake the full orchestrator merely to repeat unchanged text.

After five integrated tickets, do not start a sixth ticket. Create or update
the final train pull request, complete its validation and feedback loop, and
report the checkpoint. Continue only if the user explicitly requests
additional tickets. Never infer that exception.

Never merge the train into `main` or `master` unless either:

- the user performs the merge on GitHub; or
- the user explicitly asks Codex to perform that merge.

The approval mode named `auto-merge` applies only to matrix-selected human
pre-merge gates for ticket branches entering the train. It is not permission
to merge the train into the base branch. When the user explicitly asks Codex
to perform the final merge, record `FINAL_BASE_MERGE_AUTHORIZED` for the exact
final PR head, then use `scripts/merge_pull_request.py --action final`; a
generic startup approval, an approval-mode label, or prior merge consent for
another head is insufficient.

## Keep source status separate

Distinguish:

- analyzed;
- approved for implementation;
- implemented on a ticket branch;
- merged into the train;
- delivered in `main` or `master`;
- closed in the ticket source.

Do not close or mark source tickets delivered merely because their pull requests were merged into the train.

## Finish with traceability

Use [report-template.md](references/report-template.md) for all reports.

For every analysis gate, make the main-thread digest sufficient for human
approval without requiring the user to open the analysis thread. Explicitly
cover functional, architecture, database/data, API/contract,
security/access/privacy, and operations/deployment impacts. State `no impact
identified` for every assessed domain with no impact; never use omission to
mean no impact.

For every implemented ticket, include the pull-request link and direct links
to important file diffs in the final pull-request diff when GitHub provides
stable anchors. Explain architecture impact, technical decisions, important
symbols, tests, review findings, accepted or rejected Copilot comments,
residual risks, and gate decisions without reproducing the entire code change.

Include the independent test pull request or commit, verification-contract
coverage, baseline-red and exact-head green evidence, environment fingerprint,
Supabase/Auth status when applicable, regression tests, and the concrete
reason for every scenario left to human validation.

At live completion and every ticket-count or size checkpoint, include the final train
pull-request link and exact reviewed head. Provide only the manual tests that
remain useful beyond automated evidence, grouped as required, recommended, or
optional, with prerequisites, concise actions, and expected results.
Deduplicate tests shared by several tickets and distinguish completed human
evidence from outstanding user action.

Report code attention points separately from application attention points.
Cover material architecture, data, contracts, security, concurrency,
operations, user journeys, roles, edge cases, and recovery behavior only when
applicable. Link code concerns to the final pull-request diff. State explicitly
when no manual test or no attention point is identified; never use omission to
mean none.

Use [token_usage.py](scripts/token_usage.py) under [usage-reporting.md](references/usage-reporting.md). Report exact per-phase counters when available, the total per ticket, orchestration separately, and the train aggregate. Mark incomplete coverage as `partial` or `unavailable`; never invent a token estimate.

Use its `ledger` command at checkpoints and completion to reconcile
baseline/final/delta for every known session and every manifest phase. Count
duplicate and failed attempts instead of discarding them, and list every
phase whose counter or baseline is unavailable. A report may be `complete`
only when every orchestrator segment is present, every authoritative procedural
phase is measured, and every hidden session discovered from orchestrator
activity is mapped or explicitly reconciled.

Reuse trustworthy CI evidence only when it is tied to the exact reviewed head
commit and all required checks are available. Reviewers still run independent
risk-targeted checks for the ticket's critical surfaces. If CI evidence is
missing, stale, or broken, fall back to the project-required local validation
and disclose the duplication.

Before every ticket-to-train merge, the ticket finding event must contain the
actual GitHub collection interval, source counts, evidence reference, CI
status, Copilot status, stable finding inventory, and exactly one technical
disposition per collected finding for the same head. Source names or aggregate
counts alone are not evidence. Run the guarded merge script only after
`train_controller.py permit-merge --action ticket` passes. Before a requested
train-to-base merge, rerun the live GitHub check through the same script; any
pending or failing check blocks the merge even if local tests were green.

Before every final response, apply the yield gate from
[orchestration-control.md](references/orchestration-control.md). Do not end the
orchestrator turn while a launch outcome is unknown, an automatic successor is
ready, completed evidence remains uncaptured, or active work lacks verified
deterministic supervision. A verified background watcher may protect queued or
running work without retaining the same model conversation. At live
completion, create or update the final pull request before the final Codex
review and feedback collection.

Run `train_controller.py check --mode yield` or `--mode completion` against
the reconciled canonical manifest before every yield, completion, or
checkpoint. A nonzero result means the orchestrator must continue, capture
missing evidence, or report a real blocker. `control_guard.py` remains a
legacy-manifest diagnostic only; its duplicated `control` projection must not
override or replace the versioned procedural state.

Use terminal reason `SUPERVISED_ACTIVE` only when active work is protected by
a verified foreground wait or background watcher. It is not permission to
stop following the run. Run `control_plane_runner.py step` first; it must
return `NO_MODEL_WAKE` before a supervised yield. If the guard reports missing
ownership, visibility, supervision, handoff, or human-action notification,
correct that control defect before yielding.

At completion, early stop, and every ticket-count or size checkpoint, provide
one consolidated report for every requested ticket and state exactly what
still requires user action.
