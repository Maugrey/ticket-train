# Model and Reasoning Routing

Active policy version: `2026-08-27-v2`. Superseded matrices are preserved in
[model-routing-history.md](model-routing-history.md).

## Contents

- Routing stages
- Orchestrator startup preflight
- Fast triage
- Strict routing enforcement
- Analysis routing
- Implementation routing
- Independent acceptance-test routing
- Initial automated review routing
- Final train review routing
- Follow-up review routing
- Max authorization and exceptional Ultra
- Unavailable settings

## Routing stages

Use intrinsic criticality and complexity directly. Do not derive a combined control level.

- Route the full analysis from provisional triage values.
- Route implementation from intrinsic criticality and the validated residual
  implementation complexity, never from analysis complexity by default.
- Route independent acceptance-test authoring from effective intrinsic
  criticality and verification complexity through its dedicated matrix.
- Route initial automated review from the higher per-dimension values between
  the analysis and the actual implementation diff.
- Route focused follow-up review from current effective intrinsic criticality
  and remediation-verification complexity.

Do not route a model for deterministic control work: task polling, Git/GitHub
status reads, exact-head command execution, log hashing, token-ledger
reconciliation, or unchanged waits. Record these as `deterministic/zero-token`.
Decision-packet generation, unchanged-wait suppression, activity-budget
evaluation, and handoff-token validation are also deterministic/zero-token
through `control_plane_runner.py` and `run_registry.py`.
Use `Terra/H` only when a structured failure or finding inventory requires
bounded classification; escalate through the applicable matrix only when the
technical decision itself has that evidenced complexity or risk.

Use explicit per-thread model and reasoning overrides for every routed phase.
Never use the parent thread's model or reasoning effort as an implicit routed
setting. Record the matrix inputs, selected cell, requested model and effort,
actual model and effort, and any documented fallback.

Apply the train's proportionality profile to the classification inputs. The
profile changes evidence and scope, not the matrix cells. Never use a more
expensive setting than the selected cell merely because the parent thread or
an old remediation thread already uses it.

Legend:

- `Luna/M`: `gpt-5.6-luna` with `medium`
- `Terra/M`: `gpt-5.6-terra` with `medium`
- `Terra/H`: `gpt-5.6-terra` with `high`
- `Sol/M`: `gpt-5.6-sol` with `medium`
- `Sol/H`: `gpt-5.6-sol` with `high`
- `Sol/XH`: `gpt-5.6-sol` with `xhigh`
- `Sol/Max`: `gpt-5.6-sol` with `max`

Do not define one global capability order. Model family, reasoning effort, and
delegation mode are separate attributes. For focused-review ceilings only,
use this phase-local compatibility table:

| Full-review baseline | Allowed focused-review routes |
|---|---|
| Terra/M | Terra/M |
| Terra/H | Terra/M, Terra/H |
| Sol/H | Terra/M, Terra/H, Sol/H |
| Sol/XH | Terra/M, Terra/H, Sol/H, Sol/XH |
| Sol/Max | Terra/M, Terra/H, Sol/H, Sol/XH, Sol/Max |

`Sol/M` and Luna are intentionally absent from review matrices, so the
controller never invents a comparison between them and Terra/High.

## Orchestrator startup preflight

Recommend the main-conversation setting from orchestration complexity, not
from the highest ticket criticality or the number of child phases routed to
`XH`.

Default to `Terra/M` when the train:

- targets one repository, one base branch, and one train branch;
- stays within the normal five-ticket checkpoint;
- has a resolved, coherent source and selection;
- can delegate technical analysis, implementation, and review to routed
  threads;
- can maintain compact durable state and use normal thread-management tools;
- has no source-visible evidence of a dense or contradictory dependency graph.

Recommend `Terra/H` when at least one material orchestration condition applies:

- the run resumes from missing, stale, or inconsistent state and must reconcile
  several branches, pull requests, thread results, or gate decisions;
- the authorized scope spans several repositories, base branches, train
  branches, trackers, deployment targets, or other systems whose states must be
  reconciled;
- declared dependencies or shared central contracts make the source-visible
  ticket graph dense, contradictory, or likely to require repeated scheduling
  arbitration;
