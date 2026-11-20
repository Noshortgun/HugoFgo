#!/usr/bin/env node
/** 回放与执行器的 Python 解释器解析。查找顺序：HUGOFGO_PYTHON -> cv/.venv -> PATH。 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

export const REPLAY_PACKAGES = [
  { module: 'cv2', pip: 'opencv-python' },
  { module: 'numpy', pip: 'numpy' },
  { module: 'rapidocr_onnxruntime', pip: 'rapidocr-onnxruntime' },
];

export function venvPython(root) {
  return process.platform === 'win32'
    ? path.join(root, 'cv', '.venv', 'Scripts', 'python.exe')
    : path.join(root, 'cv', '.venv', 'bin', 'python');
}

function pythonCandidates(root) {
  return [
    { command: process.env.HUGOFGO_PYTHON?.trim(), source: 'HUGOFGO_PYTHON' },
    { command: venvPython(root), source: 'cv/.venv' },
    { command: 'python3', source: 'PATH' },
    { command: 'python', source: 'PATH' },
  ].filter((candidate) => candidate.command);
}

/** 返回 { command, source }；没有可用解释器时返回 null。显式 HUGOFGO_PYTHON 无效时不再回退。 */
export function resolvePython(root) {
  const configured = process.env.HUGOFGO_PYTHON?.trim();
  const candidates = configured
    ? [{ command: configured, source: 'HUGOFGO_PYTHON' }]
    : pythonCandidates(root).filter((candidate) => candidate.source !== 'HUGOFGO_PYTHON');
  for (const candidate of candidates) {
    if (path.isAbsolute(candidate.command) && !fs.existsSync(candidate.command)) continue;
    // 只认真正的 Python：node、被占用的 python 别名等不能通过 -c 执行。
    const result = spawnSync(candidate.command, ['-c', 'import sys; print(sys.version_info[0])'], { stdio: 'ignore' });
    if (result.status === 0) return candidate;
  }
  return null;
}

export function pipInstallCommand(python, packages = REPLAY_PACKAGES.map((item) => item.pip)) {
  return `"${python}" -m pip install ${packages.join(' ')}`;
}
