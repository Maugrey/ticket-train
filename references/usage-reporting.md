# Usage and cost reporting

Run `token_usage.py ledger --manifest MANIFEST --output LEDGER --matrix-output
MATRIX.md`. It scans counters without loading prompts into a model. The JSON ledger
is authoritative; the Markdown table is a presentation of it.

Attribute session intervals to the actual run and owner segments. Never use a
long-lived helper's entire history as one train's cost. Accumulate observed deltas
across counter resets. A boundary between samples is partial evidence. Missing
counters or uncertain attribution are unavailable/partial, never invented zero.

A shared session is counted once in the aggregate. If its phase windows cannot
be separated, keep the measurable remainder in `run:unallocated`; do not assign
the whole session to several phases. The ticket rows plus independent transverse
rows, including unallocated usage, must equal the aggregate for every counter.
Reports verify actual file hashes and arithmetic before completion.

Show per-ticket analysis, implementation, acceptance, remediation and review costs,
plus run responsibilities and missing measurements. Distinguish accounting zeros
for deterministic operations from absent observations. A run split must keep
reused analysis cost in its parent rather than duplicate it in every batch.

The runner derives orchestrator turns, tools and compactions from real local traces.
Its incremental cursor avoids rescanning unchanged history. Counters cover the
observed interval only. Report telemetry coverage, not merely an empty manual log.
Usage tokens are not subscription credits, and cached input is not free input.

RTK can compact presentation output when explicitly enabled. It is optional and
has no global hook in this skill. Preserve raw command logs and exit status. Never
filter JSON protocols, verification evidence, contracts or the exhaustive diff
used for review. Compare byte counts on actual logs; RTK estimates tokens from
bytes, not actual model billing. Once the runner keeps routine output outside the
model context, preventing unnecessary model wakes is the main saving.

For a stored verification result, run `verification_runner.py --show-result
RESULT.json --command-id COMMAND [--rtk-executable ABSOLUTE_RTK_PATH]`. It filters
an excerpt of existing logs, keeps the original status and exit code, and falls
back to a bounded raw excerpt if RTK is absent or fails. It never reruns the test.

Final reports include terminal ticket outcomes, linked PRs, checks actually run,
manual validation still needed, attention points and token coverage. Never label
a blocked ticket complete because its report exists.

Changes suggested by AI model: GPT-6 (Codex).
