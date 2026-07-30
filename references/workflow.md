# Ticket Train Workflow

## Contents

- Run configuration
- Orchestrator startup preflight
- Roles and state
- Routing triage
- Analysis phase
- Dependency consolidation and reconciliation
- Compact orchestration and durable state
- Visible thread launch and active supervision
- Token accounting
- Planning and scheduling
- Git and pull-request model
- Implementation and review
- Sequential and parallel execution
- Final train pull request and checkpoint
- Failure and recovery

## Run configuration

Resolve these values before starting:

```text
ticket_source
ticket_selection
approval_mode
execution_mode = dry-run | live
repository
base_branch = main | master | explicit branch
train_branch
max_active_analyses = 5
max_active_implementations = 2
max_integrated_tickets = 5
reasoning_cap = xhigh
authorized_reasoning_overrides = none | explicit scopes
analysis_policy = parallel-conditional
usage_reporting = exact-if-available
routing_enforcement = strict
triage_model = gpt-5.6-terra
triage_reasoning_effort = high
coordination_policy = compact-control-plane
supervision_policy = event-driven-deterministic
child_thread_visibility = user-visible
launch_reconciliation = required
supervision_mode = active-until-terminal
liveness_reporting = transitions-only | explicit user override
proportionality_profile_revision = required
cost_control_policy = strict-quality-preserving
complete_review_limit_per_stable_scope = 1
remediation_cycle_limit = 2
material_files_per_train_checkpoint = 60
schema_or_data_transformations_per_train_checkpoint = 2
structural_domains_per_train_checkpoint = 4
orchestrator_recommendation = Terra/H | Sol/H
orchestrator_actual_setting = known combination | unknown
orchestrator_preflight_confirmation = pending | confirmed | declined
```

Use these approval modes:

| Mode | Human analysis matrix | Human pre-merge matrix |
|---|---|---|
| `standard` | Apply | Apply |
| `auto-analysis` | Bypass | Apply |
| `auto-merge` | Apply | Bypass |
| `full-auto` | Bypass | Bypass |

Automated analysis, tests, independent review, remediation, and reporting remain mandatory in every mode.
Reasoning above `xhigh` always requires separate explicit user authorization.

## Orchestrator startup preflight

After resolving the source, selection, run mode, repository, and train
configuration, apply the criteria in
[model-routing.md](model-routing.md) to recommend `Terra/H` or `Sol/H` for the
main conversation.

Publish the current setting when observable, the recommendation, the concrete
criteria, and whether the current setting is recommended, overprovisioned,
underprovisioned, or unknown. Then ask:

```text
Continue this train with the current orchestrator setting? yes/no
```

Capture the initial orchestrator usage baseline before publishing the
preflight when counters are available. Do not start triage, child threads,
branches, worktrees, or other repository changes until the user confirms.
Approval bypass modes do not bypass this startup confirmation.

If the user declines, stop and recommend opening a new conversation with the
reported setting. If the current setting cannot be observed, ask the user to
verify it in the composer or explicitly accept the unknown setting.

Before triage, create the versioned proportionality profile required by
[efficiency-policy.md](efficiency-policy.md). Include it in the startup report.
Resolve only materially missing product or threat-model decisions; do not
invent enterprise-scale actors or threats that the product context does not
support.

## Roles and state

Use these roles:

- **Orchestrator:** own the queue, configuration, dependency map, gates, thread routing, compact state, and reports.
- **Triage agent:** classify the selected ticket batch in a dedicated read-only
  `Terra/H` thread without producing a technical plan.
- **Analyzer:** inspect one ticket and repository state in read-only mode.
- **Worker:** implement one ticket in its branch and worktree.
- **Reviewer:** independently review one ticket pull request in read-only mode.
- **Plan-contract validator:** perform one bounded independent completeness
  check for a `HIGH` or `MAXIMUM` implementation contract without repeating
  the technical analysis.
- **Remediator:** use a fresh compact execution context while retaining the
  original ticket branch and worktree ownership.
- **Human approver:** approve analysis plans or train integration when required by the applicable matrix and mode.

Track at least:

