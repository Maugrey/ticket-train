# Unity Local MCP Execution Profile

## Purpose

Use this profile for Unity repositories whose editor capabilities are exposed
by the AI Game Dev Unity package and its local MCP server. The profile keeps
editor lifecycle and concurrency outside prompts: persistent worktrees,
exclusive leases, readiness checks, recovery attempts, and controller events
are managed by scripts.

Cloud MCP is unsupported by this profile. Every MCP-backed phase uses the
local server associated with the Unity Editor opened on its leased worktree.

## Startup configuration

Select `environment_profile = unity-mcp-local` when the repository contains:

- `Assets/`;
- `Packages/manifest.json` with a pinned registry version of
  `com.ivanmurzak.unity.mcp`;
- `ProjectSettings/ProjectVersion.txt`.

Resolve these values before controller bootstrap:

```text
unity_repository = absolute local Git root
max_unity_editors = user override or 3
mcp_mode = local
unity_slot_root = default sibling directory or explicit safe path
```

Natural-language overrides are valid. For example:

```text
Use $ticket-train for features 25, 26 and 35 with local Unity MCP and at most 2 Unity editors.
```

If the prompt gives no editor limit, use three. The limit is global for the
run, not per ticket or per execution pair. Reject values below one or above
sixteen. Echo the resolved value in the startup preflight.

Bootstrap the controller with the local checkout:

```powershell
python scripts/train_controller.py bootstrap `
  --state <run-manifest.json> `
  --base-branch <main-or-master> `
  --approval-mode <mode> `
  --environment-profile unity-mcp-local `
  --unity-repository <absolute-unity-git-root> `
  --max-unity-editors <count>
```

## Persistent Unity slots

A Unity slot is a persistent detached Git worktree named
`unity-slot-<number>`. By default the pool is stored in the sibling directory
`<repository>-unity-slots`; its registry is stored outside the repository at
`$CODEX_HOME/ticket-train/unity-slots/<repository-id>/slots.json`.

Initialize missing slots only through:

```powershell
python scripts/unity_slot_manager.py init `
  --repository <absolute-unity-git-root> `
  --base-ref <exact-base-ref> `
  --max-editors <count>
