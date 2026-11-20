#!/usr/bin/env node
/**
 * 统一回放入口：定位 Python 解释器，先验证回放依赖真实可导入，再运行 cv/replay.py。
 *
 * 缺依赖时不启动 cv/replay.py，因此不会生成或覆盖 logs/replay/latest.json；
 * 失败时只打印一条可复制的安装命令。
 */
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { probeReplayDeps, requirementsInstallCommand, resolvePython, venvPython } from './python-env.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const python = resolvePython(root);

if (!python) {
  const configured = process.env.HUGOFGO_PYTHON?.trim();
  if (configured) console.error(`HUGOFGO_PYTHON=${configured} 不是可用的 Python 解释器。`);
  console.error('未找到 Python。请先创建虚拟环境并安装固定版本的依赖：');
  console.error('  python -m venv cv/.venv');
  console.error(`  ${requirementsInstallCommand(venvPython(root), root)}`);
  console.error('或设置 HUGOFGO_PYTHON 指向可用的解释器。');
  process.exit(1);
}

const probe = probeReplayDeps(python.command, { cwd: root });

if (probe.status !== 'ok') {
  console.error(`回放依赖检查失败：${python.command}（${python.source}）`);
  if (probe.status === 'missing') {
    for (const item of probe.missing) {
      console.error(`  缺少 ${item.pip}（import ${item.module} 失败）：${item.error}`);
    }
  } else {
    console.error(`  ${probe.detail}`);
  }
  console.error('未执行任何回放用例，本次不生成也不覆盖 logs/replay/latest.json。');
  console.error('在仓库根目录执行一次即可修复：');
  console.error(`  ${requirementsInstallCommand(python.command, root)}`);
  process.exit(1);
}

const result = spawnSync(python.command, [path.join(root, 'cv', 'replay.py'), ...process.argv.slice(2)], {
  cwd: root,
  stdio: 'inherit',
  env: { ...process.env, PYTHONUTF8: '1' },
});
process.exit(result.status ?? 1);
