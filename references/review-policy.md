# Ticket Pull-Request Review Policy

## Contents

- Review setup
- Reviewer output
- Test-evidence reuse
- GitHub and thread routing
- Copilot and external comments
- Finding ledger and deduplication
- Remediation loop
- Final train pull-request review
- Merge gates
- Parallel integration refresh

## Review setup

Review every live ticket pull request before merge into the train.
Dispatch the complete review only after the functional-readiness gate in
[verification-policy.md](verification-policy.md) passes.

The acceptance-test pull request targets the implementation branch and exists
for durable independent authorship and integration. It does not consume the
ticket's complete-review pass. Validate its provenance, scope, baseline-red
evidence, and merge result; the complete reviewer later reviews the integrated
production and test diff together.

Use a reviewer thread that:

- is separate from the implementation thread;
- has read-only repository permissions;
- uses the model and reasoning effort selected from the effective intrinsic criticality and complexity under [model-routing.md](model-routing.md);
- receives the ticket, approved analysis, acceptance criteria, project guidance, pull-request URL, base and head revisions, and test evidence;
- receives the proportionality profile and compact implementation contract;
- receives the verification contract, independent test commit and pull
  request, acceptance-coverage map, baseline-red evidence, exact-head green
  evidence, environment fingerprints, and applicable Supabase/Auth status;
- does not receive the worker's private reasoning or expected review outcome;
- does not modify the branch.

Create every reviewer as a user-visible phase thread under
[orchestration-control.md](orchestration-control.md). Never fork the
orchestrator conversation to create a review, and never replace a failed
visible review launch with a hidden subagent unless the user explicitly
authorizes that phase-specific fallback.

Dispatch the initial reviewer with the exact model and reasoning effort
selected by the initial automated-review matrix. Dispatch every focused
follow-up reviewer with the separate follow-up-review matrix and its
remediation-verification inputs. Pass both settings explicitly and require the
reviewer to report the actual values. A stronger inherited setting is a
routing mismatch, not an automatic upgrade.

Review the final ticket diff against the current train base. A pull request is preferred because it gives a durable diff and supports GitHub comments. When a pull request is unavailable during a dry-run, review an explicit commit or revision range.

Run one exhaustive complete review per stable scope. The reviewer must inspect
all acceptance criteria and material risk surfaces and return its complete
finding inventory in the first report. It must not defer discoverable findings
to later complete passes. Separate every recommendation into minimum required
correction, optional hardening, or explicitly deferred post-MVP work under
[efficiency-policy.md](efficiency-policy.md).

The reviewer verifies test sufficiency; it does not author an omitted
acceptance suite during the complete review. If functional readiness is
incomplete or its evidence is invalid, return the ticket to verification
without consuming the complete-review budget.

## Reviewer output

Prioritize consequential findings:

- correctness defects and regressions;
- missing acceptance criteria;
- security and authorization failures;
- concurrency, idempotency, and data-integrity risks;
- architecture violations;
- incompatible contracts or migrations;
- missing or misleading tests;
- out-of-scope changes.

For every finding, return:

```text
severity
title
file
line_or_symbol
evidence
impact
recommended_remediation
suggested_test
```

Use blocking severity for issues that must be fixed before merge. Keep cosmetic preferences non-blocking unless project rules require them.

Also return:

```text
reviewed_base_and_head
trusted_test_evidence_reused
independent_risk_targeted_checks
new_or_changed_risk_surface
functional_readiness_verified
acceptance_coverage_verified
baseline_red_evidence_verified
exact_head_green_evidence_verified
environment_parity_verified
supabase_auth_rls_evidence_verified_or_not_applicable
privileged_test_bypass_absent
manual_only_justifications_valid
finding_inventory_complete = yes | no
minimum_required_correction
optional_hardening
explicitly_deferred_post_mvp
```

## Test-evidence reuse

Reuse CI or worker evidence only when:

- it is tied to the exact reviewed head commit;
- its configuration and logs identify the executed checks;
- every project-required check for that stage passed;
- the evidence is current and trustworthy.

The independent reviewer still runs focused checks for the ticket's material
risk surfaces, such as authorization, concurrency, migration, compatibility,
or data integrity. Do not rerun the full project suite solely to duplicate
trustworthy exact-head evidence unless project rules require an independent
run.