- the user authorized an extended train beyond the normal five-ticket
  checkpoint and the orchestrator must coordinate several batches;
- compact durable state or normal thread controls are unavailable, forcing the
  main conversation to retain and reconcile materially more context;

Recommend `Sol/H` only when a material technical arbitration cannot be
delegated and must exceptionally occur in the main conversation. Ticket
criticality alone never causes this route.

Do not recommend `Sol/H` solely because tickets are `HIGH` or `CRITICAL`,
because human gates are expected, or because child analyses and reviews use
`Sol/XH`. Those concerns belong to the routed child phases.

Do not recommend `XH` for routine orchestration. When one isolated dependency
or architecture conflict needs deeper reasoning, keep the orchestrator at
`High` and dispatch a scoped `Sol/XH` arbitration thread.

Do not keep an overprovisioned orchestrator merely for continuity. Every
controlled successor applies this same preflight recommendation from the
current run state and receives only the bounded decision packet. Rotation does
not authorize a stronger model or effort.

After resolving the source, selection, run mode, and repository configuration,
but before triage or any child dispatch:

1. Determine the recommended orchestrator setting from the criteria above.
2. Read the current conversation's actual model and reasoning effort when
   observable. Never infer it from the user's usual default.
3. Classify the current setting:
   - `recommended`: exact match;
   - `acceptable-overprovisioned`: at least as capable but more expensive than
     recommended;
   - `underprovisioned`: below the recommended model or reasoning requirement;
   - `unknown`: actual values are not observable.
4. Publish the startup preflight from
   [report-template.md](report-template.md).
5. Ask the user whether to continue with the current conversation setting.

This confirmation is mandatory and is not bypassed by `auto-analysis`,
`auto-merge`, or `full-auto`. An initial read-only orchestrator usage baseline
may be captured so the preflight cost remains measurable. Do not launch
triage, create ticket threads, or mutate repository state before confirmation.

If the user declines, stop cleanly and state the recommended setting for a new
conversation. The skill cannot change the model or reasoning effort of the
already-running main conversation.

If the current setting is `unknown`, ask the user to verify the composer
setting or explicitly confirm continuation with an unknown setting. If the run
configuration later changes enough to alter the recommendation, publish a new
preflight and request confirmation again.

Classify common current settings deterministically:

- exact recommended combination: `recommended`;
- `Terra/H`, `Sol/H`, or a higher Sol effort when `Terra/M` is recommended:
  `acceptable-overprovisioned`;
- `Sol/H` or a higher Sol effort when `Terra/H` is recommended:
  `acceptable-overprovisioned`;
- a higher Sol effort when `Sol/H` is recommended:
  `acceptable-overprovisioned`;
- a setting below the recommendation for the selected orchestration profile:
  `underprovisioned`;
- any combination whose relative capability cannot be established:
  `unknown`.

## Fast triage

Perform one short batch routing pass in a dedicated read-only thread before
launching the full analysis threads. Triage is not a technical analysis.

Route it deterministically:

- `Terra/M` by default;
- `Terra/H` when low confidence or sensitive ambiguity can cross a routing
  boundary, or targeted searches reveal authorization, privacy, payment,
  migration, concurrency, deployment, shared-contract, or invariant risk;
- `Luna/M` only when the complete mechanical fast-path proof below is already
  available from ticket metadata.

Use only:

- ticket title, description, acceptance criteria, labels, and metadata;
- explicitly named domains, modules, files, contracts, or migrations;
- project context already held by the orchestrator;
- at most a few targeted repository searches needed to identify the named surface.

Do not:

- trace full call graphs or dependency chains;
- read modules exhaustively;
- design a solution or propose an implementation plan;
- enumerate detailed files, symbols, or tests;
- verify feasibility;
- run tests.

Return only:

```text
provisional_intrinsic_criticality
provisional_complexity
confidence
one_or_two_reasons_per_dimension
declared_or_suspected_dependencies
suspected_collision_domains
analysis_model
analysis_reasoning_effort
```

Choose the higher plausible value when evidence is incomplete. This conservative routing prevents a second full analysis. The full analyzer confirms or corrects the values as part of its one analysis.

