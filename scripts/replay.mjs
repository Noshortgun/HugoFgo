#!/usr/bin/env node
/** 统一回放入口：定位 Python 解释器并运行 cv/replay.py，退出码透传。 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const candidates = [
  process.env.HUGOFGO_PYTHON,
  process.platform === 'win32' ? path.join(root, 'cv', '.venv', 'Scripts', 'python.exe') : path.join(root, 'cv', '.venv', 'bin', 'python'),
  'python3',
  'python',
].filter(Boolean);

const interpreter = candidates.find((candidate) => {
  if (path.isAbsolute(candidate)) return fs.existsSync(candidate);
  const result = spawnSync(candidate, ['--version'], { stdio: 'ignore' });
  return result.status === 0;
});

if (!interpreter) {
  console.error('未找到 Python。请先创建虚拟环境并安装依赖：');
  console.error('  python -m venv cv/.venv');
  console.error('  cv/.venv/Scripts/python.exe -m pip install opencv-python numpy rapidocr-onnxruntime');
  console.error('或设置 HUGOFGO_PYTHON 指向可用的解释器。');
  process.exit(1);
}

const result = spawnSync(interpreter, [path.join(root, 'cv', 'replay.py'), ...process.argv.slice(2)], {
  cwd: root,
  stdio: 'inherit',
  env: { ...process.env, PYTHONUTF8: '1' },
});
process.exit(result.status ?? 1);
