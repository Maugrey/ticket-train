# Ticket Train Report Templates

## Contents

- Run configuration report
- Orchestrator startup preflight
- Live train status report
- Train resumed report
- Human action required report
- Routing triage report
- Dependency consolidation report
- Token usage block
- Ticket analysis report
- Structural-impact approval digest
- Ticket implementation report
- Review report
- Run completion or train checkpoint report

Write reports in the user's language. Keep them concise but evidence-backed. Do not reproduce the full code.

For analysis reporting, use two layers:

1. A compact structural-impact digest in the main thread. This is the
   authoritative approval surface and must be self-sufficient.
2. Detailed evidence in the ticket analysis thread and, when useful, in the
   expanded ticket analysis report.

Do not copy the full analyzer report into the main thread merely to achieve
completeness. Completeness means that every material impact, decision,
dependency, and residual risk is represented in the compact digest.

## Run configuration report

```markdown
## Ticket train configuration

- Source:
- Selection:
- Approval mode:
- Execution mode:
- Repository:
- Base branch:
- Train branch:
- Reasoning cap: xhigh
- Authorized reasoning overrides:
- Triage routing: Terra/High
- Routing enforcement: strict
- Coordination policy: compact-control-plane
- Supervision policy: event-driven-deterministic
- Child-thread visibility: user-visible
- Launch reconciliation: required
- Supervision mode: active-until-terminal
- Liveness reporting: transitions plus 15-minute liveness | user override
- Canonical run ID and fingerprint:
- Orchestrator lease owner:
- Supervisor mode and watcher ID:
- Proportionality profile revision:
- Cost-control policy: strict-quality-preserving
- Verification policy: parallel-independent-red-green
- Required environment tiers:
- Complete review limit per stable scope: 1
- Remediation cycle limit: 2
- Train size checkpoints: 60 material files / 2 schema or data transformations / 4 structural domains
- Actual orchestrator model and effort:
- Recommended orchestrator model and effort:
- Orchestrator setting status: recommended | acceptable-overprovisioned | underprovisioned | unknown
- Orchestrator startup confirmation: pending | confirmed | declined
- Analysis policy: parallel-conditional
- Token usage reporting: exact-if-available
- Maximum active analyses: 5
- Maximum active implementations: 2
- Maximum active implementation/test pairs: 2
- Maximum integrated tickets: 5
- Automated tests: mandatory
- Independent automated review: mandatory
- Final train pull request in live mode: mandatory at completion or checkpoint
- Train-to-base merge: explicit user action required
```

## Orchestrator startup preflight

Publish this after the initial read-only usage baseline when available and
before triage, child dispatch, or repository mutation:

```markdown
## Orchestrator preflight

- Recommended model and effort: Terra/High | Sol/High
- Current model and effort: <actual value> | Unknown
- Status: recommended | acceptable-overprovisioned | underprovisioned | unknown
- Recommendation criteria:
  - <one to three concrete orchestration signals>
- Ticket criticality effect: none directly; technical depth is routed to child phases
- Cost/quality implication: <concise comparison>
- Important limitation: the current conversation setting cannot be changed in place

Continue this train with the current orchestrator setting? **Yes/No**
```

Wait for an explicit answer. If the user answers no, stop before triage and
state which setting to select in a new conversation. If the current setting is
unknown, do not present it as inferred.

## Live train status report

Publish this compact report on every material state transition. During long
unchanged work, publish it at the liveness cadence from
[orchestration-control.md](orchestration-control.md) without repeating detailed
ticket reports.

```markdown
## Train status

- Last transition:
- Canonical run and owner:
- Active visible tasks: <title, ticket/phase, thread ID, state>
- Launch anomalies: none | <phase key, state, reconciliation progress>
- Current gates: none | <ticket, revision, gate>
- Next automatic action:
- User action required: none | <exact decision>
- Supervision: <mode, last check, next check>
- Durable state updated at:
- Cost anomaly: none | <reason and checkpoint>
- Functional verification gates: <ticket and status>
```

Report `User action required: none` explicitly when the train should continue
automatically. A completed child phase must be relayed before its automatic
successor is launched or awaited.

## Train resumed report