```text
ticket_id
source_locator
source_revision_or_snapshot
orchestrator_recommended_model
orchestrator_recommended_reasoning_effort
orchestrator_actual_model
orchestrator_actual_reasoning_effort
orchestrator_setting_status
orchestrator_preflight_confirmation
analysis_base_commit
proportionality_profile_revision
proportionality_profile
triage_intrinsic_criticality
triage_complexity
analysis_intrinsic_criticality
analysis_complexity
criticality_evidence
complexity_factor_assessment
criticality_confidence
complexity_confidence
analysis_revision
dependency_assumptions
conditional_variants
valid_if
invalid_if
analysis_reconciliation_status
implementation_contract_revision
plan_contract_validation_status
ticket_size_budget
train_size_budget
internal_slices
worker_self_review_status
effective_intrinsic_criticality
effective_complexity
analysis_model
analysis_reasoning_effort
implementation_model
implementation_reasoning_effort
review_model
review_reasoning_effort
routing_records_by_phase = {
  classification_checkpoint,
  matrix_cell,
  requested_model,
  requested_reasoning_effort,
  actual_model,
  actual_reasoning_effort,
  conformance
}
reasoning_authorization
orchestrator_usage_baseline
orchestrator_token_usage
triage_token_usage
analysis_initial_token_usage
analysis_consolidation_token_usage
analysis_reconciliation_token_usage
implementation_token_usage
review_initial_token_usage
remediation_codex_token_usage
remediation_copilot_token_usage
remediation_combined_token_usage
review_followup_token_usage
review_complete_pass_count
remediation_cycle_count
review_scope_revision
final_train_validation_token_usage
final_train_review_token_usage
final_train_remediation_token_usage
final_train_followup_review_token_usage
ticket_token_total
run_token_total
dependencies
collision_domain
analysis_thread_id
worker_thread_id
reviewer_thread_id
ticket_branch
worktree
pull_request
test_status
review_status
analysis_gate_status
merge_gate_status
train_commit
final_train_pull_request
final_train_pull_request_head
final_train_reviewed_head
final_train_ci_status
final_train_copilot_status
final_train_finding_ledger
manual_validation_plan
code_attention_points
application_attention_points
manifest_updated_at
last_state_transition
next_automatic_action
active_phase_keys
launch_unknown_phase_keys
visible_thread_inventory
phase_launch_records
session_usage_ledger
duplicate_session_inventory
unmeasured_phase_inventory
log_artifacts
cost_anomaly_status
```

Use this lifecycle:

```text
DISCOVERED
-> AWAITING_ORCHESTRATOR_CONFIRMATION
-> ORCHESTRATOR_CONFIRMED
-> TRIAGED
-> ANALYZING
-> ANALYZED
-> DEPENDENCIES_CONSOLIDATED
-> CLASSIFIED
-> ANALYSIS_REPORTED
-> AWAITING_ANALYSIS_APPROVAL | ANALYSIS_AUTO_APPROVED
-> RECONCILING_ANALYSIS
-> ANALYSIS_VALID | ANALYSIS_REFRESHED
-> IMPLEMENTATION_CONTRACT_READY
-> PLAN_CONTRACT_VALIDATED
-> READY_FOR_IMPLEMENTATION
-> IMPLEMENTING
-> IMPLEMENTED
-> AUTOMATED_TESTS
-> EFFECTIVE_CLASSIFIED
-> AUTO_REVIEW
-> FIXING
-> TARGETED_REVIEW
-> AUTO_REVIEW_CLEAN
-> AWAITING_HUMAN_MERGE_APPROVAL | MERGE_AUTO_APPROVED
-> READY_TO_MERGE
-> MERGED_INTO_TRAIN
-> REPORTED
```

`ANALYSIS_INVALID` returns the ticket to `ANALYZING` in its original analysis thread, then through classification, reporting, the applicable human gate, and reconciliation again.

Track the train-level lifecycle separately:

```text
TRAIN_OPEN
-> TRAIN_FINALIZING
-> FINAL_PR_OPEN
-> FINAL_PR_REVIEW
-> FINAL_PR_FIXING
-> FINAL_PR_REVIEW_CLEAN
-> AWAITING_USER_BASE_MERGE
-> DELIVERED_IN_BASE
```

If the user explicitly continues the train, return to `TRAIN_OPEN`, keep the
final pull request open when possible, and mark its previous test and review
evidence stale until the next finalization.

## Routing triage

