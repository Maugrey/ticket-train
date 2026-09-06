# Deterministic Control-Plane Runner

## Purpose

Use `scripts/control_plane_runner.py` to keep the main Codex conversation out
of deterministic supervision loops. The canonical run manifest is the train
identity. An orchestrator conversation is only a replaceable adapter segment.

The runner:

- reduces controller state to a bounded decision packet;
- suppresses unchanged wait states without a model wake;
- separates deterministic, adapter, and technical-decision actions;
- measures the current orchestrator segment;
- requests a controlled handoff before the conversation becomes oversized;
- leaves technical judgment to routed ticket threads.

It does not create Codex tasks by itself. The desktop task API remains exposed
to an active Codex conversation. The active adapter executes only the explicit
task action contained in the decision packet.

## Required loop

After bootstrap and after every changed task, GitHub, test, gate, or user
observation:

1. Record the observation through the procedural controller or deterministic
   supervisor.
2. Update current orchestrator activity when a fresh exact usage snapshot is
   available.
3. Run one control-plane step.
4. Read the packet for `packet-written`, or reuse it for `action-pending`.
5. Execute the action class named by `wake_kind`.
6. On `unchanged-suppressed`, return to the existing transition-aware wait.
7. Before a final response, require `turn_control.may_end_turn = true`.

### Response-to-successor adapter

For a user answer or a collected phase result, translate only the actual
answer/evidence to the existing exact-revision controller event, persist it
under the run's artifacts, then execute:

```powershell
python scripts/continuation_adapter.py advance --state <manifest.json> --owner <current-task-id> --expected-revision <revision> --event <event.json>
```

This checks canonical ownership, applies the event idempotently, writes the
decision packet and returns `next_actions` plus `turn_control`. It does not
infer an approval from prose. Use `INPUT_PROVIDED`, `GATE_RESOLVED`, or
`SPECIFICATION_DECISIONS_RECORDED` according to the actual gate; they are not
interchangeable. If interrupted after application, replay the same event ID
and payload to obtain the successor without asking for the decision again.
The adapter may also run without `--event` to obtain the current successor.

Execute the successor now, including reconfiguring paused supervision if
required. Record real execution receipts before the next iteration. Do not
treat `packet-written`, a prepared prompt, or a user acknowledgement as a
running operation. When a phase awaits input, resume its existing task with
the supplied answer and record `PHASE_RESUMED` after the tool succeeds.

### Runtime truth before yielding

Capture the raw `wait_threads` result (not an AI-written status summary) in a
new run artifact, then ingest it immediately:

When the host has a JavaScript tool-composition bridge, serialize the actual
tool return in that same invocation, without routing it through model-written
JSON. Resolve the host's callable task-tool name first. For example, with the
`functions.exec` bridge and the shown available tool names:

```javascript
const raw = await tools.mcp__codex_app__wait_threads({
  targets: [{threadId: childId}], timeoutMs: 0
});
const serialized = JSON.stringify(raw, null, 2);
await tools.apply_patch("*** Begin Patch\n*** Add File: " + snapshotPath + "\n"
  + serialized.split("\n").map(line => "+" + line).join("\n")
  + "\n*** End Patch");
```

Use an exact, new absolute path under the canonical run's artifacts (including
its fingerprint); never reconstruct or shorten the run directory name. If
raw capture is unavailable, report that concrete adapter limitation rather
than fabricating a product-shaped snapshot. A flattened object containing
`thread_id`/`runtime_status` is not the raw product schema and is rejected.

```powershell
python scripts/continuation_adapter.py observe --state <manifest.json> --owner <current-task-id> --expected-revision <revision> --snapshot <raw-wait-result.json>
```

`RUNTIME_OBSERVED` verifies the artifact hash, capture freshness (120 seconds),
phase identity and current owner. Only an observed active task with an
in-progress turn is liveness evidence. A multi-target wait can return only one
target: observe omitted targets separately, without treating them as missing
or relaunching them. Failed/unknown observations require bounded inspection of
the existing task, not a new task. Do not refresh a stale artifact's timestamp
or reconstruct a fake product response to pass the guard.

