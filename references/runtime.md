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
transport for train-owned workers. Owner notifications use the bundled Codex app
tool `send_message_to_thread`, which reaches the conversation already owned by the
desktop without acquiring another writer. The runner uses the app's supplied MCP
bridge and stable tool-call IDs; it does not implement the private pipe protocol.
The npm CLI's Unix-only daemon is not required. A capability file
must record an actual desktop read of native-created tasks on this executable.
Use `ticket-train-native-capability-v1`, `desktop_read_verified: true`, the
`host_executable`, its `host_sha256`, and two actual desktop read results under
`first` and `second`. Reuse an existing verified capability; do not create test
tasks during an ordinary train. An executable change requires renewed evidence.
Local metadata alone cannot prove user visibility.

Codex Desktop updates may replace the versioned executable path while a train is
waiting. On restart, the runner resolves the current native binary and renews the
capability receipt by reading the same two existing capability tasks through the
bundled desktop tool. This is a read-only, script-only check: it creates no task
and wakes no model. The project profile remains pinned; only the effective host
path and its capability receipt are refreshed.

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
The driver durably deduplicates actionable events and starts one receipt-backed
turn in the train's existing owner conversation only for a newly announced human
gate, an evidenced terminal error or verified train completion. It never creates
a separate attention task. It never resumes the desktop-owned conversation through
the worker App Server. If the owner is busy, the native task relay queues the same
recorded message; a restart reconciles the exact prompt and stable tool-call ID
instead of creating a replacement.
The exact event is embedded in its prompt, so the owner does not reload the train
manifest or reconstruct the event merely to present it.
An interrupted train-owned worker resumes in the same task from its recorded
worktree and receipts. Its bounded service-retry budget counts only failed or
consecutively interrupted turns that made no observable command or file progress;
format repairs and user-input resumes do not consume it.
The runner also projects the current phase, transition, input wait, failure or
completion into the owner task title. It writes an intent first and updates only
when that semantic status changes; this desktop call starts no model turn.
For a human gate it must show the reason, blocked and continuing scope, and every
accepted reply without summarizing them. Legacy gates whose replies referenced
worker artifacts are enriched deterministically from the already collected result;
new worker contracts require self-contained option meanings. The relay can persist the
user's exact gate answer into the driver inbox. Unchanged state consumes no model
tokens. Interrupted owner turns use bounded, receipt-driven recovery and never
create a replacement task.
`--max-seconds` creates a checkpoint, not continuous supervision. The process
does not survive computer shutdown or provide a private application notification
API. Restart the same invocation if both guardian and driver were stopped.

New actionable conditions are written to `driver/outbox/`. Read the referenced
item and present its actual question or error. Write the user's exact controller
event into `driver/inbox/`. Preserve event ID, gate, revision, semantic option and
decision reference. Technical workers cannot resolve user gates.
If the same phase needs a corrected value after an earlier answer, it may reopen
the same gate ID with a new revision. The controller archives the resolved
revision and relays the new question once; unchanged revisions remain idempotent.
When the user narrows a live train, submit one `RUN_SCOPE_REDUCED` event listing
every retained and cancelled ticket plus the user decision reference. It may
cancel only tickets whose implementation has not started. The scheduler then
uses the retained execution order and preserves earlier analysis as history.
Native input answers use `HOST_REQUEST_ANSWER` with `server_instance`,
`request_id`, the actual host `result`, and `user_decision_reference`. A response
from another server lifetime is rejected. After fixing an exhausted command's
cause, an inbox `RETRY_ACTION` with the exact `action` object and evidence in
`reason` resets only that action's retry budget.

`train_supervisor.NativeEffects` persists intent before `thread/start` and the
actual response before returning it to the scheduler. It reuses recorded tasks
and turns. A missing response is reconciled through the operation's isolated cwd
and creation interval; zero or multiple matches never authorize a new task.

The native worker transport and the desktop sidebar use different project-ID
namespaces, so raw `projectId` metadata does not place workers under the saved
project in the UI. At creation time the runner instead creates or reconciles one
clearly named sidebar section for the run and moves every real worker task into
it before starting the turn. The section mutation is receipt-driven, bounded to
three retries, and wakes no model. A temporary sidebar failure never stops the
technical work; the task remains visible in Recents until a retry succeeds.

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
migration receipt. A terminal native observation and its matching blocked effect
receipt prove an interrupted worker is idle even if its controller launch state
has not yet been collected. Historical artifacts remain available.

```text
python scripts/run_registry.py migrate-runtime --state MANIFEST --owner-thread-id OWNER --owner-epoch EPOCH
```

Changes suggested by AI model: GPT-6 (Codex).