Before creating analysis threads, create one dedicated read-only batch triage
thread at `Terra/H` under [model-routing.md](model-routing.md). Pass explicit
model and reasoning overrides. Do not perform this pass in the orchestrator
when the parent conversation uses another setting.

For each ticket:

1. Read the ticket-visible information.
2. Assign provisional intrinsic criticality and complexity.
3. Give no more than one or two reasons per dimension.
4. Identify declared or suspected dependencies and collision domains without trying to prove them.
5. State confidence.
6. Select the analysis model and reasoning effort from the routing matrix.
7. Apply the proportionality profile without expanding triage into technical
   analysis.

Do not produce a technical plan, trace the implementation, enumerate detailed files or tests, or run validation. Triage must remain materially smaller and faster than the full analysis.

Treat uncertainty conservatively so the full analysis does not need to be repeated. If routing requests `max` or `ultra`, obtain explicit user authorization before launching that analysis. Group authorization requests across the selected tickets when practical.

Use suspected dependencies as analyzer context only. Never use triage dependencies to delay an analysis.

Before launching analysis, the orchestrator performs only a mechanical routing
check: verify that every classification maps to the exact matrix cell, every
requested override is supported, and the triage thread reported `Terra/H`.
Do not repeat the ticket classification or technical reasoning.

## Analysis phase

Follow [analysis-policy.md](analysis-policy.md).

Record one common `analysis_base_commit` before launching analyses. Queue every selected ticket immediately. Create exactly one read-only full-analysis thread per ticket using explicit routed model and effort overrides. Keep at most five active at once and use a rolling window until all selected analyses finish.

Do not wait for another analysis, a human analysis approval, an upstream implementation, or a train merge before launching any selected analysis.

Require each analyzer to:

1. Read project instructions and the complete ticket.
2. Read the normalized catalog of every selected ticket and triage dependency signals.
3. Analyze against the common `analysis_base_commit`.
4. Verify whether the ticket is still applicable to that checkout.
5. Identify acceptance criteria and missing decisions.
6. Trace relevant files, symbols, data models, contracts, and tests.
7. Separate stable findings from the conditional plan.
8. Declare upstream assumptions, planned variants, `valid_if`, and `invalid_if`.
9. Return the dependency contract required by [analysis-policy.md](analysis-policy.md).
10. Confirm intrinsic criticality with the complete credible-failure evidence
    block required by [criticality.md](criticality.md).
11. Rate every complexity factor, identify decisive factors, and confirm
    complexity with the evidence required by
    [criticality.md](criticality.md).
12. State the actual model and reasoning effort.
13. State the matrix inputs and selected cell received from the orchestrator.
14. State explicitly that no files were modified.
15. Apply the proportionality profile to every failure path and recommendation.
16. Separate `minimum_required_correction`, `optional_hardening`, and
    `explicitly_deferred_post_mvp`.
17. Estimate material file, migration/data-transformation, and structural
    domain counts, and identify epic evidence.

The orchestrator checks completeness and consistency of the classification
evidence without repeating the technical analysis. Route missing evidence,
keyword-only classification, or an unsupported label back to the original
analyzer. Keep the higher evidenced classification if analyzer and
orchestrator disagree. Verify actual routing against
[model-routing.md](model-routing.md) before continuing.

After all selected analyses return, consolidate their dependency contracts. Send targeted clarification or amendment requests to the original analyzer threads when assumptions, offers, contracts, invariants, or ordering constraints conflict. Do not create replacement threads or redo stable analysis sections.

Return every consolidated analysis to the main thread using the compact,
self-sufficient structural-impact digest in the analysis report template.
Keep the detailed evidence in the ticket analysis thread and include it in the
full report when useful, but never make that thread required reading for
approval. Apply the human analysis matrix from
[criticality.md](criticality.md) independently to every ticket. For
automatically approved analysis, label the digest as informational and
continue without waiting. When human approval is required by the matrix and
mode, pause only that ticket's implementation and implementations that require
its merged result.

A waiting human analysis gate never blocks analysis or dependency consolidation for another selected ticket. It does not block an independent implementation whose own gates are satisfied.

Do not launch a second full analysis because the analyzer corrected the triage classification. Record the routing difference and use the confirmed values for implementation.

## Compact orchestration and durable state

Keep the main conversation as a control plane, not a technical work log.

