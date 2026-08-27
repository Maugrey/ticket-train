# Proportionality, Scope, and Cost-Control Policy

## Contents

- Quality and cost invariants
- Mandatory proportionality profile
- Recommendation separation
- Ticket and train size budgets
- Compact implementation contract
- Plan-contract validation
- Implementation slices and worker self-review
- Review-pass budget
- Fresh remediation contexts
- Deterministic supervision
- Logs and context packets
- Token and credit anomaly controls

## Quality and cost invariants

Treat correctness, security, and required validation as non-negotiable. Treat
token and credit efficiency as a first-class operational constraint rather
than a reason to lower quality silently.

Use these default limits:

```text
complete_analysis_passes_per_ticket = 1
acceptance_test_authoring_passes_per_ticket = 1
complete_ticket_reviews_per_stable_scope = 1
complete_final_train_reviews_per_stable_scope = 1
remediation_cycles_per_ticket = 2
remediation_cycles_for_final_train = 2
unchanged_llm_polling = prohibited
duplicate_phase_launches = prohibited unless the original is conclusively absent
```

A targeted amendment, reconciliation, or follow-up review is not a new
complete pass when it receives only the changed evidence and verifies only the
affected contract or risk surface.

Do not reduce model effort below the applicable routing matrix, omit required
tests, suppress findings, or broaden acceptance of risk to meet a cost target.
When a limit is reached, stop the automatic loop and perform the root-cause
checkpoint below.

Parallel independent acceptance-test authoring is a quality-neutral cost
shift: spend one compact bounded phase before review to avoid using exhaustive
review and remediation as functional debugging. Follow
[verification-policy.md](verification-policy.md).

## Mandatory proportionality profile

Before triage, create one versioned proportionality profile for the train.
Derive it from the ticket source, project guidance, and user-provided product
context. Ask the user only for a missing decision that could materially change
security, data, functional scope, or delivery. Otherwise publish the proposed
profile and continue under the selected approval mode.

Record:

```text
profile_revision
real_world_stakes
expected_users_and_actors
trust_assumptions
credible_threats_and_misuse
boundaries_requiring_strong_protection
acceptable_containment_and_recovery
mvp_scope
explicit_non_goals
post_mvp_hardening_candidates
evidence_and_user_decisions
```

Pass the complete compact profile to triage, every analyzer, every plan
validator, every worker, every ticket reviewer, and the final-train reviewer.
Require each phase to apply it when assessing severity, scope, and findings.

Do not inflate a finding from a generic enterprise threat model that conflicts
with the recorded actors, assets, and credible threats. Do not lower a finding
that crosses a strongly protected boundary. Mark a proposed defense as
optional hardening when it is valuable but not required by the MVP threat
model or acceptance criteria.

A material profile change invalidates affected analysis or review evidence.
A wording-only clarification does not.

## Recommendation separation

Every analysis, implementation report, review, remediation disposition, and
final report must separate recommendations into exactly these categories:

1. `minimum_required_correction`: necessary for acceptance criteria, project
   rules, a credible protected-boundary risk, or reliable operation.
2. `optional_hardening`: useful defense, resilience, maintainability, or
   observability improvement that is not required for this MVP ticket.
3. `explicitly_deferred_post_mvp`: acknowledged work intentionally excluded,
   with the reason and a durable follow-up locator when one exists.

Do not make optional hardening blocking. Do not hide required work in the
optional or deferred categories. A reviewer must cite the profile, acceptance
criterion, invariant, or project rule that makes a recommendation required.

## Ticket and train size budgets

Limit a train by cumulative review surface as well as ticket count. Use these
default soft thresholds unless project guidance defines stricter ones:

```text
material_files_per_ticket_warning = 20
material_files_per_train_checkpoint = 60
schema_or_data_transformations_per_train_checkpoint = 2
structural_domains_per_train_checkpoint = 4
maximum_complexity_tickets_in_active_implementation = 1
```

Exclude generated files, lockfiles, snapshots, and mechanical translation
mirrors from file counts only when their source change is counted and their
generated output is still validated. Count a structural domain when a ticket
materially changes one of: architecture, functional invariants, database/data,
API/contracts/integrations, security/access/privacy, or operations/deployment.

