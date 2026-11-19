#!/usr/bin/env node
/** 下载概念礼装（CE）数据与缩略图。输出 data/equips*.json 与 data/assets/atlas/ce/。 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadRemoteUnavailable } from './resource-utils.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const raw = JSON.parse(fs.readFileSync(path.join(root, 'data/atlas-equip-cn.json'), 'utf8'));
const outDir = path.join(root, 'data/assets/atlas/ce');
fs.mkdirSync(outDir, { recursive: true });

const jobs = [];
const entries = [];
for (const equip of raw) {
  if (equip.type !== 'servantEquip') continue;
  const url = equip.extraAssets?.equipFace?.equip?.[String(equip.id)];
  const icon = url ? `/data/assets/atlas/ce/${equip.id}.png` : null;
  const bondBonus = equip.rarity === 5 && (equip.skills ?? []).some((skill) => /牵绊|羁绊/.test(skill.detail ?? ''));
  const defaultCommon = bondBonus || equip.name === '万华镜' || equip.name === '黑之圣杯';
  entries.push({ id: equip.id, name: equip.name, rarity: equip.rarity, icon, ...(defaultCommon ? { commonDefault: true } : {}) });
  if (url) jobs.push({ url, file: path.join(outDir, `${equip.id}.png`) });
}

const remoteUnavailable = loadRemoteUnavailable(root);
async function download(job, attempt = 1) {
  const relative = path.relative(root, job.file).split(path.sep).join('/');
  if (remoteUnavailable.has(relative)) return 'remote-unavailable';
  if (fs.existsSync(job.file) && fs.statSync(job.file).size > 0) return 'skip';
  try {
    const res = await fetch(job.url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    fs.writeFileSync(job.file, Buffer.from(await res.arrayBuffer()));
    return 'ok';
  } catch (error) {
    if (attempt < 3) {
      await new Promise((resolve) => setTimeout(resolve, 300 * attempt));
      return download(job, attempt + 1);
    }
    return `fail:${error.message}`;
  }
}

const concurrency = 10;
let next = 0;
let done = 0;
const failures = [];
await Promise.all(Array.from({ length: concurrency }, async () => {
  while (next < jobs.length) {
    const job = jobs[next++];
    const result = await download(job);
    if (result.startsWith('fail')) failures.push(`${path.basename(job.file)} ${result}`);
    if (++done % 100 === 0) console.log(`${done}/${jobs.length}`);
  }
}));

const available = entries.map((entry) => (entry.icon && fs.existsSync(path.join(root, entry.icon)) ? entry : { ...entry, icon: null }));
fs.writeFileSync(path.join(root, 'data/equips.json'), JSON.stringify(available));
fs.writeFileSync(path.join(root, 'data/equips-by-id.json'), JSON.stringify(Object.fromEntries(available.map((entry) => [entry.id, entry]))));
console.log(`完成：${jobs.length} 个图标，失败 ${failures.length} 个`);
if (failures.length) console.log(failures.slice(0, 10).join('\n'));
console.log(`礼装 ${available.length} 个，有效图标 ${available.filter((entry) => entry.icon).length} 个`);
