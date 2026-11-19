#!/usr/bin/env node
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const manifestPath = path.join(root, 'data/resource-manifest.json');
const api = 'https://api.atlasacademy.io';
const infoUrl = `${api}/info`;
const rawDefinitions = [
  { key: 'servants', file: 'data/atlas-nice-cn.json', url: `${api}/nice/CN/servant/search` },
  { key: 'basic', file: 'data/atlas-basic-cn.json', url: `${api}/basic/CN/servant/search` },
  { key: 'equips', file: 'data/atlas-equip-cn.json', url: 'https://github.com/AtlasAcademy/chaldea-data/tree/main/dist' },
  { key: 'mystics', file: 'data/atlas-mystic-cn.json', url: `${api}/nice/CN/MC/{id}` },
];
const generatedFiles = [
  'data/servants-by-id.json',
  'data/equips.json',
  'data/equips-by-id.json',
  'data/assets/atlas/index.json',
  'data/assets/atlas/mystic/index.json',
];
const classIconIds = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 23, 25, 28, 33];
const ascensionServantIds = new Set([800100, 4000100]);

const readJson = (file) => JSON.parse(fs.readFileSync(path.join(root, file), 'utf8'));
const relative = (file) => path.relative(root, file).split(path.sep).join('/');
const absolute = (file) => path.join(root, file);
const sha256 = (data) => crypto.createHash('sha256').update(data).digest('hex');
const hashFile = (file) => sha256(fs.readFileSync(file));
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function readManifest() {
  return fs.existsSync(manifestPath) ? readJson('data/resource-manifest.json') : null;
}

async function fetchBuffer(url, attempt = 1) {
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return Buffer.from(await response.arrayBuffer());
  } catch (error) {
    if (attempt < 3) {
      await wait(400 * attempt);
      return fetchBuffer(url, attempt + 1);
    }
    throw new Error(`${url}：${error.message}`);
  }
}

async function fetchJson(url) {
  return JSON.parse((await fetchBuffer(url)).toString('utf8'));
}

function atomicWrite(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = `${file}.${process.pid}.tmp`;
  try {
    fs.writeFileSync(temporary, data);
    fs.renameSync(temporary, file);
  } finally {
    if (fs.existsSync(temporary)) fs.rmSync(temporary);
  }
}

async function fetchMysticCodes() {
  const current = fs.existsSync(absolute('data/atlas-mystic-cn.json')) ? readJson('data/atlas-mystic-cn.json') : [];
  // 日常 check 不探测 ID；只有版本变化时才按现有编号规则检查新御主礼装。
  const ids = [...new Set([1, ...Array.from({ length: 50 }, (_, index) => (index + 1) * 10), ...current.map((code) => code.id)])];
  const result = [];
  let next = 0;
  await Promise.all(Array.from({ length: 6 }, async () => {
    while (next < ids.length) {
      const id = ids[next++];
      const response = await fetch(`${api}/nice/CN/MC/${id}`);
      if (response.status === 404) continue;
      if (!response.ok) throw new Error(`御主礼装 ${id}：HTTP ${response.status}`);
      result.push(await response.json());
    }
  }));
  return result.sort((left, right) => left.id - right.id);
}

async function fetchEquipData() {
  const current = readJson('data/atlas-equip-cn.json');
  const catalogParts = await Promise.all([1, 2, 3, 4].map((part) => fetchJson(`https://raw.githubusercontent.com/AtlasAcademy/chaldea-data/main/dist/craftEssences.${part}.json`)));
  const fullRefresh = process.argv.includes('--refresh-metadata');
  const known = new Set(current.map((equip) => equip.id));
  const ids = catalogParts.flat()
    .filter((equip) => equip.type === 'servantEquip' && (fullRefresh || !known.has(equip.id)))
    .map((equip) => equip.id);
  const fetched = [];
  let next = 0;
  let completed = 0;
  await Promise.all(Array.from({ length: 8 }, async () => {
    while (next < ids.length) {
      const id = ids[next++];
      const response = await fetch(`${api}/nice/CN/equip/${id}`);
      if (response.status === 404) continue;
      if (!response.ok) throw new Error(`礼装 ${id}：HTTP ${response.status}`);
      fetched.push(await response.json());
      if (++completed % 100 === 0) console.log(`礼装元数据 ${completed}/${ids.length}`);
    }
  }));
  if (fetched.length) console.log(`${fullRefresh ? '校准' : '新增'} ${fetched.length} 张礼装`);
  const refreshed = new Set(fetched.map((equip) => equip.id));
  return [...current.filter((equip) => !refreshed.has(equip.id)), ...fetched].sort((left, right) => left.id - right.id);
}