The skill cannot change the model or reasoning effort of an already-running
main conversation. Record the actual orchestrator setting when observable and
keep its work bounded to coordination. Never use that inherited setting for a
routed child phase; the dedicated triage and ticket phases receive explicit
overrides.

Create one run directory outside the repository under the available Codex data
directory. Persist:

```text
run configuration
ticket state manifest
thread IDs and compact wait cursors
routing selections and conformance
dependency and collision graph
gate decisions and analysis revisions
branch, pull-request, and commit references
non-overlapping usage snapshots and deltas
```

Write the manifest immediately after every launch intent, tool response,
thread reconciliation, cursor advance, phase completion, gate decision,
branch or pull-request change, integration, finding-ledger update, and usage
capture. Before reporting status, reconcile the manifest with live threads and
repository state. A stale `IMPLEMENTING` or `REVIEWING` record is not an
acceptable substitute for current phase state.

Do not persist credentials, private reasoning, full prompts, raw command logs,
or complete child-thread transcripts in the manifest.

Use event-driven compact thread waits with the latest cursor. Prefer one
bounded wait for all active threads. Do not repeatedly read full child
threads, replay unchanged snapshots, narrate polling with no state change, or
wake a model merely for liveness. Read detailed thread output only when a
final report, blocker, approval request, or targeted amendment requires it.
Persist full command and test logs outside the repository and transmit only a
structured summary, errors, and bounded nearby context.

Pass compact, phase-specific handoffs:

- analysis receives normalized ticket data, shared catalog, project guidance,
  base commit, and dependency signals;
- implementation receives the approved analysis revision, selected variant,
  current train commit, scope, tests, and durable references;
- review receives the ticket, approved analysis digest, exact base/head,
  final diff, trusted test evidence, and open finding ledger;
- follow-up review receives the remediation diff, unresolved findings,
  disposition summary, and affected risk surface.
- final-train review receives the final pull request, exact base/head,
  integrated ticket digests, dependency and collision outcomes, complete
  base-to-train diff, exact-head test evidence, and the train finding ledger.

Every handoff also carries the proportionality-profile revision. Before
implementation, replace the full analysis history with the versioned compact
implementation contract from [efficiency-policy.md](efficiency-policy.md).
Remediation always receives a fresh compact packet; it keeps the same branch
and worktree but not the implementation conversation history.

Include the unique phase key and require the completion envelope from
[orchestration-control.md](orchestration-control.md) in every handoff. Treat a
missing or mismatched phase key as an incomplete child result and request a
targeted correction in the same visible thread.

Use automatic context compaction when the product applies it, but do not rely
on lossy prompt compressors for critical analysis or review. Reduce context
deterministically through scoped handoffs, durable references, filtered command
output, and fresh phase threads.

## Visible thread launch and active supervision

Follow [orchestration-control.md](orchestration-control.md) for every triage,
analysis, implementation, review, follow-up review, and final-train phase.

Create only user-visible phase threads. Never use `fork_thread` for a train
phase, because it duplicates the orchestrator history instead of creating a
clean phase context. Never use hidden collaboration subagents unless the user
explicitly authorizes that fallback for the affected phase after a visible
launch failure is reported.

Persist a unique phase key and launch intent before creation. Treat a timeout,
missing response handler, or thread-creation error as `LAUNCH_UNKNOWN` until
the bounded reconciliation protocol proves whether the task materialized. Do
not immediately retry or launch a hidden replacement; a failed response may
still have produced a visible task.

Keep the orchestrator turn active while any phase is queued, running,
launch-unknown, or ready for an automatic successor. Use bounded compact waits
with current cursors, capture completed results and usage, publish the state
transition, dispatch the next automatic phase, and wait again. Do not require
the user to notice a child completion or send a message that wakes the
orchestrator.

End the orchestrator turn only for `AWAITING_REQUIRED_USER_INPUT`, `BLOCKED`,
`COMPLETED`, or `CHECKPOINT`, after the yield gate confirms that no executable
automatic transition or uncaptured child evidence remains.

Use [efficiency-policy.md](efficiency-policy.md) as the pass and cost budget.
A model turn must correspond to a transition, failure, blocker, dispatch, or
technical decision. An unchanged wait snapshot produces no detailed read,
manifest rewrite, or progress narrative.

