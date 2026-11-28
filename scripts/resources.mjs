#!/usr/bin/env node
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { assetCategoryGaps, buildAssetJobs, countAssetCategories } from './asset-jobs.mjs';
import { migrateLegacyData } from './resource-migrate.mjs';
import { metadataSource, readJsonOr } from './resource-utils.mjs';
import { parseRegion, resourcePaths } from './resource-paths.mjs';
import { servantSkillTarget } from './skill-target.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const paths = resourcePaths(root);
const { region, absolute, relative } = paths;
const manifestPath = absolute(`data/${region}/resource-manifest.json`);
const api = 'https://api.atlasacademy.io';
const infoUrl = `${api}/info`;
const rawDefinitions = [
  { key: 'servants', file: `data/${region}/atlas-nice-cn.json`, url: `${api}/nice/${region}/servant/search` },
  { key: 'basic', file: `data/${region}/atlas-basic-cn.json`, url: `${api}/basic/${region}/servant/search` },
  { key: 'equips', file: `data/${region}/atlas-equip-cn.json`, url: 'https://github.com/AtlasAcademy/chaldea-data/tree/main/dist' },
  { key: 'mystics', file: `data/${region}/atlas-mystic-cn.json`, url: `${api}/nice/${region}/MC/{id}` },
];
const generatedFiles = [
  `data/${region}/servants-by-id.json`,
  `data/${region}/skill-effects-by-svt.json`,
  `data/${region}/equips.json`,
  `data/${region}/equips-by-id.json`,
  `data/${region}/assets/atlas/index.json`,
  `data/${region}/assets/atlas/mystic/index.json`,
];

const readJson = (file) => JSON.parse(fs.readFileSync(absolute(file), 'utf8'));
const sha256 = (data) => crypto.createHash('sha256').update(data).digest('hex');
const hashFile = (file) => sha256(fs.readFileSync(file));
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function readManifest() {
  return fs.existsSync(manifestPath) ? readJson(relative(manifestPath)) : null;
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
  const current = fs.existsSync(absolute(`data/${region}/atlas-mystic-cn.json`)) ? readJson(`data/${region}/atlas-mystic-cn.json`) : [];
  // 日常 check 不探测 ID；只有版本变化时才按现有编号规则检查新御主礼装。
  const ids = [...new Set([1, ...Array.from({ length: 50 }, (_, index) => (index + 1) * 10), ...current.map((code) => code.id)])];
  const result = [];
  let next = 0;
  await Promise.all(Array.from({ length: 6 }, async () => {
    while (next < ids.length) {
      const id = ids[next++];
      const response = await fetch(`${api}/nice/${region}/MC/${id}`);
      if (response.status === 404) continue;
      if (!response.ok) throw new Error(`御主礼装 ${id}：HTTP ${response.status}`);
      result.push(await response.json());
    }
  }));
  return result.sort((left, right) => left.id - right.id);
}

async function fetchEquipData() {
  const current = readJsonOr(absolute(`data/${region}/atlas-equip-cn.json`), []);
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
      const response = await fetch(`${api}/nice/${region}/equip/${id}`);
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
  const local = readJsonOr(absolute(`data/${region}/atlas-basic-cn.json`), []);
  const classes = [...new Set([...catalogParts.flat(), ...local].map((servant) => servant.className).filter(Boolean))];
  const groups = await Promise.all(classes.map((className) => fetchJson(`${api}/${kind}/${region}/servant/search?className=${className}&excludeCollectionNo=0`)));
  const found = new Map(groups.flat().map((servant) => [servant.id, servant]));
  const missing = local.filter((servant) => !found.has(servant.id));
  await Promise.all(missing.map(async (servant) => found.set(servant.id, await fetchJson(`${api}/${kind}/${region}/servant/${servant.id}`))));
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
    servants: readJsonOr(absolute(`data/${region}/atlas-nice-cn.json`), []),
    basic: readJsonOr(absolute(`data/${region}/atlas-basic-cn.json`), []),
    equips: readJsonOr(absolute(`data/${region}/atlas-equip-cn.json`), []),
    mystics: readJsonOr(absolute(`data/${region}/atlas-mystic-cn.json`), []),
  };
}

function plannedAssetJobs(raw) {
  return buildAssetJobs(raw).map((job) => ({ ...job, file: absolute(job.file) }));
}