async function fetchServantData(kind) {
  const catalogParts = await Promise.all([1, 2, 3, 4].map((part) => fetchJson(`https://raw.githubusercontent.com/AtlasAcademy/chaldea-data/main/dist/servants.${part}.json`)));
  const local = readJson('data/atlas-basic-cn.json');
  const classes = [...new Set([...catalogParts.flat(), ...local].map((servant) => servant.className).filter(Boolean))];
  const groups = await Promise.all(classes.map((className) => fetchJson(`${api}/${kind}/CN/servant/search?className=${className}&excludeCollectionNo=0`)));
  const found = new Map(groups.flat().map((servant) => [servant.id, servant]));
  const missing = local.filter((servant) => !found.has(servant.id));
  await Promise.all(missing.map(async (servant) => found.set(servant.id, await fetchJson(`${api}/${kind}/CN/servant/${servant.id}`))));
  return [...found.values()].sort((left, right) => left.id - right.id);
}

async function refreshRawData() {
  const [servants, basic, equips, mystics] = await Promise.all([
    fetchServantData('nice'),
    fetchServantData('basic'),
    fetchEquipData(),
    fetchMysticCodes(),
  ]);
  const values = { servants, basic, equips, mystics };
  for (const definition of rawDefinitions) {
    const data = values[definition.key];
    if (!Array.isArray(data) || !data.length) throw new Error(`${definition.key} 返回了空数据，已保留现有资源`);
    atomicWrite(absolute(definition.file), JSON.stringify(data));
  }
  return values;
}

function currentRawData() {
  return {
    servants: readJson('data/atlas-nice-cn.json'),
    basic: readJson('data/atlas-basic-cn.json'),
    equips: readJson('data/atlas-equip-cn.json'),
    mystics: readJson('data/atlas-mystic-cn.json'),
  };
}

function buildAssetJobs(raw) {
  const jobs = new Map();
  const add = (url, file) => {
    if (url && file) jobs.set(file, { url, file: absolute(file) });
  };
  for (const servant of raw.servants) {
    add(servant.extraAssets?.faces?.ascension?.['1'] ?? servant.extraAssets?.faces?.ascension?.['0'], `data/assets/atlas/faces/${servant.id}.png`);
    add(servant.extraAssets?.charaGraph?.ascension?.['1'] ?? servant.extraAssets?.charaGraph?.ascension?.['0'], `data/assets/atlas/cards/${servant.id}.png`);
    if (ascensionServantIds.has(servant.id)) {
      for (let stage = 1; stage <= 4; stage++) {
        add(servant.extraAssets?.faces?.ascension?.[String(stage)], `data/assets/atlas/faces/${servant.id}-${stage}.png`);
        add(servant.extraAssets?.charaGraph?.ascension?.[String(stage)], `data/assets/atlas/cards/${servant.id}-${stage}.png`);
      }
    }
    if (servant.id === 800100) {
      add(servant.extraAssets?.faces?.costume?.['800200'], 'data/assets/atlas/faces/800100-paladin.png');
      add(servant.extraAssets?.charaGraph?.costume?.['800200'], 'data/assets/atlas/cards/800100-paladin.png');
      add(servant.extraAssets?.faces?.costume?.['800190'], 'data/assets/atlas/faces/800100-ortenaus.png');
      add(servant.extraAssets?.charaGraph?.costume?.['800190'], 'data/assets/atlas/cards/800100-ortenaus.png');
    }
    for (const skill of servant.skills ?? []) {
      if (skill.icon) add(skill.icon, `data/assets/atlas/skills/${path.basename(new URL(skill.icon).pathname)}`);
    }
  }
  for (const equip of raw.equips) {
    if (equip.type !== 'servantEquip') continue;
    add(equip.extraAssets?.equipFace?.equip?.[String(equip.id)], `data/assets/atlas/ce/${equip.id}.png`);
  }
  for (const code of raw.mystics) {
    const assets = code.extraAssets ?? {};
    add(assets.item?.female, `data/assets/atlas/mystic/items/female-${code.id}.png`);
    add(assets.item?.male, `data/assets/atlas/mystic/items/male-${code.id}.png`);
    add(assets.masterFigure?.female, `data/assets/atlas/mystic/figures/female-${code.id}.png`);
    add(assets.masterFigure?.male, `data/assets/atlas/mystic/figures/male-${code.id}.png`);
    for (const skill of code.skills ?? []) {
      if (skill.icon) add(skill.icon, `data/assets/atlas/skills/${path.basename(new URL(skill.icon).pathname)}`);
    }
  }
  for (const id of classIconIds) add(`${api.replace('api.', 'static.')}/CN/ClassIcons/class3_${id}.png`, `public/assets/classes/${id}.png`);
  return [...jobs.values()];
}