Thresholds are checkpoints, not permission to skip a selected ticket and not
an automatic requirement that every `MAXIMUM` ticket have a separate train.
A `MAXIMUM` ticket triggers a mandatory size-and-risk checkpoint before its
implementation. It may remain in the same train, sequentially, when the
current train has capacity, its scope is coherent, and its integration does
not make the final review surface unreasonable.

At a checkpoint, either:

- continue the ticket in the current train and record why the aggregate
  review surface remains coherent;
- finalize the current train before starting it; or
- decompose it because it is an epic.

Decompose a ticket before implementation when it contains independently
deliverable acceptance outcomes and cannot be implemented, tested, reviewed,
and rolled back as one coherent change. Strong evidence includes multiple
unrelated domains, several migrations or rollout stages, a weak common test
oracle, or technical decisions that can be approved independently. Preserve
the parent acceptance criteria and dependency order. Do not split merely to
reduce a file count when that would break an invariant or create unsafe
intermediate states.

Crossing a train checkpoint freezes new implementation starts. It does not
interrupt a safe in-progress implementation. Finalize the train or obtain an
explicit user decision to extend the applicable threshold.

## Compact implementation contract

Before implementation, materialize one compact, versioned implementation
contract outside the repository. The worker receives this contract instead of
the full analysis conversation history.

Include:

```text
ticket_and_analysis_revision
current_train_head
selected_conditional_variant
acceptance_criteria
minimum_required_correction
optional_hardening_selected_for_this_ticket
explicitly_deferred_post_mvp
functional_and_security_invariants
allowed_scope_and_no_go_scope
expected_contract_data_and_architecture_impacts
likely_files_and_symbols
required_tests_and_test_oracles
verification_contract_revision
independent_test_path_ownership
required_environment_tiers
dependencies_and_collision_constraints
proportionality_profile_revision
open_decisions = none
```

Do not start implementation when a material decision remains open. Store
links or commit references for detailed evidence rather than copying logs or
entire reports.

## Plan-contract validation

For `HIGH` or `MAXIMUM` complexity, perform one short independent validation
of the compact implementation contract before code changes. This is a
contract-quality check, not a second analysis.

The validator checks only:

- acceptance-criteria coverage;
- contradictions with project guidance or the proportionality profile;
- missing invariants, rollout constraints, or test oracles;
- scope coherence and epic evidence;
- whether the proposed slices remain reviewable and safe.

Use a fresh compact visible thread, one turn, and no repository-wide
exploration. Route it no higher than the implementation setting and never
above `Sol/H`; if adequate validation would require broader reasoning, return
the contract to the original analyzer for a targeted amendment instead.
Record this phase separately as `plan-contract-validation`.

## Implementation slices and worker self-review

For a large but coherent ticket, keep one ticket branch, worktree, and pull
request while implementing internal slices. Each slice must leave the branch
in a testable state and have a named purpose, affected invariant, and targeted
verification. Do not open a review cycle for every slice.

Before opening or updating the ticket pull request, the worker performs one
self-review in the same implementation phase and fixes discovered issues
before independent review. The checklist covers:

- every acceptance criterion;
- implementation-contract and no-go scope;
- functional and protected-boundary invariants;
- migration, compatibility, rollback, and recovery where applicable;
- error and concurrency paths;
- test quality and failure oracles;
- unrelated churn, debug artifacts, and documentation obligations;
- separation of minimum correction, optional hardening, and deferred work.

Return the checklist result and final diff summary. This is not a substitute
for independent review and must not create a separate model turn solely for
report formatting.

The implementation worker does not own the independent acceptance suite. Its
self-review and unit tests run concurrently with the acceptance-test worker in
[verification-policy.md](verification-policy.md). Do not delay independent
test authorship until implementation completion and do not expose the
implementation diff before the test worker's initial commit.

## Review-pass budget

Run one exhaustive independent review of the complete stable ticket diff only
after the functional-readiness gate passes. The
reviewer must return its complete finding inventory in one response, including
all severities and all assessed risk surfaces. Do not drip findings across
successive complete reviews.

