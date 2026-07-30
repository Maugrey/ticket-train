# Risk and Complexity Policy

## Contents

- Independent dimensions
- Classification evidence standard
- Intrinsic criticality
- Complexity
- Human validation matrices
- Classification checkpoints
- Approval modes

## Independent dimensions

Classify every ticket on two independent dimensions:

1. **Intrinsic criticality:** the severity of a plausible implementation failure.
2. **Complexity:** the difficulty of understanding, implementing, and verifying the work.

Do not use implementation difficulty to raise intrinsic criticality. Do not use low impact to lower complexity.

During routing triage, use the higher materially plausible level when evidence
is incomplete so analysis is not under-routed. During full analysis and
effective classification, use the highest level supported by a specific,
credible failure path or by unresolved material uncertainty tied to such a
path. Do not preserve a higher label merely because a severe outcome is
imaginable.

The user may explicitly override a classification.

Apply the train's versioned proportionality profile from
[efficiency-policy.md](efficiency-policy.md). Severity must reflect the actual
assets, actors, credible threats, containment, recovery, and protected
boundaries recorded for the product. Generic enterprise threats do not raise
confirmed criticality without a credible causal path; MVP scope never lowers
a failure that crosses a strongly protected boundary.

## Classification evidence standard

Return separate evidence for each dimension.

### Criticality evidence

Record:

```text
credible_failure_mode
causal_path
affected_assets_data_or_users
blast_radius
detectability
containment
reversibility_and_repair
sensitive_boundary_or_invariant
residual_uncertainty
confidence
```

Apply these rules:

- Name a concrete incorrect behavior and explain how the proposed change could
  cause it. A domain label such as `authentication`, `migration`, `payment`, or
  `concurrency` is not a failure mode.
- Tie the causal path to ticket requirements, current code, data flow,
  contracts, or deployment behavior.
- Distinguish severity from likelihood. A low-probability consequence may
  still be critical when the causal path is credible.
- Assess detection, containment, rollback, and repair independently. Do not
  call an outcome recoverable without identifying the actual recovery path.
- Use `CRITICAL` only when a credible path reaches a strong critical trigger
  and the resulting grave consequence is irreversible, systemic, or not
  reliably contained and repaired.
- Prefer `HIGH` when a sensitive or shared surface is affected but credible
  failures remain detectable, contained, and reliably recoverable.
- Do not classify by keywords or by domain sensitivity alone.

If targeted analysis cannot resolve uncertainty that could credibly cross a
criticality boundary, keep the higher plausible level, mark the uncertainty,
and lower confidence. Purely hypothetical outcomes with no causal support do
not raise the confirmed classification.

### Complexity evidence

Rate every factor in the complexity table and record:

```text
factor
rating
repository_or_ticket_evidence
decisive = yes | no
residual_uncertainty
```

Identify the factors that actually determine the final level. A factor rated
`HIGH` raises overall complexity to `HIGH` only when it is decisive for
understanding, implementing, or verifying the ticket correctly. Otherwise,
several `MEDIUM` factors must interact strongly to justify `HIGH`.

Existing, well-tested project patterns may lower the `Design` factor, but do
not erase real coupling, state, delivery, integration, or verification
complexity. Missing acceptance criteria or broken validation infrastructure
raise complexity only when they materially impede correct implementation or
verification.

## Intrinsic criticality

Ask: **If the implementation is wrong, what can happen?**

### `LOW`

Use `LOW` when impact is negligible, local, immediately visible, and easy to reverse.

Typical cases:

- documentation, comments, or localized copy;
- isolated styling or presentation;
- tests-only work with no production behavior change;
- a deterministic mechanical change;
- a local behavior correction with negligible blast radius.

### `NORMAL`

Use `NORMAL` for bounded functional impact without sensitive data or invariants and with ordinary rollback or repair.

Typical cases:

- bounded business logic in one feature;
- a functional bug without sensitive-data consequences;
- an internal endpoint or component with limited impact;
- behavior reversible through an ordinary deployment;
- regression risk limited to one feature or a small user group.

### `HIGH`

Use `HIGH` for sensitive, shared, or operationally important surfaces when plausible failures remain contained, detectable, and recoverable.

Typical cases:

- an additive, reversible schema migration;
- a recoverable and verifiable backfill;
- authentication behavior that does not change an authorization boundary;
- payment integration behavior that cannot create charges, refunds, ledger entries, balances, or entitlements;
- concurrency or idempotency over recoverable, non-financial state;
- a backward-compatible shared contract;
- deployment configuration with reliable rollback;
- a cross-cutting change within established architecture that does not alter a fundamental invariant;
- operational audit behavior unrelated to a legal or compliance obligation.

### `CRITICAL`

Use `CRITICAL` when one evidenced credible failure path can cause grave
security, financial, legal, availability, or irreversible data consequences
that are not reliably contained and repaired.

Strong triggers include:

- irreversible production-data loss or corruption;
- a destructive migration or transformation without reliable recovery;
- authorization bypass, privilege escalation, cross-tenant access, RLS, secrets, or a security-boundary change;
- incorrect charges, refunds, ledgers, balances, credits, purchases, or entitlements;
- a privacy, legal, regulatory, or compliance violation;
- major unavailability with uncertain recovery;
- an uncontrolled breaking change to a broadly consumed public contract;
- an irreversible state transition;
- concurrency or idempotency that can duplicate financial or scarce-resource operations;
- a fundamental architectural invariant whose failure would have systemic consequences.

