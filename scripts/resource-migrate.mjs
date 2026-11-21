import fs from 'node:fs';
import path from 'node:path';

import { REGIONS } from './resource-paths.mjs';

function copyMissing(source, destination) {
  if (!fs.existsSync(destination)) {
    fs.cpSync(source, destination, { recursive: true });
    return true;
  }
  if (!fs.statSync(source).isDirectory() || !fs.statSync(destination).isDirectory()) return false;
  let copied = false;
  for (const name of fs.readdirSync(source)) {
    copied = copyMissing(path.join(source, name), path.join(destination, name)) || copied;
  }
  return copied;
}

/** 把旧的 data/ 国服内容复制到 data/CN/；原文件保留，直到新目录校验通过。 */
export function migrateLegacyData(paths) {
  if (paths.region !== 'CN') return null;
  const legacyManifest = path.join(paths.dataRoot, 'resource-manifest.json');
  const target = paths.regionRoot;
  if (!fs.existsSync(legacyManifest)) return null;

  if (fs.existsSync(target)) {
    let copied = false;
    for (const name of fs.readdirSync(paths.dataRoot)) {
      if (REGIONS.includes(name) || name === path.basename(legacyManifest)) continue;
      const source = path.join(paths.dataRoot, name);
      const destination = path.join(target, name);
      copied = copyMissing(source, destination) || copied;
    }
    if (copied) console.log(`已补齐 ${path.relative(paths.dataRoot, target)}/ 中缺失的旧国服资源；原文件保留。`);
    return target;
  }

  const temporary = `${target}.migrating-${process.pid}`;
  fs.rmSync(temporary, { recursive: true, force: true });
  fs.mkdirSync(temporary, { recursive: true });
  try {
    for (const name of fs.readdirSync(paths.dataRoot)) {
      if (REGIONS.includes(name) || name === path.basename(temporary) || name === path.basename(target)) continue;
      fs.cpSync(path.join(paths.dataRoot, name), path.join(temporary, name), { recursive: true });
    }
    const manifest = JSON.parse(fs.readFileSync(legacyManifest, 'utf8'));
    manifest.region = 'CN';
    manifest.files = Object.fromEntries(
      Object.entries(manifest.files ?? {}).map(([file, entry]) => [
        file.startsWith('data/CN/') ? file : `data/CN/${file.replace(/^data[\\/]/, '')}`,
        entry,
      ]),
    );
    fs.writeFileSync(path.join(temporary, 'resource-manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
    fs.renameSync(temporary, target);
    console.log(`已把现有国服资源迁移到 ${path.relative(paths.dataRoot, target)}/，原文件保留。`);
    return target;
  } catch (error) {
    fs.rmSync(temporary, { recursive: true, force: true });
    throw error;
  }
}