For Supabase/Auth/RLS work, verify that privileged credentials were used only
for fixture setup and cleanup. User-path assertions must run through the real
anonymous or authenticated boundary. A green result produced through
`service_role`, direct SQL, or an admin client does not prove ordinary-user
authorization.

If CI is missing, stale, failing for infrastructure reasons, or cannot prove
the exact reviewed commit, run the project-required local checks and disclose
why validation was duplicated. Never treat broken CI as passing evidence.

## GitHub and thread routing

The reviewer returns its complete report in the reviewer thread.

The orchestrator then:

1. Collects the review report.
2. Captures the completed review thread's token usage.
3. Mirrors actionable findings to GitHub as inline comments when exact lines are available and permissions allow.
4. Uses a general pull-request comment for cross-cutting findings.
5. Creates a fresh visible remediation thread with the same ticket branch and
   worktree and sends one compact structured remediation packet.
6. Reports the review status and usage in the main thread.

Keep the orchestrator active while the review is queued or running. Collect
and relay its final report on the first completion wake-up, then continue the
remediation or merge sequence automatically. Do not wait for the user to
notice that the review thread ended.

Keep GitHub as the durable code-linked record and threads as the orchestration channel.

If GitHub comment writes are unavailable, keep the complete report in threads and explicitly disclose the missing GitHub trace.

## Copilot and external comments

Collect GitHub Copilot, CI, human, and other review comments before declaring the pull request clean.

Do not ask the user to triage ordinary Copilot findings. For a ticket pull
request, the fresh ticket-remediation worker performs the first technical assessment. For the
final train pull request, the final-train remediation worker performs it and
may request a targeted assessment from an original ticket worker when that
context is material. The independent reviewer verifies the disposition and
the orchestrator records it.

Assign one disposition:

- `accepted-fixed`
- `accepted-deferred` with a justified follow-up
- `rejected-incorrect`
- `rejected-out-of-scope`
- `escalated-human-decision`

Never apply a Copilot suggestion automatically without technical analysis.

Do not resolve or dismiss a human review comment without either implementing it or recording a clear technical reason.

## Finding ledger and deduplication

Maintain one finding ledger for Codex, Copilot, CI, and human feedback. Store:

```text
finding_id
sources
severity
file_and_symbol
evidence
disposition
blocking
remediation_status
verification
```

Merge comments only when they describe the same underlying defect. Preserve
every source on the merged finding. The applicable ticket or final-train
remediation worker performs one technical triage of the ledger; the
independent reviewer verifies the resulting dispositions.

Do not send each external comment through a separate worker/reviewer cycle.
Batch compatible accepted findings. Record non-blocking rejected or deferred
findings without triggering remediation or follow-up review solely for that
reason.

## Remediation loop

Keep the original worker as the logical owner, but use a fresh compact visible
thread for each grouped remediation batch. Reuse the same branch and worktree;
do not reuse the accumulated implementation conversation.

For actionable findings:

1. Open one bounded collection window for available Codex, Copilot, CI, and
   human findings.
2. For every confirmed behavioral defect, require a reproducing regression
   test with pre-fix red and post-fix green evidence.
3. Send one deduplicated remediation packet with file, line, severity,
   evidence, expected outcome, source, disposition, test request, compact
   implementation contract, and proportionality-profile revision to a fresh
   remediation thread.
4. Have the remediator update the same ticket branch and worktree.
5. Run affected independent acceptance tests, regression tests, environment
   checks, and required project checks.
6. Push the update.
7. Capture the remediation thread's non-overlapping usage.
8. Have the independent reviewer inspect the remediation diff, unresolved
   findings, ledger dispositions, and affected risk surface.
9. Capture the re-review interval.
10. Repeat once if necessary. After two remediation/follow-up cycles, stop and
   perform the root-cause and cost-anomaly checkpoint from
   [efficiency-policy.md](efficiency-policy.md); never start a third automatic
   cycle.

Before every follow-up review, reassess:

- effective intrinsic criticality from the remediation diff and unresolved
  risk; and
- follow-up verification complexity under
  [model-routing.md](model-routing.md), independently of the original
  implementation complexity.

