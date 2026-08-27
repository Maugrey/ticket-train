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
4. Read only the generated packet when its status is `packet-written`.
5. Execute the action class named by `wake_kind`.
6. Do nothing when the result is `unchanged-suppressed`.

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
