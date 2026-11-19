#!/usr/bin/env node
/**
 * 独立的 ADB 设备工具，不需要 Tauri 或 Rust。
 *
 * 用法：
 *   node scripts/device.mjs list
 *   node scripts/device.mjs info [--serial emulator-5554]
 *   node scripts/device.mjs screenshot [--serial ...] [--out path.png]
 *   node scripts/device.mjs tap 0.5 0.5 [--serial ...] [--pixels]
 *   node scripts/device.mjs key 4
 *
 * adb 查找顺序：HUGOFGO_ADB -> PATH -> 常见模拟器目录。
 * 截图默认保存到系统临时目录下的 hugofgo-frames。
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const args = process.argv.slice(2);

function option(name) {
  const index = args.indexOf(name);
  if (index === -1) return undefined;
  return args[index + 1];
}

function hasFlag(name) {
  return args.includes(name);
}

function existingFile(file) {
  try {
    return fs.statSync(file).isFile();
  } catch {
    return false;
  }
}

function adbPath() {
  const configured = process.env.HUGOFGO_ADB?.trim();
  if (configured) return configured;

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

function runAdb(serial, adbArgs, { binary = false } = {}) {
  const adb = adbPath();
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

function listDevices() {
  const text = runAdb(undefined, ['devices', '-l']);
  return text
    .split(/\r?\n/)
    .slice(1)
    .map((line) => line.trim())
    .filter(Boolean)
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

function pickSerial() {
  const requested = option('--serial');
  if (requested) return requested;
  const devices = listDevices();
  const usable = devices.find((device) => device.state === 'device');
  if (!usable) throw new Error('没有处于 device 状态的设备');
  return usable.serial;
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

function deviceInfo(serial) {
  const sizeText = runAdb(serial, ['shell', 'wm', 'size']);
  const size = parseSize(sizeText);
  if (!size) throw new Error(`无法解析分辨率：${sizeText.trim()}`);
  const densityText = runAdb(serial, ['shell', 'wm', 'density']);
  const density = Number((densityText.match(/(\d+)\s*$/m) ?? [])[1]) || undefined;
  const model = runAdb(serial, ['shell', 'getprop', 'ro.product.model']).trim();
  return { serial, ...size, density, model };
}

function screenshot(serial) {
  const output = option('--out');
  const directory = path.join(os.tmpdir(), 'hugofgo-frames');
  fs.mkdirSync(directory, { recursive: true });
  const stamp = new Date().toISOString().replace(/[-:TZ.]/g, '').slice(0, 14);
  const target = output ? path.resolve(output) : path.join(directory, `${serial}-${stamp}.png`);
  const bytes = runAdb(serial, ['exec-out', 'screencap', '-p'], { binary: true });
  if (bytes.length < 24 || bytes.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a') {
    throw new Error('截图不是有效的 PNG');
  }
  fs.writeFileSync(target, bytes);
  return {
    path: target,
    width: bytes.readUInt32BE(16),
    height: bytes.readUInt32BE(20),
    bytes: bytes.length,
  };
}

function tap(serial) {
  const x = Number(args[1]);
  const y = Number(args[2]);
  if (!Number.isFinite(x) || !Number.isFinite(y)) throw new Error('用法：tap <x> <y> [--pixels]');
  let px;
  let py;
  if (hasFlag('--pixels')) {
    px = Math.round(x);
    py = Math.round(y);
  } else {
    if (x < 0 || x > 1 || y < 0 || y > 1) throw new Error('归一化坐标必须在 0 到 1 之间');
    const info = deviceInfo(serial);
    px = Math.min(info.width - 1, Math.round(x * info.width));
    py = Math.min(info.height - 1, Math.round(y * info.height));
  }
  runAdb(serial, ['shell', 'input', 'tap', String(px), String(py)]);
  return { serial, x: px, y: py };
}

function main() {
  const command = args[0];
  if (!command || command === 'help' || command === '--help') {
    console.log('用法：node scripts/device.mjs list|info|screenshot|tap|key');
    return;
  }
  if (command === 'list') {
    console.log(JSON.stringify(listDevices(), null, 2));
    return;
  }
  const serial = pickSerial();
  if (command === 'info') {
    console.log(JSON.stringify(deviceInfo(serial), null, 2));
  } else if (command === 'screenshot') {
    console.log(JSON.stringify({ serial, ...screenshot(serial) }, null, 2));
  } else if (command === 'tap') {
    console.log(JSON.stringify(tap(serial), null, 2));
  } else if (command === 'key') {
    const keycode = args[1];
    if (!keycode) throw new Error('用法：key <keycode>');
    runAdb(serial, ['shell', 'input', 'keyevent', String(keycode)]);
    console.log(JSON.stringify({ serial, keycode }, null, 2));
  } else {
    throw new Error(`未知命令：${command}`);
  }
}

try {
  main();
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