Use the focused follow-up-review matrix only when the remediation stays within
the approved analysis and previously reviewed risk surface. Give the reviewer
the remediation diff, unresolved findings, finding-ledger dispositions,
affected tests, and the minimum prior context required to verify them.

After resolving the focused follow-up matrix cell, compare it with the actual
setting of the latest `conformant` or `documented-fallback` complete review
under [model-routing.md](model-routing.md). A focused follow-up review must
never use a higher model or reasoning effort. Do not silently cap a higher
result: make the focused review ineligible and route a complete initial review
of the current ticket diff. Treat a missing trustworthy baseline the same way.

Expand a follow-up review to the complete ticket diff only when remediation
materially changes architecture or ownership, a critical schema migration or
data/recovery strategy, functional scope or acceptance behavior, a public or
shared contract with new consumers, security/access/privacy boundaries, or an
unrelated material risk surface. Reconcile the analysis and reassess the ticket
classification before routing the complete review through the initial
automated-review matrix.

Use a fresh focused-review thread with a compact handoff even when the setting
matches the initial reviewer. This prevents accumulated history and keeps the
follow-up limited to the remediation evidence. Do not retain an unnecessarily
expensive reviewer setting for conversational continuity.

If a compact handoff from the original worker is unavailable, use the durable
implementation contract, branch, pull request, findings, and project
instructions. Report missing context only when it creates material ambiguity.

Do not weaken tests, suppress valid findings, or broaden scope merely to make the review pass.

## Final train pull-request review

At every live completion or five-ticket checkpoint with integrated work,
create or update the final train pull request under
[workflow.md](workflow.md). Use it as the durable surface for the complete
base-to-train diff, CI, Codex review, Copilot review, human comments, manual
validation, and the eventual user-initiated merge.

Create or update the final pull request before dispatching the final-train
review. A review of the train branch performed before the pull request exists
does not cover the pull-request CI, Copilot feedback, or review comments and
cannot satisfy final readiness.

Use an independent final-train reviewer that:

- is separate from ticket workers and reviewers;
- receives the exact final pull-request base and head, integrated ticket
  digests, dependency outcomes, complete diff, exact-head test evidence, and
  train finding ledger;
- receives cross-ticket acceptance results and, when applicable, clean-reset
  Supabase migration/Auth/RLS/environment evidence for the exact train head;
- reviews cross-ticket behavior, integration invariants, scope composition,
  migrations, contracts, access boundaries, deployment concerns, integration
  glue, and regressions not visible in isolated ticket diffs;
- reuses trustworthy exact-commit ticket reviews and does not re-review every
  unchanged ticket file merely because it appears in the base-to-train diff;
- reports the actual model and reasoning effort and the exact reviewed head;
- never modifies the train.

Route the complete final integration review through the initial
automated-review matrix as specified by [workflow.md](workflow.md). A later
train-head update invalidates affected evidence and final readiness, but does
not force a complete second pass unless a material invalidation trigger from
[efficiency-policy.md](efficiency-policy.md) applies.

Before dispatch, record the train-level classification, the highest selected
ticket initial-review setting used as the floor, the matrix result, and the
final requested setting. After completion, verify the actual setting exactly.
Any unexplained mismatch is nonconformant and blocks readiness; a cheaper or
more expensive setting is not silently acceptable.

Collect Copilot, CI, Codex, and human feedback into one train-level finding
ledger. Apply the same dispositions and deduplication rules as ticket reviews.
Batch compatible accepted findings instead of creating one remediation cycle
per comment.

The controller requires this exact final sequence on one stable head:

1. `FINAL_REVIEW_RECORDED` with a complete inventory.
2. `FINAL_FEEDBACK_COLLECTION_STARTED` with a collection ID, start, deadline,
   head, and the four monitored sources: Codex, CI, Copilot, and human.
3. Query PR reviews, inline review threads including resolved state, general
   comments, and exact-head CI. Record one
   `FINAL_FEEDBACK_SNAPSHOT_RECORDED` with source counts, stable finding IDs,
   unresolved thread IDs, and a durable evidence reference.
4. `FINAL_FINDINGS_RECONCILED` with exactly one disposition for every finding
   ID in that snapshot and an explicit inventory of threads still unresolved
   on GitHub despite their technical disposition.
5. Only then record final report/token evidence or start grouped remediation.

