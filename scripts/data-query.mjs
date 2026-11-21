#!/usr/bin/env node
/** 查询 Atlas 原始元数据，只输出当前问题需要的字段，避免把 57 MB 单行 JSON 读进上下文。 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const rawPath = path.join(root, 'data/atlas-nice-cn.json');
const commandRoot = path.join(root, 'data/assets/atlas/commands');

function usage() {
  console.log(`用法：
  npm run data:query -- find <名称或 ID>
  npm run data:query -- servant <ID>
  npm run data:query -- assets <ID>
  npm run data:query -- counts

所有查询追加 --json 输出机器可读结果。`);
}

function readServants() {
  if (!fs.existsSync(rawPath)) {
    throw new Error(`缺少 Atlas 原始数据：${rawPath}；先运行 npm run resources:update`);
  }
  return JSON.parse(fs.readFileSync(rawPath, 'utf8'));
}

function servantSummary(servant) {
  return {
    id: servant.id,
    name: servant.name,
    originalName: servant.originalName,
    collectionNo: servant.collectionNo,
    className: servant.className,
    type: servant.type,
  };
}

function localCommandPath(id, stage, kind = 'ascension') {
  const name = kind === 'costume' ? `costume-${stage}.png` : `${stage}.png`;
  return `data/assets/atlas/commands/${id}/${name}`;
}

function preferLocal(primary, fallback) {
  if (fs.existsSync(path.join(root, primary))) return primary;
  return fallback ?? primary;
}

function assetRows(servant) {
  const assets = servant.extraAssets ?? {};
  const rows = [];
  const add = (family, key, url, local) => {
    if (!url) return;
    rows.push({ family, key, url, local, downloaded: fs.existsSync(path.join(root, local)) });
  };
  for (const [stage, url] of Object.entries(assets.faces?.ascension ?? {})) {
    const local = `data/assets/atlas/faces/${servant.id}-${stage}.png`;
    const fallback = stage === '1' ? `data/assets/atlas/faces/${servant.id}.png` : null;
    add('faces.ascension', stage, url, preferLocal(local, fallback));
  }
  for (const [stage, url] of Object.entries(assets.charaGraph?.ascension ?? {})) {
    const local = `data/assets/atlas/cards/${servant.id}-${stage}.png`;
    const fallback = stage === '1' ? `data/assets/atlas/cards/${servant.id}.png` : null;
    add('charaGraph.ascension', stage, url, preferLocal(local, fallback));
  }
  for (const [stage, url] of Object.entries(assets.commands?.ascension ?? {})) {
    add('commands.ascension', stage, url, localCommandPath(servant.id, stage));
  }
  for (const [costume, url] of Object.entries(assets.commands?.costume ?? {})) {
    add('commands.costume', costume, url, localCommandPath(servant.id, costume, 'costume'));
  }
  return rows;
}

function printServant(servant) {
  const summary = servantSummary(servant);
  console.log(`${summary.id}  ${summary.name}  #${summary.collectionNo}  ${summary.className ?? ''}`);
  if (summary.originalName && summary.originalName !== summary.name) console.log(`原名：${summary.originalName}`);
}

function counts(servants) {
  const families = {};
  let commandServants = 0;
  for (const servant of servants) {
    const rows = assetRows(servant);
    if (rows.some((row) => row.family.startsWith('commands.'))) commandServants++;
    for (const row of rows) families[row.family] = (families[row.family] ?? 0) + 1;
  }
  let downloadedCommands = 0;
  if (fs.existsSync(commandRoot)) {
    for (const directory of fs.readdirSync(commandRoot)) {
      const target = path.join(commandRoot, directory);
      if (fs.statSync(target).isDirectory()) downloadedCommands += fs.readdirSync(target).filter((name) => name.endsWith('.png')).length;
    }
  }
  return {
    servants: servants.length,
    commandServants,
    downloadedCommandFiles: downloadedCommands,
    assetFamilies: Object.fromEntries(Object.entries(families).sort(([left], [right]) => left.localeCompare(right))),
  };
}

function main(argv) {
  const json = argv.includes('--json');
  const args = argv.filter((value) => value !== '--json');
  const [action, value] = args;
  if (!action) {
    usage();
    return 1;
  }
  if (action !== 'counts' && (value === undefined || value === '')) {
    usage();
    return 1;
  }
  const servants = readServants();
  if (action === 'counts') {
    const result = counts(servants);
    if (json) {
      console.log(JSON.stringify(result, null, 2));
    } else {
      console.log(`从者 ${result.servants} 名；有指令卡原图 ${result.commandServants} 名；本地指令卡文件 ${result.downloadedCommandFiles}`);
      for (const [family, count] of Object.entries(result.assetFamilies)) console.log(`  ${family}: ${count}`);
    }
    return 0;
  }
  const matches = action === 'find'
    ? servants.filter((servant) => String(servant.id).includes(value ?? '')
        || (servant.name ?? '').includes(value ?? '')
        || (servant.originalName ?? '').includes(value ?? ''))
    : servants.filter((servant) => String(servant.id) === String(value));
  if (!matches.length) {
    console.error(`未找到从者：${value ?? '(缺少参数)'}`);
    return 2;
  }
  if (action === 'find') {
    const result = matches.slice(0, 20).map(servantSummary);
    if (json) console.log(JSON.stringify(result, null, 2));
    else result.forEach((servant) => console.log(`${servant.id}  ${servant.name}  #${servant.collectionNo}  ${servant.className ?? ''}`));
    return 0;
  }
  if (action === 'servant') {
    if (json) console.log(JSON.stringify(servantSummary(matches[0]), null, 2));
    else printServant(matches[0]);
    return 0;
  }
  if (action === 'assets') {
    const rows = assetRows(matches[0]);
    if (json) console.log(JSON.stringify(rows, null, 2));
    else {
      printServant(matches[0]);
      for (const row of rows) {
        console.log(`  ${row.family.padEnd(20)} ${row.key.padEnd(8)} ${row.downloaded ? '已下载' : '未下载'}  ${row.local}`);
      }
    }
    return 0;
  }
  usage();
  return 1;
}

try {
  process.exitCode = main(process.argv.slice(2));
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