`COLLECT_OBSERVED_PHASE_RESULTS` means read the existing result and apply its
validated completion, failure, or input-request events. Product completion
alone does not imply successful tests or authorization to advance a gate.
Terminal observations stay actionable until consumed. `OBSERVE_ACTIVE_PHASES`
means fresh evidence is missing before a supervised yield; it is not a reason
for a periodic LLM heartbeat. Capture at a transition/yield boundary, not on a
two-minute model-waking schedule. No active phases plus only an announced
human gate requires neither observations nor repeated reminders.

### Collecting existing contract results

Creation and collection have different handlers. `phase_dispatch.py` and its
JavaScript bridge only launch/observe a phase; they never collect its verdict.
For a completed `plan_contract_validation`, use the actual result reference
from its callback/context and capture a fresh raw `wait_threads` result:

```powershell
python scripts/continuation_adapter.py collect-contract --state <manifest.json> --owner <current-task-id> --expected-revision <revision> --phase-key <existing-phase-key> --result <existing-result.json> --snapshot <raw-wait-result.json>
```

No caller-written collection specification is needed. The command validates
phase/ticket/run identity, the dispatched context and required input hashes,
then applies `PLAN_CONTRACT_RESULT_COLLECTED`: observed completion, completion
envelope and the existing contract verdict commit in one manifest revision.
It uses the recorded dispatch model/effort, not the child's self-identification.
A failed contract remains failed and requests a targeted amendment; completed
execution of a validator is not automatically a passing validation.

The generated private `collections/` event journal supports replay after an
interruption. Repeat the same command, not the validation or task creation.
An applied event is immutable and replayable without a new snapshot. If the
event was not applied and its snapshot expired, capture another genuine product
snapshot and pass it; never touch the previous artifact's timestamp. Gates,
size checkpoints, execution-base binding and successor routing remain intact.

`COLLECT_OBSERVED_PHASE_RESULTS` advertises this collector per completed
contract phase. For other phase kinds, use their existing completion/result
events via `advance`; do not invent a collector file or assume this command
supports them. A missing internal handler argument is an invocation repair,
not a user decision or permission to stop without inspecting the real handler.

The regression suite includes launch -> observed finish -> atomic verdict ->
successor, negative verdicts, malformed evidence and interrupted replay:
`python -m unittest test_contract_collection` from `scripts/`.

### Generated worker handoffs

#### One-shot visible launch (desktop bridge)

Use `scripts/phase_dispatch.js` in the canonical owner's active tool invocation
with `scripts/phase_dispatch.py`. This bundles the non-transactional product
boundary into one operation without a model turn between intent and receipt.
New plan-contract intents are supported directly; other phase kinds must first
be registered through their existing controller transition. Same-task resume
continues to use `continuation_adapter.py handoff`, not this creation bridge.

Prepare a schema-version-1 dispatch specification under the private run:

- `state`, `owner`, `phase_key`: exact canonical identity;
- `title`, `target`: authorized visible task destination (discover the saved
  project first; select an explicit local target for read-only checks, or the
  already authorized worktree/slot policy for code phases);
- `intent_event`: the exact `PHASE_DISPATCHED` event for a new contract phase,
  including its strict route, real compact context descriptor and Unity need.
  Omit only when the phase is already registered.

In a single `functions.exec` invocation, load this trusted skill file through
`exec_command`, check exit code, evaluate the returned JavaScript expression,
then call it with the current bridge's `tools` and
`{python: pythonPath, script: helperPath, spec: specificationPath}`. Do not end
the turn after loading the source. No shell-only Codex/hidden worker fallback
is permitted. The helper checks the owner, current action and context hash;
the bridge passes the generated prompt/model/effort without rewriting them.

The attempt is armed before task creation. The unmodified creation return is
persisted before interpretation. A real product observation verifies task
visibility, then a second fresh observation records runtime truth after the
launch event. An already-finished worker becomes a collection action; it is
never relaunched. A queued client ID is not a running task ID.

On interruption, repeat the same specification: a saved receipt is recovered
without another creation. An armed attempt without a receipt is ambiguous and
requires bounded reconciliation of the existing product task identity, never
a blind retry. No exactly-once guarantee is claimed across a product API that
does not expose an idempotency key. A native final response still cannot be
intercepted by skill code: execute the bridge, inspect `next_actions` and obey
`turn_control` before yielding. Do not replace a missing invocation with a
periodic LLM heartbeat.

