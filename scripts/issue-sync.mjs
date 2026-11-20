#!/usr/bin/env node
/**
 * GitHub 票据同步与本地漂移检查（只读）。
 *
 * 语义：
 * - GitHub issue 是权威内容；`.scratch/backend-rebuild/issues/NN-<slug>.md` 是本地唯一的
 *   手工编辑发布源。本脚本只做「拉取 + 比较 + 报告」：从不写 GitHub，也不删除任何文件。
 * - 比较时去掉本地源首行 `# NN: 标题`，并忽略远端 body 可能带的发布抬头（`本地施工单：…` +
 *   `Spec：…` 两行）；这样直接创建的 issue 与经 `publish-issues.mjs` 发布的 issue 都能对齐。
 * - 比较前做规范化（CRLF→LF、行尾空白、末尾空行），换行风格不同不算漂移。
 * - 票据编号取自 issue 标题前缀 `NN:`（可能带字母，如 `05A`），因为 GitHub issue 号与票据
 *   编号可能不一致（例：票据 19 是 issue #20）。
 *
 * 用法：
 *   node scripts/issue-sync.mjs                  # 默认 = --check
 *   node scripts/issue-sync.mjs --check [--json] [--diff]
 *   node scripts/issue-sync.mjs --root <repo>    # 对另一个检出目录跑检查
 *   node scripts/issue-sync.mjs --target 23,24   # 校验这些票能否作为发布目标
 *   node scripts/issue-sync.mjs --no-cache       # 不写 .scratch/issue-cache/
 *   node scripts/issue-sync.mjs --allow-parent   # 允许父票据作为发布目标（默认拒绝）
 *   node scripts/issue-sync.mjs --from-file <f>  # 离线：用保存好的 gh issue list JSON 代替 gh 调用
 *
 * 退出码：0 无阻塞问题；1 存在漂移或结构问题；2 用法/安全错误（受保护票据、gh 失败）。
 * `--target` 模式只回答「这些票能否发布」：目标合法退出 0，否则退出 2；其他票的漂移仍会打印。
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const SCRIPT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DEFAULT_REPO = 'Noshortgun/HugoFgo';
const ISSUES_REL = path.join('.scratch', 'backend-rebuild', 'issues');
const CACHE_REL = path.join('.scratch', 'issue-cache');
const GH =
  process.env.GH_BIN ??
  (process.platform === 'win32' ? 'C:\\Program Files\\GitHub CLI\\gh.exe' : 'gh');

const HEADER_RE = /^#\s+(\d{1,3}[A-Za-z]?)\s*:\s*(\S.*)$/;
const FILE_ID_RE = /^(\d{1,3}[A-Za-z]?)(?:-|\.)/;
const CANONICAL_RE = /^\d{1,3}[A-Za-z]?-.+\.md$/i;
const STATUS_TEXT = {
  added: '新增（本地有源，远端无 issue）',
  modified: '修改（本地待发布正文与远端不同）',
  unchanged: '无变化',
  'source-missing': '缺本地源（远端有 issue，本地无可编辑源）',
};
const LABEL_BY_STATUS = {
  'ready-for-human': 'ready-for-human',
  'needs-triage': 'needs-triage',
};

function usageError(message) {
  console.error(`issue-sync: ${message}`);
  console.error('用法：node scripts/issue-sync.mjs [--check] [--json] [--diff] [--root <dir>] [--target <ids>] [--no-cache] [--allow-parent]');
  process.exit(2);
}

function parseArgs(argv) {
  const opts = {
    root: SCRIPT_ROOT,
    repo: DEFAULT_REPO,
    json: false,
    diff: false,
    writeCache: true,
    allowParent: false,
    fromFile: null,
    targets: [],
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--check') continue;
    if (arg === '--json') { opts.json = true; continue; }
    if (arg === '--diff') { opts.diff = true; continue; }
    if (arg === '--no-cache') { opts.writeCache = false; continue; }
    if (arg === '--allow-parent') { opts.allowParent = true; continue; }
    if (arg === '--repo') { opts.repo = argv[++index] ?? usageError('--repo 需要参数'); continue; }
    if (arg === '--root') { opts.root = path.resolve(argv[++index] ?? usageError('--root 需要参数')); continue; }
    if (arg === '--from-file') { opts.fromFile = path.resolve(argv[++index] ?? usageError('--from-file 需要参数')); continue; }
    if (arg === '--target') { opts.targets = String(argv[++index] ?? usageError('--target 需要参数')).split(','); continue; }
    if (arg === '--help' || arg === '-h') {
      console.log(fs.readFileSync(fileURLToPath(import.meta.url), 'utf8').split('*/')[0]);
      process.exit(0);
    }
    usageError(`未知参数 ${arg}`);
  }
  return opts;
}