Publish after adopting a run in the same or a new main conversation:

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
- Pending human action: none | <gate, revision, accepted replies>
- Next automatic action:
- Supervision mode and next check:
- Token/cost impact of the interruption:
```

Do not describe a full repeated analysis as recovery when a durable artifact
can be reused or reconciled.

## Human action required report

Every human gate is published as a normal main-conversation message. Do not
leave it only in a heartbeat, child report, automation output, or status table.

```markdown
# ACTION REQUIRED

## <ticket or train> — <analysis approval | pre-merge approval | decision>

- Controller revision and updated at:
- Revision or exact head:
- Why your decision is required:
- Exact question or information required:
- What was analyzed or implemented:
- Functional impact:
- Architecture impact:
- Database/data impact:
- API/contracts/integrations impact:
- Security/access/privacy impact:
- Operations/configuration/deployment impact:
- Automated evidence and review result:
- Residual risk and proportionality decision:
- Pull request or durable diff: not applicable | <link>
- Blocked until your answer:
- Work continuing independently:
- Accepted replies: `<exact reply 1>` | `<exact reply 2>` | `<feedback format>`
```

Use explicit `No impact identified` statements. Persist the corresponding
`pending_human_action` with `notification_status = ANNOUNCED` before yielding.
While it remains unresolved, each liveness message repeats the heading, gate
ID, revision, exact question, accepted replies, blocked scope, and continuing
scope. Never reduce it to "waiting for information".

## Routing triage report

Report triage as a compact batch table:

```markdown
## Routing triage

| Ticket | Provisional intrinsic criticality | Provisional complexity | Suspected dependencies | Confidence | Analysis routing | Reasons |
|---|---|---|---|---|---|---|
| <id> | LOW \| NORMAL \| HIGH \| CRITICAL | LOW \| MEDIUM \| HIGH \| MAXIMUM | <ids or none> | high \| medium \| low | <model>/<effort> | <brief evidence> |

- Reasoning authorization required:
- Authorized scope:
- Triage routing requested:
- Triage routing actually used:
- Triage routing conformance: conformant | documented-fallback | nonconformant
- Triage token usage:
```

Do not include an implementation plan in the triage report.

## Dependency consolidation report

```markdown
## Dependency consolidation

- Common analysis base commit:
- Requested ticket IDs:
- Analyses completed:
- Analysis amendments completed:
- Missing analysis reports: none | <ticket IDs>
- Duplicate analysis phase keys: none | <phase keys and disposition>

| Upstream | Downstream | Relationship | Shared contract or invariant | Implementation order |
|---|---|---|---|---|
| <id> | <id> | independent \| soft-dependency \| hard-dependency \| conflict | | |

- Conflicting assumptions resolved:
- Remaining conditional decisions:
- Tickets awaiting human analysis approval:
- Independent implementations ready:
- Cumulative train size: <files, transformations, structural domains>
- Size checkpoint: clear | ticket checkpoint | train checkpoint | epic decomposition required
- Verification contracts complete:
- Supabase/Auth environment requirements:
```

## Token usage block

Use this block after every completed phase:

```markdown
### Token usage

- Measurement: complete | partial | unavailable
- Total tokens:
- Input tokens:
- Cached input tokens:
- Output tokens:
- Reasoning output tokens:
- Scope:
- Missing measurements:
```

Use Codex-provided `total_tokens`; do not calculate it by adding the breakdown fields.

## Ticket analysis report

```markdown
## <ticket-id> — Analysis

- Title:
- Source:
- Analysis base commit:
- Analysis revision:
- Verification contract revision:
- Proportionality profile revision:
- Applicability: confirmed | partial | obsolete | blocked
- Intrinsic criticality:
- Credible failure mode and causal path:
- Affected assets, data, or users:
- Blast radius:
- Detection and containment:
- Reversibility and repair:
- Sensitive boundary or invariant:
- Criticality residual uncertainty:
- Criticality confidence:
- Complexity:
- Decisive complexity factors:
- Complexity residual uncertainty:
- Complexity confidence:
- Analysis routing requested:
- Analysis routing actually used:
- Analysis routing matrix cell:
- Analysis routing conformance: conformant | documented-fallback | nonconformant
- Routing fallback or authorization:
- Stable findings:
- Current behavior:
- Acceptance criteria:
- Conditional technical plan:
- Planned variants:
- Upstream dependencies and assumptions:
- Downstream dependency contract:
- Functional invariants:
- Data and migration assumptions:
- Shared files or registries:
- Valid if:
- Invalid if:
- Consolidation amendments:
- Reconciliation status: pending | VALID | VARIANT_SELECTED | REFRESHED_NON_MATERIAL | REFRESHED_MATERIAL | INVALID
- Architecture impact:
- Likely files and symbols:
- Data or migration impact:
- Required tests:
- Acceptance-criterion coverage map:
- State, role, negative, and recovery scenarios:
- Baseline-red expectations:
- Integrated-green oracles:
- Required environment tiers:
- Supabase/Auth/RLS verification scope:
- Manual-only scenarios and automation justifications:
- Declared dependencies:
- Discovered dependencies:
- Collision domain:
- Scheduling decision: parallel-safe | sequential | blocked
- Analysis gate: automatically approved | awaiting human approval | human approved
- Open decisions or risks:
- Minimum required correction:
- Optional hardening:
- Explicitly deferred post-MVP:
- Estimated review surface: <material files, schema/data transformations, structural domains>
- Epic assessment: coherent ticket | decomposition required