Before remediation, open one bounded collection window for Codex, CI,
Copilot, and existing human findings. Deduplicate them into one ledger and send
one compatible remediation batch. Do not create one worker cycle per source or
comment.

After remediation, run a targeted follow-up review by default. It receives
only the remediation diff, unresolved ledger entries, dispositions, affected
tests, and affected risk surface. A new complete review is allowed only when
the remediation materially changes at least one of:

- architecture or ownership boundaries;
- a critical schema migration, data transformation, or recovery strategy;
- an authorization, tenant-isolation, secret, or privacy boundary;
- functional scope or acceptance behavior;
- a public/shared contract with new consumers;
- the ticket's previously reviewed risk surface through unrelated changes.

A train-base refresh alone does not force a complete ticket review. Inspect
the changed contracts and collision domains, rerun affected tests, and perform
a targeted integration-impact review unless the semantic scope above changed.

Allow at most two automatic remediation/follow-up cycles. If blocking findings
remain, stop the loop and run a root-cause checkpoint. Determine whether the
problem is an invalid analysis contract, an unstable requirement, an epic, a
broken test oracle, or a worker/reviewer disagreement. Publish the complete
remaining ledger and obtain any material user decision. Start a third cycle
only when the user explicitly authorizes exactly one additional cycle and the
controller records a run-scoped, ticket-scoped, single-use exception linked to
that decision and checkpoint. Never change the default budget or edit the
manifest directly.

Apply the same pass budget to final-train review. The first final review
concentrates on cross-ticket interactions, integration glue, cumulative
invariants, migrations, deployment, and code not already covered by
trustworthy ticket reviews. Reuse exact-commit ticket evidence; do not
re-review every unchanged ticket file merely because it appears in the
base-to-train diff.

## Fresh remediation contexts

Use a fresh visible remediation thread with the routed implementation setting
for every remediation batch. Keep the existing branch and worktree, but do not
reuse the long implementation conversation.

Pass only:

- the compact implementation contract and proportionality profile revisions;
- exact branch, base, head, and pull-request references;
- the deduplicated finding ledger and dispositions;
- the remediation diff scope and required tests;
- links to complete logs and durable evidence.

Generate this handoff with `scripts/context_packet.py`. The packet is
hash-addressed, includes exact base/head and profile revision, contains zero
conversation-history turns, and cannot exceed 64 KiB. A fresh task without a
valid packet is not eligible for dispatch.

The original worker remains the owner in the logical manifest, but the fresh
thread is the execution context. When original implementation context is
material, include a compact worker-produced handoff rather than the entire
history.

## Deterministic supervision

Supervision is event-driven. Use thread wait cursors, GitHub status queries,
test result artifacts, and the durable manifest as the source of truth.

Use [control-plane-runner.md](control-plane-runner.md) as the only routine
read boundary for the orchestrator. Its 16 KiB decision packets replace full
manifest replay, and its semantic hash suppresses unchanged waits without a
model wake.

Use [controller-protocol.md](controller-protocol.md) for lifecycle transitions.
The controller's unchanged `WAIT_FOR_PHASE_TRANSITION` result must be handled
without an LLM turn. Model-written heartbeat loops are prohibited: a
non-LLM watcher or foreground task wait observes state, and the model wakes
only when an event changes controller revision and requires a model action.

Resolve supervision before launching any child. Prefer foreground
transition-aware waits. A verified child completion callback is the preferred
way to survive the main turn without polling. Create a run-scoped background
watcher only when it is an actual zero-model process; a recurring Codex
heartbeat automation is prohibited. Store the selected evidence in the
canonical manifest before dispatch. Installing supervision only after the user
notices a stall is a control failure.

The model is active only to:

- dispatch a newly ready phase;
- process a completion, failure, blocker, or user decision;
- reconcile an ambiguity after deterministic checks fail;
- perform technical consolidation or disposition work.

Do not wake a model merely to restate that a task is still running. Do not
perform periodic full-thread reads. Prefer a product wait that wakes on a
state transition. If the product requires bounded waits, reuse current cursors
and emit no new status or detailed read when the snapshot is unchanged.

