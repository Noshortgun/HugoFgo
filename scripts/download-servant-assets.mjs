#!/usr/bin/env node
/**
 * 下载从者卡面、头像、指令卡原图与技能图标（Atlas Academy CN 静态资源）。
 * 输出到 data/assets/atlas/，并生成 index.json 供 GUI 使用。
 * 已存在的文件会跳过，可重复运行。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ASCENSION_CARD_IDS, servantAssetJobs } from './asset-jobs.mjs';
import { loadRemoteUnavailable } from './resource-utils.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const projectRoot = root;
const raw = JSON.parse(fs.readFileSync(path.join(root, 'data/atlas-nice-cn.json'), 'utf8'));
const basic = JSON.parse(fs.readFileSync(path.join(root, 'data/atlas-basic-cn.json'), 'utf8'));
const legacyLabels = new Map(JSON.parse(fs.readFileSync(path.join(root, 'data/servants.json'), 'utf8')).map((info) => [info.id, info.classLabel]));
const standardLabels = { saber: 'Saber', archer: 'Archer', lancer: 'Lancer', rider: 'Rider', caster: 'Caster', assassin: 'Assassin', berserker: 'Berserker', ruler: 'Ruler', avenger: 'Avenger', moonCancer: 'Moon Cancer', alterEgo: 'Alter Ego', foreigner: 'Foreigner', pretender: 'Pretender', shielder: 'Shielder' };
const slim = Object.fromEntries(basic.map((info) => [info.id, { ...info, classLabel: /beast/i.test(info.className) ? 'Beast' : legacyLabels.get(info.id) ?? standardLabels[info.className] ?? info.className }]));
fs.writeFileSync(path.join(root, 'data/servants-by-id.json'), JSON.stringify(slim));
const overrides = fs.existsSync(path.join(root, 'data/name-overrides.json'))
  ? JSON.parse(fs.readFileSync(path.join(root, 'data/name-overrides.json'), 'utf8'))
  : {};
const outRoot = path.join(root, 'data/assets/atlas');
for (const dir of ['faces', 'cards', 'skills']) fs.mkdirSync(path.join(outRoot, dir), { recursive: true });

const concurrency = 8;
const jobs = [];

for (const servant of raw) {
  const info = slim[servant.id];
  if (!info) continue;
  for (const job of servantAssetJobs(servant)) {
    jobs.push({ ...job, file: path.join(root, job.file) });
  }
}

const remoteUnavailable = loadRemoteUnavailable(root);
async function download(job, attempt = 1) {
  const relative = path.relative(root, job.file).split(path.sep).join('/');
  if (remoteUnavailable.has(relative)) return 'remote-unavailable';
  if (fs.existsSync(job.file) && fs.statSync(job.file).size > 0) return 'skip';
  try {
    const res = await fetch(job.url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const buffer = Buffer.from(await res.arrayBuffer());
    fs.mkdirSync(path.dirname(job.file), { recursive: true });
    fs.writeFileSync(job.file, buffer);
    return 'ok';
  } catch (error) {
    if (attempt < 3) {
      await new Promise((resolve) => setTimeout(resolve, 400 * attempt));
      return download(job, attempt + 1);
    }
    return `fail:${error.message}`;
  }
}

let next = 0;
let done = 0;
const failures = [];
await Promise.all(Array.from({ length: concurrency }, async () => {
  while (next < jobs.length) {
    const job = jobs[next++];
    const result = await download(job);
    if (result.startsWith('fail')) failures.push(`${path.basename(job.file)} ${result}`);
    if (++done % 50 === 0) console.log(`${done}/${jobs.length}`);
  }
}));

function skillTarget(skill) {
  const func = skill.functions?.[0];
  const target = func?.funcTargetType;
  const team = func?.funcTargetTeam ?? '';
  // ptOne 是单体：对己方为 ally，对敌方为 enemy；playerAndEnemy 按己方单体处理。
  if (target === 'ptOne') return team.includes('player') ? 'ally' : 'enemy';
  return ({ self: 'self', ptOther: 'ally', ptAll: 'allAllies', ptOtherAll: 'allAllies', enemy: 'enemy', enemyAll: 'allEnemies' })[target] ?? 'allAllies';
}
function secondarySelection(skill) {
  const select = skill.script?.SelectAddInfo?.[0];
  if (select) {
    return {
      type: 'selectAddInfo',
      label: select.title || '技能二次选择',
      options: (select.btn ?? []).map((button, index) => ({ value: index, label: button.name || `选项 ${index + 1}`, ...(button.imageUrl ? { imageUrl: button.imageUrl } : {}) })),
    };
  }
  if (skill.functions?.some((func) => func.buffs?.some((buff) => buff.type === 'tdTypeChange'))) {
    return {
      type: 'noblePhantasmCardType',
      label: '选择宝具卡色',
      options: [{ value: 'arts', label: '蓝卡宝具' }, { value: 'buster', label: '红卡宝具' }, { value: 'quick', label: '绿卡宝具' }],
    };
  }
  return undefined;
}

const index = raw.map((servant) => {
  const info = slim[servant.id];
  if (!info) return null;
  const faceUrl = servant.extraAssets?.faces?.ascension?.['1'] ?? servant.extraAssets?.faces?.ascension?.['0'];
  const cardUrl = servant.extraAssets?.charaGraph?.ascension?.['1'] ?? servant.extraAssets?.charaGraph?.ascension?.['0'];
  const faceFile = `/data/assets/atlas/faces/${servant.id}.png`;
  const cardFile = `/data/assets/atlas/cards/${servant.id}.png`;
  const ascensionAssets = ASCENSION_CARD_IDS.has(servant.id) ? Object.fromEntries([1, 2, 3, 4].map((stage) => [stage, {
    face: fs.existsSync(path.join(outRoot, 'faces', `${servant.id}-${stage}.png`)) ? `/data/assets/atlas/faces/${servant.id}-${stage}.png` : null,
    card: fs.existsSync(path.join(outRoot, 'cards', `${servant.id}-${stage}.png`)) ? `/data/assets/atlas/cards/${servant.id}-${stage}.png` : null,
  }])) : undefined;
  if (servant.id === 800100) {
    ascensionAssets[4] = {
      face: fs.existsSync(path.join(outRoot, 'faces', '800100-paladin.png')) ? '/data/assets/atlas/faces/800100-paladin.png' : ascensionAssets[4].face,
      card: fs.existsSync(path.join(outRoot, 'cards', '800100-paladin.png')) ? '/data/assets/atlas/cards/800100-paladin.png' : ascensionAssets[4].card,
    };
    ascensionAssets[3] = {
      face: fs.existsSync(path.join(outRoot, 'faces', '800100-ortenaus.png')) ? '/data/assets/atlas/faces/800100-ortenaus.png' : ascensionAssets[3].face,
      card: fs.existsSync(path.join(outRoot, 'cards', '800100-ortenaus.png')) ? '/data/assets/atlas/cards/800100-ortenaus.png' : ascensionAssets[3].card,
    };
  }
  return {
    id: servant.id,
    name: overrides[servant.id] ?? servant.name,
    classLabel: info.classLabel,
    type: servant.type,
    collectionNo: servant.collectionNo,
    ...(ascensionAssets ? { ascensionAssets } : {}),
    face: faceUrl && fs.existsSync(path.join(root, faceFile)) ? faceFile : null,
    card: cardUrl && fs.existsSync(path.join(root, cardFile)) ? cardFile : null,
    noblePhantasms: (servant.noblePhantasms ?? []).map((np) => ({ id: np.id, name: np.name, card: np.card, priority: np.priority, condLimitCount: np.condLimitCount })),
    skills: (servant.skills ?? []).map((skill) => ({
      num: skill.num,
      name: skill.name,
      icon: skill.icon ? `/data/assets/atlas/skills/${path.basename(new URL(skill.icon).pathname)}` : null,
      target: skillTarget(skill),
      secondarySelection: secondarySelection(skill),
      condLimitCount: skill.condLimitCount,
      condQuestId: skill.condQuestId,
      condQuestPhase: skill.condQuestPhase,
      priority: skill.priority,
    })),
  };
}).filter(Boolean);

fs.writeFileSync(path.join(outRoot, 'index.json'), JSON.stringify({ servants: index }));
const totalMB = (JSON.stringify(index).length / 1024 / 1024).toFixed(1);
console.log(`完成：${jobs.length} 个资源，失败 ${failures.length} 个`);
if (failures.length) console.log(failures.slice(0, 10).join('\n'));
console.log(`index.json 覆盖 ${index.length} 名从者（约 ${totalMB} MB 元数据）`);
