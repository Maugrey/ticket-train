# Analysis and contract decisions

Analyze read-only against the exact source revision and repository base. Load
[criticality.md](criticality.md) for classification; use the controller's routing
tables when a selected route is insufficient. Do not re-open already answered
product questions. Separate source facts, technical deductions and proposals.

The result must include:
- Stable findings with source/code evidence, current behavior and intended result.
- Intrinsic criticality and analysis complexity, each with concrete evidence.
- Residual implementation complexity and verification complexity after planning;
  lower ratings need demonstrated reduction, not merely a detailed plan.
- A compact implementation contract: owned surfaces, interfaces, invariants,
  failure behavior, non-goals, source/contract revisions and exact base.
- A verification contract: acceptance criteria mapped to observable assertions,
  fixtures, commands, baseline-red strategy, integrated-green strategy,
  environment requirements, deterministic or manual coverage and known gaps.
- Hard dependencies, potential shared-file collisions, structural domains,
  schema/data transformations and the smallest independently reviewable slices.
- Scope provenance and any deviation under [scope-governance.md](scope-governance.md).

Provide artifacts rather than a transcript. The runner supplies the relevant
machine event validator. For a validation-only ticket, prepare a verification
plan and evidence template as `verification_plan_reference` and
`verification_evidence_reference`; do not invent an implementation phase.

Dependency consolidation proposes a complete graph and schedule. Parallel-safe
work needs disjoint material surfaces and no unmet hard dependency. Sequential
ordering must not place a prerequisite behind its dependent. The controller
checks cycles, concurrency and size thresholds. If splitting is appropriate,
propose actual ticket groups and branches before implementation, keeping hard
dependencies in one group unless a separately approved delivery plan resolves them.

A contract validator checks traceability, actionable implementation steps,
verification completeness and whether claimed complexity reductions hold. Return
all findings together. HIGH/MAXIMUM plans need this validation before execution.
A failed validation calls for a targeted contract amendment, not another unchanged
analysis or a new product approval. Reuse unaffected evidence and source decisions.

Re-analyze only invalidated findings after source, base or scope changes. Preserve
stable technical findings and approval references. Route validation checks a
completed analysis when its final classification needs stronger coverage.

Changes suggested by AI model: GPT-6 (Codex).