function normalizeId(raw) {
  const match = String(raw ?? '').trim().match(/^(\d{1,3})\s*([A-Za-z]?)$/);
  if (!match) return null;
  return `${Number(match[1])}${match[2].toUpperCase()}`;
}

function idSortKey(id) {
  const match = String(id).match(/^(\d+)([A-Z]?)$/);
  return match ? [Number(match[1]), match[2]] : [9999, String(id)];
}

function compareIds(left, right) {
  const [leftNumber, leftSuffix] = idSortKey(left);
  const [rightNumber, rightSuffix] = idSortKey(right);
  return leftNumber - rightNumber || leftSuffix.localeCompare(rightSuffix);
}

function normalizeBody(text) {
  return String(text ?? '')
    .replace(/^\uFEFF/, '')
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map((line) => line.replace(/[ \t]+$/, ''))
    .join('\n')
    .replace(/\n+$/, '')
    .trimStart();
}

function ghRun(args) {
  const result = spawnSync(GH, args, { encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 });
  if (result.error) throw new Error(`无法执行 gh（${GH}）：${result.error.message}`);
  if (result.status !== 0) {
    throw new Error(`gh ${args.join(' ')} 失败：${(result.stderr || result.stdout || '').trim()}`);
  }
  return result.stdout;
}

function fetchRemoteTickets(repo, fromFile) {
  const raw = fromFile
    ? fs.readFileSync(fromFile, 'utf8')
    : ghRun([
        'issue', 'list',
        '--repo', repo,
        '--state', 'all',
        '--limit', '500',
        '--json', 'number,title,state,labels,body,url',
      ]);
  return JSON.parse(raw).map((issue) => ({
    number: issue.number,
    title: issue.title,
    state: issue.state,
    labels: (issue.labels ?? []).map((label) => label.name).sort(),
    body: issue.body ?? '',
    url: issue.url ?? '',
  }));
}

function readLocalFiles(issuesDir) {
  if (!fs.existsSync(issuesDir)) {
    throw new Error(`找不到票据目录：${issuesDir}`);
  }
  return fs
    .readdirSync(issuesDir)
    .filter((file) => file.toLowerCase().endsWith('.md'))
    .sort()
    .map((file) => {
      const text = fs.readFileSync(path.join(issuesDir, file), 'utf8');
      const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/);
      const header = lines[0].match(HEADER_RE);
      const fileIdRaw = file.match(FILE_ID_RE)?.[1] ?? null;
      const fileId = normalizeId(fileIdRaw);
      const declaredId = header ? normalizeId(header[1]) : null;
      const notes = [];

      if (/\.current\.md$/i.test(file)) notes.push('遗留双份 *.current.md');
      else if (/^\d{1,3}[A-Za-z]?\.md$/i.test(file)) notes.push('遗留单号文件 NN.md');
      if (!declaredId) notes.push('缺少「# NN: 标题」首行，不能作为发布源');
      if (declaredId && fileId && declaredId !== fileId) {
        notes.push(`文件名编号 ${fileId} 与正文编号 ${declaredId} 不一致`);
      }

      return {
        file,
        text,
        lines,
        fileId,
        declaredId,
        id: declaredId ?? fileId,
        title: header ? header[2].trim() : null,
        canonical:
          Boolean(declaredId) &&
          declaredId === fileId &&
          CANONICAL_RE.test(file) &&
          notes.length === 0,
        notes,
      };
    });
}

function stripPublishHeader(text) {
  const lines = String(text ?? '').replace(/\r\n?/g, '\n').split('\n');
  if (/^本地施工单：/.test(lines[0] ?? '') && /^Spec：/.test(lines[1] ?? '')) {
    let index = 2;
    while (index < lines.length && lines[index].trim() === '') index += 1;
    return lines.slice(index).join('\n');
  }
  return text;
}

function localStatus(entry) {
  return entry.text.match(/^\*\*Status:\*\*\s*([\w-]+)/m)?.[1] ?? 'ready-for-agent';
}

