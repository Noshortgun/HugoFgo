#!/usr/bin/env node
/** 只读扫描可能同时控制模拟器的外部自动化进程。 */
import { spawnSync } from 'node:child_process';

const isWindows = process.platform === 'win32';

const AUTOMATION_PATTERNS = [
  { label: 'BBChannel', match: ({ name, base }) => /bbchannel/i.test(base) || /bbchannel/i.test(name) },
  {
    label: 'Fate/Grand Automata',
    match: ({ name, base }) => /fate[-_ ]?grand[-_ ]?automata|^fga(\.exe)?$/i.test(base) || /^fga(\.exe)?$/i.test(name),
  },
  {
    label: 'FGO-py',
    match: ({ base, commandLine }) => /^(python|py)/i.test(base) && /fgo[-_]py\b/i.test(commandLine),
  },
  {
    label: 'AutoFGO',
    match: ({ name, base }) => /auto[-_ ]?fgo/i.test(base) || /auto[-_ ]?fgo/i.test(name),
  },
  {
    label: '本项目实机脚本 farm_ember.py',
    match: ({ base, commandLine }) => /^(python|py)/i.test(base) && /farm_ember\b/i.test(commandLine),
  },
];

/** 返回 { ok: true, partial?, matches } 或 { ok: false, error }。 */
export function scanAutomationProcesses() {
  const scan = isWindows ? scanWindows() : scanPosix();
  if (!scan.ok) return scan;

  const matches = [];
  for (const item of scan.processes) {
    const pattern = AUTOMATION_PATTERNS.find((candidate) => candidate.match(processIdentity(item)));
    if (pattern) matches.push({ ...item, label: pattern.label });
  }
  return { ok: true, partial: scan.partial, matches };
}

function processIdentity(item) {
  const executable = item.commandLine.match(/^\s*"([^"]+)"|^\s*(\S+)/);
  const executablePath = executable ? (executable[1] ?? executable[2]) : item.name;
  return {
    name: item.name,
    base: (executablePath ?? '').split(/[\\/]/).pop() || item.name,
    commandLine: item.commandLine,
  };
}

function scanWindows() {
  const script = [
    '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8',
    'Get-CimInstance Win32_Process | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress',
  ].join('; ');
  const result = spawnSync('powershell', ['-NoProfile', '-NonInteractive', '-Command', script], {
    encoding: 'utf8',
    maxBuffer: 32 * 1024 * 1024,
    timeout: 20_000,
  });
  if (result.status === 0) {
    try {
      const parsed = JSON.parse((result.stdout ?? '').replace(/^\uFEFF/, '').trim() || '[]');
      const list = Array.isArray(parsed) ? parsed : [parsed];
      return {
        ok: true,
        processes: list
          .filter((item) => item && item.ProcessId)
          .map((item) => ({
            pid: item.ProcessId,
            name: item.Name ?? '',
            commandLine: item.CommandLine ?? '',
          })),
      };
    } catch {
      // 继续走 tasklist 回退
    }
  }
  const fallback = spawnSync('tasklist', ['/FO', 'CSV', '/NH'], { encoding: 'utf8', timeout: 20_000 });
  if (fallback.status === 0) {
    const processes = (fallback.stdout ?? '')
      .split(/\r?\n/)
      .map((line) => line.match(/^"([^"]+)","(\d+)"/))
      .filter(Boolean)
      .map((match) => ({ pid: Number(match[2]), name: match[1], commandLine: '' }));
    return { ok: true, processes, partial: true };
  }
  return { ok: false, error: (result.stderr || fallback.stderr || '进程列表不可读').trim() };
}

function scanPosix() {
  const result = spawnSync('ps', ['-eo', 'pid=,args='], { encoding: 'utf8', timeout: 20_000 });
  if (result.status !== 0) return { ok: false, error: (result.stderr || '进程列表不可读').trim() };
  const processes = (result.stdout ?? '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [pid, ...rest] = line.split(/\s+/);
      const commandLine = rest.join(' ');
      return { pid: Number(pid), name: commandLine.split(/\s+/)[0] ?? '', commandLine };
    });
  return { ok: true, processes };
}
