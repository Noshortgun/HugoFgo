#!/usr/bin/env node
/**
 * 工程预检：在任何实施或实机会话前运行的只读检查。
 *
 * 默认完全只读：不写文件、不操作模拟器、不发送任何实机输入。
 * 实机输入必须显式 --live 开启；未开启时预检只执行 adb 的只读查询。
 *
 * 用法：
 *   node scripts/preflight.mjs                     # 实机会话前：git + 回放依赖 + adb + 外部进程
 *   node scripts/preflight.mjs --offline           # 只做代码：跳过 adb 与外部进程检查
 *   node scripts/preflight.mjs --live              # 进入实机会话：外部冲突与设备问题提升为阻断（预检本身不发送输入）
 *   node scripts/preflight.mjs --allow-branch      # 确认在非施工分支上继续
 *   node scripts/preflight.mjs --serial emulator-5554
 *   node scripts/preflight.mjs --json              # 机器可读输出
 *
 * 退出码：0 无阻断项（警告不影响退出码）；1 存在阻断项；2 参数错误。
 */
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { deviceInfo, listDevices, resolveAdb } from './adb.mjs';
import { scanAutomationProcesses } from './automation-processes.mjs';
import { pipInstallCommand, REPLAY_PACKAGES, resolvePython, venvPython } from './python-env.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const isWindows = process.platform === 'win32';
const SECTIONS = ['仓库', '回放环境', '设备', '外部自动化', '实机输入'];
const WORK_BRANCH = /^(ticket|integration|feature|fix|chore|issue)\//;
const SUPPORTED_WIDTH = 1280;
const SUPPORTED_HEIGHT = 720;

const PROBE_SCRIPT = `
import importlib
import importlib.metadata
import json
import platform

packages = ${JSON.stringify(
  Object.fromEntries(REPLAY_PACKAGES.map((item) => [item.module, item.pip])),
)}
modules = {}
missing = []
for module, distribution in packages.items():
    try:
        importlib.import_module(module)
        try:
            version = importlib.metadata.version(distribution)
        except Exception:
            version = "已安装"
        modules[module] = version
    except Exception as exc:
        missing.append({"module": module, "pip": distribution, "error": str(exc)})
print(json.dumps({"version": platform.python_version(), "modules": modules, "missing": missing}))
`;

function hasFlag(name, alias) {
  return process.argv.includes(name) || Boolean(alias && process.argv.includes(alias));
}

function usage(error) {
  if (error) console.error(`${error}\n`);
  console.error(`用法：node scripts/preflight.mjs [选项]

  --offline        跳过设备与外部进程检查（只做代码实施）
  --live           标记实机会话（默认只读）；外部进程与设备问题改为阻断，预检本身不发送输入
  --allow-branch   确认在非施工分支上继续
  --serial <id>    指定 adb 设备序列号
  --json           输出 JSON
  --help           显示本帮助`);
}

const results = [];

function add(section, severity, title, { detail, fix } = {}) {
  results.push({ section, severity, title, detail, fix });
}

function gitRun(args) {
  const result = spawnSync('git', ['-C', root, ...args], { encoding: 'utf8' });
  if (result.error) return { ok: false, error: result.error.message };
  return {
    ok: result.status === 0,
    stdout: (result.stdout ?? '').trim(),
    stderr: (result.stderr ?? '').trim(),
    error: result.status === 0 ? undefined : (result.stderr ?? '').trim(),
  };
}

function checkGit({ allowBranch }) {
  const inside = gitRun(['rev-parse', '--show-toplevel']);
  if (!inside.ok) {
    add('仓库', 'fail', '当前目录不是 git 仓库，或 git 不可用', {
      detail: inside.error || '无法执行 git rev-parse --show-toplevel',
      fix: 'git init',
    });
    return;
  }
  if (path.resolve(inside.stdout) !== path.resolve(root)) {
    add('仓库', 'warn', `git 顶层目录与脚本目录不一致：${inside.stdout}`);
  }

  const branchResult = gitRun(['rev-parse', '--abbrev-ref', 'HEAD']);
  const branch = branchResult.ok ? branchResult.stdout : '';
  const detached = branch === 'HEAD';

  if (!branchResult.ok) {
    add('仓库', 'fail', '无法读取当前分支', {
      detail: branchResult.error || 'git rev-parse --abbrev-ref HEAD 失败',
      fix: 'git status',
    });
  } else if (detached) {
    if (allowBranch) {
      add('仓库', 'warn', '处于分离 HEAD（已用 --allow-branch 确认）', {
        detail: '提交不会属于任何分支，容易丢失',
      });
    } else {
      add('仓库', 'fail', '处于分离 HEAD', {
        detail: '在这里实施会丢失分支归属',
        fix: 'git switch -c ticket/<描述>',
      });
    }
  } else if (!WORK_BRANCH.test(branch)) {
    if (allowBranch) {
      add('仓库', 'warn', `非施工分支：${branch}（已用 --allow-branch 确认）`, {
        detail: '施工分支约定为 ticket/*、integration/*、feature/*、fix/*',
      });
    } else {
      add('仓库', 'fail', `非施工分支：${branch}`, {
        detail: '在原型或发布分支上实施，会把实验内容混入提交；确认要继续时加 --allow-branch',
        fix: 'git switch -c ticket/<描述>',
      });
    }
  } else {
    add('仓库', 'ok', `分支 ${branch}`);
  }

  checkSync(branch, detached);
  checkDirty();
}

