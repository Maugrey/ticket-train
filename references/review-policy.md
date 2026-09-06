# Review and remediation

Review the complete initial PR against its actual base/head and accepted scope.
The reviewer is independent of implementation. Verify functional evidence before
review; do not substitute code inspection for a missing acceptance gate.
Return one exhaustive finding inventory with severity, concrete trigger, affected
code, consequence and actionable correction. Separate defects from optional ideas.
Do not prescribe speculative compatibility or architecture beyond the source.

The runner collects CI, Codex, Copilot and human feedback on the same commit.
Disposition every finding: fix, already resolved with evidence, duplicate of a
named finding, or reject with technical reason. Keep stable finding IDs and source
references. Never turn missing CI/Copilot responses into a clean review. Poll
mechanically within the bounded collection deadline; ask for judgment only on
actual feedback or a terminal service limitation.

Group valid corrections into one fresh remediation task with a compact finding
ledger and exact base. Preserve approved scope. Its completion records the commit,
changed surface, actual checks and unresolved risks. After verification, use a
focused follow-up review of the correction and its interaction surface. A material
scope change requires a full review and new scope revision. Respect the controller's
review coverage floors and explicit limits; repeated fixes need a root-cause
checkpoint, not another unrestricted cycle.

The final train PR receives its own verification and review. Reuse valid ticket
reviews where exact commits remain applicable. State evidence for any integration
risk that invalidates that reuse. Collect final feedback after the final commit;
a changed PR head invalidates stale verification and finding closure.

Only the guarded merge script may merge a ready ticket into the train. It checks
controller authorization and the live GitHub head, base, draft state and checks.
A final merge into the project base requires separate user authorization.

Changes suggested by AI model: GPT-6 (Codex).