Use `control_guard.py` to enforce state, visibility, pass budgets, captured
usage, and completion prerequisites. Use deterministic `gh --json` queries for
pull-request head, checks, and comment inventory when GitHub is in scope.
Parse test commands into durable result artifacts with command, exit status,
head commit, duration, and concise failure summary. Update the manifest from
those artifacts; do not ask a model to rediscover status from raw logs.
Use `scripts/verification_runner.py` for exact-head command execution. Its
structured result records zero model tokens; invoke a model only to adjudicate
a failed result that is not mechanically classifiable.

Use an internal deterministic check interval appropriate to the host. Do not
generate periodic liveness through a model. When the host requires unchanged
liveness, emit one deterministic product notification from the manifest.
Report transitions immediately. Verified visible child tasks are sufficient
evidence that work is continuing. A pending human action is announced once in
the main conversation. If it is the only remaining action, pause or delete the
watcher and produce no further model wake or default reminder until the user
responds. An explicitly requested reminder is deterministic and never reloads
the orchestrator context.

Treat every orchestrator conversation as one replaceable activity segment.
Warn at 10 million segment tokens and rotate before another ordinary model
action at 25 million tokens, 50 model wakes, 500 tool calls, or one context
compaction. A controlled rotation preserves the run ID and creates one fresh
visible adapter; it does not repeat a technical phase and is not a duplicate
session.

## Logs and context packets

Write complete command, test, CI, and review-support logs under the external
run directory. Do not commit them. Record paths, producing phase, head commit,
timestamps, exit status, and content hash in the manifest.

Pass models a structured summary, all error lines, and only the nearby context
needed to diagnose them. Default limits per handoff are 200 log lines and 20
KiB of excerpts; provide the durable log path for deeper targeted reads.
These limits may be exceeded for a specific failure when omission would harm
diagnosis, but never attach an unchanged complete log repeatedly.

Use fresh phase threads and durable references to prevent accumulated history.
Do not use lossy prompt compression for acceptance criteria, security
invariants, migrations, or review findings.

## Token and credit anomaly controls

Capture a baseline and final counter for every session and calculate its delta
with `token_usage.py ledger`. Include authoritative sessions, duplicate
attempts, failed attempts, reused-session phase windows, orchestration, and
finalization. List every unmeasured phase explicitly.

Tokens are not equivalent to subscription credits. Report both this
limitation and the observable drivers: input, cached input, output, reasoning
output, model/effort, turns, complete passes, remediation cycles, duplicate
attempts, and missing measurements.

Trigger a cost anomaly checkpoint before any new expensive repeat when one of
these occurs:

- a duplicate phase or session exists;
- a phase has already consumed its one complete pass;
- acceptance-test authoring would be regenerated instead of amended narrowly;
- a third remediation cycle would be required;
- a thread compacted more than once or its context can no longer be handed off
  compactly;
- one model phase exceeds 50 million total tokens;
- a focused follow-up review exceeds twice the total tokens of its complete
  review baseline;
- usage measurement for a completed phase is missing;
- unchanged polling or repeated full-thread reads were detected;
- actual routing is more expensive than the matrix-selected setting;
- cumulative train size crossed a configured checkpoint.
- a hidden session discovered in orchestrator activity is not mapped to an
  authorized phase;
- another manifest or run ID exists for the same canonical run fingerprint;
- a completed analysis would be repeated because orchestration moved to a new
  conversation.
- an orchestrator segment crossed a hard activity budget but continued without
  an accepted controlled handoff;
- a decision packet exceeded 16 KiB or unchanged state was replayed into a
  model conversation;

At the checkpoint, preserve completed work, report consumed and missing usage
by phase, identify the cause, and propose the least expensive quality-neutral
continuation. Any option that lowers review depth, model routing, tests, or
protected-boundary coverage requires explicit user approval.

The procedural controller blocks every successor event while one of these
anomalies remains open. Resolve it as `restart-fresh-compact`,
`continue-quality-neutral`, or `user-approved-quality-tradeoff`; the last form
requires a durable reference to the user's explicit decision.
