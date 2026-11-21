#!/usr/bin/env node
/** 校验感知回归素材：字段完整、来源标注、基页合法、文件存在；按来源分组报告。 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fixturePath = path.join(root, 'executor/perception/fixtures/screens-cn.json');
const BASE_PAGES = new Set(['menu', 'support', 'team', 'battle', 'card', 'result', 'drops', 'ap_recovery', 'story', 'loading', 'unknown']);
const BASIS = new Set(['annotated', 'rule', 'inferred', 'holdout']);
const REQUIRED = ['file', 'base', 'variants', 'overlays', 'actionable', 'basis'];

function fail(errors, file, message) {
  errors.push(`${file}：${message}`);
}

const data = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
const errors = [];
const seen = new Set();
const counts = new Map();

for (const group of ['regression', 'crossSession']) {
  const cases = data[group];
  if (!Array.isArray(cases)) {
    fail(errors, group, '必须是数组');
    continue;
  }
  for (const [index, testCase] of cases.entries()) {
    const where = `${group}[${index}] ${testCase?.file ?? '(无文件)'}`;
    for (const field of REQUIRED) {
      if (testCase[field] === undefined) fail(errors, where, `缺少字段 ${field}`);
    }
    if (!BASE_PAGES.has(testCase.base)) fail(errors, where, `未知基页 ${testCase.base}`);
    if (!BASIS.has(testCase.basis)) fail(errors, where, `未知来源 ${testCase.basis}（可选 annotated/rule/inferred/holdout）`);
    if (typeof testCase.actionable !== 'boolean') fail(errors, where, 'actionable 必须是布尔值');
    for (const field of ['variants', 'overlays', 'absentControls', 'actionableControls']) {
      if (testCase[field] !== undefined && !Array.isArray(testCase[field])) {
        fail(errors, where, `${field} 必须是数组`);
      }
    }
    if (testCase.file) {
      if (seen.has(testCase.file)) fail(errors, where, '重复的文件');
      seen.add(testCase.file);
      if (!fs.existsSync(path.join(root, testCase.file))) fail(errors, where, '截图不存在');
      counts.set(testCase.basis, (counts.get(testCase.basis) ?? 0) + 1);
    }
  }
}

for (const [basis, count] of [...counts].sort()) {
  console.log(`${basis}: ${count} 帧`);
}
console.log(`合计 ${seen.size} 帧`);

if (errors.length) {
  for (const error of errors) console.error(`错误 ${error}`);
  process.exit(1);
}
console.log('感知回归素材校验通过');
