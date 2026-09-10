# Runtime

`scripts/control_plane_runner.py drive` is the execution entrypoint. It redirects
to the hash-checked release pinned in the manifest. `step` is a diagnostic view.

## Preparation

Use `run_registry.py discover` with repository, train branch, source and tickets.
`init` with the same identity creates or finds the run and returns its lease.
Use the actual visible conversation ID, not an agent path such as `/root`.
Bootstrap once with `train_controller.py bootstrap --state MANIFEST --base-branch
BASE --approval-mode MODE --owner-thread-id OWNER --owner-epoch EPOCH`.
Record existing authorization in `ORCHESTRATOR_CONFIRMED`; do not ask again for
a model choice. The driver registers its actual native process as supervision, with
`watcher_consumes_model_tokens: false`.

Store one project profile next to the manifest:

```json
{
  "revision": "source-and-environment-revision",
  "repository": "ABSOLUTE_LOCAL_REPOSITORY",
  "github_repository": "owner/repository",
  "tickets": {"T-1": {"source_reference": "EXACT_SOURCE", "source_revision": "REVISION"}},
  "native_visibility_evidence": "VERIFIED_HOST_CAPABILITY_JSON",
  "host_executable": "NATIVE_CODEX_EXECUTABLE",
  "final_verification": {
    "verification_plan_reference": "PLAN_JSON",
    "verification_evidence_reference": "EVIDENCE_TEMPLATE_JSON"
  }
}
```

Resolve values from the repository and ticket source. The profile contains
project inputs, not another workflow state. Prepare real verification coverage;
do not invent passing assertions in an evidence template.

On Windows, the desktop native `codex.exe app-server --stdio` is the host
transport. The npm CLI's Unix-only daemon is not required. A capability file
must record an actual desktop read of native-created tasks on this executable.
Use `ticket-train-native-capability-v1`, `desktop_read_verified: true`, the
`host_executable`, its `host_sha256`, and two actual desktop read results under
`first` and `second`. Reuse an existing verified capability; do not create test
tasks during an ordinary train. An executable change requires renewed evidence.
Local metadata alone cannot prove user visibility. No private application pipe
or undocumented bridge is used.

## Execute and answer

```text
python scripts/control_plane_runner.py drive --state MANIFEST --profile PROFILE --owner-thread-id OWNER --owner-epoch EPOCH
```

Add `--preflight-only` to check capability, project inputs and the pinned release
without dispatching workers. A successful preflight pins a copy of the profile;
resume with that same profile rather than changing running worker inputs.

The command starts a small guardian and a driver. The driver holds the OS lock,
replays event receipts, collects results and executes successors. The guardian
restarts a crashed driver at most three times. Host reconnection and per-command
retries are separately bounded. The guardian and driver provide continuous
script-only observation; do not create a scheduled app heartbeat or polling model.
The driver durably deduplicates actionable events and creates one small task with
compact context only for a newly announced human gate, an evidenced terminal error
or verified train completion. Native project metadata is recorded when available,
but the desktop currently lists app-server-created tasks under Recents even when
their accepted project metadata and repository root match. Keep their working
directories isolated; changing `cwd` does not repair this UI grouping. The relay
uses the profile's `attention_model` / `attention_reasoning_effort` settings, which
default to `gpt-6-astra` / `medium`. The exact event is embedded in its prompt, so
it does not load the train manifest or accumulated history merely to present it.
For a human gate it must show the reason, blocked and continuing scope, and every
accepted reply without summarizing them. Legacy gates whose replies referenced
worker artifacts are enriched deterministically from the already collected result;
new worker contracts require self-contained option meanings. The relay can persist the
user's exact gate answer into the driver inbox. Unchanged state consumes no model
tokens. Its own initial turn uses the same bounded, receipt-driven interruption
recovery as technical workers and never creates a replacement task.
`--max-seconds` creates a checkpoint, not continuous supervision. The process
does not survive computer shutdown or provide a private application notification
API. Restart the same invocation if both guardian and driver were stopped.

New actionable conditions are written to `driver/outbox/`. Read the referenced
item and present its actual question or error. Write the user's exact controller
event into `driver/inbox/`. Preserve event ID, gate, revision, semantic option and
decision reference. Technical workers cannot resolve user gates.
Native input answers use `HOST_REQUEST_ANSWER` with `server_instance`,
`request_id`, the actual host `result`, and `user_decision_reference`. A response
from another server lifetime is rejected. After fixing an exhausted command's
cause, an inbox `RETRY_ACTION` with the exact `action` object and evidence in
`reason` resets only that action's retry budget.

`train_supervisor.NativeEffects` persists intent before `thread/start` and the
actual response before returning it to the scheduler. It reuses recorded tasks
and turns. A missing response is reconciled through the operation's isolated cwd
and creation interval; zero or multiple matches never authorize a new task.

Worker tasks inherit the repository's Codex project through native `projectId`
metadata. The native project catalog is matched once against the profile's exact
repository root; the isolated worktree remains the task's `cwd`. This needs no AI
decision.

`phase_dispatch.py` implements dispatch, collection, Git integration, verification,
PRs and feedback. Workers return technical JSON; the adapter supplies identities
and journals the whole validated event transaction. No callback is required.

Project-specific operations may use one deterministic adapter:

```json
{"commands": {"ACTION_NAME": {"argv": ["python", "ABSOLUTE_ADAPTER.py"], "replay": "reconcile", "timeout_seconds": 600}}}
```

The last argv argument is an immutable input JSON path containing the action,
manifest, credentials, output path and receipt directory. Return `{ "events":
[...] }` at that output path. Persist receipts and reconcile before replaying an
effect. Keep stdout/stderr on disk. A missing contract produces an outbox item.

Native approval/input requests are preserved in the effects request directory.
They need the user's actual response; transport code never approves them.
Do not resume another server's active task; `notLoaded` does not prove it idle.

## Ownership and releases

Every writer checks owner and unpredictable epoch under the manifest lock, then
compares revision. Never repair an old credential by reading the newest epoch.
Persistent lock files are normal: the OS lock, not file existence, proves a lease.
Do not unlink lock files while another process could hold one.

`bootstrap` does not silently migrate an existing procedure. Pin a new runtime
only when workers and commands are idle, retaining the previous release and a
migration receipt. Historical artifacts remain available.

```text
python scripts/run_registry.py migrate-runtime --state MANIFEST --owner-thread-id OWNER --owner-epoch EPOCH
```

Changes suggested by AI model: GPT-6 (Codex).