function checkSync(branch, detached) {
  if (!branch || detached || branch === 'HEAD') return;

  const upstream = gitRun(['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}']);
  const hasUpstream = upstream.ok && upstream.stdout;
  const remoteBranch = gitRun(['rev-parse', '--verify', '--quiet', `origin/${branch}`]).ok
    ? `origin/${branch}`
    : undefined;
  let base = hasUpstream ? upstream.stdout : undefined;
  let note = '';
  let setUpstreamFix;

  if (!base) {
    if (remoteBranch) {
      base = remoteBranch;
      note = '（未设置上游）';
      setUpstreamFix = `git branch --set-upstream-to=${remoteBranch} ${branch}`;
    } else if (gitRun(['rev-parse', '--verify', '--quiet', 'origin/main']).ok) {
      base = 'origin/main';
      note = '（未设置上游，改为对比 origin/main）';
    }
  }

  if (!base) {
    add('仓库', 'warn', '未设置远端跟踪分支', {
      detail: '无法判断与远端的领先/落后关系',
      fix: 'git remote -v && git branch -vv',
    });
  } else {
    const counts = gitRun(['rev-list', '--left-right', '--count', `${base}...HEAD`]);
    if (!counts.ok) {
      add('仓库', 'warn', `无法比较与 ${base} 的差距`, { detail: counts.error });
    } else {
      const [behind, ahead] = counts.stdout.split(/\s+/).map(Number);
      if (behind > 0) {
        add('仓库', 'warn', `相对 ${base} 领先 ${ahead} / 落后 ${behind}${note}`, {
          detail: '落后说明基线过期，实施前先同步，避免重复修复',
          fix: `git log --oneline HEAD..${base}`,
        });
      } else if (!hasUpstream) {
        add('仓库', 'warn', `相对 ${base} 领先 ${ahead} / 落后 ${behind}${note}`, {
          detail: setUpstreamFix ? '分支尚未关联上游，推送前先建立跟踪关系' : '没有上游分支时，这只能说明与主线的差距',
          fix: setUpstreamFix ?? `git log --oneline HEAD..${base}`,
        });
      } else {
        add('仓库', 'ok', `相对 ${base} 领先 ${ahead} / 落后 ${behind}`);
      }
    }
  }
}

function checkDirty() {
  const status = gitRun(['status', '--porcelain', '--untracked-files=no']);
  if (!status.ok) {
    add('仓库', 'warn', '无法读取已跟踪文件的改动状态', { detail: status.error });
    return;
  }
  const lines = status.stdout ? status.stdout.split(/\r?\n/).filter(Boolean) : [];
  if (!lines.length) {
    add('仓库', 'ok', '已跟踪文件无未提交改动');
    return;
  }
  const preview = lines.slice(0, 8).map((line) => ` ${line}`).join('\n');
  add('仓库', 'warn', `${lines.length} 个已跟踪文件有未提交改动`, {
    detail: lines.length > 8 ? `${preview}\n …还有 ${lines.length - 8} 个` : preview,
    fix: 'git status --short',
  });
}