Touching a sensitive domain does not automatically make a ticket `CRITICAL`.
Name the boundary or invariant and its credible failure path. Prefer `HIGH`
when containment, detection, rollback, and repair are reliable.

## Complexity

Ask: **How difficult is the work to understand, implement, and verify correctly?**

Evaluate these factors:

| Factor | `LOW` | `MEDIUM` | `HIGH` | `MAXIMUM` |
|---|---|---|---|---|
| Scope | One isolated concern | One feature across a few layers | Several modules or shared contracts | Many subsystems or system-wide behavior |
| Coupling | Few or no dependencies | Known, bounded dependencies | Numerous or indirect interactions | Systemic or poorly mapped dependencies |
| Design | Existing pattern, no material decision | Adaptation of a known pattern | Several significant technical choices | New architecture or strategy with major uncertainty |
| State and invariants | Stateless or simple local state | A few controlled transitions | Complex invariants, concurrency, or many edge cases | Distributed consistency or interdependent invariants |
| Integrations | None or trivial | One stable integration | Several APIs, systems, or persistence mechanisms | Coordinated multi-system compatibility or rollout |
| Delivery | Ordinary deployment | Simple configuration or migration | Migration, backfill, or transitional compatibility | Multi-stage migration, recovery, double-write, or progressive rollout |
| Verification | Deterministic local test | Targeted feature tests | Multi-layer tests, complex fixtures, or E2E | Production-like validation, recovery testing, or a weak test oracle |

Assign:

- `LOW` when every factor is low.
- `MEDIUM` when one or more factors are medium, none is high or maximum, and the ticket remains bounded to one feature.
- `HIGH` when at least one factor is decisively high for correct delivery, or
  several medium factors interact strongly.
- `MAXIMUM` when at least two factors are maximum, or one decisive maximum factor exists, such as a system-wide redesign, distributed consistency, a multi-stage data migration, or replacement of a central mechanism with many uncertain dependencies.

Do not classify from code volume, ticket priority, or domain keywords alone. A
one-line authorization change can be low-complexity and critical; a large
visual refactor can be maximum-complexity and low-criticality.

`MAXIMUM` complexity triggers the size-and-risk checkpoint in
[efficiency-policy.md](efficiency-policy.md), not an automatic separate train.
Decompose only when the ticket is an epic or the cumulative review surface is
no longer coherent.

## Human validation matrices

Apply these matrices to the confirmed analysis classification in `standard` and `auto-merge` modes.

### Human validation of the analysis

| Intrinsic criticality ↓ / Complexity → | `LOW` | `MEDIUM` | `HIGH` | `MAXIMUM` |
|---|---:|---:|---:|---:|
| `LOW` | No | No | No | **Yes** |
| `NORMAL` | No | No | **Yes** | **Yes** |
| `HIGH` | **Yes** | **Yes** | **Yes** | **Yes** |
| `CRITICAL` | **Yes** | **Yes** | **Yes** | **Yes** |

When required, do not implement until the user approves the analysis and technical plan.

Apply this matrix again if an approved plan changes materially before implementation.

A pending human analysis gate pauses only the affected ticket's implementation and implementations that require its merged result. It never delays initial analysis, dependency consolidation, or conditional-plan amendments for any selected ticket.

Treat an explicitly presented and approved conditional variant as part of the approved plan. Do not request reapproval merely to select that variant or to refresh non-material paths, symbols, commands, or equivalent details. Require a new approval when reconciliation materially changes strategy, scope, architecture impact, acceptance behavior, intrinsic criticality, complexity, or risk.

### Human validation before merge into the train

Apply this matrix to the higher of the analysis classification and the effective classification from the final diff.

| Intrinsic criticality ↓ / Complexity → | `LOW` | `MEDIUM` | `HIGH` | `MAXIMUM` |
|---|---:|---:|---:|---:|
| `LOW` | No | No | No | No |
| `NORMAL` | No | No | No | No |
| `HIGH` | No | No | No | **Yes** |
| `CRITICAL` | **Yes** | **Yes** | **Yes** | **Yes** |

When required, do not merge into the train until automated gates are clean and the user approves the technical implementation report and required human test evidence. Do not require line-by-line human code review unless project policy or the user explicitly requires it.

## Classification checkpoints

Use three distinct checkpoints without duplicating the technical analysis:

1. **Routing triage:** assign provisional values quickly from ticket-visible signals only. Use them solely to route the analysis model and effort. Prefer the higher plausible routing cell when a material boundary is uncertain.
2. **Analysis classification:** the single full technical analysis returns the required evidence blocks, confirms or corrects both values, and drives the analysis human gate and implementation routing. It may confirm a lower level than triage when explicit evidence removes the provisional concern.
3. **Effective classification:** after implementation, inspect the actual diff and verification evidence. Use this reassessment for review routing and the pre-merge human gate.

Keep the higher classification per dimension between analysis and implementation. Never silently downgrade. Report scope expansion and return to an applicable human gate when the effective classification crosses one.

Treat unresolved material uncertainty with a credible causal path as the higher
plausible level and report low confidence. If the analyzer and orchestrator
disagree, keep the higher evidenced level, not the higher unsupported label,
and report the disagreement.

## Approval modes

| Mode | Human analysis matrix | Human pre-merge matrix |
|---|---|---|
| `standard` | Apply | Apply |
| `auto-analysis` | Bypass | Apply |
| `auto-merge` | Apply | Bypass |
| `full-auto` | Bypass | Bypass |

These modes configure only human validation. They never disable routing triage, full automated analysis, automated tests, independent automated review, remediation, reporting, train limits, model-effort authorization, or train-to-base merge restrictions.