function regenerateIndexes() {
  for (const script of ['download-servant-assets.mjs', 'download-equip-assets.mjs', 'download-mystic-assets.mjs']) {
    const result = spawnSync(process.execPath, [path.join(root, 'scripts', script), '--region', region], {
      cwd: root,
      env: { ...process.env, HUGOFGO_DATA_ROOT: paths.dataRoot },
      stdio: 'inherit',
    });
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
    region,
    source: api,
    version: info[region],
    updatedAt: new Date().toISOString(),
    counts: {
      servants: raw.servants.length,
      equips: raw.equips.length,
      mysticCodes: raw.mystics.length,
      files: Object.values(files).filter((entry) => entry.sha256).length,
      remoteUnavailable: Object.values(files).filter((entry) => entry.remoteUnavailable).length,
    },
    assetCategories: countAssetCategories(jobs),
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
    console.log(`远端 ${region}：${info[region].hash}；本地尚未建立资源清单，请运行 npm run resources:update -- --region ${region}`);
    return;
  }
  const remote = info[region];
  const latest = manifest.version.hash === remote.hash;
  console.log(`远端 ${region}：${remote.hash}`);
  console.log(latest ? `已是最新：${remote.hash}` : `发现更新：${manifest.version.hash} → ${remote.hash}`);
  console.log(`本地清单：${manifest.counts.files} 个文件；远端版本时间：${new Date(remote.timestamp * 1000).toISOString()}`);
}

async function verify() {
  const manifest = readManifest();
  if (!manifest) throw new Error(`尚未建立 ${region} 资源清单，请先运行 npm run resources:update -- --region ${region}`);
  const invalid = verifyManifest(manifest);
  if (invalid.length) {
    console.error(`校验失败：${invalid.length} 个文件`);
    console.error(invalid.slice(0, 20).map((item) => `${item.reason}：${item.file}`).join('\n'));
    process.exitCode = 1;
    return;
  }
  console.log(`校验通过：${manifest.counts.files} 个文件的 SHA-256 均与清单一致`);
}

function contract() {
  const manifest = readManifest();
  if (!manifest) throw new Error('尚未建立资源清单，请先运行 npm run resources:update');
  const gaps = assetCategoryGaps(manifest);
  if (gaps.length) {
    console.error(`资源类别合约失败：缺少非空类别 ${gaps.join(', ')}`);
    console.error('修改资源类别后先运行 npm run resources:update，并提交更新后的资源清单。');
    process.exitCode = 1;
    return;
  }
  const categories = Object.entries(manifest.assetCategories)
    .map(([name, count]) => `${name}=${count}`).join(', ');
  console.log(`资源类别合约通过：${categories}`);
}