`Terra/M` is appropriate because the ordinary pass is bounded to ticket-visible
classification and routing. If a ticket contains an ambiguous or plausible
signal involving authorization, privacy, payments, irreversible data,
concurrency, migrations, shared contracts, deployment safety, or a fundamental
invariant, classify to the higher plausible criticality or complexity rather
than investigating deeply during triage.

If triage confidence is low and the uncertainty could cross a routing boundary,
use `Terra/H`, choose the more demanding plausible matrix cell, and do not
spend additional triage tokens trying to replace the full analysis.

The Luna triage fast path requires all of these facts to be explicitly true:

```text
fully_specified
direct_deterministic_oracle
no_shared_contract
no_critical_invariant
no_migration
no_concurrency
no_access_boundary
reversible
immediately_detectable
```

It may return only `LOW/LOW` with high confidence and no dependency or
collision signal. Any missing proof or newly discovered ambiguity routes the
triage to Terra; do not let Luna make an uncertain downstream routing choice.

## Strict routing enforcement

Apply every matrix lookup through `scripts/train_controller.py`. The matrices
in this reference and the controller constants are a versioned pair; change and
test them together. A manually selected model/effort is not valid merely
because it is stronger. The controller rejects any unexplained mismatch.

Before dispatching any analysis, implementation, initial review, or follow-up
review:

1. Record the classification checkpoint used by that phase.
2. Resolve the exact matrix row and column without judgment or interpolation.
3. Record the exact selected model and reasoning effort.
4. Verify that the thread tool supports both requested values.
5. Pass both values explicitly when creating the thread.
6. For a reused thread, verify that its current model and effort exactly match
   the selected cell before sending the next phase.

Require every routed thread to report its actual model and reasoning effort.
Record one routing status:

- `conformant`: actual values exactly match the selected cell;
- `documented-fallback`: the exact setting was unavailable and the fallback
  follows this reference;
- `nonconformant`: any other mismatch.

A stronger or more expensive model or effort is not automatically conformant.
For example, `Sol/XH` is nonconformant when the selected cell is `Sol/H`.

Do not continue to the next phase after an unexplained `nonconformant` result.
Report the mismatch and obtain an explicit user decision instead of silently
rerunning an expensive phase or accepting the deviation.

Do not reuse a thread merely to preserve conversational continuity when its
setting differs from the routed setting. Create a new phase thread and provide
a compact handoff containing the ticket, approved analysis revision, exact
diff or commit range, unresolved findings, tests, and durable references.

## Analysis routing

| Intrinsic criticality ↓ / Complexity → | `LOW` | `MEDIUM` | `HIGH` | `MAXIMUM` |
|---|---:|---:|---:|---:|
| `LOW` | Terra/M | Terra/H | Sol/H | Sol/XH |
| `NORMAL` | Terra/H | Sol/M | Sol/H | Sol/XH |
| `HIGH` | Sol/H | Sol/H | Sol/XH | Sol/XH |
| `CRITICAL` | Sol/XH | Sol/XH | Sol/XH | Sol/Max |

Use Terra for simple, bounded analysis, `Sol/M` for the intermediate
`NORMAL/MEDIUM` case, and Sol/High or above only when the actual exploration,
ambiguity, or consequences justify it. Intrinsic criticality `CRITICAL`
imposes an `XH` floor. Reserve `Max` for `CRITICAL/MAXIMUM`.

Create only one full analysis thread per ticket. Do not launch a second analyzer merely because the confirmed classification differs from triage.

If the confirmed values require a route not covered by the dispatched route:

- do not restart or duplicate the analysis;
- finish the single analysis at its original routed setting;
- dispatch one targeted validation of only the risk-sensitive or complex
  sections at the confirmed route;
- block consolidation until that validation passes or returns the analysis to
  scoped reconciliation;
- use the confirmed values for every downstream gate and routing decision;
- apply any human analysis validation required by the confirmed values.

Route dependency consolidation amendments and pre-implementation reconciliation back to the original analysis thread at its existing setting. Do not create a second analyzer for a targeted revision.

When reconciliation marks an analysis `INVALID`, perform the scoped revision under [analysis-policy.md](analysis-policy.md). If the confirmed classification now requires a higher effort than the original thread can use, apply the routing matrix and the separate Max authorization rule. Create a replacement thread only when the original thread cannot perform the required revision, then report the substitution and preserve all stable findings.