function checkPython() {
  const python = resolvePython(root);
  if (!python) {
    const configured = process.env.HUGOFGO_PYTHON?.trim();
    add('回放环境', 'fail', '未找到可用的 Python 解释器', {
      detail: configured
        ? `HUGOFGO_PYTHON=${configured} 无法执行 Python 代码`
        : '已尝试 cv/.venv 与 PATH 中的 python3/python',
      fix: configured
        ? (isWindows ? 'set HUGOFGO_PYTHON=C:\\path\\to\\python.exe' : 'export HUGOFGO_PYTHON=/path/to/python')
        : `python -m venv cv/.venv && ${pipInstallCommand(venvPython(root))}`,
    });
    return;
  }

  const probe = spawnSync(python.command, ['-c', PROBE_SCRIPT], {
    cwd: root,
    encoding: 'utf8',
    env: { ...process.env, PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1' },
    timeout: 120_000,
  });

  if (probe.error?.code === 'ETIMEDOUT') {
    add('回放环境', 'fail', 'Python 依赖探测超时', {
      detail: `解释器：${python.command}`,
      fix: pipInstallCommand(python.command),
    });
    return;
  }

  let report;
  try {
    report = JSON.parse((probe.stdout ?? '').trim());
  } catch {
    const stderr = (probe.stderr ?? '').trim().split(/\r?\n/).slice(0, 3).join('\n');
    add('回放环境', 'fail', '无法运行 Python 依赖探测', {
      detail: stderr || `解释器：${python.command}（退出码 ${probe.status}）`,
      fix: pipInstallCommand(python.command),
    });
    return;
  }

  if (report.missing?.length) {
    const packages = report.missing.map((item) => item.pip);
    add('回放环境', 'fail', `缺少回放依赖：${packages.join('、')}`, {
      detail: report.missing.map((item) => ` ${item.module}: ${item.error}`).join('\n'),
      fix: pipInstallCommand(python.command, packages),
    });
  } else {
    const versions = REPLAY_PACKAGES.map((item) => `${item.module} ${report.modules?.[item.module] ?? '?'}`).join(' / ');
    add('回放环境', 'ok', `Python ${report.version}（${python.source}）可导入全部回放依赖`, {
      detail: versions,
    });
  }

  checkReplayAssets(python.command);
}

function checkReplayAssets(python) {
  const templatesDir = path.join(root, 'cv', 'templates');
  let templates = [];
  try {
    templates = fs.readdirSync(templatesDir).filter((name) => name.endsWith('.png'));
  } catch {
    templates = [];
  }
  if (!templates.length) {
    add('回放环境', 'fail', '缺少识别模板 cv/templates/*.png', {
      detail: '回放与执行器都依赖这些模板',
      fix: `"${python}" cv/build_templates.py`,
    });
  } else {
    add('回放环境', 'ok', `识别模板 ${templates.length} 个`);
  }

  const labels = path.join(root, 'cv', 'labels.json');
  if (!fs.existsSync(labels)) {
    add('回放环境', 'fail', '缺少回放标注 cv/labels.json', {
      fix: 'git checkout -- cv/labels.json',
    });
  } else {
    add('回放环境', 'ok', '回放标注 cv/labels.json 存在');
  }
}

function checkDevice({ serial }) {
  let adb;
  try {
    adb = resolveAdb();
  } catch (error) {
    const hint = isWindows
      ? 'set HUGOFGO_ADB=C:\\path\\to\\platform-tools\\adb.exe'
      : 'export HUGOFGO_ADB=/path/to/platform-tools/adb';
    add('设备', 'fail', '未找到 adb', {
      detail: error.message,
      fix: `${hint}  然后重跑 node scripts/preflight.mjs`,
    });
    return { ready: false };
  }

  const adbCommand = adb === 'adb' ? 'adb' : `"${adb}"`;

  let devices;
  try {
    devices = listDevices();
  } catch (error) {
    add('设备', 'fail', 'adb 无法列出设备', {
      detail: error.message,
      fix: `${adbCommand} kill-server && ${adbCommand} start-server`,
    });
    return { ready: false };
  }

  if (!devices.length) {
    add('设备', 'fail', 'adb 未发现任何设备', {
      detail: `adb：${adb}`,
      fix: `${adbCommand} devices -l`,
    });
    return { ready: false };
  }

  const requested = serial || process.env.HUGOFGO_SERIAL?.trim();
  const usable = devices.filter((device) => device.state === 'device');
  const unauthorized = devices.filter((device) => device.state === 'unauthorized');
  let selected;

  if (requested) {
    selected = devices.find((device) => device.serial === requested);
    if (!selected) {
      add('设备', 'fail', `指定设备 ${requested} 不存在`, {
        detail: `当前设备：${devices.map((device) => `${device.serial}(${device.state})`).join('、')}`,
        fix: `${adbCommand} devices -l`,
      });
      return { ready: false };
    }
    if (selected.state !== 'device') {
      add('设备', 'fail', `设备 ${requested} 当前状态为 ${selected.state}`, {
        detail: '只有 device 状态才能截图与发送输入',
        fix: `${adbCommand} kill-server && ${adbCommand} start-server`,
      });
      return { ready: false };
    }
  } else if (usable.length === 1) {
    selected = usable[0];
  } else if (usable.length > 1) {
    add('设备', 'warn', `发现 ${usable.length} 个可用设备，未选择`, {
      detail: `候选：${usable.map((device) => device.serial).join('、')}；预检不会猜测该操作哪一台`,
      fix: 'node scripts/preflight.mjs --serial <serial>',
    });
    return { ready: false };
  } else {
    add('设备', 'fail', '没有处于 device 状态的设备', {
      detail: devices.map((device) => `${device.serial}：${device.state}`).join('\n'),
      fix: `${adbCommand} devices -l`,
    });
    if (unauthorized.length) {
      add('设备', 'fail', '设备未授权调试', {
        detail: `${unauthorized.map((device) => device.serial).join('、')} 处于 unauthorized`,
        fix: `在模拟器画面中允许 USB 调试，然后执行 ${adbCommand} kill-server && ${adbCommand} start-server`,
      });
    }
    return { ready: false };
  }

  for (const device of devices) {
    if (device !== selected && device.state !== 'device') {
      add('设备', 'warn', `残留设备 ${device.serial} 处于 ${device.state}`, {
        detail: '通常来自上次未退出的模拟器会话',
        fix: `${adbCommand} kill-server && ${adbCommand} start-server`,
      });
    }
  }

  let info;
  try {
    info = deviceInfo(selected.serial);
  } catch (error) {
    add('设备', 'fail', `无法读取设备 ${selected.serial} 的分辨率`, {
      detail: error.message,
      fix: `${adbCommand} -s ${selected.serial} shell wm size`,
    });
    return { ready: false };
  }

  const supported = info.width === SUPPORTED_WIDTH && info.height === SUPPORTED_HEIGHT;
  if (!supported) {
    add('设备', 'fail', `分辨率 ${info.width}×${info.height} 不受支持`, {
      detail: '坐标以 1280×720 为基准换算；其他分辨率会被执行器拒绝，不能盲点',
      fix: `${adbCommand} -s ${selected.serial} shell wm size ${SUPPORTED_WIDTH}x${SUPPORTED_HEIGHT}`,
    });
    return { ready: false, serial: selected.serial };
  }

  add('设备', 'ok', `${selected.serial}（${info.model || '未知型号'}）${info.width}×${info.height} density ${info.density ?? '?'}`, {
    detail: `adb：${adb}`,
  });
  return { ready: true, serial: selected.serial, adb };
}

function checkAutomation({ live }) {
  const scan = scanAutomationProcesses();
  if (!scan.ok) {
    add('外部自动化', live ? 'fail' : 'warn', '无法读取进程列表', {
      detail: `${scan.error}\n进程列表不可读时无法排除另一个程序同时控制模拟器`,
      fix: isWindows ? 'tasklist' : 'ps -eo pid=,args=',
    });
    return 'unknown';
  }

  const matches = scan.matches;
  if (!matches.length) {
    add('外部自动化', 'ok', scan.partial ? '未发现 BBChannel 类进程（仅按进程名匹配）' : '未发现 BBChannel 类自动化进程');
    return 'clear';
  }

  const detail = matches
    .slice(0, 6)
    .map((match) => {
      const commandLine = match.commandLine.length > 160
        ? `${match.commandLine.slice(0, 160)}…`
        : match.commandLine;
      return ` ${match.label}：${match.name} (pid ${match.pid})${commandLine ? `\n    ${commandLine}` : ''}`;
    })
    .join('\n');
  const fix = isWindows
    ? `${matches.map((match) => `taskkill /PID ${match.pid} /F`).join(' && ')}   # 确认不是正在使用的程序`
    : `kill ${matches.map((match) => match.pid).join(' ')}`;
  add(
    '外部自动化',
    live ? 'fail' : 'warn',
    `发现 ${matches.length} 个可能控制模拟器的进程`,
    {
      detail: `${detail}\n同时运行会争夺模拟器输入，实机操作前必须关闭`,
      fix,
    },
  );
  return 'conflict';
}

function checkLiveSession({ live, device, automation }) {
  if (!live) {
    add('实机输入', 'ok', '未开启（默认只读，未发送任何输入）', {
      detail: '需要操作模拟器时显式加 --live；预检本身始终不发送输入，实机操作由执行器负责',
    });
    return;
  }
  if (automation === 'conflict') {
    add('实机输入', 'fail', '外部自动化进程占用模拟器，不能进入实机会话', {
      detail: '两个程序同时控制模拟器会互相干扰',
      fix: '关闭外部自动化进程后重跑 node scripts/preflight.mjs --live',
    });
    return;
  }
  if (automation === 'unknown') {
    add('实机输入', 'fail', '无法确认外部自动化状态，不能进入实机会话', {
      detail: '进程列表不可读时无法排除另一个程序同时控制模拟器',
      fix: isWindows ? 'tasklist' : 'ps -eo pid=,args=',
    });
    return;
  }
  if (!device.ready) {
    add('实机输入', 'fail', '设备未就绪，不能进入实机会话', {
      fix: '修复上方设备阻断项后重跑 node scripts/preflight.mjs --live',
    });
    return;
  }
  add('实机输入', 'ok', '实机会话已就绪（--live）', {
    detail: '预检本身未发送输入；实机操作由 Python 执行器负责',
  });
}

function renderText(mode) {
  const lines = [`HugoFgo 预检  ${new Date().toLocaleString('sv-SE')}  模式：${mode}`];
  for (const section of SECTIONS) {
    const items = results.filter((item) => item.section === section);
    if (!items.length) continue;
    lines.push('', section);
    for (const item of items) {
      lines.push(`  [${item.severity}] ${item.title}`);
      if (item.detail) {
        for (const line of item.detail.split(/\r?\n/)) lines.push(`         ${line}`);
      }
      if (item.fix) lines.push(`         修复：${item.fix}`);
    }
  }

  const fails = results.filter((item) => item.severity === 'fail');
  const warns = results.filter((item) => item.severity === 'warn');
  lines.push('');
  if (fails.length) {
    lines.push(`结论：存在 ${fails.length} 个阻断项、${warns.length} 个警告，当前环境不适合继续`);
    lines.push(`下一条命令：${fails[0].fix ?? '修复上方阻断项后重跑 node scripts/preflight.mjs'}`);
  } else if (warns.length) {
    lines.push(`结论：可以继续（${warns.length} 个警告，无阻断项）`);
  } else {
    lines.push('结论：可以继续（全部检查通过）');
  }
  return lines.join('\n');
}

function main() {
  const args = process.argv.slice(2);
  const known = new Set(['--offline', '--live', '--allow-branch', '--serial', '--json', '--help', '-h']);
  for (const arg of args) {
    if (arg.startsWith('-') && !known.has(arg)) {
      usage(`未知参数：${arg}`);
      return 2;
    }
  }
  if (hasFlag('--help', '-h')) {
    usage();
    return 0;
  }

  const offline = hasFlag('--offline');
  const live = hasFlag('--live');
  const allowBranch = hasFlag('--allow-branch');
  const serialIndex = args.indexOf('--serial');
  const serial = serialIndex === -1 ? undefined : args[serialIndex + 1];

  if (offline && live) {
    usage('--offline 与 --live 不能同时使用：离线预检不会连接设备');
    return 2;
  }
  if (serialIndex !== -1 && (!serial || serial.startsWith('--'))) {
    usage('--serial 需要设备序列号');
    return 2;
  }

  const mode = offline ? '离线实施（--offline）' : live ? '实机（--live，允许输入）' : '实机只读（默认）';

  checkGit({ allowBranch });
  checkPython();

  let device = { ready: false };
  let automation = 'unknown';
  if (offline) {
    add('设备', 'warn', '设备检查已跳过（--offline）', {
      detail: '只适合代码实施；实机操作前去掉 --offline 重跑',
    });
    add('外部自动化', 'warn', '进程检查已跳过（--offline）');
  } else {
    device = checkDevice({ serial });
    automation = checkAutomation({ live });
  }
  checkLiveSession({ live, device, automation });

  const fails = results.filter((item) => item.severity === 'fail');
  const warns = results.filter((item) => item.severity === 'warn');

  if (hasFlag('--json')) {
    console.log(JSON.stringify({
      generatedAt: new Date().toISOString(),
      mode,
      liveInput: live,
      offline,
      healthy: fails.length === 0,
      failures: fails.length,
      warnings: warns.length,
      nextCommands: fails.map((item) => item.fix).filter(Boolean),
      checks: results,
    }, null, 2));
  } else {
    console.log(renderText(mode));
  }

  return fails.length ? 1 : 0;
}

process.exitCode = main();
