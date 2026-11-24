export type RunPhase = 'idle' | 'running' | 'stopping' | 'completed' | 'stopped' | 'failed';

export interface RunEvent {
  run_id: string;
  type: string;
  timestamp: number;
  data: Record<string, unknown>;
}

export interface RunState {
  phase: RunPhase;
  runId: string;
  serial: string;
  jobName: string;
  jobPath: string;
  startedAtMs: number;
  screen: string;
  wave: number;
  turn: number;
  action: string;
  actionDetail: string;
  completedRuns: number;
  recoveryUsed: Record<string, number>;
  logDir: string;
  error: string;
  notice: string;
  channel: string;
  exitCode: number | null;
}

export function emptyRunState(): RunState {
  return {
    phase: 'idle', runId: '', serial: '', jobName: '', jobPath: '', startedAtMs: 0,
    screen: '', wave: 1, turn: 1, action: '', actionDetail: '', completedRuns: 0,
    recoveryUsed: {}, logDir: '', error: '', notice: '', channel: 'scrcpy', exitCode: null,
  };
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

function integer(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

export function applyRunEvent(state: RunState, event: RunEvent): RunState {
  if (state.runId && event.run_id && state.runId !== event.run_id) return state;
  const data = event.data ?? {};
  const next: RunState = {
    ...state,
    runId: event.run_id || state.runId,
    wave: integer(data.wave) ?? state.wave,
    turn: integer(data.turn) ?? state.turn,
    logDir: text(data.run_dir) ?? text(data.evidence_dir) ?? state.logDir,
  };
  if (event.type === 'run_started') {
    next.phase = 'running';
    next.serial = text(data.serial) ?? state.serial;
    next.startedAtMs ||= event.timestamp * 1000;
    next.channel = text(data.channel) ?? state.channel;
    if (!next.jobName && next.jobPath) {
      next.jobName = next.jobPath.split(/[\\/]/).pop()?.replace(/\.json$/i, '') ?? '';
    }
  } else if (event.type === 'channel_changed') {
    next.channel = text(data.to) ?? state.channel;
    next.notice = text(data.message) ?? `设备通道已切换为 ${next.channel}`;
  } else if (event.type === 'warning') {
    next.notice = text(data.message) ?? text(data.reason) ?? state.notice;
  } else if (event.type === 'state_changed') {
    next.phase = 'running';
    next.screen = text(data.state) ?? state.screen;
  } else if (event.type === 'action') {
    next.phase = 'running';
    next.action = text(data.control) ?? state.action;
    next.actionDetail = text(data.detail) ?? text(data.reason) ?? '已发送输入，等待画面确认';
  } else if (event.type === 'action_confirmed') {
    next.phase = 'running';
    next.actionDetail = `画面已确认${text(data.stage) ? ` · ${text(data.stage)}` : ''}`;
  } else if (event.type === 'card_batch_submitted') {
    next.phase = 'running';
    next.action = '选择指令卡';
    const interval = data.interval_ms ?? data.configured_interval_ms;
    next.actionDetail = `已提交卡批次${typeof interval === 'number' ? ` · 间隔 ${Math.round(interval)} ms` : ''}，等待实际受理`;
  } else if (event.type === 'turn_started') {
    next.phase = 'running';
    next.wave = integer(data.wave) ?? next.wave;
    next.turn = integer(data.turn) ?? next.turn;
  } else if (event.type === 'wave_started') {
    next.phase = 'running';
    next.wave = integer(data.wave) ?? next.wave;
    next.turn = 1;
  } else if (event.type === 'recovery_used') {
    const item = text(data.item);
    const count = integer(data.count);
    if (item && count !== undefined) next.recoveryUsed = { ...state.recoveryUsed, [item]: count };
  } else if (event.type === 'battle_completed') {
    next.phase = 'running';
    next.completedRuns = integer(data.completed_runs) ?? state.completedRuns;
  } else if (event.type === 'drain_started') {
    next.phase = 'running';
    next.completedRuns = integer(data.completed_runs) ?? state.completedRuns;
    next.notice = '固定场数已完成，正在清空剩余 AP；此阶段不使用恢复道具';
  } else if (event.type === 'finished') {
    next.phase = 'completed';
    next.completedRuns = integer(data.completed_runs) ?? state.completedRuns;
    next.error = '';
  } else if (event.type === 'stopped') {
    next.phase = 'stopped';
    next.error = '';
  } else if (event.type === 'failed') {
    next.phase = 'failed';
    next.error = text(data.message) ?? text(data.reason) ?? '执行器异常退出';
    next.exitCode = integer(data.exit_code) ?? state.exitCode;
  }
  return next;
}