## Implementation routing

| Intrinsic criticality ↓ / Complexity → | `LOW` | `MEDIUM` | `HIGH` | `MAXIMUM` |
|---|---:|---:|---:|---:|
| `LOW` | Terra/M¹ | Terra/M | Sol/H | Sol/XH |
| `NORMAL` | Terra/M | Terra/H | Sol/H | Sol/XH |
| `HIGH` | Sol/M | Sol/H | Sol/XH | Sol/XH |
| `CRITICAL` | Sol/H | Sol/XH | Sol/XH | Sol/Max |

¹ `Luna/M` is allowed only through the complete mechanical fast path.

The column is `residual_implementation_complexity`, not the analysis
complexity. The analysis and, when applicable, the plan-contract validator
must report:

```text
analysis_complexity
residual_implementation_complexity
verification_complexity
complexity_reduction_evidence
unresolved_implementation_difficulty
```

Treat the reconciled analysis as the implementation contract, but do not
reduce effort merely because a contract exists. Lower residual complexity only
when evidence identifies which design uncertainty or implementation factor was
actually removed. Keep `CRITICAL/MAXIMUM` at Sol/Max when coding itself still
contains maximum difficulty; route it to Sol/XH when validated residual
complexity is High.

An implementation worker must not improvise through a material gap in the
approved analysis. If it discovers a new architecture decision, invariant,
scope expansion, weak test oracle, or implementation uncertainty that could
change either classification, stop before making the material change. Route
the evidence to analysis reconciliation, reclassify, apply any renewed human
gate, and dispatch the resulting implementation setting explicitly.

For the bounded `HIGH`/`MAXIMUM` plan-contract validation required by
[efficiency-policy.md](efficiency-policy.md), use `Terra/M` by default and
`Terra/H` only when the compact contract contains several contradictions or a
sensitive surface. It is a one-turn completeness check without repository-wide
exploration, not a second technical analysis. Return broader uncertainty to
the analyzer instead of raising this phase to Sol.

### Remediation routing

Use the implementation matrix with the current effective intrinsic
criticality and the actual complexity of the remediation batch. Do not reuse
the analysis criticality or original implementation complexity. Classify the
delta as `mechanical`, `bounded-behavioral`, `cross-cutting`, or
`material-scope`. A `material-scope` delta returns to analysis reconciliation
and a new full review; it is not eligible for ordinary remediation dispatch.

`Luna/M` may replace `Terra/M` only for a `LOW/LOW` mechanical correction with
the complete fast-path proof. `CRITICAL/MAXIMUM` remediation remains Sol/Max
and requires its own scoped authorization.

## Independent acceptance-test routing

Classify verification complexity independently from implementation complexity:

- `LOW`: one deterministic behavior, local fixture, direct oracle, no material
  role or environment variation;
- `MEDIUM`: several scenarios or layers, stable integration, clear fixtures and
  expected results;
- `HIGH`: roles/RLS, Auth sessions or cookies, concurrency, migration,
  multi-layer E2E, environment parity, or several interacting negative paths;
- `MAXIMUM`: hosted and local parity, multi-system identity or data lifecycle,
  recovery testing, weak oracle, or several interdependent environments.

Resolve effective intrinsic criticality and verification complexity through
this dedicated matrix:

| Intrinsic criticality ↓ / Verification complexity → | `LOW` | `MEDIUM` | `HIGH` | `MAXIMUM` |
|---|---:|---:|---:|---:|
| `LOW` | Terra/M¹ | Terra/H | Sol/H | Sol/XH |
| `NORMAL` | Terra/H | Terra/H | Sol/H | Sol/XH |
| `HIGH` | Sol/H | Sol/H | Sol/XH | Sol/XH |
| `CRITICAL` | Sol/H | Sol/XH | Sol/XH | Sol/Max |

¹ `Luna/M` is allowed only for one fully specified deterministic behavior with
a direct oracle and the complete mechanical proof. Several scenarios, roles,
environment variation, migration, concurrency, or an indirect oracle disable
the fast path immediately.