### Complexity factor assessment

| Factor | Rating | Decisive | Evidence | Residual uncertainty |
|---|---|---|---|---|
| Scope | LOW \| MEDIUM \| HIGH \| MAXIMUM | yes \| no | | |
| Coupling | LOW \| MEDIUM \| HIGH \| MAXIMUM | yes \| no | | |
| Design | LOW \| MEDIUM \| HIGH \| MAXIMUM | yes \| no | | |
| State and invariants | LOW \| MEDIUM \| HIGH \| MAXIMUM | yes \| no | | |
| Integrations | LOW \| MEDIUM \| HIGH \| MAXIMUM | yes \| no | | |
| Delivery | LOW \| MEDIUM \| HIGH \| MAXIMUM | yes \| no | | |
| Verification | LOW \| MEDIUM \| HIGH \| MAXIMUM | yes \| no | | |

### Token usage

| Phase | Measurement | Total | Input | Cached input | Output | Reasoning output |
|---|---|---:|---:|---:|---:|---:|
| Initial analysis | | | | | | |
| Dependency consolidation | | | | | | |
| Analysis reconciliation | | | | | | |
| **Analysis total** | | | | | | |
```

Always return the consolidated digest to the main thread. Keep the expanded
report in the ticket analysis thread unless the user requests it or a material
point cannot be represented safely in the digest. Automatically approved
digests are informational and must not wait for a response. A required human
gate pauses implementation of that ticket, not other selected analyses.

## Structural-impact approval digest

Publish one digest per ticket after dependency consolidation and before its
analysis gate. Keep each impact bullet to one sentence when possible. Add a
second sentence only when needed to expose a material choice or consequence.

Use exactly one of these semantic labels for every impact domain, translated
into the user's language (for example, `No impact identified` becomes `Aucun
impact identifié` in French):

- `No impact identified`
- `Non-structural impact`
- `Structural impact`
- `Uncertain`

Never omit a domain. Never use `No impact identified` merely because the
analyzer did not discuss it. `Uncertain` coverage blocks an approval request
when the uncertainty could change scope, architecture, behavior, data,
security, operations, classification, or scheduling.

The required domains are:

- **Functional / business rules:** user-visible behavior, business invariants,
  lifecycle rules, compatibility, and important error semantics.
- **Architecture:** new or changed boundaries, shared primitives, services,
  modules, ownership, or core architectural decisions.
- **Database / data:** schema, migration, backfill, constraints, indexes,
  fixtures, existing-data remediation, and retention.
- **API / contracts / integrations:** public or internal contracts, payloads,
  events, external services, and downstream consumers.
- **Security / access / privacy:** authentication, authorization, roles,
  tenant isolation, secrets, sensitive-data exposure, and auditability.
- **Operations / configuration / deployment:** environment variables,
  schedulers, CI/CD, infrastructure, rollout, observability, and operational
  recovery.

Use this compact template:

```markdown
### <ticket-id> — Analysis revision <revision>

