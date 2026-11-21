#!/usr/bin/env node
/** 把完整 verify 日志压缩成失败测试与第一处异常，供 GitHub Actions 注释使用。 */
import fs from 'node:fs';

const file = process.argv[2] ?? 'logs/verify.log';
if (!fs.existsSync(file)) {
  console.log(`没有验证日志：${file}`);
  process.exit(0);
}

const text = fs.readFileSync(file, 'utf8').replace(/\x1b\[[0-9;]*m/g, '');
const lines = text.split(/\r?\n/);
const patterns = [
  /^(ERROR|FAIL): /,
  /^(AssertionError|AttributeError|TypeError|ValueError|RuntimeError):/,
  /^FAILED \(/,
  /^\[verify\].*失败/,
  /Process completed with exit code/,
];
const selected = [];
for (const raw of lines) {
  const line = raw.trim();
  if (patterns.some((pattern) => pattern.test(line)) && !selected.includes(line)) selected.push(line);
}
const summary = selected.length ? selected : lines.filter(Boolean).slice(-20);
for (const line of summary.slice(0, 20)) {
  const message = line.replaceAll('%', '%25').replaceAll('\r', '%0D').replaceAll('\n', '%0A');
  console.log(`::error title=验证失败摘要::${message}`);
}
console.log(`验证失败摘要：${summary.length} 条；完整日志见 ${file}`);
