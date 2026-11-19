import fs from 'node:fs';
import path from 'node:path';

export function loadRemoteUnavailable(root) {
  try {
    const manifest = JSON.parse(fs.readFileSync(path.join(root, 'data/resource-manifest.json'), 'utf8'));
    return new Set(Object.entries(manifest.files ?? {}).filter(([, entry]) => entry.remoteUnavailable).map(([file]) => file));
  } catch {
    return new Set();
  }
}
