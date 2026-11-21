import { listen, type UnlistenFn } from '@tauri-apps/api/event';
import type { Region } from './resources';
import type { RunSettings } from './runSettings';
import { invoke, isTauri } from './tauri';
import { emptyRunState, type RunEvent, type RunPhase, type RunState } from './runState';

interface RunStatusPayload {
  running: boolean;
  run_id: string;
  serial: string;
  job_name: string;
  job_path: string;
  started_at_ms: number;
  status: RunPhase;
  screen: string;
  wave: number;
  turn: number;
  action: string;
  completed_runs: number;
  recovery_used: Record<string, number>;
  log_dir: string;
  error: string;
  exit_code: number | null;
}

export interface StartRunInput {
  serial: string;
  jobPath: string;
  settings: RunSettings;
  region: Region;
}

export const browserRunDisabledReason = '浏览器预览不能启动执行器；请使用 Tauri 桌面版运行作业';
export const fakeRunRequested = import.meta.env.VITE_HUGOFGO_FAKE_RUN === '1';

function fromStatus(status: RunStatusPayload): RunState {
  return {
    ...emptyRunState(),
    phase: status.status,
    runId: status.run_id,
    serial: status.serial,
    jobName: status.job_name,
    jobPath: status.job_path,
    startedAtMs: status.started_at_ms,
    screen: status.screen,
    wave: status.wave,
    turn: status.turn,
    action: status.action,
    completedRuns: status.completed_runs,
    recoveryUsed: status.recovery_used,
    logDir: status.log_dir,
    error: status.error,
    exitCode: status.exit_code,
  };
}

export const runApi = {
  start: async (input: StartRunInput): Promise<RunState> => {
    if (!isTauri) throw new Error(browserRunDisabledReason);
    const status = await invoke<RunStatusPayload>('start_run', {
      serial: input.serial,
      jobPath: input.jobPath,
      settings: input.settings,
      region: input.region,
    });
    return fromStatus(status);
  },
  stop: async (): Promise<RunState> => {
    if (!isTauri) throw new Error(browserRunDisabledReason);
    return fromStatus(await invoke<RunStatusPayload>('stop_run'));
  },
  getState: async (): Promise<RunState> => {
    if (!isTauri) return emptyRunState();
    return fromStatus(await invoke<RunStatusPayload>('get_run_state'));
  },
  subscribe: (handler: (event: RunEvent) => void): Promise<UnlistenFn> => {
    if (!isTauri) return Promise.resolve(() => undefined);
    return listen<RunEvent>('run-event', (event) => handler(event.payload));
  },
};
