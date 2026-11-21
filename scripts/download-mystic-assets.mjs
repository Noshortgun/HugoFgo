#!/usr/bin/env node
/**
 * 下载御主礼装（魔术礼装）数据与素材：立绘、道具图标、技能图标。
 * 输出到 data/assets/atlas/mystic/ 与 data/assets/atlas/skills/。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { resourcePaths } from './resource-paths.mjs';
import { loadRemoteUnavailable } from './resource-utils.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const paths = resourcePaths(root);
const { region } = paths;
const raw = JSON.parse(fs.readFileSync(paths.absolute(`data/${region}/atlas-mystic-cn.json`), 'utf8'));
const outRoot = paths.regionFile('assets/atlas/mystic');
for (const dir of ['items', 'figures']) fs.mkdirSync(path.join(outRoot, dir), { recursive: true });
const skillDir = paths.regionFile('assets/atlas/skills');
fs.mkdirSync(skillDir, { recursive: true });

const jobs = [];
for (const code of raw) {
  const assets = code.extraAssets ?? {};
  if (assets.item?.female) jobs.push({ url: assets.item.female, file: path.join(outRoot, 'items', `female-${code.id}.png`) });
  if (assets.item?.male) jobs.push({ url: assets.item.male, file: path.join(outRoot, 'items', `male-${code.id}.png`) });
  if (assets.masterFigure?.female) jobs.push({ url: assets.masterFigure.female, file: path.join(outRoot, 'figures', `female-${code.id}.png`) });
  if (assets.masterFigure?.male) jobs.push({ url: assets.masterFigure.male, file: path.join(outRoot, 'figures', `male-${code.id}.png`) });
  for (const skill of code.skills ?? []) {
    if (!skill.icon) continue;
    const base = path.basename(new URL(skill.icon).pathname);
    jobs.push({ url: skill.icon, file: path.join(skillDir, base) });
  }
}

const remoteUnavailable = loadRemoteUnavailable(root, region);
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
      await new Promise((resolve) => setTimeout(resolve, 400 * attempt));
      return download(job, attempt + 1);
    }
    return `fail:${error.message}`;
  }
}

const concurrency = 6;
let next = 0;
const failures = [];
await Promise.all(Array.from({ length: concurrency }, async () => {
  while (next < jobs.length) {
    const job = jobs[next++];
    const result = await download(job);
    if (result.startsWith('fail')) failures.push(`${path.basename(job.file)} ${result}`);
  }
}));

function skillTarget(skill) {
  const func = skill.functions?.[0];
  const target = func?.funcTargetType;
  const team = func?.funcTargetTeam ?? '';
  // ptOne 是单体：对己方为 ally，对敌方为 enemy；playerAndEnemy 按己方单体处理。
  if (target === 'ptOne') return team.includes('player') ? 'ally' : 'enemy';
  // Order Change 由 orderChangeSkill 单独处理，UI 不走普通目标选择。
  if (target === 'ptselectOneSub') return 'ally';
  return ({ self: 'self', ptOther: 'ally', ptAll: 'allAllies', ptOtherAll: 'allAllies', enemy: 'enemy', enemyAll: 'allEnemies' })[target] ?? 'allAllies';
}

const orderChangePattern = /order\s*change|交换/i;
const index = raw.map((code) => {
  const skills = (code.skills ?? []).map((skill) => ({
    num: skill.num,
    name: skill.name,
    detail: skill.detail ?? '',
    icon: skill.icon ? paths.webPath(`assets/atlas/skills/${path.basename(new URL(skill.icon).pathname)}`) : null,
    target: skillTarget(skill),
  }));
  const orderChangeSkill = skills.findIndex((skill) => orderChangePattern.test(skill.name) || orderChangePattern.test(skill.detail));
  const has = (relative) => fs.existsSync(paths.regionFile(relative));
  const femaleItem = paths.webPath(`assets/atlas/mystic/items/female-${code.id}.png`);
  const maleItem = paths.webPath(`assets/atlas/mystic/items/male-${code.id}.png`);
  const femaleFigure = paths.webPath(`assets/atlas/mystic/figures/female-${code.id}.png`);
  const maleFigure = paths.webPath(`assets/atlas/mystic/figures/male-${code.id}.png`);
  return {
    id: code.id,
    name: code.name,
    shortName: code.shortName,
    item: has(femaleItem) ? femaleItem : null,
    maleItem: has(maleItem) ? maleItem : null,
    figure: has(femaleFigure) ? femaleFigure : null,
    maleFigure: has(maleFigure) ? maleFigure : null,
    skills,
    ...(orderChangeSkill >= 0 ? { orderChangeSkill } : {}),
  };
});

fs.writeFileSync(path.join(outRoot, 'index.json'), JSON.stringify({ mysticCodes: index }));
console.log(`完成：${jobs.length} 个资源，失败 ${failures.length} 个`);
if (failures.length) console.log(failures.slice(0, 10).join('\n'));
console.log(`礼装 ${index.length} 套，带换人 ${index.filter((code) => code.orderChangeSkill !== undefined).length} 套`);
