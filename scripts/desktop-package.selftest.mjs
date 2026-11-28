import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { validateDesktopConfig } from './desktop-package.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const config = JSON.parse(fs.readFileSync(path.join(root, 'src-tauri/tauri.conf.json'), 'utf8'));
const resources = validateDesktopConfig(config, root);
const destinations = resources.map(([, destination]) => destination);

assert.equal(config.build.beforeDevCommand, 'npm run dev', 'Tauri 开发预览必须先启动 Vite 前端');

for (const required of [
  'scripts/resources.mjs',
  'scripts/download-servant-assets.mjs',
  'scripts/download-equip-assets.mjs',
  'scripts/download-mystic-assets.mjs',
  'executor/',
  'schemas/job-v1.schema.json',
  'cv/requirements.txt',
  'seed/CN/servants.json',
  'seed/CN/name-overrides.json',
  'runtime/node/',
  'resources/scrcpy/',
]) {
  assert(destinations.includes(required), `安装包缺少 ${required}`);
}
assert(!destinations.some((destination) => /^data\//.test(destination)), '图库或原始元数据不得进入安装包');
assert(!destinations.some((destination) => /^(dist|public|captures|logs)\//.test(destination)), '开发产物不得进入安装包');

const bundledGallery = structuredClone(config);
bundledGallery.bundle.resources['../data/CN/assets/'] = 'data/CN/assets/';
assert.throws(() => validateDesktopConfig(bundledGallery, root), /图库/);

console.log('桌面打包配置自检通过');
