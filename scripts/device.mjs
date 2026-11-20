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
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { deviceInfo, listDevices, runAdb } from './adb.mjs';

const args = process.argv.slice(2);

function option(name) {
  const index = args.indexOf(name);
  if (index === -1) return undefined;
  return args[index + 1];
}

function hasFlag(name) {
  return args.includes(name);
}

function pickSerial() {
  const requested = option('--serial');
  if (requested) return requested;
  const devices = listDevices();
  const usable = devices.find((device) => device.state === 'device');
  if (!usable) throw new Error('没有处于 device 状态的设备');
  return usable.serial;
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

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    main();
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  }
}
