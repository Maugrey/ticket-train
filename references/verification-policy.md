# Independent Parallel Verification Policy

## Contents

- Objective and non-negotiable invariant
- Verification contract
- Parallel branch and worktree model
- Independent acceptance-test worker
- Red and green evidence
- Functional-readiness gate
- Failure adjudication
- Supabase and Auth verification
- Reviewer responsibilities
- Remediation and regression tests
- Final-train verification
- Manual validation boundary
- Cost and concurrency controls

## Objective and non-negotiable invariant

Make user testing a final confirmation rather than the first reliable defect
detection layer. Every objectively automatable acceptance behavior must be
exercised before automated code review. Human testing cannot guarantee that no
bug exists, but ordinary functional, access-control, data, and environment
defects must not be knowingly deferred to the user.

Design and author acceptance tests in parallel with implementation. Preserve
independence by deriving both work packets from the approved analysis and
versioned contracts, never from one worker's private reasoning or current
code. The test author may inspect the implementation only after its initial
test commit and baseline-red evidence are durable.

Do not replace worker unit tests, independent review, project-required checks,
or applicable human gates. Add a functional-readiness gate before review.

## Verification contract

The analyzer produces a versioned verification contract beside the compact
implementation contract. Do not start the implementation/test pair while a
material behavior lacks an observable oracle.

For every acceptance criterion and material invariant, record:

```text
requirement_id
behavior_or_invariant
nominal_scenario
negative_and_boundary_scenarios
initial_state_and_fixture
actor_and_role
action
observable_expected_result
forbidden_result
verification_layer = unit | integration | api | e2e | migration | environment
environment_tier = local | preview | hosted-staging | not-applicable
automation_owner = implementation-worker | acceptance-test-worker
red_baseline_expectation
green_success_oracle
```

Also record:

```text
verification_contract_revision
public_or_black_box_surface
state_transition_matrix
role_and_permission_matrix
error_and_recovery_matrix
concurrency_or_idempotency_cases
existing_data_and_migration_cases
environment_assumptions
external_integration_boundaries
operational_configuration_contract
manual_only_scenarios_and_justification
```

Use `not applicable` explicitly. An omitted scenario is not evidence that it
was assessed. Every proposed manual-only scenario must state why automation is
not feasible; convenience, time pressure, and generic caution are not valid
reasons.

## Parallel branch and worktree model

After analysis reconciliation and every applicable human analysis gate:

1. Record one exact `execution_pair_base` from the current train.
2. Create the implementation branch and worktree from that commit.
3. Create an acceptance-test branch and separate worktree from the same commit.
4. Launch one visible implementation worker and one visible independent
   acceptance-test worker concurrently.
5. Keep both branches narrow and owned by their worker until initial completion.

Use names such as:

```text
codex/<ticket-id>-<slug>
codex/<ticket-id>-acceptance-tests
```

The implementation worker owns production code and implementation-proximate
unit tests. The acceptance-test worker owns black-box, API, integration, E2E,
access-control, migration, and environment-verification tests. Declare exact
test-path ownership in the contract. Shared test helpers are a collision
domain and require an explicit owner.

The acceptance-test worker commits its initial tests before seeing or merging
the implementation diff. After both initial workers complete:

1. Push both branches.
2. Open or update the ticket pull request from implementation into the train.
3. Open a test pull request from the acceptance-test branch into the
   implementation branch, or integrate the exact test commit through an
   equivalent durable merge.
4. Validate the prospective combined head before merging the test change.
5. Merge the test change into the implementation branch only after test
   authorship and baseline evidence are captured.

The resulting ticket pull request into the train must contain production code,
worker unit tests, and independent acceptance tests. Do not merge the
acceptance-test branch directly into the train.

For `unity-mcp-local`, an editor-backed implementation or acceptance-test
phase uses a leased persistent `unity-slot-N` worktree instead of a disposable
worktree. The branches and independence rules above do not change. Each phase
declares whether it needs `none`, `editor-read`, `editor-write`, `playmode-ui`,
or `build`; only editor-backed phases consume the global pool. The default is
three simultaneously leased editors, including analysis and verification
leases. See [unity-mcp-local.md](unity-mcp-local.md).