## Dependency consolidation and reconciliation

After analysis:

1. Build the directed dependency graph from every consolidated dependency contract.
2. Mark relationships `independent`, `soft-dependency`, `hard-dependency`, or `conflict`.
3. Build collision domains and the implementation schedule.
4. Preserve every conditional variant and validity condition in ticket state.

Before every implementation:

1. Compare the analysis base with the current train.
2. Compare merged predecessor diffs and reports with the ticket's assumptions and variants.
3. Assign `VALID`, `VARIANT_SELECTED`, `REFRESHED_NON_MATERIAL`, `REFRESHED_MATERIAL`, or `INVALID`.
4. Route any required revision to the original analysis thread.
5. Reclassify intrinsic criticality and complexity after a material revision.
6. Reapply the human analysis matrix when the plan changed materially.
7. Recompute ticket and cumulative train size budgets.
8. Materialize the compact implementation contract.
9. For `HIGH` or `MAXIMUM` complexity, complete the bounded independent
   plan-contract validation.

Do not request new human approval for an unchanged plan, a previously approved variant, or a non-material refresh. Do not start implementation until reconciliation and every applicable analysis gate are complete.

## Token accounting

Follow [usage-reporting.md](usage-reporting.md).

Before triage, capture the orchestrator baseline. Track every child thread ID and label it by ticket and phase. After each child thread completes, let the orchestrator read its final cumulative counter so the child's final response is included.

Persist each completed phase capture and delta before dispatching its
successor. Do not defer implementation and review accounting until the final
report. If a counter is unavailable, record that fact immediately so restart
recovery preserves the known measurement boundary.

Use non-overlapping deltas for reused analyzer or other explicitly reused
phase threads. Remediation and focused re-review use fresh sessions. Report:

- phase usage after triage, initial analysis, dependency consolidation,
  analysis reconciliation, plan-contract validation, implementation, initial review, remediation,
  follow-up review, final train validation, final train review, final train
  remediation, and final train follow-up review;
- a ticket total after its final automated review and remediation;
- orchestration separately;
- an aggregate at the train checkpoint.

Use `total_tokens` as the reported total and expose the requested breakdown. Do not add cached or reasoning breakdowns to the supplied total. Mark missing counters `unavailable` and aggregates with incomplete coverage `partial`.

At every checkpoint and completion, run `token_usage.py ledger` against the
manifest. Include every authoritative, duplicate, failed, or cancelled session
and list every unmeasured phase. Tokens are not a weekly-credit counter; report
that limitation and the observable cost drivers without inventing a conversion.

## Planning and scheduling

After collecting the selected analyses:

1. Use the consolidated directed dependency graph.
2. Build collision domains for shared files, modules, contracts, schemas, migrations, invariants, fixtures, central configuration, generated files, and lockfiles.
3. Mark implementations `parallel-safe`, `sequential`, or `blocked`.
4. Give a concrete reason for every implementation-parallelism decision.
5. Treat implementation uncertainty as sequential.
6. Apply the cumulative file, migration/data-transformation, structural-domain,
   and epic checkpoints from [efficiency-policy.md](efficiency-policy.md).

Do not use the maximum concurrency as a utilization target.

## Git and pull-request model

For live execution:

1. Create one train branch from the resolved base branch.
2. Create each ticket branch from the train head that is valid for that ticket.
3. Use one worktree per active ticket branch.
4. Push the ticket branch and open a pull request targeting the train.
5. Keep ticket pull requests narrow and short-lived.
6. Merge ticket pull requests into the train only after all applicable gates.
7. At live finalization, push the train and create or update one final pull
   request from the train into the resolved base branch.
8. Keep the final pull request open across remediation updates and, when the
   user explicitly continues the train, across the next authorized batch.

Follow repository naming rules. Otherwise use the `codex/` prefix and names such as:

```text
codex/train-<date-or-scope>
codex/<ticket-id>-<short-slug>
```

Preserve project-required AI attribution in commits and pull-request descriptions.

Treat the final pull-request diff as the durable ticket diff. Link directly to important files in that diff when GitHub anchors are available.

The final train pull-request description must include the integrated ticket
list, architecture and data summary, automated evidence, remaining manual
validation, known risks, and project-required AI attribution. Creating or
updating this pull request is part of live train execution; merging it is not.

## Implementation and review

