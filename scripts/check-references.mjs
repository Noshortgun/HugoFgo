#!/usr/bin/env node
/** 检查公开文档链接、模板文件与模板来源；本机唯一来源需用 --include-local 强制检查。 */
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const defaultRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function markdownFiles(root) {
  const files = ['README.md', 'HANDOFF.md', 'GLOSSARY.md', 'AGENTS.md']
    .filter((file) => fs.existsSync(path.join(root, file)));
  for (const directory of ['docs', 'scripts']) {
    const base = path.join(root, directory);
    if (!fs.existsSync(base)) continue;
    for (const entry of fs.readdirSync(base, { withFileTypes: true, recursive: true })) {
      if (!entry.isFile() || !entry.name.endsWith('.md')) continue;
      const parent = entry.parentPath ?? entry.path;
      files.push(path.relative(root, path.join(parent, entry.name)));
    }
  }
  return [...new Set(files)].sort();
}

function linkedPaths(markdown) {
  const links = [];
  const pattern = /\[[^\]]*\]\(([^)]+)\)/g;
  for (const match of markdown.matchAll(pattern)) {
    let target = match[1].trim();
    if (target.startsWith('<') && target.endsWith('>')) target = target.slice(1, -1);
    target = target.split(/\s+["']/)[0];
    if (!target || target.startsWith('#') || target.startsWith('//') || /^[a-z][a-z0-9+.-]*:/i.test(target)) continue;
    target = target.split('#')[0];
    if (!target) continue;
    try { target = decodeURIComponent(target); } catch {}
    links.push(target);
  }
  return links;
}

function captureSources(text) {
  return text.match(/captures\/[A-Za-z0-9_./-]+\.png/g) ?? [];
}

function gitTracked(root, relativePath) {
  const result = spawnSync('git', ['ls-files', '--cached', '--', relativePath], {
    cwd: root,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'ignore'],
  });
  return result.status === 0 && result.stdout.trim().length > 0;
}

export function checkReferences({ root = defaultRoot, includeLocal = false, isTracked = gitTracked } = {}) {
  const errors = [];
  let linkCount = 0;
  let templateCount = 0;
  let sourceCount = 0;
  let skippedLocal = 0;

  for (const relativeFile of markdownFiles(root)) {
    const absoluteFile = path.join(root, relativeFile);
    const markdown = fs.readFileSync(absoluteFile, 'utf8');
    for (const target of linkedPaths(markdown)) {
      linkCount += 1;
      const resolved = path.resolve(path.dirname(absoluteFile), target);
      if (!fs.existsSync(resolved)) errors.push(`${relativeFile}：链接不存在 ${target}`);
    }
  }

  const manifestRelative = 'executor/perception/templates/cn/manifest.json';
  const manifestPath = path.join(root, manifestRelative);
  if (!fs.existsSync(manifestPath)) {
    errors.push(`缺少模板清单 ${manifestRelative}`);
  } else {
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    const entries = [...Object.values(manifest.templates ?? {}), ...Object.values(manifest.emblems ?? {})];
    for (const entry of entries) {
      templateCount += 1;
      if (entry.file && !fs.existsSync(path.join(path.dirname(manifestPath), entry.file))) {
        errors.push(`${manifestRelative}：模板不存在 ${entry.file}`);
      }
    }
  }

  const sourceFiles = ['cv/build_templates.py', 'executor/perception/build_templates.py'];
  const sources = new Set();
  if (fs.existsSync(manifestPath)) {
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    for (const entry of [...Object.values(manifest.templates ?? {}), ...Object.values(manifest.emblems ?? {})]) {
      if (entry.source) sources.add(entry.source);
    }
  }
  for (const relativeFile of sourceFiles) {
    const absoluteFile = path.join(root, relativeFile);
    if (!fs.existsSync(absoluteFile)) {
      errors.push(`缺少模板构建脚本 ${relativeFile}`);
      continue;
    }
    for (const source of captureSources(fs.readFileSync(absoluteFile, 'utf8'))) sources.add(source);
  }

  for (const source of [...sources].sort()) {
    sourceCount += 1;
    if (fs.existsSync(path.join(root, source))) continue;
    if (!includeLocal && !isTracked(root, source)) {
      skippedLocal += 1;
      continue;
    }
    errors.push(`模板来源不存在 ${source}`);
  }

  return { errors, linkCount, templateCount, sourceCount, skippedLocal };
}

export function main(argv = process.argv.slice(2)) {
  const unknown = argv.filter((arg) => arg !== '--include-local');
  if (unknown.length) {
    console.error(`未知参数 ${unknown.join(' ')}；仅支持 --include-local`);
    return 2;
  }
  const result = checkReferences({ includeLocal: argv.includes('--include-local') });
  for (const error of result.errors) console.error(`错误 ${error}`);
  console.log(`文档链接 ${result.linkCount}；模板 ${result.templateCount}；来源 ${result.sourceCount}；跳过本机缺失来源 ${result.skippedLocal}`);
  if (result.errors.length) return 1;
  console.log('文档与模板引用检查通过');
  return 0;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  process.exit(main());
}
