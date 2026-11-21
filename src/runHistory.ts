import { invoke, isTauri } from './tauri';

export type RunResult = 'completed' | 'stopped' | 'failed';

export interface RunRecord {
  id: string;
  jobName: string;
  jobFile: string;
  serial: string;
  region: string;
  startedAt: number;
  finishedAt: number;
  durationMs: number;
  result: RunResult;
  reason: string;
  battlesCompleted: number;
  recoveryUsed: Record<string, number>;
  runDir: string;
  error: string | null;
}

export interface RunArtifacts {
  directory: string;
  logFile: string;
  screenshots: string[];
}

export const browserHistoryMessage = '浏览器预览不保存或读取运行记录；请使用 Tauri 桌面版查看真实历史。';

export const runHistoryApi = {
  list: async (limit = 100): Promise<RunRecord[]> => {
    if (!isTauri) return [];
    return invoke<RunRecord[]>('list_run_history', { limit });
  },
  get: async (id: string): Promise<RunRecord> => {
    if (!isTauri) throw new Error(browserHistoryMessage);
    return invoke<RunRecord>('get_run_record', { id });
  },
  artifacts: async (id: string): Promise<RunArtifacts> => {
    if (!isTauri) throw new Error(browserHistoryMessage);
    return invoke<RunArtifacts>('open_run_artifacts', { id });
  },
};

export function formatRunDuration(durationMs: number): string {
  const seconds = Math.max(0, Math.floor(durationMs / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  return hours
    ? `${hours} 小时 ${String(minutes).padStart(2, '0')} 分 ${String(rest).padStart(2, '0')} 秒`
    : `${minutes} 分 ${String(rest).padStart(2, '0')} 秒`;
}