function regenerateIndexes() {
  for (const script of ['download-servant-assets.mjs', 'download-equip-assets.mjs', 'download-mystic-assets.mjs']) {
    const result = spawnSync(process.execPath, [path.join(root, 'scripts', script)], { cwd: root, stdio: 'inherit' });
    if (result.status !== 0) throw new Error(`${script} 执行失败`);
  }
}

const unavailableFiles = new Set();

function verifyManifest(manifest) {
  const invalid = [];
  for (const [file, entry] of Object.entries(manifest?.files ?? {})) {
    const target = absolute(file);
    const exists = fs.existsSync(target);
    if (entry.remoteUnavailable && !exists) continue;
    if (!exists) invalid.push({ file, reason: '缺失', url: entry.url });
    else if (!entry.sha256 || hashFile(target) !== entry.sha256) invalid.push({ file, reason: 'SHA-256 不匹配', url: entry.url });
  }
  return invalid;
}

function writeManifest(info, raw, jobs) {
  const entries = new Map(jobs.map((job) => [relative(job.file), job.url]));
  for (const definition of rawDefinitions) entries.set(definition.file, definition.url);
  for (const file of generatedFiles) entries.set(file, null);
  const files = {};
  for (const [file, url] of entries) {
    const target = absolute(file);
    if (!fs.existsSync(target)) {
      if (url && unavailableFiles.has(file)) files[file] = { url, remoteUnavailable: true };
      continue;
    }
    const stat = fs.statSync(target);
    files[file] = { size: stat.size, sha256: hashFile(target), ...(url ? { url } : {}) };
  }
  const manifest = {
    schemaVersion: 1,
    region: 'CN',
    source: api,
    version: info.CN,
    updatedAt: new Date().toISOString(),
    counts: {
      servants: raw.servants.length,
      equips: raw.equips.length,
      mysticCodes: raw.mystics.length,
      files: Object.values(files).filter((entry) => entry.sha256).length,
      remoteUnavailable: Object.values(files).filter((entry) => entry.remoteUnavailable).length,
    },
    files,
  };
  atomicWrite(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
  return manifest;
}

async function downloadJobs(jobs) {
  let next = 0;
  let completed = 0;
  await Promise.all(Array.from({ length: 8 }, async () => {
    while (next < jobs.length) {
      const job = jobs[next++];
      try {
        const data = await fetchBuffer(job.url);
        atomicWrite(job.file, data);
      } catch (error) {
        if (!String(error.message).includes('HTTP 404')) throw error;
        unavailableFiles.add(relative(job.file));
        console.log(`远端暂无素材，保留为空：${relative(job.file)}`);
      }
      if (++completed % 100 === 0 || completed === jobs.length) console.log(`下载 ${completed}/${jobs.length}`);
    }
  }));
}

async function check() {
  const manifest = readManifest();
  const info = await fetchJson(infoUrl);
  if (!manifest) {
    console.log(`远端 CN：${info.CN.hash}；本地尚未建立资源清单，请运行 npm run resources:update`);
    return;
  }
  const latest = manifest.version.hash === info.CN.hash;
  console.log(latest ? `已是最新：${info.CN.hash}` : `发现更新：${manifest.version.hash} → ${info.CN.hash}`);
  console.log(`本地清单：${manifest.counts.files} 个文件；远端版本时间：${new Date(info.CN.timestamp * 1000).toISOString()}`);
}

async function verify() {
  const manifest = readManifest();
  if (!manifest) throw new Error('尚未建立资源清单，请先运行 npm run resources:update');
  const invalid = verifyManifest(manifest);
  if (invalid.length) {
    console.error(`校验失败：${invalid.length} 个文件`);
    console.error(invalid.slice(0, 20).map((item) => `${item.reason}：${item.file}`).join('\n'));
    process.exitCode = 1;
    return;
  }
  console.log(`校验通过：${manifest.counts.files} 个文件的 SHA-256 均与清单一致`);
}

async function update() {
  const previous = readManifest();
  for (const [file, entry] of Object.entries(previous?.files ?? {})) {
    if (entry.remoteUnavailable) unavailableFiles.add(file);
  }
  const oldJobs = previous ? [] : buildAssetJobs(currentRawData());
  const oldUrls = new Map(oldJobs.map((job) => [relative(job.file), job.url]));
  const invalid = previous ? verifyManifest(previous) : [];
  const rawInvalid = invalid.some((item) => rawDefinitions.some((definition) => definition.file === item.file));
  const info = await fetchJson(infoUrl);
  const forceMetadata = process.argv.includes('--refresh-metadata');
  const changed = forceMetadata || !previous || previous.version.hash !== info.CN.hash || rawInvalid;
  const raw = changed ? await refreshRawData() : currentRawData();
  if (!previous) console.log('首次建立清单：URL 未变化的已有文件会直接纳入，后续可检测本地损坏。');

  const jobs = buildAssetJobs(raw);
  const jobsToDownload = [];
  for (const job of jobs) {
    const file = relative(job.file);
    const entry = previous?.files?.[file];
    const exists = fs.existsSync(job.file);
    if (entry?.url === job.url && entry.remoteUnavailable && !exists) continue;
    if (entry?.url === job.url && exists && hashFile(job.file) === entry.sha256) continue;
    if (!previous && oldUrls.get(file) === job.url && exists) continue;
    jobsToDownload.push(job);
  }
  console.log(jobsToDownload.length ? `需要增量下载 ${jobsToDownload.length} 个文件` : '素材无需下载');
  await downloadJobs(jobsToDownload);
  regenerateIndexes();
  const manifest = writeManifest(info, raw, jobs);
  const remaining = verifyManifest(manifest);
  if (remaining.length) throw new Error(`更新后仍有 ${remaining.length} 个文件未通过校验`);
  console.log(`资源已更新并通过校验：${manifest.version.hash}，${manifest.counts.files} 个文件`);
}

const command = process.argv[2] ?? 'check';
if (command === 'self-test') {
  const jobs = buildAssetJobs({ servants: [{ id: 1, extraAssets: {}, skills: [{ icon: 'https://example.test/skill.png' }, { icon: 'https://example.test/skill.png' }] }], equips: [], mystics: [] });
  assert.equal(jobs.length, 1);
  assert.equal(sha256('hugofgo'), 'a50054b94984a3926e94175ad5b485917ec7913cc2977cf6bf362576576e6ab5');
  assert.equal(verifyManifest({ files: { 'data/name-overrides.json': { sha256: hashFile(absolute('data/name-overrides.json')) } } }).length, 0);
  assert.equal(verifyManifest({ files: { 'data/name-overrides.json': { sha256: 'broken' } } }).length, 1);
  assert.equal(verifyManifest({ files: { 'data/not-published.png': { remoteUnavailable: true } } }).length, 0);
  console.log('资源管理器自检通过');
} else if (command === 'check') await check();
else if (command === 'update') await update();
else if (command === 'verify') await verify();
else throw new Error(`未知命令：${command}；可用 check / update / verify`);
