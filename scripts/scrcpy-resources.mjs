#!/usr/bin/env node
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export function verifyScrcpyResources(root) {
  const directory = path.join(root, 'resources', 'scrcpy');
  const manifestPath = path.join(directory, 'manifest.json');
  if (!fs.existsSync(manifestPath)) throw new Error(`scrcpy 资源清单缺失：${manifestPath}`);
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  const version = manifest.version;
  const serverFile = manifest.server_file;
  const expected = manifest.sha256?.toLowerCase();
  const release = `https://github.com/Genymobile/scrcpy/releases/download/v${version}`;
  if (typeof version !== 'string' || typeof serverFile !== 'string' || path.basename(serverFile) !== serverFile
      || !/^[0-9a-f]{64}$/.test(expected || '') || manifest.source !== `${release}/scrcpy-server-v${version}`
      || manifest.checksums !== `${release}/SHA256SUMS.txt` || manifest.license !== 'LICENSE.txt') {
    throw new Error(`scrcpy 清单未固定到官方服务端、来源与已审计 SHA-256：${manifestPath}`);
  }
  const server = path.join(directory, serverFile);
  if (!fs.existsSync(server)) throw new Error(`scrcpy 服务端缺失：${server}；请恢复官方 ${version} 固定资源`);
  const actual = createHash('sha256').update(fs.readFileSync(server)).digest('hex');
  if (actual !== expected) throw new Error(`scrcpy 服务端 SHA-256 不匹配：期望 ${expected}，实际 ${actual}`);
  const license = path.join(directory, manifest.license);
  if (!fs.existsSync(license)) throw new Error(`scrcpy Apache-2.0 许可证缺失：${license}`);
  return { version, server, sha256: actual };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const result = verifyScrcpyResources(root);
  console.log(`scrcpy 固定资源校验通过：${result.version} ${result.sha256} ${result.server}`);
}
