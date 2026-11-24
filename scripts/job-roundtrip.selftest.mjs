import assert from 'node:assert/strict';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const root = fileURLToPath(new URL('../', import.meta.url));
const bundle = await build({
  stdin: {
    contents: "export * from './src/job.ts'; export { emptyServant, team, mysticCodes } from './src/data.ts'; export { inspectUnsupportedJob } from './src/jobValidation.ts'; export { frontlineEntries, normalizeServantOrder } from './src/servantOrder.ts';",
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
const sparseJob = structuredClone(job);
sparseJob.formation = [
  { slot: 0, name: '娄希', svtId: 705000, role: 'Berserker', support: true, ceId: 9403520 },
  { slot: 2, name: '奥伯龙', svtId: 2800100, role: 'Pretender' },
  { slot: 4, name: '   ', support: true, ce: '不应保留', ceId: 123 },
];
sparseJob.strategy.servantOrder = ['娄希', '奥伯龙', '旧空槽引用'];
sparseJob.strategy.servantOrderSlots = [0, 2, 4];
const sparseAction = {
  kind: 'skill', title: '保留空槽技能', detail: '目标也必须保留', icon: 'skill_00400',
  memberSlot: 4, skill: 0, playerTargetSlot: 4,
};
const sparseSwap = {
  kind: 'orderChange', title: '保留空槽换人', detail: '', icon: 'skill_00400',
  swapOutSlot: 0, swapInSlot: 4,
};
sparseJob.waves[0].turns[0].actions = [sparseAction, sparseSwap];
sparseJob.waves[0].turns[0].npPositions = { 4: 2 };
let sparseNextId = 100;
const sparseApplied = jobModule.applyFormation(
  sparseJob,
  jobModule.team.map((member) => ({ ...member })),
  jobModule.team,
  () => `sparse-member-${sparseNextId++}`,
);
assert.deepEqual(sparseApplied.members.map((member) => Boolean(member.empty)), [false, true, false, true, true, true],
  '省略槽位和纯空白名称必须归一为空槽，且不得继承编辑器从者');
assert.equal(sparseApplied.members[4].support, false, '空槽不得保留助战要求');
assert.equal(sparseApplied.members[4].ce, '', '空槽不得保留概念礼装');
assert.equal(sparseApplied.members[4].ceId, undefined, '空槽不得保留概念礼装 ID');
const sparseConverted = jobModule.jobToSnapshot(sparseJob, sparseApplied.members, jobModule.mysticCodes[0]);
const sparsePlan = sparseConverted.snapshot;
assert.equal(sparsePlan.actionsBySlot['1-1'].length, 2, '空槽动作与换人都必须保留');
assert.equal(sparsePlan.actionsBySlot['1-1'][0].playerTarget, sparseApplied.members[4].memberInstanceId,
  '空槽己方目标不得降级为无目标');
assert.equal(sparsePlan.actionsBySlot['1-1'][1].swapInMemberInstanceId, sparseApplied.members[4].memberInstanceId,
  '空槽换人引用不得删除');
assert.equal(sparsePlan.npPositionsBySlot['1-1'][sparseApplied.members[4].memberInstanceId], 2,
  '空槽宝具卡序不得过滤');
const sparseNormalized = jobModule.normalizeServantOrder(sparseJob.strategy, sparseApplied.members);
assert.deepEqual(sparseNormalized.servantOrderSlots, [0, 2, 4], '空槽从者顺序引用必须保留');
const sparseSaved = jobModule.buildJobFile({
  members: sparseApplied.members,
  mystic: jobModule.mysticCodes[0],
  fallback: sparseNormalized,
  fallbackByWave: { 1: null, 2: null, 3: null },
  snapshot: sparsePlan,
  name: sparseJob.name,
  base: sparseJob,
});
assert.deepEqual(sparseSaved.formation.map((entry) => entry.slot), [0, 1, 2, 3, 4, 5], '导出必须写全六槽');
assert.deepEqual(sparseSaved.formation.map((entry) => entry.name.trim()), ['娄希', '', '奥伯龙', '', '', '']);
assert.equal(sparseSaved.formation[4].support, undefined, '导出空槽不得带助战字段');
assert.equal(sparseSaved.formation[4].ce, undefined, '导出空槽不得带概念礼装');
assert.deepEqual(sparseSaved.waves[0].turns[0].actions, [sparseAction, sparseSwap],
  '空槽动作、目标和换人必须原样往返');
assert.deepEqual(sparseSaved.waves[0].turns[0].npPositions, { 4: 2 }, '空槽宝具卡序必须原样往返');
assert.deepEqual(sparseSaved.strategy.servantOrderSlots, [0, 2, 4], '空槽从者优先级必须原样往返');
const emptyDiagnostics = jobModule.inspectUnsupportedJob(sparseSaved);
const emptyDiagnosticPaths = emptyDiagnostics.map((diagnostic) => diagnostic.path);
assert.ok(emptyDiagnosticPaths.some((path) => path.endsWith('.memberSlot')), '启动前诊断必须定位空槽动作');
assert.ok(emptyDiagnosticPaths.some((path) => path.endsWith('.playerTargetSlot')), '启动前诊断必须定位空槽目标');
assert.ok(emptyDiagnosticPaths.some((path) => path.endsWith('.swapInSlot')), '启动前诊断必须定位空槽换人');
assert.ok(emptyDiagnosticPaths.some((path) => path.includes('.npPositions')), '启动前诊断必须定位空槽宝具卡序');
assert.ok(emptyDiagnosticPaths.includes('$.strategy.servantOrderSlots[2]'), '启动前诊断必须定位空槽从者优先级');
assert.deepEqual(jobModule.frontlineEntries(sparseApplied.members).map((entry) => entry.slot), [0, 2],
  '前排候选必须使用物理 0–2 槽，不得压缩后排或过滤后重排');

const clearedMember = jobModule.emptyServant();
clearedMember.support = true;
clearedMember.ce = '不可见礼装';
clearedMember.ceId = 999;
const clearedMembers = sparseApplied.members.map((member) => ({ ...member }));
const previousEmptyId = clearedMembers[4].memberInstanceId;
clearedMembers[4] = clearedMember;
const clearedPlan = jobModule.remapPlanMember(sparsePlan, previousEmptyId, clearedMember.memberInstanceId);
const clearedSaved = jobModule.buildJobFile({
  members: clearedMembers,
  mystic: jobModule.mysticCodes[0],
  fallback: sparseNormalized,
  fallbackByWave: { 1: null, 2: null, 3: null },
  snapshot: clearedPlan,
  name: sparseJob.name,
  base: sparseJob,
});
assert.deepEqual(clearedSaved.waves[0].turns[0].actions, [sparseAction, sparseSwap],
  '清空从者后，旧动作必须改挂到新的空槽运行态而不是丢失槽位');
assert.equal(clearedSaved.formation[4].support, undefined, '即使运行态残留，导出空槽也必须清除助战字段');
assert.equal(clearedSaved.formation[4].ceId, undefined, '即使运行态残留，导出空槽也必须清除礼装字段');

console.log('作业加载与保存往返自检通过（含空槽归一、非法引用保留、六槽导出与既有格式回归）');
