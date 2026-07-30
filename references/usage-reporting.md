# Token Usage Reporting

## Contents

- Reporting policy
- Counters
- Measurement lifecycle
- Phase and ticket accounting
- Commands
- Availability and limitations

## Reporting policy

Report token consumption after every completed ticket phase and in the consolidated train report. Use exact Codex counters when available. Never estimate missing usage.

Set:

```text
usage_reporting = exact-if-available
```

Apply this policy in dry-run and live execution. Include work that ended blocked, failed, or cancelled when counters exist.

Use [token_usage.py](../scripts/token_usage.py) to read only known thread IDs. Do not search prompts or expose session content.

## Counters

Report:

- `total_tokens`
- `input_tokens`
- `cached_input_tokens`
- `output_tokens`
- `reasoning_output_tokens`

Optionally retain `cache_write_input_tokens` in machine-readable artifacts.

Treat `total_tokens` as the authoritative total supplied by Codex. Cached input and reasoning output are breakdown fields; do not add them to `total_tokens`.

Token counts are not a direct measure of subscription credits, billing, or remaining rate limits.
Never translate them into a percentage of weekly quota without an
authoritative product counter. Report token drivers and duplicate work
separately so the user can correlate them with observed credit consumption.

## Measurement lifecycle

At train start:

1. Create a durable run-state and usage directory outside the repository under
   the available Codex data directory.
2. Capture the orchestrator baseline before triage.
3. Record every created analysis, worker, and reviewer thread ID with ticket and phase labels.

Persist each phase's launch key and usage baseline before dispatch. When a
launch outcome is ambiguous, do not declare a new thread or zero baseline until
the visible-thread reconciliation protocol identifies the authoritative thread.
Never count a forked copy of orchestrator history as a new zero-baseline phase;
train phases must not use forks.

For a newly created dedicated thread:

1. Capture its cumulative total after completion.
2. Use a zero baseline with `--new-thread`.
3. Let the orchestrator report the result after the child thread's final turn completes.

Write the completed capture and delta to durable run state before starting the
next automatic phase. Do not postpone implementation, review, or remediation
measurement until train completion.

For a reused thread, such as a targeted amendment in the original analysis
thread:

1. Capture a baseline before the new phase.
2. Capture again after the phase.
3. Report the delta.

At run completion, early stop, or train checkpoint:

1. Capture the orchestrator again.
2. Calculate its run delta.
3. Sum only non-overlapping phase deltas.
4. Separate ticket work from orchestration.
5. Mark the aggregate `partial` if any requested phase lacks exact counters.
6. Reconcile the visible thread inventory with recorded measurements and list
   every missing phase explicitly.
7. Refuse a `complete` measurement status when any known phase, duplicate
   attempt, failed attempt with counters, or final-train phase is absent.
8. Produce the session ledger with baseline, final counter, and delta for every
   authoritative or duplicate session recorded in the manifest.

Keep snapshots in the external run-state directory and do not commit them.
Retain them until the train completes or is explicitly abandoned, so a
restarted orchestrator can continue without reconstructing prior consumption.

## Phase and ticket accounting

Prefer these phase labels:

```text
run:triage
orchestrator-preflight
orchestrator-coordination
<ticket-id>:analysis-initial
<ticket-id>:analysis-consolidation
<ticket-id>:analysis-reconciliation
<ticket-id>:plan-contract-validation
<ticket-id>:implementation
<ticket-id>:acceptance-test-authoring
<ticket-id>:baseline-red-validation
<ticket-id>:integrated-green-validation
<ticket-id>:environment-parity-validation
<ticket-id>:test-remediation
<ticket-id>:review-initial
<ticket-id>:remediation-codex
<ticket-id>:remediation-copilot
<ticket-id>:remediation-combined
<ticket-id>:review-followup
run:final-train-validation
run:final-train-review
run:final-train-remediation
run:final-train-review-followup
```

When main-thread phases cannot be isolated without overlapping windows, report one `orchestration` delta instead of allocating tokens arbitrarily among tickets.

For each requested ticket, sum its non-overlapping initial analysis,
analysis-consolidation, analysis-reconciliation, plan-contract validation,
implementation, acceptance-test authoring, test remediation, initial review,
remediation, and follow-up review deltas, even when the ticket ends
blocked, failed, or cancelled. For the run total, add batch triage, ticket
totals, final-train validation, final-train review, final-train remediation,
final-train follow-up review, and the non-overlapping orchestrator delta.

Do not count the same thread interval twice. Parallel execution changes elapsed time, not accounting rules.

Baseline-red, integrated-green, and environment validation are normally
deterministic command phases with zero child-model tokens. Still report their
duration, logs, and `0` model tokens when confirmed; use `unavailable` rather
than zero when the execution boundary was not captured.

Do not create extra model turns solely to separate Codex and Copilot
remediation usage. Use `remediation-combined` when the worker processes a
single deduplicated packet. Split the labels only when phases were already
separate and have exact non-overlapping counters.

Record each fresh remediation thread as its own session. Count duplicate,
failed, and cancelled attempts when counters exist; do not deduplicate their
consumption away. For a reused session, require an explicit phase baseline in
the manifest. Mark that phase unavailable when the baseline is missing rather
than assuming zero.

## Commands

Capture the current orchestrator:

```powershell
python scripts/token_usage.py capture --current --output <baseline.json>
```

Capture known child threads:

```powershell
python scripts/token_usage.py capture `
  --thread <analysis-thread-id>=<ticket-id>:analysis `
  --thread <worker-thread-id>=<ticket-id>:implementation `
  --output <after.json>
```

Calculate a delta and declare newly created threads:

```powershell
python scripts/token_usage.py diff `
  --before <baseline.json> `
  --after <after.json> `
  --new-thread <analysis-thread-id> `
  --new-thread <worker-thread-id> `
  --output <delta.json>
```

Aggregate non-overlapping phase deltas:

```powershell
python scripts/token_usage.py sum `
  --input <analysis-delta.json> `
  --input <implementation-delta.json> `
  --input <review-delta.json> `
  --output <aggregate.json>
```

Reconcile the manifest inventory into a per-session ledger at checkpoints and
completion:

```powershell
python scripts/token_usage.py ledger `
  --manifest <run-manifest.json> `
  --output <usage-ledger.json>
```

The ledger includes authoritative and duplicate session attempts, explicit
baseline/final/delta values when available, phase coverage, and an inventory
of unmeasured phases. It also reports non-content diagnostics such as assistant
messages, tool calls, token-counter events, and context-compaction events when
available. Use it to detect an omitted or repeatedly awakened session before
reporting a complete aggregate.

Use the skill's absolute script path when the current working directory is not the skill directory.

## Availability and limitations

Return one status:

- `complete`: every requested measurement has exact counters;
- `partial`: some measurements are exact and others unavailable;
- `unavailable`: no exact counter could be read.

Typical unavailable cases:

- a remote task has no local rollout file;
- the session log is missing or no longer retained;
- the tool cannot resolve the thread ID;
- the local rollout schema no longer exposes token counters.

Do not replace an unavailable count with text-length heuristics.

The orchestrator can measure a child task including its final response because it reads the log after completion. The orchestrator cannot include the tokens used to generate the consolidated report currently being written; state that the orchestration total is measured immediately before that report.

Missing token reporting never justifies omitting the section from a phase or
completion report. Use `partial` or `unavailable`, identify the missing thread
or interval, and preserve the last trustworthy measurement boundary.
