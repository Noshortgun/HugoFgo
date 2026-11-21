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
  exitCode: number | null;
}

export function emptyRunState(): RunState {
  return {
    phase: 'idle', runId: '', serial: '', jobName: '', jobPath: '', startedAtMs: 0,
    screen: '', wave: 1, turn: 1, action: '', actionDetail: '', completedRuns: 0,
    recoveryUsed: {}, logDir: '', error: '', exitCode: null,
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
  } else if (event.type === 'state_changed') {
    next.phase = 'running';
    next.screen = text(data.state) ?? state.screen;
  } else if (event.type === 'action') {
    next.phase = 'running';
    next.action = text(data.control) ?? state.action;
    next.actionDetail = text(data.detail) ?? text(data.reason) ?? '';
  } else if (event.type === 'turn_started' || event.type === 'wave_started') {
    next.phase = 'running';
  } else if (event.type === 'recovery_used') {
    const item = text(data.item);
    const count = integer(data.count);
    if (item && count !== undefined) next.recoveryUsed = { ...state.recoveryUsed, [item]: count };
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
