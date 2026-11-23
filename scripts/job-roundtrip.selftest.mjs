import assert from 'node:assert/strict';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const root = fileURLToPath(new URL('../', import.meta.url));
const bundle = await build({
  stdin: {
    contents: "export * from './src/job.ts'; export { team, mysticCodes } from './src/data.ts';",
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
console.log('作业加载与保存往返自检通过');
