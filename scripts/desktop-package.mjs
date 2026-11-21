#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const NODE_VERSION = '22.14.0';

export function validateDesktopConfig(config, root) {
  if (config.productName !== 'HugoFgo' || !config.identifier) throw new Error('桌面产品名或标识符缺失');
  if (config.bundle?.active !== true) throw new Error('必须启用 Tauri bundle');
  if (config.bundle.targets !== 'nsis') throw new Error('Windows 发布必须生成 NSIS 安装包');
  if (!config.bundle.icon?.includes('icons/icon.ico')) throw new Error('Windows 安装包缺少 icon.ico');
  const resources = Object.entries(config.bundle.resources ?? {});
  if (!resources.length) throw new Error('安装包缺少运行时资源映射');
  for (const [source, destination] of resources) {
    if (destination.startsWith('data/')) throw new Error(`图库不得进入安装包：${destination}`);
    if (/^(?:\.\.\/)?(?:dist|public|captures|logs)(?:\/|$)/.test(source)) {
      throw new Error(`开发产物不得进入安装包：${source}`);
    }
    if (!source.startsWith('runtime/') && !fs.existsSync(path.resolve(root, 'src-tauri', source))) {
      throw new Error(`安装包资源不存在：${source}`);
    }
  }
  return resources;
}

async function prepareNode(root) {
  if (process.platform !== 'win32') throw new Error('Windows 安装包只能在 Windows 上构建');
  const runtimeRoot = path.join(root, 'src-tauri', 'runtime');
  const output = path.join(runtimeRoot, 'node');
  const archive = path.join(runtimeRoot, `node-v${NODE_VERSION}-win-x64.zip`);
  const extracted = path.join(runtimeRoot, 'extracted');
  const unpacked = path.join(extracted, `node-v${NODE_VERSION}-win-x64`);
  fs.mkdirSync(runtimeRoot, { recursive: true });
  fs.rmSync(output, { recursive: true, force: true });
  fs.rmSync(extracted, { recursive: true, force: true });
  try {
    console.log(`下载随包 Node.js ${NODE_VERSION} 运行时…`);
    const response = await fetch(`https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-win-x64.zip`);
    if (!response.ok) throw new Error(`Node.js 下载失败：HTTP ${response.status}`);
    fs.writeFileSync(archive, Buffer.from(await response.arrayBuffer()));
    const result = spawnSync('powershell.exe', [
      '-NoProfile', '-NonInteractive', '-Command',
      'Expand-Archive -LiteralPath $env:HUGOFGO_NODE_ARCHIVE -DestinationPath $env:HUGOFGO_NODE_EXTRACT -Force',
    ], {
      stdio: 'inherit',
      env: { ...process.env, HUGOFGO_NODE_ARCHIVE: archive, HUGOFGO_NODE_EXTRACT: extracted },
    });
    if (result.status !== 0) throw new Error('无法解压 Node.js 运行时');
    fs.mkdirSync(output, { recursive: true });
    fs.copyFileSync(path.join(unpacked, 'node.exe'), path.join(output, 'node.exe'));
    fs.copyFileSync(path.join(unpacked, 'LICENSE'), path.join(output, 'LICENSE.txt'));
  } finally {
    fs.rmSync(archive, { force: true });
    fs.rmSync(extracted, { recursive: true, force: true });
  }
}

function run(root, args) {
  const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
  const result = spawnSync(npm, args, { cwd: root, stdio: 'inherit', shell: process.platform === 'win32' });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${args.join(' ')} 失败，退出码 ${result.status ?? 1}`);
}

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const config = JSON.parse(fs.readFileSync(path.join(root, 'src-tauri/tauri.conf.json'), 'utf8'));
  validateDesktopConfig(config, root);
  await prepareNode(root);
  if (!process.argv.includes('--prepare-only')) {
    run(root, ['run', 'build']);
    run(root, ['run', 'replay']);
    run(root, ['run', 'tauri', 'build']);
  }
}
