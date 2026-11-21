import fs from 'node:fs';
import path from 'node:path';

export function loadRemoteUnavailable(root, region = 'CN') {
  const dataRoot = path.resolve(process.env.HUGOFGO_DATA_ROOT || path.join(root, 'data'));
  try {
    const manifest = JSON.parse(fs.readFileSync(path.join(dataRoot, region, 'resource-manifest.json'), 'utf8'));
    return new Set(Object.entries(manifest.files ?? {}).filter(([, entry]) => entry.remoteUnavailable).map(([file]) => file));
  } catch {
    return new Set();
  }
}