## Independent acceptance-test worker

Give the worker only:

- normalized ticket and acceptance criteria;
- approved analysis digest;
- verification contract;
- proportionality profile;
- exact execution-pair base;
- project test guidance and allowed test paths;
- environment contract and non-secret setup references.

Do not give it the implementation prompt, proposed internal design, private
worker reasoning, implementation diff, or expected shortcuts before its first
test commit.

Require it to:

1. Map every contract row to an executable test or justified manual-only case.
2. Prefer public, black-box, and externally observable behavior.
3. Include negative, boundary, role, state-transition, and recovery cases when
   applicable.
4. Avoid mocks at the ticket's material boundary when a deterministic local or
   staging integration is available.
5. Run and preserve baseline-red evidence against the execution-pair base.
6. Return its branch, commit, changed test files, coverage map, environment
   requirements, red results, and unresolved contract ambiguities.

The worker may add deterministic fixtures and test-only helpers, but must not
modify production behavior. If a production seam is required for testability,
return a contract amendment request; do not implement the seam on the test
branch.

Route the acceptance-test worker through the dedicated acceptance matrix in
[model-routing.md](model-routing.md), using effective intrinsic criticality
and verification complexity. Verification complexity may exceed residual
implementation complexity when environment parity, roles, migrations,
concurrency, or a weak oracle make testing harder than coding. `Luna/M` is
allowed only for one deterministic `LOW/LOW` behavior with the complete
mechanical proof; several scenarios or any indirect oracle require Terra or
Sol. Record requested and actual routing normally.

## Red and green evidence

Execute red, green, environment, migration, and project-required commands with
`scripts/verification_runner.py`. Provide a JSON plan containing the exact
worktree, expected Git head, and argv list for every command. The runner writes
full stdout/stderr logs outside the repository, verifies that the head did not
change, and returns a hashed structured result with `model_tokens = 0`.

When a verification plan includes Unity Editor, Play Mode, UI, or build tools,
first acquire the controller-authorized slot at the exact tested head. Invoke
the local MCP through that editor only. The slot lease is execution evidence;
it does not replace `verification_runner.py` results, exact-head checks, or
captured logs.

The controller accepts both `passed` and `failed` runner results so failure
evidence is durable. It allows review only after a passing result. A model may
classify a failed result as implementation defect, test defect, environment
defect, contract ambiguity, or infrastructure flake; it must not supervise a
passing command or narrate unchanged output.

Require both evidence states:

### Baseline red

Run the independent acceptance tests against the exact execution-pair base.
Record:

```text
base_commit
test_commit
environment_fingerprint
commands
expected_failures
observed_failures
unexpected_passes
log_artifacts
red_evidence_status = demonstrated | invalid | not-applicable
```

`demonstrated` requires meaningful failures at the intended oracle. A compile
failure, missing endpoint, or missing symbol is acceptable only when absence of
that surface is the expected pre-ticket behavior and the remaining assertions
will exercise behavior after integration. A setup failure, unavailable
environment, broken fixture, or unrelated failure is not red evidence.

Use `not-applicable` only for a behavior-preserving change whose contract
explains why no baseline behavior should fail. Record the justification.

### Integrated green

Merge or construct the prospective combined head from the implementation and
test commits. Run every contract test, affected worker test, and required
project check. Record the exact combined head and environment fingerprint.

`green` requires:

- all required commands passed;
- no unexpected skip, quarantine, retry-only success, or weakened assertion;
- no environment fallback that bypasses the material boundary;
- the tested head equals the head submitted to review.

Any later code, test, migration, fixture, dependency, or relevant environment
change invalidates affected green evidence.

## Functional-readiness gate

Before dispatching automated code review, run:

```powershell
python scripts/control_guard.py check-verification --state <run-manifest.json> --ticket <ticket-id>
```

Store the gate under `control.verification_gates.<ticket-id>` with these
machine-readable fields:

```text
implementation_contract_revision
verification_contract_revision
execution_pair_base
implementation_thread_id
implementation_branch
acceptance_test_thread_id
acceptance_test_branch
acceptance_test_commit
acceptance_test_pull_request
independent_test_authorship = complete
implementation_disclosed_before_test_commit = false
acceptance_coverage_status = complete
baseline_red_status = demonstrated | not-applicable
baseline_red_base
baseline_red_not_applicable_reason
acceptance_tests_integrated = true
integrated_green_status = passed
integrated_green_head
ticket_head
environment_parity_status = passed | not-applicable
environment_fingerprint
operational_change_applicable = true | false
operational_preflight_status = passed | not-applicable
operational_preflight_evidence
required_configuration_inventory
supabase_auth_applicable = true | false
supabase_auth_verification_status = passed | not-applicable
privileged_credentials_setup_only = true
automatable_manual_scenarios = []
unresolved_validation_failures = []
logs_captured = true
```

The gate requires:

- approved implementation and verification contract revisions;
- independent acceptance-test authoring completed;
- complete acceptance-criterion and invariant mapping;
- valid baseline-red evidence or justified non-applicability;
- acceptance-test commit integrated into the ticket branch;
- exact-head green evidence;
- required environment-parity status;
- explicit operational-change classification and, when applicable, verified
  presence of every required repository variable, secret name, scheduler,
  deployment setting, provider configuration, and health endpoint without
  reading or logging secret values;
- Supabase/Auth status when applicable;
- zero automatable scenario delegated to the user;
- full logs captured outside the repository;
- no unresolved specification, test, environment, or implementation failure.

Do not use code review to complete this gate. A ticket that fails functional
readiness returns to failure adjudication before consuming a complete review.

## Failure adjudication

Classify every combined-head failure before changing code or tests:

- `implementation-defect`: the implementation violates the approved contract;
- `test-defect`: the test contradicts the contract or has a faulty oracle;
- `environment-defect`: configuration, migration, fixture, service health, or
  parity is invalid;
- `contract-ambiguity`: expected behavior is missing or contradictory;
- `infrastructure-flake`: nondeterministic infrastructure prevented evidence.

Use logs and durable contract rows, not worker authority, to decide. Send an
implementation defect to a fresh implementation-remediation context. Send a
test defect to the original acceptance-test worker or a fresh test-remediation
context. Send contract ambiguity to the analyzer and reapply any material
human analysis gate. Repair the environment before rerunning either worker.

Do not modify a correct test merely to make implementation pass. Do not modify
production behavior to satisfy a test that contradicts the approved contract.
After two failed adjudication/remediation cycles, trigger the existing
root-cause and cost checkpoint.

## Supabase and Auth verification

When a ticket touches Supabase, Postgres policies, Auth, sessions, cookies,
SSR auth, storage, or hosted project configuration, add a Supabase environment
contract and use real Supabase boundaries for integration evidence.

Record:

```text
supabase_scope
local_stack_required
hosted_preview_or_staging_required
schema_and_migration_state
seed_and_reset_command
auth_site_url_and_redirect_expectations
enabled_auth_provider_expectations
cookie_and_ssr_expectations
roles_and_test_users
rls_tables_views_functions_and_storage
client_key_classes_under_test
hosted_only_behavior
environment_fingerprint_without_secrets
```

Use a resettable local Supabase stack for deterministic schema, migration,
Auth, database, and RLS tests whenever representative. Use a disposable
preview or staging project for hosted-only behavior such as OAuth providers,
email delivery, redirect allowlists, HTTPS cookie behavior, or project-level
configuration. Never run destructive validation against production.

Privileged credentials may create fixtures and perform cleanup only. Execute
user-path assertions with the real `anon`/publishable or authenticated client
and the intended JWT/session. Never use `service_role`, a secret key, direct
SQL, or an admin client to prove that an ordinary user is authorized; doing so
would bypass the boundary under test.

Cover the applicable matrix:

- anonymous user;
- authenticated owner or member;
- authenticated non-owner or non-member;
- each privileged application role;
- missing, expired, invalid, refreshed, and stale-claim sessions;
- sign-out, revocation, and recovery behavior;
- direct Data API access subject to RLS;
- SSR cookie propagation, callback, redirect, and refresh behavior;
- migration/reset/seed reproducibility;
- hosted environment configuration and key separation.

