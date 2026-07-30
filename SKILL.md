---
name: ticket-train
description: Explicitly orchestrate bounded, cost-controlled trains of implementation tickets from a user-provided source. Use when the user invokes $ticket-train to triage tickets in visible threads, apply a product proportionality profile, reconcile plans, run implementation and independent acceptance-test authoring in parallel branches and worktrees, enforce red/green functional and environment gates including Supabase/Auth/RLS when applicable, route model effort strictly, run one exhaustive review plus grouped remediation and targeted re-review, validate the final train pull request, report manual tests and attention points, measure per-session and per-phase token usage, enforce human gates, and stop at ticket-count or review-surface checkpoints. Do not use for ordinary one-off coding tasks or without explicit invocation.
---

# Ticket Train

Orchestrate a bounded, reviewable implementation train while keeping ticket analysis, implementation, and review contexts separate.

## Load the workflow

Read these references before starting:

- [workflow.md](references/workflow.md) for lifecycle, concurrency, branches, gates, and stopping rules.
- [orchestration-control.md](references/orchestration-control.md) for visible-thread launch, ambiguous outcomes, active supervision, durable recovery, and yield gates.
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
12. Actual orchestrator model and reasoning effort, when observable.
13. Recommended orchestrator model and effort under the startup preflight.
14. Proportionality profile revision and its material assumptions.
15. Train size budget and current estimated review surface.
16. Review-pass budget, fixed to one complete pass and at most two grouped
    remediation/follow-up cycles per stable scope.
17. Cost-control policy, fixed to `strict-quality-preserving`.
18. Verification policy, fixed to `parallel-independent-red-green`.
19. Environment tiers required by the selected tickets.

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

Follow [orchestration-control.md](references/orchestration-control.md) for
every phase dispatch. Record a unique phase key and launch intent before
creation, reconcile asynchronous or ambiguous outcomes before retrying, and
keep the orchestrator active until it reaches an allowed human gate, blocker,
completion, or checkpoint.

Use [train_supervisor.py](scripts/train_supervisor.py) to reconcile changed
thread snapshots, GitHub status, and test artifacts into the manifest. Do not
use LLM turns for unchanged polling or raw-log status extraction.

Keep a mapping from ticket to source locator, analysis base commit, intrinsic criticality, complexity, conditional assumptions, validity conditions, reconciliation state, routing decisions, routing conformance, dependencies, worker thread, reviewer thread, branch, worktree, pull request, usage snapshots, gate state, and train commit. Also track the final train pull request, reviewed head, CI and Copilot state, final finding ledger, manual validation plan, and code/application attention points.

Also track the proportionality profile revision, ticket and cumulative train
size budgets, compact implementation contract, plan-contract validation,
internal slices, worker self-review, complete-review count, remediation-cycle
count, log artifacts, all authoritative and duplicate sessions, unmeasured
phases, and cost-anomaly checkpoints.

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

At a high level:

1. Normalize the selected tickets.
2. Capture the initial orchestrator usage baseline when available.
3. Recommend `Terra/H` or `Sol/H` for the orchestrator, report the current
   setting, and obtain explicit confirmation to continue.
4. Create and version the mandatory proportionality profile and transmit it
   to every technical phase.
5. Perform a short routing triage in a dedicated read-only thread with an
   explicit `Terra/H` override, without producing a technical plan.
6. Record one common analysis base commit and queue every selected ticket for analysis immediately.
7. Resolve every routed matrix cell mechanically, dispatch every phase with
   explicit model and reasoning settings, and record requested, actual, and
   conformance values.
   Apply the visible launch and active supervision protocol to every dispatch;
   never interpret an ambiguous creation response as proof that no task was
   created.
8. Route and run one full analysis per ticket with at most five active threads, without dependency or human-gate waits.
9. Consolidate returned dependency contracts and route targeted amendments to original analysis threads.
10. Require every recommendation to separate the minimum required correction,
    optional hardening, and explicitly deferred post-MVP work.
11. Return every consolidated analysis to the main thread through the compact,
   self-sufficient structural-impact digest required by the report template,
   together with its measured token usage.
12. Confirm intrinsic criticality and complexity.
13. Apply the matrix-based human analysis gate independently to each ticket.
14. Build dependency and collision maps and apply the cumulative train-size
    checkpoint.
