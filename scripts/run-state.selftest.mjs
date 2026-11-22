import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';

const source = fs.readFileSync(new URL('../src/runState.ts', import.meta.url), 'utf8');
const output = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const runState = await import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`);

let state = runState.emptyRunState();
assert.equal(state.phase, 'idle');
assert.equal(state.action, '');

state = runState.applyRunEvent(state, {
  run_id: 'run-1', type: 'state_changed', timestamp: 1,
  data: { state: 'battle', wave: 2, turn: 3 },
});
state = runState.applyRunEvent(state, {
  run_id: 'run-1', type: 'action', timestamp: 2,
  data: { control: '从者1技能1', wave: 2, turn: 3 },
});
state = runState.applyRunEvent(state, {
  run_id: 'run-1', type: 'recovery_used', timestamp: 3,
  data: { item: 'bronze', count: 2 },
});
assert.equal(state.phase, 'running');
assert.equal(state.wave, 2);
assert.equal(state.turn, 3);
assert.equal(state.action, '从者1技能1');
assert.deepEqual(state.recoveryUsed, { bronze: 2 });

const finished = runState.applyRunEvent(state, {
  run_id: 'run-1', type: 'finished', timestamp: 4,
  data: { completed_runs: 1 },
});
assert.equal(finished.phase, 'completed');
assert.equal(finished.completedRuns, 1);
assert.equal(runState.applyRunEvent(finished, {
  run_id: 'run-2', type: 'failed', timestamp: 5, data: { reason: 'stale' },
}), finished, '其他运行的延迟事件不得覆盖当前状态');

const failed = runState.applyRunEvent(state, {
  run_id: 'run-1', type: 'failed', timestamp: 5,
  data: { reason: 'device_error', message: '设备离线', evidence_dir: 'logs/farm/run-1' },
});
assert.equal(failed.phase, 'failed');
assert.equal(failed.error, '设备离线');
assert.equal(failed.logDir, 'logs/farm/run-1');

const fallbackNotice = runState.applyRunEvent(state, {
  run_id: 'run-1', type: 'channel_changed', timestamp: 6,
  data: { from: 'scrcpy', to: 'adb', message: 'scrcpy 启动失败，按用户已确认的回退策略改用 ADB' },
});
assert.equal(fallbackNotice.notice, 'scrcpy 启动失败，按用户已确认的回退策略改用 ADB');
assert.equal(fallbackNotice.channel, 'adb');
const warning = runState.applyRunEvent(fallbackNotice, {
  run_id: 'run-1', type: 'warning', timestamp: 7,
  data: { reason: 'scrcpy_start_failed', message: '资源校验失败' },
});
assert.equal(warning.notice, '资源校验失败');
console.log('运行状态事件归约自检通过');