The worker must:

1. Receive a reconciled analysis whose human analysis gate is satisfied or bypassed.
2. Re-read the ticket analysis, project instructions, and current train base.
3. Use the implementation model and effort selected from [model-routing.md](model-routing.md).
4. Stay within the approved scope.
5. Implement the ticket and required tests.
6. Avoid unrelated refactors and formatting churn.
7. Run targeted checks and project-required checks.
8. Push the branch and open or update its pull request.
9. For large coherent tickets, implement named internal testable slices in the
   same branch and pull request.
10. Perform the same-phase worker self-review required by
    [efficiency-policy.md](efficiency-policy.md) and fix its findings before
    independent review.
11. Return a technical implementation report with the three recommendation
    categories.
12. Let the orchestrator measure the completed implementation thread.

Launch the worker and every reviewer through the visible-thread protocol in
[orchestration-control.md](orchestration-control.md). A launch-tool error does
not authorize direct implementation in the orchestrator, a conversation fork,
or a hidden worker.

After implementation:

1. Reassess effective intrinsic criticality and complexity from the actual diff.
2. Keep the higher value per dimension between analysis and implementation.
3. Report scope expansion and any newly applicable human gate.
4. Route the independent reviewer from the effective values under [model-routing.md](model-routing.md).
5. Run one exhaustive independent review under
   [review-policy.md](review-policy.md) and require the complete finding
   inventory in its first response.
6. Merge Codex, Copilot, CI, and human findings into the deduplicated ledger
   from [review-policy.md](review-policy.md).
7. Route accepted actionable findings to one fresh compact remediation thread
   in one batch while retaining the ticket branch and worktree.
8. Re-review only the remediation delta, unresolved findings, dispositions,
   and affected risk surface unless a material change requires a full review.
9. Allow at most two grouped remediation and focused-review cycles. If blocking
   findings remain, stop for root-cause reassessment instead of beginning a
   third automatic cycle.
10. Measure each non-overlapping remediation and review interval without
    creating extra turns solely to separate token attribution.

## Sequential execution

For sequential or dependent tickets:

1. Analyze every selected ticket under the parallel-conditional policy without waiting for predecessors.
2. Complete the predecessor's analysis gate.
3. Implement the predecessor.
4. Complete its automated tests, review, and required human pre-merge validation.
5. Merge the predecessor pull request into the train.
6. Reconcile the dependent ticket's analysis against the updated train.
7. Complete any renewed human analysis gate caused by a material revision.
8. Implement the dependent ticket.

Do not pre-create dependent worktrees from a stale train base.

## Parallel execution

Run at most two parallel implementations, and only for tickets proven independent.

Serialize integration:

1. Merge one validated ticket into the train.
2. Update every remaining ticket branch onto the new train.
3. Confirm it still merges cleanly.
4. Rerun affected tests.
5. Run a targeted integration-impact review against the new base. Run a new
   complete review only when the refresh materially changes architecture, a
   critical migration or data strategy, an authorization/privacy boundary,
   functional scope, a shared contract, or an unrelated risk surface.
6. Reassess both dimensions and complete any human gate applicable to the updated diff.
7. Merge the next ticket.

Do not rely on a clean textual merge as proof of semantic compatibility.

## Final train pull request and checkpoint

Count ticket pull requests merged into the train. Do not count review-fix commits as additional tickets.

When a selected live queue finishes with at least one integrated ticket, when
the fifth ticket is integrated, or when a cumulative size checkpoint is
crossed, finalize the current train. A stopped run
with no integrated ticket produces only the consolidated run report.

Finalize in this order:

1. Freeze train finalization and do not start another implementation while its
   evidence is being collected.
2. Run the proportional project-required verification suite, reusing
   trustworthy exact-head ticket evidence and adding integration-level checks.
3. Push the current train head.
4. Create or update one final train pull request targeting the resolved base
   branch and record its URL and exact head.
5. Route one independent final-train review through
   the initial automated-review matrix. Derive one train-level classification
   from consolidated evidence and use the highest selected ticket
   initial-review setting as its floor. Do not combine a criticality row from
   one ticket with a complexity column from another merely to force a higher
   cell; raise routing only for evidenced train-level interactions.
   Concentrate the review on cross-ticket interactions, integration glue,
   cumulative invariants, migrations, deployment, and code not covered by
   trustworthy unchanged ticket reviews. Record the computed train classification, every integrated ticket's
   initial-review setting, the resulting floor, requested setting, actual
   setting, and routing conformance before accepting the review. A pre-PR
   branch review cannot substitute for this final pull-request review.
