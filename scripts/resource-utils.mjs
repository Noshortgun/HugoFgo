import fs from 'node:fs';
import path from 'node:path';

export function readJsonOr(file, fallback) {
  return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : fallback;
}

export function metadataSource(root, region, name, env = process.env) {
  const local = path.join(path.resolve(env.HUGOFGO_DATA_ROOT || path.join(root, 'data')), region, name);
  return fs.existsSync(local) ? local : path.join(root, 'seed', region, name);
}

export function loadRemoteUnavailable(root, region = 'CN') {
  const dataRoot = path.resolve(process.env.HUGOFGO_DATA_ROOT || path.join(root, 'data'));
  try {
    const manifest = JSON.parse(fs.readFileSync(path.join(dataRoot, region, 'resource-manifest.json'), 'utf8'));
    return new Set(Object.entries(manifest.files ?? {}).filter(([, entry]) => entry.remoteUnavailable).map(([file]) => file));
  } catch {
    return new Set();
  }
}