function expectedLabel(entry) {
  const status = localStatus(entry);
  if (status === 'done') return null;
  return LABEL_BY_STATUS[status] ?? 'ready-for-agent';
}

function diffStat(localLines, remoteLines) {
  const local = localLines.split('\n');
  const remote = remoteLines.split('\n');
  const width = remote.length + 1;
  let previous = new Uint16Array(width);
  let current = new Uint16Array(width);
  for (let i = 1; i <= local.length; i += 1) {
    for (let j = 1; j <= remote.length; j += 1) {
      current[j] =
        local[i - 1] === remote[j - 1]
          ? previous[j - 1] + 1
          : Math.max(previous[j], current[j - 1]);
    }
    [previous, current] = [current, previous];
    current.fill(0);
  }
  const common = previous[remote.length];
  return { localOnly: local.length - common, remoteOnly: remote.length - common };
}

function sectionRefs(body, heading) {
  const lines = normalizeBody(body).split('\n');
  const refs = new Set();
  let inSection = false;
  for (const line of lines) {
    if (/^##\s+/.test(line)) {
      inSection = new RegExp(`^##\\s+${heading}\\s*$`, 'i').test(line);
      continue;
    }
    if (!inSection) continue;
    for (const match of line.matchAll(/#(\d+)\b/g)) refs.add(Number(match[1]));
  }
  return [...refs];
}

function textDiff(localText, remoteText) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hugofgo-issue-sync-'));
  const localFile = path.join(dir, 'local.md');
  const remoteFile = path.join(dir, 'remote.md');
  fs.writeFileSync(localFile, localText, 'utf8');
  fs.writeFileSync(remoteFile, remoteText, 'utf8');
  const result = spawnSync(
    'git',
    ['-C', dir, 'diff', '--no-index', '--no-color', '--unified=2', '--', 'local.md', 'remote.md'],
    { encoding: 'utf8' },
  );
  fs.rmSync(dir, { recursive: true, force: true });
  return (result.stdout ?? '')
    .split('\n')
    .filter((line) => !/^(diff --git|index |--- |\+\+\+ )/.test(line))
    .join('\n')
    .trimEnd();
}

function writeCache(cacheDir, repo, tickets) {
  fs.mkdirSync(cacheDir, { recursive: true });
  const fetchedAt = new Date().toISOString();
  for (const ticket of tickets) {
    const payload = {
      ticket: ticket.id,
      githubNumber: ticket.number,
      title: ticket.title,
      state: ticket.state,
      labels: ticket.labels,
      url: ticket.url,
      body: ticket.body,
      repo,
      fetchedAt,
    };
    fs.writeFileSync(path.join(cacheDir, `${ticket.id}.json`), `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
  }
  const index = {
    repo,
    fetchedAt,
    issues: tickets.map((ticket) => ({
      ticket: ticket.id,
      githubNumber: ticket.number,
      state: ticket.state,
      labels: ticket.labels,
    })),
  };
  fs.writeFileSync(path.join(cacheDir, 'index.json'), `${JSON.stringify(index, null, 2)}\n`, 'utf8');
  return cacheDir;
}

function main() {
  const opts = parseArgs(process.argv.slice(2));
  const issuesDir = path.join(opts.root, ISSUES_REL);
  const findings = [];

  let remote;
  try {
    remote = fetchRemoteTickets(opts.repo, opts.fromFile);
  } catch (error) {
    console.error(`issue-sync: ${error.message}`);
    process.exit(2);
  }

  const localFiles = readLocalFiles(issuesDir);

  const remoteByTicket = new Map();
  const remoteDuplicates = new Map();
  for (const issue of remote) {
    const id = normalizeId(issue.title.match(/^\s*(\d{1,3}[A-Za-z]?)\s*:/)?.[1]);
    if (!id) {
      findings.push({
        severity: 'warning',
        kind: 'unparsable-title',
        ticket: null,
        files: [],
        message: `issue #${issue.number} 标题没有「NN: 」前缀，无法归入票据：${issue.title}`,
      });
      continue;
    }
    if (remoteByTicket.has(id)) {
      const list = remoteDuplicates.get(id) ?? [remoteByTicket.get(id).number];
      list.push(issue.number);
      remoteDuplicates.set(id, list);
      continue;
    }
    remoteByTicket.set(id, { ...issue, id });
  }
  for (const [id, numbers] of remoteDuplicates) {
    findings.push({
      severity: 'error',
      kind: 'duplicate-remote-number',
      ticket: id,
      files: [],
      message: `票据 ${id} 在远端对应多个 issue：${numbers.map((number) => `#${number}`).join(', ')}`,
    });
  }

  const localByTicket = new Map();
  for (const entry of localFiles) {
    if (!entry.id) {
      findings.push({
        severity: 'warning',
        kind: 'unparsable-file',
        ticket: null,
        files: [entry.file],
        message: `无法从文件名或正文判断票据编号：${entry.file}`,
      });
      continue;
    }
    const list = localByTicket.get(entry.id) ?? [];
    list.push(entry);
    localByTicket.set(entry.id, list);
  }

  const canonicalById = new Map();
  const githubNumberToId = new Map(
    [...remoteByTicket.values()].map((issue) => [issue.number, issue.id]),
  );
  const allIds = [...new Set([...localByTicket.keys(), ...remoteByTicket.keys()])].sort(compareIds);

  for (const id of allIds) {
    const files = localByTicket.get(id) ?? [];
    const canonical = files.filter((entry) => entry.canonical);
    if (canonical.length > 1) {
      findings.push({
        severity: 'error',
        kind: 'duplicate-number',
        ticket: id,
        files: canonical.map((entry) => entry.file),
        message: `票据 ${id} 有多个可作为发布源的文件：${canonical.map((entry) => entry.file).join(', ')}`,
      });
    }
    if (canonical.length === 1) canonicalById.set(id, canonical[0]);
    if (!files.length) continue;

    if (!canonical.length) {
      const hint = githubNumberToId.has(Number(String(id).replace(/[A-Z]$/, '')))
        ? `；文件名编号 ${id} 其实是 GitHub issue 号（对应票据 ${githubNumberToId.get(Number(String(id).replace(/[A-Z]$/, '')))}），不是票据编号`
        : '';
      if (!remoteByTicket.has(id)) {
        findings.push({
          severity: 'warning',
          kind: 'orphan-legacy-file',
          ticket: id,
          files: files.map((entry) => entry.file),
          message: `文件编号 ${id} 在本地与远端都没有对应票据，只是遗留副本：${files
            .map((entry) => entry.file)
            .join(', ')}${hint}`,
        });
      }
      continue;
    }

    const extras = files.filter((entry) => entry !== canonical[0]);
    for (const extra of extras) {
      findings.push({
        severity: 'warning',
        kind: extra.notes.some((note) => note.startsWith('文件名编号'))
          ? 'id-mismatch'
          : 'legacy-file',
        ticket: id,
        files: [extra.file],
        message: `票据 ${id} 的额外文件 ${extra.file}：${extra.notes.join('；') || '非规范命名'}（发布源是 ${canonical[0].file}）`,
      });
    }
  }

  for (const id of [...remoteByTicket.keys()].sort(compareIds)) {
    if (canonicalById.has(id)) continue;
    if (findings.some((finding) => finding.kind === 'duplicate-number' && finding.ticket === id)) continue;
    const issue = remoteByTicket.get(id);
    const files = (localByTicket.get(id) ?? []).map((entry) => entry.file);
    findings.push({
      severity: issue.state === 'OPEN' ? 'error' : 'warning',
      kind: 'no-local-source',
      ticket: id,
      files,
      message: `票据 ${id}（issue #${issue.number}，${issue.state}）没有「NN-<slug>.md + # NN: 标题」形式的手工编辑源${
        files.length ? `；现有文件不能作为发布源：${files.join(', ')}` : ''
      }`,
    });
  }

  const ticketIds = allIds.filter((id) => remoteByTicket.has(id) || canonicalById.has(id));

  const parentRefs = new Map();
  for (const id of ticketIds) {
    const canonical = canonicalById.get(id);
    const issue = remoteByTicket.get(id);
    const body = canonical?.text ?? issue?.body ?? '';
    const parentNumbers = sectionRefs(body, 'Parent');
    const declaresChildren = sectionRefs(body, 'Children').length > 0 || /^##\s+(Sub-issues|Children)\s*$/im.test(body);
    for (const parentNumber of parentNumbers) {
      const parentId = [...remoteByTicket.values()].find((item) => item.number === parentNumber)?.id ?? null;
      if (!parentId) {
        findings.push({
          severity: 'warning',
          kind: 'unknown-parent',
          ticket: id,
          files: canonical ? [canonical.file] : [],
          message: `票据 ${id} 声明的父票据 #${parentNumber} 不在远端票据列表中`,
        });
        continue;
      }
      const list = parentRefs.get(parentId) ?? { by: [], declared: false };
      list.by.push(id);
      parentRefs.set(parentId, list);
    }
    if (declaresChildren) {
      const list = parentRefs.get(id) ?? { by: [], declared: false };
      list.declared = true;
      parentRefs.set(id, list);
    }
  }

  const tickets = ticketIds.map((id) => {
    const issue = remoteByTicket.get(id) ?? null;
    const source = canonicalById.get(id) ?? null;
    const protection = [];
    if (issue && issue.state !== 'OPEN') protection.push('closed');
    if (parentRefs.has(id)) protection.push('parent');

    const result = {
      id,
      githubNumber: issue?.number ?? null,
      title: issue?.title ?? source?.title ?? null,
      state: issue?.state ?? null,
      labels: issue?.labels ?? [],
      source: source?.file ?? null,
      files: (localByTicket.get(id) ?? []).map((entry) => entry.file),
      protected: protection,
      status: 'source-missing',
      labelDrift: null,
      diff: null,
    };

    if (source && !issue) {
      result.status = 'added';
      return result;
    }
    if (!source) {
      result.status = 'source-missing';
      return result;
    }

    const localBody = normalizeBody(source.lines.slice(1).join('\n'));
    const remoteBody = normalizeBody(stripPublishHeader(issue.body));
    if (localBody !== remoteBody) {
      result.status = 'modified';
      result.diff = diffStat(localBody, remoteBody);
      result.localBodyForDiff = localBody;
    } else {
      result.status = 'unchanged';
    }

    const expected = expectedLabel(source);
    if (issue.state === 'OPEN' && expected && !issue.labels.includes(expected)) {
      result.labelDrift = { expected, remote: issue.labels };
      if (result.status === 'unchanged') result.status = 'modified';
    }
    return result;
  });

  const drifting = tickets.filter(
    (ticket) => ticket.status !== 'unchanged' && ticket.status !== 'source-missing' && !ticket.protected.length,
  );
  const protectedDrift = tickets.filter(
    (ticket) => ticket.status !== 'unchanged' && ticket.status !== 'source-missing' && ticket.protected.length,
  );
  const errors = findings.filter((finding) => finding.severity === 'error');
  const warnings = findings.filter((finding) => finding.severity === 'warning');
  const ok = drifting.length === 0 && errors.length === 0;

  const targetIds = opts.targets.map((raw) => normalizeId(raw));
  const targetProblems = [];
  for (const [index, raw] of opts.targets.entries()) {
    const id = targetIds[index];
    const ticket = id ? tickets.find((item) => item.id === id) : null;
    if (!id || !ticket) {
      targetProblems.push(`票据 ${raw}：在本地与远端都找不到，不能作为发布目标`);
      continue;
    }
    if (ticket.state !== 'OPEN') {
      targetProblems.push(
        `票据 ${id}（issue #${ticket.githubNumber}）已关闭（${ticket.state}），拒绝作为发布目标；需要重开请人工处理`,
      );
    }
    const parents = parentRefs.get(id);
    if (parents && !opts.allowParent) {
      const reason = parents.declared
        ? '正文声明了自己的子票据'
        : `被子票据 ${parents.by.join(', ')} 声明为父票据`;
      targetProblems.push(
        `票据 ${id}（issue #${ticket.githubNumber}）是父票据（${reason}），拒绝作为发布目标；确需改动用 --allow-parent`,
      );
    }
    if (!ticket.source) {
      targetProblems.push(`票据 ${id} 没有「NN-<slug>.md + # NN: 标题」形式的手工编辑源，无法发布`);
    }
  }

  let cacheDir = null;
  if (opts.writeCache) {
    cacheDir = writeCache(
      path.join(opts.root, CACHE_REL),
      opts.repo,
      [...remoteByTicket.values()].sort((left, right) => compareIds(left.id, right.id)),
    );
  }

  if (opts.json) {
    const payload = {
      ok: opts.targets.length ? targetProblems.length === 0 : ok,
      repo: opts.repo,
      root: opts.root,
      issuesDir: path.relative(opts.root, issuesDir) || '.',
      cache: cacheDir ? path.relative(opts.root, cacheDir) : null,
      tickets: tickets.map(({ localBodyForDiff, ...rest }) => rest),
      drift: drifting.map((ticket) => ticket.id),
      protectedDrift: protectedDrift.map((ticket) => ticket.id),
      findings,
      targets: opts.targets.length ? { requested: targetIds, problems: targetProblems } : null,
    };
    console.log(JSON.stringify(payload, null, 2));
    if (targetProblems.length) process.exit(2);
    process.exit(opts.targets.length || ok ? 0 : 1);
  }

  const rows = tickets.map((ticket) => {
    const diff = ticket.diff ? `-${ticket.diff.localOnly} +${ticket.diff.remoteOnly}` : '-';
    const notes = [];
    if (ticket.protected.length) notes.push(`protected:${ticket.protected.join('+')}`);
    if (ticket.labelDrift) notes.push(`label:${ticket.labelDrift.expected}`);
    return [
      ticket.id.padEnd(4),
      (ticket.githubNumber ? `#${ticket.githubNumber}` : '-').padEnd(5),
      (ticket.state ?? '-').padEnd(6),
      (ticket.labels.join(',') || '-').padEnd(19),
      ticket.status.padEnd(14),
      diff.padEnd(8),
      notes.join(' '),
    ].join(' ');
  });

  console.log(`repo: ${opts.repo}  root: ${opts.root}${cacheDir ? `  cache: ${path.relative(opts.root, cacheDir)}` : ''}`);
  console.log('ID   GH    STATE  LABELS              STATUS         DIFF     NOTES');
  for (const row of rows) console.log(row);

  if (warnings.length) {
    console.log('\n结构提示（可发布但有遗留文件）：');
    for (const finding of warnings) console.log(`  ! [${finding.kind}] ${finding.message}`);
  }
  if (errors.length) {
    console.log('\n结构错误（阻塞发布）：');
    for (const finding of errors) console.log(`  x [${finding.kind}] ${finding.message}`);
  }
  if (protectedDrift.length) {
    console.log('\n受保护票据的漂移（不会作为发布目标，仅供参考）：');
    for (const ticket of protectedDrift) {
      console.log(
        `  - #${ticket.githubNumber ?? '-'} 票据 ${ticket.id}（${ticket.protected.join('+')}）${STATUS_TEXT[ticket.status]}${
          ticket.diff ? ` diff -${ticket.diff.localOnly} +${ticket.diff.remoteOnly}` : ''
        }`,
      );
    }
  }

  if (opts.diff) {
    for (const ticket of drifting) {
      if (!ticket.localBodyForDiff) continue;
      console.log(`\n=== 票据 ${ticket.id} 本地源 ${ticket.source} vs issue #${ticket.githubNumber} ===`);
      console.log(textDiff(ticket.localBodyForDiff, normalizeBody(remoteByTicket.get(ticket.id).body)));
    }
  }

  if (opts.targets.length) {
    if (targetProblems.length) {
      console.error('\n发布目标检查失败：');
      for (const problem of targetProblems) console.error(`  x ${problem}`);
      process.exit(2);
    }
    console.log(`\n发布目标检查通过：${targetIds.join(', ')}`);
    if (drifting.length) {
      console.log(`（其余票据仍有漂移：${drifting.map((ticket) => ticket.id).join(', ')}；--target 模式只看目标票据，退出码 0）`);
    }
    process.exit(0);
  }

  const summary = [
    `共 ${tickets.length} 票：无变化 ${tickets.filter((ticket) => ticket.status === 'unchanged').length}`,
    `修改 ${tickets.filter((ticket) => ticket.status === 'modified').length}`,
    `新增 ${tickets.filter((ticket) => ticket.status === 'added').length}`,
    `缺本地源 ${tickets.filter((ticket) => ticket.status === 'source-missing').length}`,
  ];
  console.log(`\n${summary.join('，')}；阻塞漂移 ${drifting.length}，结构错误 ${errors.length}，结构提示 ${warnings.length}。`);

  if (drifting.length) {
    console.log(`漂移票据：${drifting.map((ticket) => ticket.id).join(', ')}`);
  }
  if (!ok) {
    console.log('检查未通过：先处理上面的漂移与结构问题；本命令不写 GitHub，也不自动删除重复文件。');
    process.exit(1);
  }
  console.log('检查通过：本地发布源与 GitHub 一致。');
  process.exit(0);
}

main();