- Classification: <intrinsic criticality> / <complexity>
- Proportionality profile: <revision and decisive assumption>
- Criticality basis: <credible failure, blast radius, containment and recovery>
- Complexity basis: <decisive factors, interactions and confidence>
- Functional / business rules: **<impact label>** — <explicit summary>
- Architecture: **<impact label>** — <explicit summary>
- Database / data: **<impact label>** — <explicit summary>
- API / contracts / integrations: **<impact label>** — <explicit summary>
- Security / access / privacy: **<impact label>** — <explicit summary>
- Operations / configuration / deployment: **<impact label>** — <explicit summary>
- Dependencies and scheduling: <predecessors, collisions, parallel/sequential decision>
- Verification: <independent test scope, red/green oracle, environment tiers, Supabase/Auth applicability>
- Decisions submitted for approval:
  - <decision and recommended option>
- Minimum required correction:
  - <required MVP work and evidence>
- Optional hardening:
  - <non-blocking improvement or None identified>
- Explicitly deferred post-MVP:
  - <deferred work and reason or None identified>
- Important defaults already resolved:
  - <material default that does not require user choice>
- Explicitly out of scope:
  - <nearby work intentionally excluded>
- Residual risks: <none identified, or concise list>
- Size and epic checkpoint: <counts, coherent | checkpoint | decomposition required>
- Gate: informational | awaiting human approval | human approved
- Coverage: complete | incomplete
```

Rules:

- If there is no item under decisions, write `None`.
- If there is no minimum correction, optional hardening, or post-MVP deferral,
  write `None identified` under the applicable heading; never omit a heading.
- If there is no important resolved default, write `None`.
- If there is no nearby excluded work, write `None identified`.
- If there is no residual risk, write `None identified`.
- `Coverage: complete` asserts that all six domains were assessed and every
  material decision, dependency, and risk from the detailed analysis appears
  in the digest.
- Use `Coverage: incomplete` if evidence is missing or a material point is
  still being reconciled. Do not request human approval in that state.
- When several tickets need approval, present their digests together. The user
  may approve them in one response, but approvals remain ticket- and
  revision-specific.
- Keep implementation details, exhaustive file lists, and full test matrices
  in the detailed analysis unless one is itself a material approval concern.
- A link to or identifier for the analysis thread may be included as optional
  evidence; it never replaces the digest.

Replace the former pattern of publishing only a `Plan consolidé` sentence plus
`Recommandations soumises à approbation`. Those fields may remain as a batch
overview, but they are not sufficient for a human gate without the digest
above.

## Ticket implementation report

```markdown
## <ticket-id> — Implementation

- Pull request:
- Independent acceptance-test branch and commit:
- Test pull request or durable integration reference:
- Final effective intrinsic criticality:
- Final effective complexity:
- Pre-implementation reconciliation:
- Implementation contract revision:
- Plan-contract validation: not required | passed | failed
- Internal implementation slices:
- Worker self-review: passed | findings fixed | blocked
- Verification contract revision:
- Independent test authorship: complete | blocked
- Acceptance coverage: complete | incomplete
- Baseline red: demonstrated | invalid | not applicable
- Integrated green exact head:
- Environment parity: passed | not applicable | blocked
- Supabase/Auth/RLS gate: passed | not applicable | blocked
- Automatable scenarios left to user: 0 | <blocking list>
- Conditional variant selected:
- Analysis changes before implementation:
- Renewed human analysis gate:
- Result:
- Acceptance criteria satisfied:
- Architecture impact:
- Technical decisions:
- Important alternatives rejected:
- Public contracts or data model changes:

### Important files

- [<file> — PR diff](<direct-file-diff-link>)
  - Purpose:
  - Important symbols:

### Verification

- Automated tests:
- Project checks:
- Red evidence and base commit:
- Green evidence and exact head:
- Environment fingerprint:
- Supabase/Auth test roles and boundary:
- Regression tests added:
- Human tests, if required:
- Why each human test could not be automated:

### Review

- Independent reviewer:
- Review routing requested:
- Review routing actually used:
- Review routing matrix cell:
- Review routing conformance: conformant | documented-fallback | nonconformant
- Reasoning authorization or fallback:
- Blocking findings initially:
- Findings fixed:
- Copilot comments accepted:
- Copilot comments rejected and why:
- Finding ledger disposition summary:
- Trusted exact-head CI evidence reused:
- Independent risk-targeted checks:
- Remaining non-blocking findings:
- Complete review passes for stable scope: <count>/1
- Remediation cycles: <count>/2
- Minimum required correction:
- Optional hardening:
- Explicitly deferred post-MVP:

### Integration

- Human pre-merge gate: not required | bypassed by mode | awaiting approval | approved
- Train merge commit:
- Residual risks:

