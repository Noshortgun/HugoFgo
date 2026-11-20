#!/usr/bin/env node
/**
 * issue-sync.mjs 的自检：用临时目录 + 离线 JSON 固定夹具验证分类与保护规则。
 * 不访问网络、不调用 gh、不需要测试框架。
 * 用法：node scripts/issue-sync.selftest.mjs
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const script = path.join(path.dirname(fileURLToPath(import.meta.url)), 'issue-sync.mjs');
const ISSUES_REL = path.join('.scratch', 'backend-rebuild', 'issues');
const SPEC_REL = '.scratch/backend-rebuild/spec.md';

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'hugofgo-issue-sync-selftest-'));
const issuesDir = path.join(root, ISSUES_REL);
fs.mkdirSync(issuesDir, { recursive: true });

function writeTicket(file, id, title, bodyLines) {
  fs.writeFileSync(
    path.join(issuesDir, file),
    [`# ${id}: ${title}`, '', ...bodyLines, ''].join('\n'),
    'utf8',
  );
}

function publishedBody(file, id, title, bodyLines) {
  return [
    `本地施工单：\`.scratch/backend-rebuild/issues/${file}\``,
    `Spec：\`${SPEC_REL}\``,
    '',
    '',
    ...bodyLines,
  ].join('\n');
}

function issue(number, title, state, labels, body) {
  return { number, title, state, labels: labels.map((name) => ({ name })), body, url: `https://example.invalid/${number}` };
}

const alphaBody = ['**Status:** ready-for-agent', '', '## Goal', '', 'alpha'];
const betaBody = ['**Status:** ready-for-agent', '', '## Goal', '', 'beta local'];
const deltaBody = ['**Status:** done', '', '## Goal', '', 'delta local'];
const parentBody = ['**Status:** ready-for-agent', '', '## Goal', '', 'parent'];
const childBody = ['## Parent', '', '#5', '', '## What to build', '', 'child'];
const sixBody = ['**Status:** ready-for-agent', '', '## Goal', '', 'six'];
const sevenBody = ['**Status:** ready-for-agent', '', '## Goal', '', 'seven'];

writeTicket('01-alpha.md', '01', 'Alpha', alphaBody);
writeTicket('02-beta.md', '02', 'Beta', betaBody);
writeTicket('03-gamma.md', '03', 'Gamma', ['**Status:** ready-for-agent', '', '## Goal', '', 'gamma']);
writeTicket('04-delta.md', '04', 'Delta', deltaBody);
writeTicket('05-parent.md', '05', 'Parent', parentBody);
writeTicket('05A-child.md', '05A', 'Child', childBody);
writeTicket('06-one.md', '06', 'Six', sixBody);
writeTicket('06-two.md', '06', 'Six', sixBody);
writeTicket('07-seven.md', '07', 'Seven', sevenBody);
fs.writeFileSync(path.join(issuesDir, '07.md'), '本地施工单：`.scratch/backend-rebuild/issues/07-seven.md`\n', 'utf8');
fs.writeFileSync(path.join(issuesDir, '07.current.md'), '## What to build\n\n遗留副本\n', 'utf8');
fs.writeFileSync(path.join(issuesDir, '16.md'), '## Parent\n\n#5\n\n## What to build\n\n05B 的遗留粘贴副本\n', 'utf8');

const remote = [
  issue(1, '01: Alpha', 'OPEN', ['ready-for-agent'], publishedBody('01-alpha.md', '01', 'Alpha', alphaBody)),
  issue(2, '02: Beta', 'OPEN', ['ready-for-agent'], publishedBody('02-beta.md', '02', 'Beta', ['**Status:** ready-for-agent', '', '## Goal', '', 'beta remote'])),
  issue(4, '04: Delta', 'CLOSED', ['ready-for-agent'], publishedBody('04-delta.md', '04', 'Delta', ['**Status:** done', '', '## Goal', '', 'delta remote'])),
  issue(5, '05: Parent', 'OPEN', ['ready-for-agent'], publishedBody('05-parent.md', '05', 'Parent', parentBody)),
  issue(16, '05A: Child', 'OPEN', ['ready-for-agent'], publishedBody('05A-child.md', '05A', 'Child', childBody)),
  issue(6, '06: Six', 'OPEN', ['ready-for-agent'], publishedBody('06-one.md', '06', 'Six', sixBody)),
  issue(7, '07: Seven', 'OPEN', ['ready-for-agent'], publishedBody('07-seven.md', '07', 'Seven', sevenBody)),
  issue(8, '08: Remote only', 'OPEN', ['ready-for-agent'], '## What to build\n\n远端独有'),
];
const fixture = path.join(root, 'remote.json');
fs.writeFileSync(fixture, JSON.stringify(remote), 'utf8');

function run(args) {
  const result = spawnSync(process.execPath, [script, '--root', root, '--from-file', fixture, ...args], {
    encoding: 'utf8',
  });
  return { code: result.status, stdout: result.stdout ?? '', stderr: result.stderr ?? '' };
}

function runJson(args) {
  const { code, stdout, stderr } = run(['--json', ...args]);
  assert.ok(stdout.trim().startsWith('{'), `期望 JSON 输出，实际：${stdout}\n${stderr}`);
  return { code, payload: JSON.parse(stdout) };
}

const failures = [];
function check(name, fn) {
  try {
    fn();
    console.log(`ok   ${name}`);
  } catch (error) {
    failures.push(`${name}: ${error.message}`);
    console.log(`FAIL ${name}\n     ${error.message.replaceAll('\n', '\n     ')}`);
  }
}

const full = runJson([]);
const byId = new Map(full.payload.tickets.map((ticket) => [ticket.id, ticket]));

check('检查返回非零并列出漂移票据', () => {
  assert.equal(full.code, 1);
  assert.deepEqual(full.payload.drift, ['2', '3']);
  assert.equal(full.payload.ok, false);
});

check('分类：无变化 / 修改 / 新增 / 缺本地源', () => {
  assert.equal(byId.get('1').status, 'unchanged');
  assert.equal(byId.get('2').status, 'modified');
  assert.deepEqual(byId.get('2').diff, { localOnly: 1, remoteOnly: 1 });
  assert.equal(byId.get('3').status, 'added');
  assert.equal(byId.get('8').status, 'source-missing');
});

check('重复编号被报为 error，且不重复报缺本地源', () => {
  const finding = full.payload.findings.filter((item) => item.kind === 'duplicate-number' && item.ticket === '6');
  assert.equal(finding.length, 1);
  assert.equal(finding[0].severity, 'error');
  assert.deepEqual(finding[0].files, ['06-one.md', '06-two.md']);
  const missing = full.payload.findings.filter((item) => item.kind === 'no-local-source' && item.ticket === '6');
  assert.equal(missing.length, 0);
});

check('遗留双份文件被报告，发布源仍唯一', () => {
  const legacy = full.payload.findings
    .filter((item) => item.kind === 'legacy-file' && item.ticket === '7')
    .map((item) => item.files[0])
    .sort();
  assert.deepEqual(legacy, ['07.current.md', '07.md']);
  assert.equal(byId.get('7').source, '07-seven.md');
  assert.equal(byId.get('7').status, 'unchanged');
});

check('孤立遗留副本按 issue 号提示对应票据', () => {
  const orphan = full.payload.findings.find((item) => item.kind === 'orphan-legacy-file' && item.ticket === '16');
  assert.ok(orphan, '缺少 orphan-legacy-file');
  assert.match(orphan.message, /票据 5A/);
});

check('父票据受保护，不进入阻塞漂移', () => {
  assert.deepEqual(byId.get('5').protected, ['parent']);
  assert.ok(!full.payload.drift.includes('5'));
});

check('已关闭票据受保护：漂移只列在 protectedDrift', () => {
  assert.equal(byId.get('4').status, 'modified');
  assert.deepEqual(byId.get('4').protected, ['closed']);
  assert.deepEqual(full.payload.protectedDrift, ['4']);
});

check('发布目标：父票据被拒绝（退出码 2）', () => {
  const { code, stderr } = run(['--target', '5']);
  assert.equal(code, 2);
  assert.match(stderr, /父票据/);
});

check('发布目标：已关闭票据被拒绝（退出码 2）', () => {
  const { code, stderr } = run(['--target', '4']);
  assert.equal(code, 2);
  assert.match(stderr, /已关闭/);
});

check('发布目标：可发布票据通过（退出码 0）', () => {
  assert.equal(run(['--target', '1']).code, 0);
});

check('发布目标：--allow-parent 放行父票据', () => {
  assert.equal(run(['--target', '5', '--allow-parent']).code, 0);
});

check('--json 模式下受保护目标同样退出码 2', () => {
  const { code, payload } = runJson(['--target', '4']);
  assert.equal(code, 2);
  assert.ok(payload.targets.problems.length > 0);
});

const cleanRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'hugofgo-issue-sync-clean-'));
fs.mkdirSync(path.join(cleanRoot, ISSUES_REL), { recursive: true });
fs.writeFileSync(path.join(cleanRoot, ISSUES_REL, '01-alpha.md'), ['# 01: Alpha', '', ...alphaBody, ''].join('\n'), 'utf8');
const cleanFixture = path.join(cleanRoot, 'remote.json');
fs.writeFileSync(
  cleanFixture,
  JSON.stringify([issue(1, '01: Alpha', 'OPEN', ['ready-for-agent'], publishedBody('01-alpha.md', '01', 'Alpha', alphaBody))]),
  'utf8',
);

check('全部一致时退出码 0 且 ok=true', () => {
  const result = spawnSync(
    process.execPath,
    [script, '--root', cleanRoot, '--from-file', cleanFixture, '--json'],
    { encoding: 'utf8' },
  );
  assert.equal(result.status, 0);
  const payload = JSON.parse(result.stdout);
  assert.equal(payload.ok, true);
  assert.deepEqual(payload.drift, []);
});

fs.rmSync(root, { recursive: true, force: true });
fs.rmSync(cleanRoot, { recursive: true, force: true });

if (failures.length) {
  console.error(`\n${failures.length} 项自检失败`);
  process.exit(1);
}
console.log('\nissue-sync 自检全部通过');