Create a fresh visible acceptance-test thread with explicit model and effort.
It receives the verification contract, not the implementation diff. Require
the same routing-conformance record as any other routed phase. Deterministic
red/green, environment, and test-command execution does not require a new
model setting or model turn.

## Initial automated review routing

| Intrinsic criticality ↓ / Complexity → | `LOW` | `MEDIUM` | `HIGH` | `MAXIMUM` |
|---|---:|---:|---:|---:|
| `LOW` | Terra/M | Terra/H | Sol/H | Sol/XH |
| `NORMAL` | Terra/H | Sol/H | Sol/H | Sol/XH |
| `HIGH` | Sol/H | Sol/XH | Sol/XH | Sol/XH |
| `CRITICAL` | Sol/XH | Sol/XH | Sol/XH | Sol/Max |

Use the higher per-dimension values between the approved analysis and the
actual implementation diff. Initial review must independently verify the
complete ticket diff, acceptance criteria, project rules, test evidence, and
material risk surfaces.

Use `H` for bounded review surfaces whose expected behavior and test oracle
are clear. Use `XH` when criticality or complexity requires broader
cross-checking, including every `CRITICAL` ticket and every `MAXIMUM`
complexity review. Reserve `Max` for the combined `CRITICAL` and `MAXIMUM`
case. Ultra is not a reasoning effort in the standard routing system.

## Final train review routing

Route the complete final pull-request review through the initial automated
review matrix after the final pull request exists.

`Complete` here means complete coverage of integration risk, cumulative scope,
and previously unreviewed code at the exact final head. Reuse trustworthy
exact-commit ticket reviews; do not spend another full pass re-reviewing every
unchanged ticket file.

Before dispatch:

1. Derive a train-level intrinsic criticality and complexity from consolidated
   ticket evidence and evidenced cross-ticket interactions.
2. Resolve that classification through the initial-review matrix.
3. For every integrated ticket, identify its latest trustworthy full review at
   the exact reviewed commit.
4. Mark its setting as an applicable floor only when that evidence is
   invalidated, its protected surface participates in an integration
   interaction, or unreviewed integration code affects that surface.
5. Reuse unaffected exact-commit ticket reviews without applying their setting
   as a floor. Select the strongest applicable review floor through the
   review-only compatibility table.
6. Record the classification, matrix cell, floor, requested model and effort,
   and authorization or fallback before creating the reviewer.
7. Verify the reviewer's actual model and effort after dispatch.

Do not combine a criticality row from one ticket with a complexity column from
another merely to manufacture a higher matrix cell. Require explicit evidence
for both applicable and non-applicable ticket floors. Treat any unexplained
mismatch, including a cheaper or more expensive setting, as `nonconformant`.

## Follow-up review routing

Route a focused follow-up review from:

- the current effective intrinsic criticality after considering the
  remediation diff and unresolved-finding risk; and
- the complexity of verifying the remediation, not the original ticket's
  implementation complexity.

Before matrix lookup, classify the remediation delta:

- `mechanical`: assertion, message, documentation, fixture, or configuration
  correction with a direct deterministic oracle;
- `bounded-behavioral`: a local behavior correction with a reproducing
  regression test and no new contract or invariant;
- `cross-cutting`: several modules or a protected boundary interact, while the
  approved scope remains valid;
- `material-scope`: architecture, schema/data strategy, authorization,
  functional scope, shared contract, or another protected surface changed.

`material-scope` never uses a focused re-review. The controller requires a new
scope revision and a full review. For the other classes, use the verification
complexity demonstrated by the delta and its oracle; do not inherit the
original ticket complexity or the parent conversation's setting.

| Effective intrinsic criticality ↓ / Follow-up verification complexity → | `LOW` | `MEDIUM` | `HIGH` | `MAXIMUM` |
|---|---:|---:|---:|---:|
| `LOW` | Terra/M | Terra/H | Sol/H | Sol/XH |
| `NORMAL` | Terra/H | Sol/H | Sol/H | Sol/XH |
| `HIGH` | Sol/H | Sol/H | Sol/XH | Sol/XH |
| `CRITICAL` | Sol/H | Sol/XH | Sol/XH | Sol/Max |

Classify follow-up verification complexity as:

- `LOW`: one or a few precisely identified findings, a narrow remediation
  diff, a direct regression test, and no new invariant or decision;
