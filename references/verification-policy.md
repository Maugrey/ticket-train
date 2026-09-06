# Independent acceptance and verification

An independent acceptance worker starts from the same base as implementation in
a distinct branch/worktree. Derive assertions from the accepted contract and
actual user-visible behavior, not from the implementer's choices. Commit the tests
and provide precise integration instructions. The runner combines both commits
before deterministic verification. Never claim tests passed merely because they
were authored successfully.

Map every acceptance criterion to a meaningful assertion, fixture and command.
For a behavior change, demonstrate baseline red against the reserved base and
green against the integrated commit. Existing behavior or a validation-only ticket
may have no meaningful red failure; explain why rather than fabricate one.
Record uncovered criteria explicitly. A read-only analysis is not runtime proof.

Return `artifacts.verification_plan_reference` for verification_runner.py and
`artifacts.verification_evidence_reference` for the controller event. Plans use
argv arrays, command IDs, timeouts and resource names; no shell string evaluation.
The adapter inserts the actual worktree/head and observed results. Evidence
assertions such as acceptance coverage, environment parity and baseline red must
refer to checks actually performed, not optimistic template defaults.

The executor journals each command, raw stdout/stderr, exit status and duration.
It compares the plan and worktree fingerprint before reusing success. Its process
owns resource locks even when the calling orchestrator is interrupted. An unknown
exit outcome is reconciled, never relabeled as passed or blindly repeated.
On Windows, the dedicated executor also contains its commands in a native Job
Object: killing the executor stops its remaining child commands. This prevents
an orphaned command from outliving the executor's resource locks.

Choose the real environment. For operational changes verify required configuration
and its presence without leaking credentials. For Supabase/Auth/RLS, exercise
real role/session boundaries in the configured local environment. Privileged
setup credentials must not cross into the tested request path. Use applicable
repository instructions and documentation instead of adding generic environment
checks unrelated to this ticket.

Classify failed checks as implementation defect, test defect, environment defect,
contract ambiguity or infrastructure flake using actual logs. Bound recovery.
Do not treat Docker or Unity availability failures as code defects or lower test
coverage to declare success. Isolate a blocked ticket before independent work.

Reviewers reuse current verified evidence. Rerun only checks invalidated by new
changes or unresolved findings. The final train needs verification for its exact
final PR commit, including relevant integration risks.

Automate feasible scenarios. Reserve physical device behavior, unavailable external
systems and inherently subjective checks for explicit manual validation. For
Unity/mobile, simulator proof does not replace device lifecycle, resume, close
callbacks or platform integration tests. State what was actually exercised.

Changes suggested by AI model: GPT-6 (Codex).
