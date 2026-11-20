#!/usr/bin/env node
/** adb 定位与只读查询，供 device.mjs 与 preflight.mjs 共用。 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

/** adb 查找顺序：HUGOFGO_ADB -> PATH -> 常见模拟器目录。 */
export function resolveAdb() {
  const configured = process.env.HUGOFGO_ADB?.trim();
  if (configured) {
    if (path.isAbsolute(configured) && !existingFile(configured)) {
      throw new Error(`HUGOFGO_ADB 指向的 adb 不存在：${configured}`);
    }
    return configured;
  }

  const localAppData = process.env.LOCALAPPDATA ?? '';
  const candidates = [
    'adb',
    path.join(localAppData, 'Android', 'Sdk', 'platform-tools', 'adb.exe'),
    'C:\\LDPlayer\\LDPlayer14\\adb.exe',
    'D:\\leidian\\LDPlayer14\\adb.exe',
    'C:\\LDPlayer\\LDPlayer9\\adb.exe',
    'C:\\Program Files\\Netease\\MuMuPlayer-12.0\\shell\\adb.exe',
    'C:\\Program Files\\Netease\\MuMu Player 12\\shell\\adb.exe',
  ];
  for (const candidate of candidates) {
    if (candidate === 'adb') {
      const found = spawnSync('where', ['adb'], { encoding: 'utf8' });
      if (found.status === 0) return 'adb';
    } else if (existingFile(candidate)) {
      return candidate;
    }
  }
  throw new Error('未找到 adb。请设置 HUGOFGO_ADB 指向 adb.exe');
}

export function runAdb(serial, adbArgs, { binary = false } = {}) {
  const adb = resolveAdb();
  const fullArgs = serial ? ['-s', serial, ...adbArgs] : adbArgs;
  const result = spawnSync(adb, fullArgs, {
    encoding: binary ? 'buffer' : 'utf8',
    maxBuffer: 128 * 1024 * 1024,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const message = binary ? result.stderr?.toString('utf8') : result.stderr;
    throw new Error(`adb 失败：${String(message ?? '').trim()}`);
  }
  return result.stdout;
}

export function listDevices() {
  const text = runAdb(undefined, ['devices', '-l']);
  return text
    .split(/\r?\n/)
    .slice(1)
    .map((line) => line.trim())
    .filter((line) => /^\S+\s+(device|offline|unauthorized|recovery|sideload|bootloader|unknown)\b/.test(line))
    .map((line) => {
      const [serial, state, ...rest] = line.split(/\s+/);
      const info = { serial, state };
      for (const part of rest) {
        if (part.startsWith('model:')) info.model = part.slice(6).replaceAll('_', ' ');
        else if (part.startsWith('product:')) info.product = part.slice(8);
        else if (part.startsWith('device:')) info.device = part.slice(7);
      }
      return info;
    });
}

function parseSize(text) {
  let physical;
  let overridden;
  for (const line of text.split(/\r?\n/)) {
    const match = line.match(/(\d+)\s*x\s*(\d+)/);
    if (!match) continue;
    const size = { width: Number(match[1]), height: Number(match[2]) };
    if (line.includes('Override')) overridden = size;
    else physical = size;
  }
  return overridden ?? physical;
}

export function deviceInfo(serial) {
  const sizeText = runAdb(serial, ['shell', 'wm', 'size']);
  const size = parseSize(sizeText);
  if (!size) throw new Error(`无法解析分辨率：${sizeText.trim()}`);
  const densityText = runAdb(serial, ['shell', 'wm', 'density']);
  const density = Number((densityText.match(/(\d+)\s*$/m) ?? [])[1]) || undefined;
  const model = runAdb(serial, ['shell', 'getprop', 'ro.product.model']).trim();
  return { serial, ...size, density, model };
}

function existingFile(file) {
  try {
    return fs.statSync(file).isFile();
  } catch {
    return false;
  }
}