6. Collect exact-head CI results and available Copilot, Codex, and human
   comments into one deduplicated train finding ledger.
7. Technically assess every Copilot suggestion. Never apply it automatically
   and never ask the user to triage ordinary findings.
8. Batch compatible accepted findings once. When ticket-specific context is
   material, request a compact targeted handoff; apply accepted fixes from a
   fresh remediation branch and fresh remediation thread based on the current train. Classify
   each remediation batch from its actual risk and complexity, route it through
   the implementation matrix, and use a dedicated final-train remediation
   worker for cross-ticket findings.
9. Merge at most one remediation pull request into the train at a time. The
   final train pull request updates automatically with the train head. Apply
   any human pre-merge gate required by the remediation classification and
   approval mode.
10. After every head update, invalidate only evidence affected by the changed
    risk surface, rerun affected and required checks, and route targeted
    verification through the follow-up-review matrix and complete-review
    ceiling. Expand to a full review only for the material invalidation
    triggers in [efficiency-policy.md](efficiency-policy.md). Stop after two
    final remediation cycles for root-cause reassessment.
11. Declare the final pull request ready only when its recorded head matches
    the train, mandatory exact-head checks pass, automated review has no
    blocking finding, and every available comment has a disposition.
12. If Copilot is not configured, unavailable, or does not respond within the
    bounded monitoring window, report that status. Do not block indefinitely
    unless repository policy or the user made Copilot review mandatory.
13. Build the concise manual validation plan and code/application attention
    summaries required by [report-template.md](report-template.md).
14. Capture final train validation, review, remediation, follow-up review, and
    final pre-report orchestration usage separately when counters permit it.
15. Produce the completion or checkpoint report for every requested ticket,
    including blocked, failed, cancelled, and completed work.
16. Wait for the user.

Before step 16, run the completion checklist from
[report-template.md](report-template.md). Do not declare the train closed when
token accounting, task inventory, exact-head CI/Copilot status, routing
conformance, manual validation, attention points, or any requested ticket
report is missing. Mark unavailable evidence explicitly rather than omitting
it.

Tie every test, Codex review, Copilot collection, and readiness statement to an
exact final pull-request head. If Codex is explicitly asked to merge the final
pull request, refresh CI and all newly available comments immediately before
the merge.

When the selected queue finishes before five integrations, the user may merge
the final pull request or explicitly continue the same train. Continuing marks
the finalization evidence stale and returns the train to `TRAIN_OPEN`.

After the fifth integrated ticket, the train remains frozen. Do not start a
sixth ticket unless the user explicitly authorizes additional tickets.

The user may:

- merge the final train pull request on GitHub;
- explicitly ask Codex to merge it into the base branch;
- explicitly authorize additional tickets without merging.

Only the user may initiate the third option. Record the exception and any new ticket limit.

## Failure and recovery

On restart or context compaction, recover the run from the durable state
manifest and verify live branch, pull-request, thread, and gate state before
continuing. Do not replay completed technical phases merely to reconstruct
context.

Reconcile every `LAUNCH_UNKNOWN`, queued, or running visible phase before
dispatching anything new. Capture child results that completed while the
orchestrator was inactive, recompute the next automatic action, report the
recovered state, and continue without requiring a user command unless a human
gate or blocker is active.

Pause a ticket when:

- its source changed materially after analysis;
- reconciliation marks its plan `INVALID` until the original analyzer revises it;
- the worker exceeds scope;
- tests cannot run or fail persistently;
- review has unresolved blocking findings;
- a required human gate is waiting;
- credentials or environments are unavailable.

Continue unrelated tickets only when doing so cannot invalidate the blocked ticket or consume its required integration order.

Do not treat an ambiguous task-tool response as a confirmed failure. Exhaust
the bounded visible-thread reconciliation protocol first. If reliable visible
launch or monitoring remains unavailable, preserve the manifest and report a
platform blocker instead of silently using hidden execution.

Never hide a failure by weakening tests, review criteria, branch protection, or project rules.