Do not treat a list of nominally dispositioned sources as proof that comments
were actually collected. A later pull-request head invalidates the snapshot
and all downstream dispositions. Collect a fresh snapshot after the new
exact-head verification and follow-up review.

For remediation:

1. Ask an original ticket worker for a compact targeted handoff only when its
   context materially reduces ambiguity.
2. Create a fresh remediation branch and worktree from the current train.
3. Reassess intrinsic criticality and complexity from the accepted batch and
   route one final-train remediation worker through the implementation matrix.
4. Have that worker apply the accepted batch, including cross-ticket
   corrections.
5. Open a remediation pull request into the train, apply any matrix-based
   human pre-merge gate selected by the approval mode, and serialize its merge.
6. Rerun affected and mandatory tests after merge.
7. Route focused verification through the follow-up-review matrix and its
   complete-review ceiling, expanding to a full review when required.
8. Review against the new final pull-request head and collect newly available
   Copilot and CI feedback.

Use at most two final remediation/follow-up cycles. A third required cycle is
a root-cause checkpoint, not an automatic continuation.

Do not push review fixes directly to the base branch. Do not revive a stale
ticket branch merely because the finding originated in that ticket.

Copilot review is evidence, not an automatic source-code mutation. If Copilot
is unconfigured, unavailable, or does not respond within the bounded
monitoring window, record that state and continue with mandatory Codex review
and repository checks unless repository policy or the user explicitly made
Copilot mandatory.

`timed_out` is valid only after the recorded collection deadline.
`not_configured` and `unavailable` require connector or repository evidence;
they are not convenient defaults. When Copilot responds, inventory every
comment and suppressed finding exposed by the review payload, assess each
technically, and record an accepted, rejected, deferred, or escalated
disposition before readiness.

The final pull request is ready for user action only when:

- the pull-request head equals the current train head;
- required exact-head checks pass;
- the independent Codex review covering that head has no blocking finding;
- every available Copilot, CI, Codex, and human comment has a disposition;
- every accepted blocking finding is verified fixed;
- outstanding manual tests and attention points are reported explicitly.

If the user asks Codex to merge the final pull request, refresh its head,
checks, reviews, and newly available comments immediately before merging.
Never interpret creation, review completion, or readiness as permission to
merge into `main` or `master`.

## Merge gates

A ticket pull request may merge into the train only when:

- the source and approved scope still match;
- the branch is based on the current train or has been refreshed;
- required automated tests pass;
- project-required checks pass;
- its verification contract has complete acceptance coverage;
- independent baseline-red and exact-head integrated-green evidence is valid;
- environment parity and applicable Supabase/Auth/RLS validation passed;
- no objectively automatable ordinary scenario was delegated to the user;
- independent automated review has no blocking findings;
- Copilot, CI, and review comments have a recorded disposition;
- every blocking ledger finding is verified fixed;
- effective intrinsic criticality and complexity are known;
- any human pre-merge validation required by [criticality.md](criticality.md) and the selected approval mode is complete.

In `standard` and `auto-analysis` modes:

- apply the human pre-merge matrix;
- when required, obtain user approval of the technical implementation report and required human test evidence;
- do not require line-by-line human code review unless project policy or the user explicitly requires it.

`auto-merge` and `full-auto` bypass only this human gate.

Automated review and automated tests remain mandatory in every mode.

Never merge the train into `main` or `master` under this ticket-level merge authority.

## Parallel integration refresh

When parallel work produced multiple ticket pull requests:

1. Merge only one ticket into the train at a time.
2. Refresh each remaining branch onto the updated train.
3. Reconcile each remaining ticket analysis against the updated train under [analysis-policy.md](analysis-policy.md).
4. Route material analysis revisions to the original analyzer and reapply any required human analysis gate.
5. Recompute the pull-request diff.
6. Rerun affected tests.
7. Reassess intrinsic criticality and complexity.
8. Run a targeted integration-impact review against the updated base. Reroute
   a complete review only when a material invalidation trigger applies.
9. Repeat any human pre-merge gate applicable to the materially changed diff.

Do not treat a prior clean review against an obsolete train base as sufficient.
The refreshed review may focus on the new train-base delta, the recomputed
ticket diff, and affected risk surfaces when the ticket implementation itself
did not change materially.