15. Before each implementation, reconcile its conditional analysis against the current train and merged predecessors.
16. Materialize compact implementation and verification contracts. For `HIGH`
    or `MAXIMUM` complexity, run the bounded plan-contract validation.
17. Schedule only proven-independent ticket execution pairs in parallel.
18. Route the implementation and independent acceptance-test workers from the
    confirmed classification and verification complexity.
19. Create implementation and acceptance-test branches/worktrees from the same
    exact train commit. Keep test authorship independent until its first commit
    and baseline-red evidence are durable.
20. Use internal implementation slices for large coherent tickets and require
    the worker's same-phase self-review.
21. Push both branches, open the ticket pull request as draft, and integrate
    the independent test commit through a test pull request into the ticket
    branch or an equivalent durable merge.
22. Run baseline-red, exact-head integrated-green, environment-parity, and
    applicable Supabase/Auth/RLS checks. Adjudicate failures before review.
23. Run `control_guard.py check-verification` and mark the ticket pull request
    ready for automated code review only when functional readiness passes.
24. Reassess both dimensions from the final production and test diff.
25. Route one exhaustive independent automated review through the
    initial-review matrix and require its complete finding inventory.
26. Consolidate Codex, Copilot, CI, and human findings into one deduplicated
    disposition ledger, then send one grouped remediation packet to a fresh
    compact remediation thread on the same branch and worktree.
27. Require a reproducing regression test for every confirmed behavioral
    defect, then run a targeted re-review by default. Permit another full review only for
    the material scope changes listed in the efficiency policy, never merely
    because the train base advanced.
28. Stop automatic remediation after two cycles and perform a root-cause and
    cost-anomaly checkpoint instead of looping.
29. Measure non-overlapping triage, analysis, consolidation, reconciliation,
    plan-contract validation, implementation, acceptance-test authoring,
    red/green/environment validation, initial review, remediation, follow-up review, final train
    validation, and orchestration usage.
30. Apply the matrix-based human pre-merge gate selected by the run mode.
31. Serialize merges into the train.
32. Report each integrated ticket and its ticket total in the main thread.
33. When the requested live queue finishes or a ticket-count or size
    checkpoint is reached, freeze finalization, push the train, and create or update one
    final train pull request against the base branch.
34. Run integrated cross-ticket acceptance and applicable clean-reset
    Supabase/Auth/RLS checks on the exact train head.
35. Review cross-ticket interactions and unreviewed integration surfaces in
    the current final pull request, reuse trustworthy unchanged ticket-review
    evidence, consolidate all feedback, and apply the same grouped-remediation
    and targeted-re-review limits.
36. Report the final pull request, its exact reviewed head, readiness, a
    deduplicated manual validation plan, and separate code and application
    attention points.
37. Report the measured run total with baseline/final/delta for every known
    session, including duplicate attempts and explicitly unmeasured phases.
38. Keep the train frozen after five integrated tickets or a cumulative-size
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

When a human analysis or pre-merge gate is required, pause only the affected ticket and implementations that depend on it. Continue every selected analysis and any independent implementation whose gates are satisfied.

After five integrated tickets, do not start a sixth ticket. Create or update
the final train pull request, complete its validation and feedback loop, and
report the checkpoint. Continue only if the user explicitly requests
additional tickets. Never infer that exception.

Never merge the train into `main` or `master` unless either:

- the user performs the merge on GitHub; or
- the user explicitly asks Codex to perform that merge.

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
phase whose counter or baseline is unavailable.

Reuse trustworthy CI evidence only when it is tied to the exact reviewed head
commit and all required checks are available. Reviewers still run independent
risk-targeted checks for the ticket's critical surfaces. If CI evidence is
missing, stale, or broken, fall back to the project-required local validation
and disclose the duplication.

Before every final response, apply the yield gate from
[orchestration-control.md](references/orchestration-control.md). Do not end the
orchestrator turn while visible work is queued or running, a launch outcome is
unknown, an automatic successor is ready, or completed evidence remains to be
captured. At live completion, create or update the final pull request before
the final Codex review and feedback collection.

Run [control_guard.py](scripts/control_guard.py) against the reconciled durable
manifest before yielding, completion, or checkpoint. A failing guard means the
orchestrator must continue, capture missing evidence, or report a real blocker;
never ignore the failure to produce a shorter final response.

At completion, early stop, and every ticket-count or size checkpoint, provide
one consolidated report for every requested ticket and state exactly what
still requires user action.
