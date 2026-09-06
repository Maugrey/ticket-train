// Evaluates to one desktop bridge operation. No Node, filesystem or network APIs.
// Invoke only in the canonical owner's turn. Python enforces the lease and gates.
(async function dispatchPhase(tools, config) {
  const callable = (names) => {
    const name = names.find(name => typeof tools[name] === "function");
    if (!name) throw new Error("Required desktop tool unavailable: " + names.join(" / "));
    return tools[name].bind(tools);
  };
  // Resolve every required tool before arming a side effect.
  const create = callable(["mcp__codex_app__create_thread", "codex_app__create_thread"]);
  const wait = callable(["mcp__codex_app__wait_threads", "codex_app__wait_threads"]);
  const projects = callable(["mcp__codex_app__list_projects", "codex_app__list_projects"]);
  const exec = callable(["exec_command"]);
  const patch = callable(["apply_patch"]);
  const quote = value => "'" + String(value).replaceAll("'", "''") + "'";
  const unwrap = raw => {
    if (!raw || raw.isError) throw new Error("Desktop tool returned an error; reconcile, never repeat creation");
    if (raw.threadId || raw.clientThreadId || raw.projects) return raw;
    if (raw.structuredContent) return unwrap(raw.structuredContent);
    const blocks = (raw.content || []).filter(b => b.type === "text");
    if (blocks.length !== 1) throw new Error("Unexpected desktop result; reconcile existing attempt");
    return unwrap(JSON.parse(blocks[0].text));
  };
  const command = async (operation, observation) => {
    const result = await exec({cmd: "& " + quote(config.python) + " " + quote(config.script)
      + " " + operation + " --spec " + quote(config.spec)
      + (observation ? " --observation " + quote(observation) : ""), max_output_tokens: 12000});
    if (result.exit_code !== 0 || result.session_id) throw new Error("Dispatch adapter did not complete: " + result.output);
    return JSON.parse(result.output);
  };
  const saveRaw = async (path, value) => {
    const result = await patch("*** Begin Patch\n*** Add File: " + path + "\n"
      + JSON.stringify(value, null, 2).split("\n").map(line => "+" + line).join("\n") + "\n*** End Patch");
    if (result?.isError) throw new Error("Cannot persist raw receipt; do not repeat creation");
  };
  // Product tools require project discovery before creating a project task.
  await projects({});
  const prepared = await command("begin");
  if (prepared.status === "already-recorded") return prepared;
  let raw = prepared.raw_receipt;
  if (prepared.may_create === true) {
    raw = await create(prepared.tool_request);
    // Persist the unmodified return before interpreting it or observing the task.
    await saveRaw(prepared.receipt_path, raw);
  } else if (prepared.status !== "recover-receipt") {
    throw new Error("No authorized launch or recoverable receipt");
  }
  const receipt = unwrap(raw);
  let observation;
  if (receipt.threadId) {
    const snapshot = await wait({targets: [{threadId: receipt.threadId, hostId: receipt.hostId || "local"}], timeoutMs: 0});
    observation = prepared.attempt_directory + "/observation-" + Date.now() + "-" + Math.random().toString(16).slice(2) + ".json";
    await saveRaw(observation, snapshot);
  }
  const recorded = await command("record", observation);
  if (receipt.threadId) {
    // Runtime evidence must be newer than the launch/resume event. Keep its
    // real capture time; never touch an old snapshot's mtime to pass a gate.
    const fresh = await wait({targets: [{threadId: receipt.threadId, hostId: receipt.hostId || "local"}], timeoutMs: 0});
    const freshPath = prepared.attempt_directory + "/runtime-" + Date.now() + "-" + Math.random().toString(16).slice(2) + ".json";
    await saveRaw(freshPath, fresh);
    const successor = await command("observe", freshPath);
    return {...recorded, controller_revision: successor.controller_revision,
      next_actions: successor.next_actions, turn_control: successor.turn_control};
  }
  return recorded;
})
