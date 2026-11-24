import assert from 'node:assert/strict';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const root = fileURLToPath(new URL('../', import.meta.url));
const bundle = await build({
  stdin: {
    contents: "export * from './src/job.ts'; export { team, mysticCodes } from './src/data.ts'; export { inspectUnsupportedJob } from './src/jobValidation.ts';",
    resolveDir: root,
    sourcefile: 'job-roundtrip.ts',
    loader: 'ts',
  },
  bundle: true,
  platform: 'node',
  format: 'esm',
  write: false,
  logLevel: 'silent',
});
globalThis.window = {};
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
const jobModule = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
const source = fs.readFileSync(new URL('../jobs/娄希、娄希、奥伯龙.json', import.meta.url), 'utf8');
const job = jobModule.parseJobFile(source);
job.extraField = { preserved: true };
job.strategyByWave = { 1: job.strategy };
let nextId = 1;
const applied = jobModule.applyFormation(
  job,
  jobModule.team.map((member) => ({ ...member })),
  jobModule.team,
  () => `test-member-${nextId++}`,
);
const { snapshot } = jobModule.jobToSnapshot(job, applied.members, jobModule.mysticCodes[0]);
const saved = jobModule.buildJobFile({
  members: applied.members,
  mystic: jobModule.mysticCodes[0],
  fallback: job.strategy,
  fallbackByWave: { 1: null, 2: null, 3: null },
  snapshot,
  name: job.name,
  base: job,
});

assert.deepEqual(
  saved.formation.map((member) => member.npId),
  [705001, 705001, 2800101, undefined, undefined, undefined],
  '没有 Atlas 资产时，加载后再保存也必须保留 npId',
);
assert.deepEqual(saved.extraField, { preserved: true }, '再次保存必须保留原作业的额外字段');
assert.deepEqual(saved.strategy.strategyOrder, ['servant', 'cards', 'crit'], '旧作业往返保存不得新增显式克制判据');
assert.equal(saved.strategyByWave, undefined, '清空每面策略后不得从原作业复活旧值');

const longJob = structuredClone(job);
longJob.strategyByWave = undefined;
longJob.waves = Array.from({ length: 3 }, (_, waveIndex) => ({
  turnCount: 12,
  target: waveIndex + 1,
  turns: Array.from({ length: 12 }, () => ({ actions: [] })),
}));
longJob.waves[0].turns[0].attack = { mode: 'verify', cards: [{ slot: 0, type: 'buster' }] };
let longNextId = 1;
const longApplied = jobModule.applyFormation(
  longJob,
  jobModule.team.map((member) => ({ ...member })),
  jobModule.team,
  () => `long-member-${longNextId++}`,
);
const { snapshot: longSnapshot, warnings: longWarnings } = jobModule.jobToSnapshot(longJob, longApplied.members, jobModule.mysticCodes[0]);
assert.equal(longSnapshot.waveCount, 3, '三面作业必须完整保留面数');
assert.equal(longSnapshot.attackBySlot, undefined, '旧固定卡序不得进入编辑状态');
assert.ok(longWarnings.some((warning) => warning.includes('固定卡序已移除')), '忽略旧固定卡序必须明确提示');
assert.deepEqual(longSnapshot.turnCountByWave, { 1: 12, 2: 12, 3: 12 }, '导入不得截断每面回合数');
const longSaved = jobModule.buildJobFile({
  members: longApplied.members,
  mystic: jobModule.mysticCodes[0],
  fallback: longJob.strategy,
  fallbackByWave: { 1: null, 2: null, 3: null },
  snapshot: longSnapshot,
  name: longJob.name,
  base: longJob,
});
assert.equal(longSaved.waves.length, 3, '保存必须保留三面作业');
assert.deepEqual(longSaved.waves.map((wave) => wave.turns.length), [12, 12, 12], '保存不得截断每面回合数');
assert.equal(longSaved.waves[0].turns[0].attack, undefined, '再次保存不得复活旧固定卡序');

const fourWaveJob = structuredClone(longJob);
fourWaveJob.waves.push(structuredClone(longJob.waves[0]));
assert.throws(() => jobModule.parseJobFile(JSON.stringify(fourWaveJob)), /1～3 面/, '四面作业必须拒绝导入，不能静默截断');

const unsupported = structuredClone(longJob);
unsupported.waves[1].turns[2].actions = [{
  kind: 'skill',
  title: '需要二次选择的技能',
  detail: '测试',
  icon: 'skill_00400',
  memberSlot: 0,
  skill: 0,
  secondarySelection: { type: 'noblePhantasmCardType', index: 0, value: 'buster', label: '红卡' },
}];
const unsupportedDiagnostics = jobModule.inspectUnsupportedJob(unsupported);
assert.deepEqual(
  unsupportedDiagnostics.map(({ wave, turn }) => ({ wave, turn })),
  [{ wave: 2, turn: 3 }],
  '启动前检查必须定位二次选择所在的面与回合',
);
console.log('作业加载与保存往返自检通过（含三面十二回合、四面拒绝、旧固定卡序移除与不支持动作定位）');
