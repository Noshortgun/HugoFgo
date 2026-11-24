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

function inspectSlotReferences(job: HugoFgoJob): JobDiagnostic[] {
  const diagnostics: JobDiagnostic[] = [];
  const formation = new Map(job.formation.map((entry) => [entry.slot, entry.name.trim()]));
  const specified = (slot: number | undefined) => slot !== undefined && Boolean(formation.get(slot));
  const add = (path: string, message: string, wave?: number, turn?: number) => {
    diagnostics.push({ path, message, wave, turn });
  };
  const checkSlot = (slot: number | undefined, path: string, label: string, wave?: number, turn?: number) => {
    if (slot !== undefined && !specified(slot)) add(path, `${label}引用了空槽位：${slot} 号位没有指定从者`, wave, turn);
  };
  const checkStrategy = (strategy: HugoFgoJob['strategy'], path: string, wave?: number) => {
    (strategy.servantOrderSlots ?? []).forEach((slot, index) => {
      checkSlot(slot, `${path}.servantOrderSlots[${index}]`, '补刀策略', wave);
    });
  };

  checkStrategy(job.strategy, '$.strategy');
  Object.entries(job.strategyByWave ?? {}).forEach(([wave, strategy]) => {
    if (strategy) checkStrategy(strategy, `$.strategyByWave.${wave}`, Number(wave));
  });
  job.waves.forEach((wave, waveIndex) => {
    wave.turns.forEach((turn, turnIndex) => {
      const turnPath = `$.waves[${waveIndex}].turns[${turnIndex}]`;
      turn.actions.forEach((action, actionIndex) => {
        const actionPath = `${turnPath}.actions[${actionIndex}]`;
        if ((action.kind === 'skill' || action.kind === 'attack') && action.memberSlot === undefined) {
          add(`${actionPath}.memberSlot`, action.kind === 'skill' ? '从者技能必须指定 memberSlot' : '宝具攻击必须指定 memberSlot', waveIndex + 1, turnIndex + 1);
        }
        (['memberSlot', 'playerTargetSlot', 'swapOutSlot', 'swapInSlot'] as const).forEach((key) => {
          checkSlot(action[key], `${actionPath}.${key}`, '动作', waveIndex + 1, turnIndex + 1);
        });
      });
      Object.keys(turn.npPositions ?? {}).forEach((slot) => {
        checkSlot(Number(slot), `${turnPath}.npPositions.${slot}`, '宝具卡序', waveIndex + 1, turnIndex + 1);
      });
      turn.attack?.cards.forEach((card, index) => {
        checkSlot(card.slot, `${turnPath}.attack.cards[${index}].slot`, '普通指令卡', waveIndex + 1, turnIndex + 1);
      });
    });
  });
  return diagnostics;
}

/** 编译检查覆盖 Schema 与作业结构；执行器尚未支持的能力在这里先被明确拦下。 */
export function inspectUnsupportedJob(job: HugoFgoJob): JobDiagnostic[] {
  const diagnostics: JobDiagnostic[] = inspectSlotReferences(job);
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
  const diagnostics = [...remote.diagnostics, ...local].filter((diagnostic, index, all) => (
    all.findIndex((item) => item.path === diagnostic.path && item.message === diagnostic.message) === index
  ));
  return {
    ok: remote.ok && diagnostics.length === 0,
    summary: remote.ok
      ? (diagnostics.length ? `发现 ${diagnostics.length} 个执行能力问题` : remote.summary)
      : remote.summary,
    diagnostics,
  };
}