Verify that authorization does not rely on user-editable metadata. Verify
views and privileged functions do not bypass the intended RLS model. Use
project-specific guidance and current Supabase documentation when implementing
the tests.

If hosted-only verification is required but credentials or a disposable
environment are unavailable, mark the gate `environment-blocked`; do not call
it passed or silently transfer an automatable check to the user.

The same fail-closed rule applies to deployment and operations changes. When a
ticket adds or changes a workflow, scheduler, cron endpoint, provider, runtime
environment variable, webhook, queue, or background worker, inventory the
required configuration and verify its presence through deterministic CLI/API
metadata before review. Never infer configuration from documentation or
`env.example`. A missing repository variable or secret name is an explicit
environment/input gate, not a successful implementation and not a post-merge
manual test.

## Reviewer responsibilities

The reviewer receives the final implementation/test diff, contracts,
baseline-red evidence, exact-head green evidence, environment fingerprints,
and coverage map. It verifies rather than authors the missing acceptance suite.

The reviewer must check:

- every acceptance criterion and material invariant maps to evidence;
- independent tests were committed before implementation disclosure;
- red failures exercised the intended oracle;
- green evidence covers the exact reviewed head and correct environment;
- tests were not weakened, skipped, over-mocked, or made implementation-aware;
- privileged setup did not bypass user-path authorization assertions;
- environment or fixture failures were not mislabeled as product success;
- remaining gaps are genuinely non-automatable and proportionately reported.

The reviewer still performs independent risk-targeted checks and code review.
It must not accept a passing suite as proof that architecture, security,
concurrency, migration, or test quality is correct.

## Remediation and regression tests

Every confirmed behavioral defect found by Codex, Copilot, CI, QA, or a human
must receive a reproducing regression test before or with the fix. Record:

```text
finding_id
reproduction_test
pre_fix_failure_evidence
post_fix_pass_evidence
exact_fixed_head
```

Waive the regression test only when automation is technically infeasible, and
record the concrete reason and alternative evidence. Do not waive it merely
because the finding arrived during review.

Test-only remediation remains owned by an independent test context. Review
follow-up remains targeted to the changed production code, regression test,
unresolved finding, and affected risk surface.

## Final-train verification

Before final-train review, run the integrated acceptance suites on the exact
train head and add cross-ticket scenarios derived from dependency contracts.
For applicable Supabase work, start from a clean reset, apply migrations in
delivery order, seed representative roles, and rerun affected Auth/RLS and
environment checks.

Repeat the operational configuration preflight on the final head whenever any
integrated ticket classified it as applicable. Record the inventory and
evidence in `FINAL_VERIFICATION_RECORDED`; final readiness is rejected while a
required scheduler variable, secret name, provider setting, or deployment
endpoint is absent.

Reuse ticket evidence only when its commits, fixtures, environment contract,
and behavior are unchanged in the train. The final review concentrates on
cross-ticket interactions and evidence invalidated by integration.

## Manual validation boundary

Reserve user testing for:

- subjective UX, wording, or visual judgment;
- physical devices or external systems unavailable to automation;
- explicit product acceptance decisions;
- a final smoke confirmation in the target environment;
- a scenario whose safe automated execution is demonstrably infeasible.

For each requested manual test, report the reason it was not automated, prior
automated evidence, prerequisites, concise action, and expected result. If an
ordinary automatable scenario remains, the functional-readiness gate is not
complete.

## Cost and concurrency controls

Parallel test authoring adds one bounded model phase but should reduce review
diagnosis and remediation. Keep the cost controlled:

- at most one acceptance-test worker per active implementation worker;
- at most two active implementation/test pairs;
- one initial acceptance-test authoring pass;
- deterministic red/green and environment execution without model narration;
- zero model tokens for command execution, exact-head checks, log capture, and
  unchanged CI polling;
- compact contracts instead of implementation-thread history;
- targeted test remediation rather than regenerating the suite;
- no complete code review before functional readiness.

Measure `acceptance-test-authoring`, `baseline-red-validation`,
`integrated-green-validation`, `environment-parity-validation`, and
`test-remediation` separately. Deterministic command execution has no child
model usage but still records duration and logs.