- `MEDIUM`: several related findings or several files or layers, with a clear
  expected result and verification method, and no material redesign;
- `HIGH`: a cross-module fix, concurrency, migration, access-control or
  data-integrity interaction, a broad regression surface, or an incomplete or
  indirect test oracle;
- `MAXIMUM`: a multi-system change, weak verification oracle, broad redesign,
  recovery-sensitive behavior, or verification that requires reconstructing
  the complete technical reasoning.

Use this matrix only for a scoped remediation whose approved analysis remains
valid. Reassess both intrinsic criticality and follow-up verification
complexity from the actual remediation diff and unresolved findings before
dispatch.

Apply a non-bypassable focused-review ceiling after resolving the matrix cell:

1. Find the latest complete ticket-diff review with a `conformant` or
   `documented-fallback` routing status.
2. Use its actual model and effort as the ceiling. Never let an unexplained
   overprovisioned or otherwise `nonconformant` execution raise the ceiling.
3. Compare the follow-up matrix result with the ceiling using the strict order
   above.
4. If the result is equal or lower, dispatch the focused follow-up review at
   the exact matrix-selected setting.
5. If the result is higher, do not cap it and do not dispatch a focused
   follow-up review. Reconcile the analysis as needed, reassess intrinsic
   criticality and implementation complexity from the current complete diff,
   and route a complete review through the initial automated-review matrix.

If no trustworthy complete-review baseline exists, do not run a focused
follow-up review. Run a complete initial review instead. A newly completed
`conformant` or `documented-fallback` full review becomes the baseline for
later remediation cycles.

Do not use a focused follow-up review when remediation materially changes
architecture or ownership, a critical schema/data/recovery strategy,
functional scope or acceptance behavior, a public/shared contract with new
consumers, security/access/privacy boundaries, or introduces an unrelated
material risk surface. Reconcile
the analysis, reassess the ticket's intrinsic criticality and implementation
complexity, and run a complete initial review through the initial automated
review matrix instead.

A train-base refresh alone is not a reason for a complete review. Use the
follow-up matrix for a targeted integration-impact review of changed contracts,
collision domains, and affected tests unless one of the material triggers
above applies.

Treat `MAXIMUM` follow-up verification complexity as exceptional. Confirm
that the work still qualifies as a focused follow-up before routing it;
otherwise return to complete initial review. Ultra is not a reasoning effort
in the standard follow-up-review matrix.

## Max authorization and exceptional Ultra

Set the default reasoning cap to `xhigh`.

Before using `max`, obtain explicit user authorization. Approval modes such as
`full-auto` do not grant this authorization. Persist the authorization with an
exact stage, ticket or run scope, optional head, user-decision reference, and
timestamp. A boolean supplied by a dispatch event is not authorization.

After triage, group predictable requests per ticket when possible:

```text
Ticket <id> routes to Sol/Max for <analysis, initial review, or follow-up
review> because its applicable classification is CRITICAL/MAXIMUM. Authorize
reasoning above xhigh for this ticket stage?
```

Record the authorized scope: stage, ticket, selected ticket set, or whole run. Never infer a broader scope.

If authorization is denied, use `Sol/XH`, continue under the selected human-validation mode, and report the capped execution. Do not block solely because higher reasoning was declined.

Ultra is a delegated multi-agent execution mode, not `Max+`. It is excluded
from all matrices, ceilings, fallbacks, and comparisons. It may be used only
outside standard Ticket Train routing after a separate explicit user request
for a genuinely divisible task whose sub-agent ownership, visibility, budget,
and result consolidation are defined. Never nest Ultra implicitly inside a
Ticket Train phase.

## Unavailable settings

Do not silently substitute a model or effort.

- If Terra is unavailable, use an available Sol model at the same effort and report the substitution.
- If Sol is unavailable, use the strongest available coding model within the authorized cap and report the substitution.
- If `max` is authorized but unavailable, use `Sol/XH` and report the fallback.
- If a tool cannot set per-thread model or effort, do not launch the routed
  phase in an inherited parent setting. Pause that phase and ask the user
  whether to use a documented available fallback.

For a human-gated ticket, include any routing fallback in the material presented for approval.