### Token usage

| Phase | Measurement | Total | Input | Cached input | Output | Reasoning output |
|---|---|---:|---:|---:|---:|---:|
| Initial analysis | | | | | | |
| Dependency consolidation | | | | | | |
| Analysis reconciliation | | | | | | |
| Plan-contract validation | | | | | | |
| Implementation | | | | | | |
| Acceptance-test authoring | | | | | | |
| Baseline-red validation | | | | | | |
| Integrated-green validation | | | | | | |
| Environment-parity validation | | | | | | |
| Test remediation | | | | | | |
| Initial review | | | | | | |
| Codex remediation | | | | | | |
| Copilot remediation | | | | | | |
| Combined remediation | | | | | | |
| Follow-up review | | | | | | |
| **Ticket total** | | | | | | |

- Missing measurements:
```

Use the final pull-request diff for important-file links. If a stable file anchor is unavailable, link to the pull request's Files changed view and include the exact path.

## Review report

```markdown
## <ticket-id> — Automated review

- Pull request:
- Train base revision:
- Ticket head revision:
- Effective intrinsic criticality:
- Effective complexity:
- Requested model and effort:
- Actual model and effort:
- Routing matrix cell:
- Routing conformance: conformant | documented-fallback | nonconformant
- Reasoning authorization or fallback:
- Status: clean | changes requested | blocked
- Functional-readiness gate: passed | invalid
- Acceptance coverage verified:
- Baseline-red evidence verified:
- Exact-head green evidence verified:
- Environment and Supabase/Auth evidence verified:
- Privileged boundary bypass absent:
- Manual-only justifications valid:
- Blocking findings:
- Non-blocking findings:
- Copilot and CI comments processed:
- Finding ledger dispositions:
- Trusted exact-head evidence reused:
- Independent risk-targeted checks:
- Remediation sent to worker:
- Follow-up review scope: none | focused | full
- Finding inventory complete: yes | no
- Complete review passes for stable scope: <count>/1
- Remediation cycles: <count>/2

### Token usage

- Measurement:
- Total tokens:
- Input tokens:
- Cached input tokens:
- Output tokens:
- Reasoning output tokens:
```

## Run completion or train checkpoint report

```markdown
## Train summary

- Train branch:
- Base branch:
- Final train pull request:
- Final train pull-request head:
- Final train pull-request status: open | ready | blocked | unavailable
- Exact head covered by final Codex review:
- Final train review routing requested:
- Final train review routing actually used:
- Final train review routing conformance: conformant | documented-fallback | nonconformant
- Final CI status:
- Copilot review status: received | pending | unavailable | not configured | timed out
- Final GitHub feedback collection ID and deadline:
- Final GitHub feedback snapshot ID and exact head:
- Collected source counts: Codex / CI / Copilot / human
- Unresolved review threads:
- Final finding-ledger status:
- Run status: completed | stopped | five-ticket checkpoint
- Terminal reason: AWAITING_REQUIRED_USER_INPUT | BLOCKED | COMPLETED | CHECKPOINT
- Visible task inventory and final states:
- Launch anomalies and duplicate-attempt dispositions:
- Durable state reconciliation: complete | partial
- Canonical run identity and orchestrator handoffs:
- Supervision history and liveness compliance:
- Human actions announced and resolved:
- Requested tickets and final states:
- Analysis dependency relationships:
- Analysis reconciliation outcomes:
- Material analysis revisions and renewed approvals:
- Integrated tickets: <count>/5
- Pull requests and ticket commits:
- Intrinsic criticality distribution:
- Complexity distribution:
- Model and reasoning usage:
- Proportionality profile:
- Train size budget and checkpoints:
- Complete-review and remediation-cycle budgets:
- Cost anomalies and root-cause checkpoints:
- Routing nonconformities and user decisions:
- Max/ultra authorizations and fallbacks:
- Token measurement coverage: complete | partial | unavailable
- Full verification results:
- Independent acceptance coverage:
- Integrated red/green evidence:
- Environment-parity results:
- Supabase/Auth/RLS results:
- Base-to-train review result:
- Architecture changes:
- Data and migration changes:
- Known risks:
- Source tickets not yet marked delivered:
- Required user action:

### Finalization completeness

