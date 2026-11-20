#!/usr/bin/env node
/** 统一回放入口：定位 Python 解释器并运行 cv/replay.py，退出码透传。 */
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { pipInstallCommand, resolvePython, venvPython } from './python-env.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const python = resolvePython(root);

if (!python) {
  const configured = process.env.HUGOFGO_PYTHON?.trim();
  if (configured) console.error(`HUGOFGO_PYTHON=${configured} 不是可用的 Python 解释器。`);
  console.error('未找到 Python。请先创建虚拟环境并安装依赖：');
  console.error('  python -m venv cv/.venv');
  console.error(`  ${pipInstallCommand(venvPython(root))}`);
  console.error('或设置 HUGOFGO_PYTHON 指向可用的解释器。');
  process.exit(1);
}

const result = spawnSync(python.command, [path.join(root, 'cv', 'replay.py'), ...process.argv.slice(2)], {
  cwd: root,
  stdio: 'inherit',
  env: { ...process.env, PYTHONUTF8: '1' },
});
process.exit(result.status ?? 1);
