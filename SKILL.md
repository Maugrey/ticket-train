---
name: ticket-train
description: Run an explicitly requested batch of development tickets with deterministic orchestration, separate technical workers, independent verification and review, durable recovery, and measured token usage. Use only when the user invokes $ticket-train.
---

# Ticket Train

The Python runner owns scheduling, task receipts, result collection and retries.
Models own technical analysis, implementation, tests and judgment. The conversation
resolves scope and presents decisions and results.

## Start or resume

1. Resolve repository, ticket source and selected IDs, reusing existing choices.
   Read [ticket-sources.md](references/ticket-sources.md) only for source normalization.
2. Use `run_registry.py discover` before creating anything. Resume the matching
   canonical run; do not reconstruct its state from conversations.
3. Read [runtime.md](references/runtime.md) to prepare and operate the runner.
   It is the single operational procedure. Scripts also provide `--help`.
4. Store one project profile with source references, environment requirements
   and verification commands. Repair missing inputs before launching work.
5. Initialize and bootstrap once with the actual conversation ID and returned
   ownership epoch. Reuse the user's approval mode; otherwise use `standard`.
6. Run `control_plane_runner.py drive` through the pinned release. Its guardian
   and driver are the sole continuous watcher. They observe tasks, collect results,
   retry bounded failures and write actionable outbox items without idle model
   wakes. Each new human gate, terminal error or completed train starts one
   receipt-backed turn in the train's owner conversation through the desktop's
   native task relay, with compact event context. Meaningful phase changes update
   the owner conversation title through a script-only desktop call.
7. Persist each user answer against its exact gate and revision, then continue
   the same runner. An acknowledgement or an emitted packet is not execution.

After interruption, restart the same manifest and command. Receipts establish
whether a task exists. A missing callback never authorizes a replacement.
Do not add a monitor conversation, scheduled heartbeat or polling model. Decision
tasks exist only for new actionable events; unchanged state never starts a model
turn. Restart the same script after interruption.

## Authorization and scope

A live train request authorizes its required visible workers, isolated worktrees,
ticket PRs and final train PR. It does not authorize merging the final train into
the project's base branch. Honor narrower instructions and prior authorization.
`dry-run` produces analyses and reports without implementation or PRs.
Validation-only tickets need their real validation phases, not invented code work.

Use recorded product context: prototype or production, data to preserve, target
environment and compatibility requirements. Routine internal organization or a
testing seam is an implementation decision. Ask only for missing information
that changes product behavior or an irreversible operation.

Every behavioral deviation or missing product specification needs an explicit
user choice. Auto modes bypass risk matrices, not specification decisions.
Read [scope-governance.md](references/scope-governance.md) when a deviation exists.
Retain concrete option IDs and full meaning; never reinterpret A/B labels.
Reuse decisions for the same source and options across analysis retries.

A requested split must produce separate canonical batches, branches and final
deliveries. Record the exact ticket assignment before implementation. Reject
incomplete or dependency-breaking splits; a free-text promise is insufficient.

## Progressive role context

Workers receive an exact bounded context file and only their relevant reference.
Do not copy whole conversations, this entire skill, all references or full logs.
The controller's tables and validators remain authoritative; the runner extracts
only the relevant event contract. Do not silently downgrade a routed model.
Keep the user's orchestrator model without another model-choice confirmation.

| Work | Reference |
|---|---|
| Triage and risk | [criticality.md](references/criticality.md) |
| Analysis, dependencies, contract validation | [analysis-policy.md](references/analysis-policy.md) |
| Implementation | [workflow.md](references/workflow.md) |
| Acceptance and verification | [verification-policy.md](references/verification-policy.md) |
| Review, finding disposition, remediation | [review-policy.md](references/review-policy.md) |
| Unity operations | [unity-mcp-local.md](references/unity-mcp-local.md) |
| Routing questions or unavailable models | [model-routing.md](references/model-routing.md) |
| Reports and cost anomalies | [usage-reporting.md](references/usage-reporting.md) |

## Lifecycle and recovery

Triage precedes analysis. Reuse valid analysis against its source revision and
exact base. Consolidate hard dependencies and shared-file collisions before work.
Implementation and acceptance use independent workers and branches at one base.
Integrate their commits, then verify the combined commit before review.
The implementer's own tests do not replace independent acceptance evidence.

The initial review is exhaustive and independent. Batch valid findings into a
fresh remediation context; review the changed surface unless evidence requires
a full review. Preserve satisfied gates and applicable risk floors.
The controller bounds concurrency, review cycles and train size. Isolate blocked
tickets and continue independent ones. An environment failure is not proof of
a code defect. Ticket PRs target the train; the final PR targets the project base.
Verify, review and collect external feedback against the actual final commit.

`procedure` is the only workflow state. Legacy views are derived. Writers require
owner, epoch and revision under one lock. Never create a second orchestrator.
Persist external intent and actual response. Reconcile ambiguous operations;
missing list entries and stale RUNNING flags are not liveness evidence.
Never resume a desktop-owned active task through a separate App Server.
Use the bundled `send_message_to_thread` app relay for owner attention; keep the
isolated App Server transport for train-owned technical workers.

Verification journals individual commands. Reuse passed commands only for the
same plan and unchanged worktree. Preserve raw logs and exit codes. Keep resource
leases while commands run; recover dead owners with OS locks. Bound service
retries by actual failed or interrupted turns, independently of result repairs
and user-input resumes, then report repeated failures with their evidence.
Pin scripts and role instructions per run. Installed updates do not change an
active run. Migrate explicitly at an idle boundary and retain the old release.
Treat the desktop executable path as volatile: after an application update, the
runner resolves the current binary and renews visibility evidence by reading the
two existing capability tasks through the desktop bridge. It creates no probe task.

## Cost and delivery

Waits, status reads, GitHub polling and commands do not wake a model in the runner.
Technical workers use fresh bounded context. Measure actual session intervals,
counter resets, turns, tools and compactions. Unknown usage is unavailable,
never zero. Unassignable shared work stays in an explicit unallocated row.
All displayed totals must agree with the ledger.

RTK is optional for command presentation. Preserve original logs and exit codes;
never filter machine protocols, contracts, evidence or exhaustive diff inputs.
Do not install a global hook or alter Codex configuration during an ordinary train.
Output compression is not a measured reduction in billed tokens.

Finish with ticket outcomes, PR/report links, checks actually performed, remaining
manual checks, blockers and measured token coverage. An uncollected worker or a
stopped process is not a completed train.

Changes suggested by AI model: GPT-6 (Codex).