```

Initialization is idempotent. It creates only missing worktrees, preserves
existing slot directories, and configures each slot with all AI Game Dev
tools, prompts, and resources enabled. It uses the `unity-mcp-cli` version
matching the Unity package version pinned in `Packages/manifest.json`.

The generated worktree-local `.codex/config.toml` is a managed slot override.
Before switching a slot to another branch or detached commit, the manager
preserves its bytes in memory, restores the tracked template for the Git
transition, reapplies the local override, and restores `skip-worktree`. This is
automatic and reversible; it must not create a user gate or require a manual
stash. Project files other than this exact managed override remain subject to
the ordinary clean-worktree refusal.

The stable worktree path preserves Unity's imported Library and the ignored
`UserSettings/AI-Game-Developer-Config.json` across tickets. This minimizes
reimports, repeated agent configuration, port changes, and Windows firewall
prompts. It cannot suppress a firewall prompt caused by an actual executable
or operating-system policy change.

Do not create one Unity editor per Codex task. A task receives a lease on a
slot and works in that slot's worktree. When its phase ends, release the lease
but keep the editor ready by default. Lowering the configured limit disables
surplus slots without deleting their worktrees.

## Per-phase capability declaration

Every technical phase in this profile declares exactly one requirement:

```text
none          no Unity Editor or local MCP needed
editor-read   inspect scenes, prefabs, serialized state, or editor-only data
editor-write  modify Unity assets or perform editor-backed authoring
playmode-ui   run Play Mode, UI, input, or visual acceptance scenarios
build         execute editor-backed build or player pipeline operations
```

The declaration applies independently to:

- full analysis and analysis reconciliation;
- implementation;
- independent acceptance-test authoring;
- deterministic ticket verification;
- initial review and focused re-review;
- remediation;
- final exact-head verification, final review, and final remediation.

Triage stays fast and does not reserve an editor. It must nevertheless classify
the full analysis requirement. `none` is valid and should be preferred when
repository files and ordinary commands provide sufficient evidence.

Do not infer that every Unity ticket needs MCP for every phase. Reserve an
editor only when the phase's evidence or action depends on the Unity Editor.
Conversely, do not downgrade to `none` merely because the editor pool is busy.

## Deterministic acquire and release

The controller emits one of these actions:

```text
INITIALIZE_UNITY_SLOTS_DETERMINISTICALLY
ACQUIRE_UNITY_SLOT_DETERMINISTICALLY
WAIT_FOR_UNITY_SLOT
RELEASE_UNITY_SLOT_DETERMINISTICALLY
```

Execute initialization, acquisition, and release through the adapter:

```powershell
python scripts/unity_slot_adapter.py --state <run-manifest.json>
```

One invocation performs exactly one authorized Unity resource transition and
records its idempotent controller event. Repeat only when the returned
`next_actions` authorizes another deterministic Unity transition.

Do not invoke this adapter for `RECORD_*`, `DISPATCH_*`, gate, review, or other
controller actions. In particular, record a phase dispatch intent before the
controller can authorize slot acquisition. A refusal naming a non-Unity next
action means the orchestrator called the wrong handler; execute that named
action instead of treating the refusal as an environment failure.

On acquisition, the manager:

1. obtains an exclusive registry lock;
2. rejects project dirt while preserving the exact managed Codex MCP override;
3. checks out the required branch, or an exact detached head for read-only and
   final verification;
4. verifies that the observed head equals the controller-authorized head;
5. reapplies the stable AI Game Dev configuration;
6. opens the Unity Editor;
7. waits for local MCP readiness and records status evidence;
8. retries close/open/readiness at most twice after the initial attempt;
9. quarantines a slot that cannot prepare the requested revision and tries the
   next eligible slot;
10. records `BLOCKED_HUMAN` with diagnostics only after the bounded eligible
    pool or readiness recovery is exhausted.

The controller refuses to launch or resume an MCP-backed phase without its
matching active lease. A completed, failed, blocked, or input-waiting phase
releases the slot deterministically. User silence never consumes an editor
indefinitely.

## Branch and worktree rules

For the Unity profile, persistent slots replace disposable phase worktrees for
every phase that requires an editor:

- implementation switches one leased slot to the implementation branch;
- independent acceptance-test authoring switches another slot to its separate
  test branch when it needs the editor;
- read-only analysis and review use an exact detached head unless a phase
  branch is explicitly required;
- ticket verification uses the exact implementation or remediation head;
- final verification uses the exact final pull-request head detached.

Branches remain separate exactly as in the generic train. The slot is a
reusable execution location, not a shared branch. Never let two owners use one
slot concurrently, and never switch a leased slot behind its owner's task.

If only one side of an implementation/acceptance pair requires MCP, only that
side consumes a slot. With the default limit, any combination of analysis,
implementation, tests, or review may use at most three editors concurrently.
Non-MCP phases remain governed by their normal analysis and execution-pair
limits.

After the two execution workers complete, release their leases, integrate the
independent test commit on the implementation branch through the controller's
deterministic execution-pair integration action, then reacquire the slot that
already owns that branch for Unity-backed green verification. Creating a
second disposable worktree for the same branch is forbidden by Git and defeats
slot persistence.

## Dry-run behavior

A Unity dry run remains read-only for project content and Git history. The
controller may initialize persistent slots and open a slot when an analysis
needs `editor-read`; this is local environment provisioning, not ticket
implementation. It must use an exact detached base and must not create ticket
branches, commit, push, or modify tracked assets.

## Failure and recovery

Automatic recovery is bounded. After the manager exhausts its retries, report
one explicit human action containing:

- affected slot and phase;
- local worktree path;
- last readiness or CLI error;
- exact branch and expected head;
- whether the slot is dirty;
- the smallest requested intervention, such as closing a modal Unity dialog
  or approving a firewall prompt.

Independent phases that do not need the unavailable slot remain eligible.
Never silently switch to cloud MCP, start an unmanaged Unity Editor, increase
the editor limit, recreate a slot directory, or duplicate the technical phase.

## Security and public-repository hygiene

Keep the slot registry and full MCP output outside both the Unity project and
the ticket-train skill repository. Record stable configuration hashes and
paths, not auth tokens, ephemeral connection URLs, secrets, machine-specific
firewall rules, or the full ignored Unity settings file. The public skill
contains no project name, user path, server credential, or environment value.

## Upstream references

- [Unity MCP CLI](https://github.com/ivanmurzak/unity-mcp/blob/main/cli/README.md)
  documents `configure`, `open`, `wait-for-ready`, `status`, `close`, and
  `run-tool` automation.
- [Unity MCP server setup](https://github.com/ivanmurzak/unity-mcp/wiki/Server-Setup)
  documents deterministic path-derived local ports, which is why stable slot
  paths matter.
- [Unity MCP troubleshooting](https://github.com/ivanmurzak/unity-mcp/wiki/Troubleshooting)
  documents the project-local
  `UserSettings/AI-Game-Developer-Config.json` configuration.