| Requirement | Status | Exact head or evidence | Missing action |
|---|---|---|---|
| Final pull request created before final review | complete \| blocked \| unavailable | | |
| Full project verification | complete \| partial \| failed | | |
| Final Codex review routing conformant | yes \| no | | |
| Exact-head CI collected | complete \| pending \| unavailable | | |
| Copilot/comments dispositioned | complete \| pending \| unavailable \| not configured | | |
| GitHub feedback snapshot covers final head | complete \| stale \| missing | | |
| Token accounting reported | complete \| partial \| unavailable | | |
| Functional verification summarized | complete \| incomplete | | |
| Manual validation summarized | complete \| incomplete | | |
| Code/application attention points summarized | complete \| incomplete | | |

### Manual validation summary

- Human evidence already completed:
- Outstanding required tests:
- Recommended tests:
- Optional tests:

| Priority | Status | Area or journey | Preconditions, role, and data | Concise action | Expected result | Related tickets |
|---|---|---|---|---|---|---|
| required before base merge \| recommended \| optional | outstanding \| passed \| failed \| not applicable | | | | | |

- For every outstanding manual test, state why automation was technically
  infeasible and what automated evidence already exists. An ordinary
  automatable scenario means functional verification is incomplete.

### Code attention points

| Priority | Concern | Final PR diff | Why it matters | Failure signal or mitigation |
|---|---|---|---|---|
| blocking \| important \| informational | | | | |

### Application attention points

| Priority | Journey, role, or environment | What to watch | Expected behavior | Related tickets |
|---|---|---|---|---|
| blocking \| important \| informational | | | | |

### Token usage by ticket

| Ticket | Analysis | Contract validation | Implementation | Test authoring | Red/green/environment | Initial review | Remediation | Follow-up review | Ticket total | Coverage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| <ticket-id> | | | | | | | | | | |

### Aggregate token usage

- Ticket work:
- Batch triage:
- Final train validation:
- Final train review:
- Final train remediation:
- Final train follow-up review:
- Orchestration:
- Measured train total:
- Input tokens:
- Cached input tokens:
- Output tokens:
- Reasoning output tokens:
- Missing measurements:
- Measurement boundary: orchestration measured immediately before this report

### Session token ledger

| Session | Ticket/phase | Attempt | Authoritative or duplicate | Baseline | Final | Delta | Coverage |
|---|---|---:|---|---:|---:|---:|---|
| <thread-id> | | | | | | | complete \| unavailable |

- Duplicate sessions included:
- Failed or cancelled attempts included:
- Unmeasured phases:
- Session diagnostics: <assistant messages, tool calls, token counter events, context compactions>
- Credit limitation: token counters do not expose weekly subscription-credit consumption

When this is the five-ticket checkpoint:

The train is frozen. Codex will not start another ticket or merge the train into
the base branch without an explicit user instruction.
```

Do not replace this completion report with a short GO/PR status. If a required
item is unavailable, retain the row, mark it accurately, and explain the
remaining action. Before publishing, apply the yield gate from
[orchestration-control.md](orchestration-control.md) and confirm that no phase
is queued, running, launch-unknown, automatically runnable, or waiting for its
final report or usage capture.

Build the manual validation summary from actual ticket gates, automated-test
limits, review findings, final diff risks, and project instructions. Do not
produce a generic regression checklist.

- Deduplicate scenarios shared by several tickets.
- Keep each action short and include a concrete expected result.
- Identify the required environment, role, fixture, or data only when needed.
- Mark previously completed human evidence separately; do not ask the user to
  repeat it without a material head change or a concrete regression risk.
- Use `required before base merge` only when an applicable non-bypassed gate or
  repository rule requires it. Bypassed or advisory validation remains
  `recommended` or `optional`.
- If no manual test is useful, write `No manual test identified` explicitly.

Derive attention points from the final diff and finding ledger:

- Code attention may cover architecture, invariants, schema or migrations,
  contracts, authorization, concurrency, error handling, observability,
  rollout, rollback, or concentrated maintenance risk.
- Application attention may cover critical journeys, roles and permissions,
  boundary inputs, lifecycle transitions, recovery behavior, background jobs,
  deployment configuration, or user-visible regressions.
- Link code concerns to the final pull-request file diff or Files changed view.
- Separate blocking concerns from residual or informational attention.
- If a category has no item, write `No code attention point identified` or
  `No application attention point identified`; never rely on omission.
