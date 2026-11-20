#!/usr/bin/env node
/** 回放与执行器的 Python 解释器解析与依赖探测。查找顺序：HUGOFGO_PYTHON -> cv/.venv -> PATH。 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

export const REPLAY_PACKAGES = [
  { module: 'cv2', pip: 'opencv-python' },
  { module: 'numpy', pip: 'numpy' },
  { module: 'rapidocr_onnxruntime', pip: 'rapidocr-onnxruntime' },
];

/** 回放依赖的固定版本清单：安装、预检提示与 CI 都以它为准。 */
export const REQUIREMENTS_FILE = path.join('cv', 'requirements.txt');

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

/** 唯一、可复制的安装命令：按固定版本清单给指定解释器装依赖。 */
export function requirementsInstallCommand(python, root) {
  return `"${python}" -m pip install -r "${path.join(root, REQUIREMENTS_FILE)}"`;
}

const PROBE_SCRIPT = `
import importlib
import importlib.metadata
import json
import platform

packages = ${JSON.stringify(
  Object.fromEntries(REPLAY_PACKAGES.map((item) => [item.module, item.pip])),
)}
modules = {}
missing = []
for module, distribution in packages.items():
    try:
        importlib.import_module(module)
        try:
            version = importlib.metadata.version(distribution)
        except Exception:
            version = "已安装"
        modules[module] = version
    except Exception as exc:
        missing.append({"module": module, "pip": distribution, "error": str(exc)})
print(json.dumps({"version": platform.python_version(), "modules": modules, "missing": missing}))
`;

/**
 * 在运行任何回放用例前确认真实依赖可导入（不是只看包名是否存在）。
 * 返回 { status: 'ok'|'missing'|'error'|'timeout', python, report?, missing?, detail? }。
 */
export function probeReplayDeps(python, { cwd, timeout = 120_000 } = {}) {
  const result = spawnSync(python, ['-c', PROBE_SCRIPT], {
    cwd,
    encoding: 'utf8',
    env: { ...process.env, PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1' },
    timeout,
  });

  if (result.error?.code === 'ETIMEDOUT') {
    return { status: 'timeout', python, detail: `依赖探测超过 ${Math.round(timeout / 1000)} 秒未返回` };
  }
  if (result.error) return { status: 'error', python, detail: result.error.message };

  const lines = (result.stdout ?? '').trim().split(/\r?\n/);
  let report;
  try {
    report = JSON.parse(lines[lines.length - 1]);
  } catch {
    const stderr = (result.stderr ?? '').trim().split(/\r?\n/).slice(0, 3).join('\n');
    return { status: 'error', python, detail: stderr || `依赖探测失败（退出码 ${result.status}）` };
  }

  if (report.missing?.length) return { status: 'missing', python, report, missing: report.missing };
  return { status: 'ok', python, report };
}
