import { invoke, isTauri } from './tauri';
import type { HugoFgoJob } from './types';

export interface JobDiagnostic {
  path: string;
  message: string;
  wave?: number;
  turn?: number;
}

export interface JobValidation {
  ok: boolean;
  summary: string;
  diagnostics: JobDiagnostic[];
}

function locationLabel(diagnostic: JobDiagnostic) {
  if (diagnostic.wave !== undefined && diagnostic.turn !== undefined) return `第 ${diagnostic.wave} 面 · 回合 ${diagnostic.turn}`;
  if (diagnostic.wave !== undefined) return `第 ${diagnostic.wave} 面`;
  return '作业';
}

export function describeJobDiagnostic(diagnostic: JobDiagnostic) {
  return `${locationLabel(diagnostic)}：${diagnostic.message}${diagnostic.path ? `（${diagnostic.path}）` : ''}`;
}

/** 编译检查覆盖 Schema 与作业结构；执行器尚未支持的能力在这里先被明确拦下。 */
export function inspectUnsupportedJob(job: HugoFgoJob): JobDiagnostic[] {
  const diagnostics: JobDiagnostic[] = [];
  job.waves.forEach((wave, waveIndex) => {
    wave.turns.forEach((turn, turnIndex) => {
      turn.actions.forEach((action, actionIndex) => {
        if (action.secondarySelection) {
          diagnostics.push({
            path: `$.waves[${waveIndex}].turns[${turnIndex}].actions[${actionIndex}].secondarySelection`,
            message: '此技能使用后还需要额外选择，当前无法自动执行；请移除该动作后再运行',
            wave: waveIndex + 1,
            turn: turnIndex + 1,
          });
        }
      });
    });
  });
  return diagnostics;
}

export async function validateJobFile(jobPath: string, job: HugoFgoJob): Promise<JobValidation> {
  const local = inspectUnsupportedJob(job);
  let remote: JobValidation = { ok: true, summary: '', diagnostics: [] };
  if (isTauri) {
    remote = await invoke<JobValidation>('validate_job', { jobPath });
  }
  const diagnostics = [...remote.diagnostics, ...local];
  return {
    ok: remote.ok && diagnostics.length === 0,
    summary: remote.ok
      ? (diagnostics.length ? `发现 ${diagnostics.length} 个执行能力问题` : remote.summary)
      : remote.summary,
    diagnostics,
  };
}