Regression commands: `python -m unittest test_phase_dispatch` from `scripts/`
and `node --test scripts/test_phase_dispatch.js` from the skill root.

#### Individual handoff generation

After recording a dispatch intent, or resolving a same-task input request:

```powershell
python scripts/continuation_adapter.py handoff --state <manifest.json> --owner <current-task-id> --expected-revision <revision> --phase-key <phase-key>
```

Use the returned `prompt`, `model`, and `thinking` with the named product tool.
Choose the already-authorized project/worktree or leased Unity slot; the
generator does not authorize another editor. For resumption use the exact
`existing_thread_id`. The generator verifies the real compact context file
and emits run/phase/owner IDs, exact refs, the structured result schema and
mandatory `send_message_to_thread` notification instructions for completion,
failure, blocking and input needs. Save its `callback_contract_reference` in
the launch receipt. A generated handoff is not a launch receipt and cannot
authorize a yield. On ambiguous creation, reconcile the existing identity.

This protocol also applies to test and review workers. Consolidation uses
the controller's bounded technical-model action, not an unregistered helper
with independent ownership of the manifest. Existing untracked artifacts
must be inspected and reconciled by the canonical owner, not recreated.

Delivery is not execution acknowledgement. An unchanged automatic action
returns `action-pending` with the same packet reference and original wake
class until a controller event records its outcome. Do not create another
packet, rerun a command, or create a duplicate task merely because it was
delivered again: reconcile existing process/task/result evidence first.

`NO_MODEL_WAKE` controls notifications, not whether the current turn may end.
A foreground wait can be quiet and still require `CONTINUE_IN_CURRENT_TURN`.
The packet embeds the same yield checks as `train_controller.py check`.
Queued client IDs cannot rely on a future child's callback to justify a yield.
Action payloads retain routing, Unity leases, answers and gate details; the
16 KiB limit rejects an oversized packet instead of silently removing fields.

```powershell
python scripts/control_plane_runner.py step --state <manifest.json>
```

The packet is stored outside the repository under:

```text
<run-directory>/control-plane/wake-packets/
```

It contains only:

- run identity and controller revision;
- ticket state projection;
- active phase identifiers and task IDs;
- the single pending human action, when present;
- finalization status;
- compact cost and rotation status;
- bounded controller-authorized next actions.

Each next action also carries its fail-closed `executor_kind` classification.
The runner increments durable decision-packet counters only when it writes a
new packet; an unchanged suppressed observation does not count as a model wake.
The adapter records action spans and wake reasons through
`orchestration_metrics.py` around execution, without starting a telemetry-only
model turn.

It never contains a transcript, private reasoning, raw logs, full analysis, or
source-code diff. The hard packet limit is 16 KiB.

## Wake classes

Interpret `wake_kind` mechanically:

| Wake kind | Behavior |
|---|---|
| `NO_MODEL_WAKE` | Keep waiting through the transition-aware task connector, or stop the watcher for a pure human gate. Do not write a status message. |
| `RUN_DETERMINISTIC` | Execute the named guarded script or structured Git/GitHub operation without technical narration. |
| `WAKE_ADAPTER` | Use the active Codex adapter only to create, resume, reconcile, or announce the named visible task/gate. |
| `WAKE_TECHNICAL_DECISION` | Route the bounded evidence to the appropriate technical phase or perform the explicitly named consolidation/disposition. |
| `ROTATE_ORCHESTRATOR` | Perform the controlled handoff below before another ordinary model action. |
| `COMPLETE_CONTROLLED_HANDOFF` | Finish or accept the already prepared successor transfer; do not dispatch other work. |

An unchanged wait must not create a new controller event, packet, model turn,
thread read, or user-facing heartbeat. Pending human actions remain visible
through their already published `ACTION REQUIRED` message. When no technical
work is active, reminders are disabled by default and supervision stops. A
user may explicitly opt into a deterministic product reminder, but it never
wakes the orchestrator.

## Orchestrator activity budget

Record cumulative counters for the current orchestrator segment:

```powershell
python scripts/control_plane_runner.py record-activity `
  --state <manifest.json> `
  --thread-id <current-main-thread-id> `
  --baseline-total-tokens <baseline> `
  --latest-total-tokens <current> `
  --model-wakes <count> `
  --tool-calls <count> `
  --context-compactions <count>
```

Defaults:

```text
soft_total_tokens = 10,000,000
hard_total_tokens = 25,000,000
max_model_wakes = 50
max_tool_calls = 500
max_context_compactions = 1
decision_packet_max_bytes = 16,384
```

The soft threshold produces a warning in the next compact packet. Any hard
threshold requires rotation. These are context-control limits, not quality or
subscription-credit limits. They never lower routed model effort, remove a
test, or weaken review.

Measure activity at every material transition and before a new model phase.
Do not create a model turn solely to obtain a counter. When exact counters are
temporarily unavailable, continue compactly but rotate at the wake, tool-call,
or compaction threshold.

## Controlled orchestrator handoff

Rotation is part of an explicitly invoked train and does not create a new run.
It creates exactly one visible successor orchestrator with no inherited
conversation history.

1. Run `control_plane_runner.py step` and retain the generated packet.
2. Prepare a single-use transfer:

```powershell
python scripts/run_registry.py prepare-handoff `
  --state <manifest.json> `
  --from-thread <current-thread-id> `
  --reason <budget|compaction|manual> `
  --packet <decision-packet.json>
```

3. Create one new user-visible Codex task. Give it only:

   - the canonical manifest path;
   - the decision-packet path and hash;
   - the returned single-use handoff token;
   - the instruction to invoke this skill in resume-adapter mode.

4. The successor atomically accepts ownership:

```powershell
python scripts/run_registry.py accept-handoff `
  --state <manifest.json> `
  --to-thread <successor-thread-id> `
  --handoff-token <single-use-token>
```

5. Verify that the canonical lease names the successor. The former
   orchestrator becomes read-only and must not dispatch, apply events, or
   answer later wake-ups for that run.

When supervision uses `EVENT_CALLBACK`, the successor then executes
`RECONFIGURE_EVENT_CALLBACKS_FOR_CURRENT_OWNER`: it sends the new compact
callback target to every active visible child and records the verified target
before any other dispatch or yield.

If visible successor creation conclusively fails, cancel the prepared transfer
with the same single-use token and report the platform blocker:

```powershell
python scripts/run_registry.py cancel-handoff `
  --state <manifest.json> `
  --from-thread <current-thread-id> `
  --handoff-token <single-use-token>
```

Never use a fork for the successor. Never create two successors. Never accept
a handoff through a generic takeover flag. The packet plus single-use token is
the only automatic-rotation authority.

## User-visible continuity

The old and new conversations must both show a one-line handoff record:

```text
Train <run-id> transferred from <old thread> to <new thread> at controller
revision <revision>; no technical phase was repeated.
```

The user-facing train report remains consolidated by canonical run ID. Token
reporting counts every orchestrator segment separately and then sums them as
`orchestration`. A controlled successor is not a duplicate session.

## Host limitation

These scripts cannot intercept the desktop model's native final-response
operation or wake it through an unavailable host API. They reject a premature
yield when called and provide deterministic successors; the active adapter
must execute tool calls and honor the guard. Do not claim that merely writing
a decision packet installs an autonomous service. Prefer verified child
callbacks; otherwise keep a transition-aware foreground wait or use a real,
verified zero-model connector. Do not compensate with repeated idle LLM wakes.

When the host exposes only transition-aware waiting from an active Codex turn,
use one long `wait_threads` call over all active visible tasks and feed only a
changed snapshot back to the deterministic supervisor. A timeout with no
change returns directly to `control_plane_runner.py step`; if that result is
`unchanged-suppressed`, do not narrate it or reread any task.

The decision packet contains `supervision_projection.active_visible_tasks`.
Those verified child tasks are the user-facing progress signal. If the
projection is `ACTIVE_WITH_VISIBILITY_GAP`, wake the adapter exactly once to
repair or report the missing visible task identity; do not silently poll an
unidentified activity.

When a stable non-LLM task connector becomes available, attach it to the same
manifest and packet protocol. Do not redesign the lifecycle or move technical
judgment into the runner.
