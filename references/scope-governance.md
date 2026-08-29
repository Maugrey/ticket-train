# Specification and Scope Governance

## Purpose

Ticket Train implements the authorized request. It does not silently convert a
useful idea, a conservative assumption, or an ambiguous sentence into required
MVP scope.

This policy defines a human approval gate for specification deviations and
scope expansions. It is independent of the criticality/complexity validation
matrices. Approval modes never bypass it.

## Specification fidelity

The ticket source, applicable project rules, and prior explicit user decisions
form the authorized specification. Do not silently resolve a gap by inventing
a default, narrowing a requirement, changing observable behavior, selecting
one plausible interpretation, altering an acceptance criterion, or adding a
compatibility rule.

A distinct user decision is mandatory whenever analysis or a later phase:

- adds or removes scope;
- supplies a functional or product precision absent from the source;
- chooses between plausible interpretations;
- introduces an unspecified default or fallback;
- changes observable behavior, error semantics, data semantics, a contract,
  compatibility, migration behavior, or an acceptance criterion;
- changes an architecture constraint, security posture, operational semantic,
  or measurable non-functional requirement carried by the specification;
- contradicts, weakens, strengthens, or replaces any source requirement.

This does not require approval for an internal technical choice when all
reasonable options preserve the exact authorized observable behavior,
contracts, data semantics, acceptance criteria, and non-functional project
rules. If an internal choice leaks into any of those surfaces, it is a
specification decision and must be presented.

## Authorized scope

Classify every planned item with exactly one origin:

- `source-explicit`: unambiguously required by the selected ticket source;
- `project-mandated`: required by applicable repository instructions or an
  architecture, security, testing, or delivery rule;
- `user-approved`: added through a recorded explicit user decision;
- `derived-necessary`: strictly necessary to satisfy a cited explicit
  criterion, with evidence that a narrower implementation cannot satisfy it;
- `scope-expansion-proposed`: useful or protective work not yet authorized;
- `specification-deviation-proposed`: an interpretation, precision, default,
  restriction, behavior, contract, data, error, compatibility, or acceptance
  choice that the source does not determine explicitly;
- `optional`: non-blocking work not selected for the ticket;
- `deferred`: explicitly excluded from the current train.

For `derived-necessary`, record the source criterion, the causal necessity
evidence, and the narrower alternative that was considered and rejected.
Ambiguous wording is not authorization.

## Product lifecycle and existing-state posture

The proportionality profile records:

```text
product_lifecycle_stage = prototype | pre-MVP | private-beta | public-beta | production | unknown
existing_state_compatibility_posture = disposable | preserve-if-cheap | preserve-required | migrate-required | unknown
existing_state_value_and_users
compatibility_source_evidence
scope_expansion_policy = explicit-approval-required
specification_deviation_policy = explicit-approval-required
```

Do not infer production compatibility requirements for a prototype or pre-MVP
product. Conversely, do not discard production state when project rules or a
user decision require preservation.

Treat compatibility, migration, backfill, legacy preservation, rollout
bridges, deprecation shims, dual reads/writes, version markers, and historical
state repair as `scope-expansion-proposed` unless one of the first four
authorized origins proves them necessary.

Distinguish similarly named work. For example, an explicit request to migrate
Unity assets authorizes those asset transformations; it does not authorize a
save-game migration.

## Mandatory analysis assessment

Every complete analysis returns one `scope_assessment` with:

```text
assessment_revision
product_lifecycle_stage
existing_state_compatibility_posture
compatibility_source_evidence
classification_basis = authorized-scope-only
classification_scope_item_ids[]
items[] = item_id, description, scope_origin, origin evidence
proposals[] = proposal_id, category, description, source_gap,
              minimal_variant, expanded_variant, impact, recommendation
specification_alignment = exact | decision-required
specification_deviations[] = deviation_id, item_id, kind, spec_reference,
                             unresolved_point, why_resolution_required,
                             options[], recommended_option_id
```

Each proposal impact covers scope, cost, latency, risk, tests, and the
criticality/complexity classification if approved. The active classification
and model route use authorized scope only. An unapproved proposal may report a
projected route, but it cannot inflate the active route. `exact` is valid only
when no interpretation, precision, default, reduction, or other deviation is
needed. Silence never means that unspecified behavior was authorized.

## Non-bypassable specification-deviation gate

When a scope proposal or specification deviation exists, the controller
creates a `specification_deviation` gate. The main task presents every scope
proposal as minimal versus expanded and every specification deviation as a
set of mutually exclusive options. For each decision, show:

- the source gap;
- the exact source text or documented absence;
- the unresolved point and why implementation cannot proceed without choosing;
- every viable option, including minimal and expanded paths when applicable;
- cost, latency, risk, test, and routing impact;
- a recommendation.

The user may approve, reject, defer, or select a presented variant. A required
specification precision is resolved only by an explicit selected option. One
reply may resolve several items, but every item keeps its own recorded decision
and selected option.

This gate applies in `standard`, `auto-analysis`, `auto-merge`, and
`full-auto`. It is not a high-criticality analysis gate and has no bypass
setting. Only a direct user decision may resolve it.

While it is open, block the affected contract validation, implementation,
acceptance-test authoring, review-driven expansion, and remediation. Continue
independent analyses or work whose scope does not depend on the proposal.

After rejection or deferral of an optional expansion, amend the active contracts to the minimal path
and record the proposal as deferred or out of scope. After approval, record the
new active-scope revision and contract revisions, recompute classification,
and run only the targeted route validation required by the new route. Do not
repeat the complete analysis.

## Later-phase protection

Implementation, acceptance, remediation, ticket review, and final-train
review receive the active scope revision and the complete decision ledger.

- A worker must not add an unapproved scope item while filling an
  implementation detail.
- A worker must stop when it discovers that a specification needs a precision
  or that the planned implementation differs from its literal or explicitly
  approved meaning. It cannot label the choice an implementation detail.
- Acceptance tests must not turn optional compatibility behavior into a
  required oracle.
- A reviewer proposing new functionality or compatibility opens a scope
  proposal; it does not create a blocking defect.
- A finding caused solely by rejected, deferred, or never-authorized behavior
  is `rejected-out-of-scope`, non-blocking, and cannot trigger remediation.
- A remediator may fix only accepted findings inside authorized scope.
  Material scope returns to this gate before code changes.

Project-mandated correctness, security, or data-integrity work remains
required when the repository rule genuinely applies. The reviewer must cite
that rule rather than labeling generic hardening as mandatory.