async function update() {
  const previous = readManifest();
  for (const [file, entry] of Object.entries(previous?.files ?? {})) {
    if (entry.remoteUnavailable) unavailableFiles.add(file);
  }
  const oldJobs = previous ? [] : plannedAssetJobs(currentRawData());
  const oldUrls = new Map(oldJobs.map((job) => [relative(job.file), job.url]));
  const invalid = previous ? verifyManifest(previous) : [];
  const rawInvalid = invalid.some((item) => rawDefinitions.some((definition) => definition.file === item.file));
  const info = await fetchJson(infoUrl);
  const forceMetadata = process.argv.includes('--refresh-metadata');
  const changed = forceMetadata || !previous || previous.version.hash !== info[region].hash || rawInvalid;
  const raw = changed ? await refreshRawData() : currentRawData();
  if (!previous) console.log('首次建立清单：URL 未变化的已有文件会直接纳入，后续可检测本地损坏。');

  const jobs = plannedAssetJobs(raw);
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
  const jobs = buildAssetJobs({
    servants: [{
      id: 1,
      extraAssets: {
        faces: {
          ascension: { 1: 'https://example.test/face.png' },
          costume: { 101: 'https://example.test/costume-face.png' },
        },
        commands: { ascension: { 1: 'https://example.test/command.png', 2: 'https://example.test/command2.png' } },
      },
      skills: [{ icon: 'https://example.test/skill.png' }, { icon: 'https://example.test/skill.png' }],
    }],
    equips: [],
    mystics: [],
  });
  const categories = countAssetCategories(jobs);
  assert.equal(categories['faces.ascension'], 1);
  assert.equal(categories['faces.costume'], 1);
  assert.ok(jobs.some((job) => job.category === 'faces.costume'
    && job.file === 'data/assets/atlas/faces/1-costume-101.png'
    && job.url === 'https://example.test/costume-face.png'));
  assert.equal(categories['commands.ascension'], 2);
  assert.equal(categories['skills.servant'], 1);
  assert.equal(categories.classes, 15);
  assert.deepEqual(assetCategoryGaps(readManifest()), []);
  assert.deepEqual(assetCategoryGaps({ assetCategories: { 'commands.ascension': 0 } }),
    ['faces.ascension', 'commands.ascension', 'ce', 'skills.servant']);
  assert.equal(sha256('hugofgo'), '2212e77a8b58479809a0e21d448299900cd9eb570458f15b07510cecb56c4ec8');
  assert.equal(verifyManifest({ files: { 'data/name-overrides.json': { sha256: hashFile(absolute('data/name-overrides.json')) } } }).length, 0);
  assert.equal(verifyManifest({ files: { 'data/name-overrides.json': { sha256: 'broken' } } }).length, 1);
  assert.equal(verifyManifest({ files: { 'data/not-published.png': { remoteUnavailable: true } } }).length, 0);
  assert.deepEqual(readJsonOr(path.join(root, '.scratch', 'missing-raw-metadata.json'), []), []);
  const seedRoot = path.join(root, '.scratch', 'resource-bootstrap-test');
  fs.rmSync(seedRoot, { recursive: true, force: true });
  fs.mkdirSync(path.join(seedRoot, 'seed', 'CN'), { recursive: true });
  fs.writeFileSync(path.join(seedRoot, 'seed', 'CN', 'servants.json'), '[]');
  assert.equal(metadataSource(seedRoot, 'CN', 'servants.json', {}), path.join(seedRoot, 'seed', 'CN', 'servants.json'));
  fs.mkdirSync(path.join(seedRoot, 'user-data', 'CN'), { recursive: true });
  fs.writeFileSync(path.join(seedRoot, 'user-data', 'CN', 'servants.json'), '[]');
  assert.equal(metadataSource(seedRoot, 'CN', 'servants.json', { HUGOFGO_DATA_ROOT: path.join(seedRoot, 'user-data') }), path.join(seedRoot, 'user-data', 'CN', 'servants.json'));
  fs.rmSync(seedRoot, { recursive: true, force: true });
  assert.equal(parseRegion(['verify']), 'CN');
  assert.equal(parseRegion(['verify', '--region', 'JP']), 'JP');
  assert.throws(() => parseRegion(['verify', '--region', '../CN']));
  const testRoot = path.join(root, '.scratch', 'resource-paths-test');
  const testPaths = resourcePaths(root, { argv: ['verify', '--region', 'TW'], env: { HUGOFGO_DATA_ROOT: testRoot } });
  assert.equal(testPaths.regionRoot, path.join(testRoot, 'TW'));
  assert.equal(testPaths.absolute('data/assets/atlas/index.json'), path.join(testRoot, 'TW', 'assets/atlas/index.json'));
  assert.equal(testPaths.webPath('assets/atlas/index.json'), '/data/TW/assets/atlas/index.json');
  const targetOf = (...funcTargetTypes) => servantSkillTarget({ functions: funcTargetTypes.map((funcTargetType) => ({ funcTargetType, funcTargetTeam: 'player' })) });
  assert.equal(targetOf('ptOther', 'self'), 'allAllies', '希耶尔三技能作用于除自身外的己方全体，不应要求选择目标');
  assert.equal(targetOf('self', 'ptOne'), 'ally', '两仪式三技能后续效果要求选择己方单体');
  assert.equal(targetOf('self', 'ptOne'), 'ally', '帕尔瓦蒂三技能后续效果要求选择己方单体');
  assert.equal(targetOf('self', 'enemy'), 'enemy', '忒修斯三技能后续效果要求选择敌方单体');
  assert.equal(targetOf('ptAll', 'ptOneOther'), 'ally', '奥德修斯一技能的 ptOneOther 需要先选择己方单体');
  assert.equal(targetOf('ptAll', 'ptRandom'), 'allAllies', '难敌一技能的 ptRandom 是随机效果，不要求选择目标');
  assert.equal(targetOf('self', 'ptFull'), 'self', '大和武尊一技能的 ptFull 是全体效果，不要求选择目标');
  assert.equal(targetOf('self', 'fieldOther'), 'self', '美游三技能的 fieldOther 是场地效果，不要求选择目标');
  assert.equal(targetOf('self', 'commandTypeSelfTreasureDevice'), 'self', '卫宫三技能只追加宝具卡色选择，不要求选择目标');
  console.log('资源管理器自检通过');
} else if (command === 'check') {
  migrateLegacyData(paths);
  await check();
} else if (command === 'update') {
  migrateLegacyData(paths);
  await update();
} else if (command === 'verify') {
  migrateLegacyData(paths);
  await verify();
}
else if (command === 'contract') contract();
else throw new Error(`未知命令：${command}；可用 check / update / verify / contract / self-test`);
