# Parallel Conditional Analysis Policy

## Contents

- Default policy
- Analyzer inputs
- Required analysis structure
- Classification evidence
- Dependency consolidation
- Human analysis gates
- Pre-implementation reconciliation
- Analysis revision

## Default policy

Set:

```text
analysis_policy = parallel-conditional
```

Queue every selected ticket for full analysis immediately after triage. Keep at most five analysis threads active and use a rolling window until every selected ticket has been analyzed.

Never delay an analysis because:

- another ticket analysis is unfinished;
- another ticket analysis awaits human approval;
- an upstream implementation is unfinished;
- an upstream ticket has not merged into the train.

Run every initial analysis against the same recorded `analysis_base_commit`. Dependencies make an analysis conditional; they do not serialize analysis execution.

## Analyzer inputs

Give every analyzer:

- its complete normalized ticket;
- the common `analysis_base_commit`;
- the normalized catalog of all selected tickets;
- declared dependencies;
- suspected dependencies and collision domains from triage;
- applicable project instructions.
- the current proportionality profile and revision from
  [efficiency-policy.md](efficiency-policy.md).

Do not give it another analyzer's private reasoning. When targeted amendments are later required, send only the relevant reports, dependency contracts, and evidence.

## Required analysis structure

Require every analysis to separate:

### Stable findings

- current behavior;
- acceptance criteria;
- applicable project constraints;
- relevant current files, symbols, data models, contracts, and tests;
- intrinsic criticality and complexity evidence;
- risks that do not depend on another selected ticket.

### Classification evidence

Return the complete evidence standard from
[criticality.md](criticality.md).

For intrinsic criticality, identify the credible failure mode, causal path,
affected assets or users, blast radius, detection, containment, recovery,
sensitive boundary or invariant, residual uncertainty, and confidence.

For complexity, rate all seven factors, cite ticket or repository evidence,
mark decisive factors, and explain how interacting factors determine the final
level.

Do not classify from a sensitive keyword, ticket priority, code volume, or an
imagined worst case without a credible causal path. Distinguish a conservative
provisional triage route from the confirmed evidence-based classification.

### Conditional plan

- upstream tickets that may affect the plan;
- explicit assumptions about their expected result;
- planned alternatives for plausible upstream outcomes;
- decisions that cannot yet be resolved;
- precise validity conditions.

### Proportional recommendations and size evidence

Return:

```text
minimum_required_correction
optional_hardening
explicitly_deferred_post_mvp
estimated_material_files
estimated_schema_or_data_transformations
structural_domains_changed
epic_evidence
```

Tie every required recommendation to an acceptance criterion, project rule,
credible protected-boundary failure, or operational necessity. Apply the
train's proportionality profile; do not turn generic hardening into blocking
MVP scope.

### Dependency contract

Return:

```text
upstream_dependencies
downstream_consumers
contracts_expected_from_upstream
contracts_offered_to_downstream
functional_invariants
data_and_migration_assumptions
shared_files_or_registries
valid_if
invalid_if
```

Do not present an uncertain dependency as an established implementation fact.

## Dependency consolidation

After every selected analysis returns:

1. Reconcile the requested ticket IDs, analysis launch records, completed
   thread IDs, captured final reports, and usage records. Do not publish a gate
   until every requested ticket is represented or explicitly reported blocked.
2. Build the directed dependency graph.
3. Compare each upstream offer with downstream expectations.
4. Compare shared contracts, invariants, data assumptions, collision domains, and ordering constraints.
5. Mark each relationship `independent`, `soft-dependency`, `hard-dependency`, or `conflict`.
6. Route targeted clarification or amendment requests to the original analysis thread when reports disagree or omit required dependency information.
7. Measure amendment usage as `analysis-reconciliation`.
8. Publish one consolidated digest per requested ticket and the implementation schedule in the main thread.
9. Build the initial ticket and cumulative train size-budget assessment under
   [efficiency-policy.md](efficiency-policy.md).

Before requesting any human analysis approval, assert and report:

```text
requested_ticket_ids == consolidated_digest_ticket_ids
missing_analysis_reports = none
duplicate_analysis_phase_keys = none
```

If an analysis completed but its report was not captured, keep the
orchestrator active, retrieve it, and update durable state. Do not rely on the
user to point out the omission.

Do not create another analysis thread for an amendment while the original thread is available. Do not redo stable portions of an analysis.

Before accepting an analysis as consolidated, verify that both evidence blocks
are complete and internally consistent. Route missing evidence or an
unsupported label to the original analyzer for a targeted amendment in every
approval mode. Do not repeat the technical analysis in the orchestrator.

## Human analysis gates

Apply the human analysis matrix from [criticality.md](criticality.md) to each consolidated ticket analysis.

- Publish the compact structural-impact approval digest from
  [report-template.md](report-template.md) in the main thread before requesting
  approval.
- Make that digest self-sufficient: the user may inspect the analysis thread
  for evidence, but must not need it to discover a material impact or decision.
- Cover every required impact domain explicitly. Use `no impact identified`
  only after the domain was assessed; use `uncertain` when evidence is
  incomplete.
- Do not request approval while digest coverage is incomplete. Route missing
  or uncertain material points to the original analysis thread first.
- Bind approval to the ticket ID and consolidated analysis revision shown in
  the digest. A batch response may approve several tickets, but record the
  decision independently for each revision.
- `standard` and `auto-merge` apply the matrix.
- `auto-analysis` and `full-auto` bypass only the human analysis gate.
- A required approval blocks implementation of that ticket.
- It also blocks implementations that require that ticket's merged result.
- It never blocks analysis or dependency consolidation for any selected ticket.
- Independent implementations whose gates are satisfied may continue.

If a consolidated or reconciled analysis changes materially after approval,
invalidate the approval, publish a revised structural-impact digest, and apply
the matrix again. Do not require reapproval for a pre-approved conditional
variant or a non-material refresh.

## Pre-implementation reconciliation

Before starting any implementation, compare:

- `analysis_base_commit`;
- the current train head;
- merged upstream diffs and implementation reports;
- the analysis assumptions, variants, `valid_if`, and `invalid_if` conditions.

Assign one result:

- `VALID`: the original plan applies unchanged.
- `VARIANT_SELECTED`: an already documented conditional variant matches the actual upstream result.
- `REFRESHED_NON_MATERIAL`: only paths, symbol names, commands, or equivalent technical details changed.
- `REFRESHED_MATERIAL`: strategy, architecture impact, scope, acceptance behavior, criticality, complexity, or risk changed.
- `INVALID`: no documented plan or variant remains valid.

For `VALID`, start implementation when all other gates pass.

For `VARIANT_SELECTED`, record the chosen variant. Do not request another human approval when the original approval explicitly covered it.

For `REFRESHED_NON_MATERIAL`, update the analysis in its original thread, report the refresh, and continue without a new human gate.

For `REFRESHED_MATERIAL`, update the original analysis, reclassify both dimensions, publish the revised report, and apply the human analysis matrix again.

For `INVALID`, return the ticket to its original analysis thread for a scoped reanalysis against the current train. Reuse stable findings, reclassify, and apply the human analysis matrix to the revised result. Do not require user authorization merely to perform this required revision; any applicable human approval applies to the resulting plan.

After a valid reconciliation, materialize the compact implementation contract
from [efficiency-policy.md](efficiency-policy.md). Do not hand the worker the
complete analysis-thread history. For `HIGH` or `MAXIMUM` complexity, run the
single bounded plan-contract validation before implementation. A failed
contract check returns only the missing or contradictory fields to the
original analyzer; it never starts a second complete analysis.

## Analysis revision

Keep one analysis thread per ticket throughout initial analysis, consolidation amendments, and pre-implementation reconciliation.

Create a replacement thread only when the original is unavailable. Give the replacement the ticket, original report, dependency graph, upstream diffs, reconciliation evidence, classifications, model-routing history, and human-gate state. Report the substitution.

If revision routing requires `max` or `ultra`, preserve the separate explicit authorization rule from [model-routing.md](model-routing.md).
