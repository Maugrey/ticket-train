// Run with: node --test scripts/test_phase_dispatch.js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const dispatch = vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'phase_dispatch.js'), 'utf8'));

function harness(options = {}) {
  const calls = [];
  const receipt = options.queued ? {clientThreadId: 'queued-1'} : {threadId: 'worker-1', hostId: 'local'};
  const raw = {content: [{type: 'text', text: JSON.stringify(receipt)}], isError: false};
  const request = {target: {type: 'project', projectId: 'p', environment: {type: 'local'}},
    title: 'Contract check', prompt: 'Exact bounded handoff', model: 'gpt-5.6-terra', thinking: 'medium'};
  const prepared = {status: options.recover ? 'recover-receipt' : 'launch-armed', may_create: !options.recover,
    raw_receipt: options.recover ? raw : undefined, receipt_path: 'C:/run/receipt.json',
    attempt_directory: 'C:/run/attempt', tool_request: request};
  const tools = {
    mcp__codex_app__list_projects: async () => {calls.push('projects'); return {projects: [{id: 'p'}]};},
    mcp__codex_app__create_thread: async args => {calls.push(['create', args]); return raw;},
    mcp__codex_app__wait_threads: async args => {
      calls.push(['wait', args]);
      if (options.waitError) throw new Error('transport interrupted');
      return {polls: [{thread: {id: 'worker-1', hostId: 'local'}, latestTurn: {status: 'inProgress'}}]};
    },
    apply_patch: async patch => {calls.push(['persist', patch]); return {isError: false};},
    exec_command: async args => {
      const operation = / (begin|record|observe) --spec /.exec(args.cmd)[1];
      calls.push(operation);
      const value = operation === 'begin' ? prepared : operation === 'record'
        ? {status: 'recorded', thread_id: receipt.threadId || null, controller_revision: 4}
        : {controller_revision: 5, next_actions: [{action: 'WAIT_FOR_PHASE_RESULTS'}], turn_control: {may_end_turn: true}};
      return {exit_code: 0, output: JSON.stringify(value)};
    }
  };
  return {tools, calls, request, raw, config: {python: 'C:/Python/python.exe', script: 'C:/skill/phase_dispatch.py', spec: 'C:/run/spec.json'}};
}

test('one invocation creates once, persists raw receipt, records launch then observes fresh runtime', async () => {
  const h = harness();
  const result = await dispatch(h.tools, h.config);
  assert.deepEqual(h.calls.map(c => Array.isArray(c) ? c[0] : c),
    ['projects', 'begin', 'create', 'persist', 'wait', 'persist', 'record', 'wait', 'persist', 'observe']);
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls[2][1])), h.request);
  assert.ok(h.calls[3][1].includes(JSON.stringify(h.raw, null, 2).split('\n').map(l => '+' + l).join('\n')));
  assert.equal(result.controller_revision, 5);
  assert.equal(result.turn_control.may_end_turn, true);
});

test('recovered receipt never creates another task', async () => {
  const h = harness({recover: true});
  await dispatch(h.tools, h.config);
  assert.equal(h.calls.filter(c => c[0] === 'create').length, 0);
  assert.ok(h.calls.includes('record'));
});

test('missing host tool rejects before arming any side effect', async () => {
  const h = harness();
  delete h.tools.mcp__codex_app__wait_threads;
  await assert.rejects(dispatch(h.tools, h.config), /Required desktop tool unavailable/);
  assert.deepEqual(h.calls, []);
});

test('interrupted observation preserves receipt and never retries creation', async () => {
  const h = harness({waitError: true});
  await assert.rejects(dispatch(h.tools, h.config), /transport interrupted/);
  assert.deepEqual(h.calls.map(c => Array.isArray(c) ? c[0] : c), ['projects', 'begin', 'create', 'persist', 'wait']);
});

test('queued client ID is recorded but not passed to wait_threads as a real task', async () => {
  const h = harness({queued: true});
  const result = await dispatch(h.tools, h.config);
  assert.equal(result.thread_id, null);
  assert.equal(h.calls.filter(c => c[0] === 'wait').length, 0);
  assert.ok(h.calls.includes('record'));
});
