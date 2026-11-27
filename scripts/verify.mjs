#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { requirementsInstallCommand, resolvePython, venvPython } from './python-env.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';

export function createVerificationSteps(pythonCommand) {
  return [
    { name: 'frontend-build', command: npm, args: ['run', 'build'] },
    { name: 'run-state', command: process.execPath, args: [path.join(root, 'scripts/run-state.selftest.mjs')] },
    { name: 'run-settings', command: process.execPath, args: [path.join(root, 'scripts/run-settings.selftest.mjs')] },
    { name: 'job-roundtrip', command: process.execPath, args: [path.join(root, 'scripts/job-roundtrip.selftest.mjs')] },
    { name: 'secondary-selection', command: process.execPath,
      args: [path.join(root, 'scripts/secondary-selection.selftest.mjs')] },
    { name: 'executor-unit', command: pythonCommand,
      args: ['-m', 'unittest', 'discover', '-s', 'executor', '-t', '.', '-v'] },
    { name: 'device-selftest', command: pythonCommand, args: ['-m', 'executor.device', '--self-test'] },
    { name: 'references', command: process.execPath, args: [path.join(root, 'scripts/check-references.mjs')] },
    { name: 'references-selftest', command: process.execPath, args: [path.join(root, 'scripts/check-references.selftest.mjs')] },
    { name: 'fixtures', command: process.execPath, args: [path.join(root, 'scripts/check-perception-fixtures.mjs')] },
    { name: 'resources-selftest', command: process.execPath, args: [path.join(root, 'scripts/resources.mjs'), 'self-test'] },
    { name: 'desktop-selftest', command: process.execPath, args: [path.join(root, 'scripts/desktop-package.selftest.mjs')] },
    { name: 'scrcpy-resources', command: process.execPath, args: [path.join(root, 'scripts/scrcpy-resources.mjs')] },
    { name: 'replay', command: npm, args: ['run', 'replay'] },
    { name: 'issues-selftest', command: npm, args: ['run', 'issues:selftest'] },
  ];
}

export function main(argv = process.argv.slice(2)) {
  const fixtureScript = path.join(root, 'scripts/check-perception-fixtures.mjs');
  if (!fs.existsSync(fixtureScript)) {
    console.error(`缺少必需验证入口：${fixtureScript}`);
    return 1;
  }
  const python = resolvePython(root);
  if (!python) {
    console.error('未找到 Python。请先创建虚拟环境并安装固定版本的依赖：');
    console.error(`  ${requirementsInstallCommand(venvPython(root), root)}`);
    return 1;
  }
  const steps = createVerificationSteps(python.command);
  if (argv.includes('--list')) {
    for (const step of steps) console.log(`${step.name}: ${step.command} ${step.args.join(' ')}`);
    return 0;
  }
  for (const step of steps) {
    console.log(`\n[verify] ${step.name}`);
    const result = spawnSync(step.command, step.args, {
      cwd: root,
      stdio: 'inherit',
      env: { ...process.env, PYTHONUTF8: '1' },
      shell: step.command === npm,
    });
    if (result.error) console.error(result.error.message);
    if (result.status !== 0) {
      console.error(`[verify] ${step.name} 失败，退出码 ${result.status ?? 1}`);
      return result.status ?? 1;
    }
  }
  console.log('\n[verify] 全部验证通过');
  return 0;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  process.exit(main());
}
