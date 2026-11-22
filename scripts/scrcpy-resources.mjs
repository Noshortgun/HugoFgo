#!/usr/bin/env node
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const VERSION = '3.3.3';
const SERVER_FILE = 'scrcpy-server.jar';
const SHA256 = '7e70323ba7f259649dd4acce97ac4fefbae8102b2c6d91e2e7be613fd5354be0';
const SOURCE = `https://github.com/Genymobile/scrcpy/releases/download/v${VERSION}/scrcpy-server-v${VERSION}`;

export function verifyScrcpyResources(root) {
  const directory = path.join(root, 'resources', 'scrcpy');
  const manifestPath = path.join(directory, 'manifest.json');
  if (!fs.existsSync(manifestPath)) throw new Error(`scrcpy 资源清单缺失：${manifestPath}`);
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  if (manifest.version !== VERSION || manifest.server_file !== SERVER_FILE || manifest.sha256 !== SHA256
      || manifest.source !== SOURCE) {
    throw new Error(`scrcpy 清单未固定到官方 ${VERSION} 服务端与已审计 SHA-256：${manifestPath}`);
  }
  const server = path.join(directory, SERVER_FILE);
  if (!fs.existsSync(server)) throw new Error(`scrcpy 服务端缺失：${server}；请恢复官方 ${VERSION} 固定资源`);
  const actual = createHash('sha256').update(fs.readFileSync(server)).digest('hex');
  if (actual !== SHA256) throw new Error(`scrcpy 服务端 SHA-256 不匹配：期望 ${SHA256}，实际 ${actual}`);
  const license = path.join(directory, 'LICENSE.txt');
  if (!fs.existsSync(license)) throw new Error(`scrcpy Apache-2.0 许可证缺失：${license}`);
  return { version: VERSION, server, sha256: actual };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const result = verifyScrcpyResources(root);
  console.log(`scrcpy 固定资源校验通过：${result.version} ${result.sha256} ${result.server}`);
}
