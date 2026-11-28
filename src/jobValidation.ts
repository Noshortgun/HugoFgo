import jobSchema from '../schemas/job-v1.schema.json';
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

type ActionSlotReferenceKey = 'memberSlot' | 'playerTargetSlot' | 'swapOutSlot' | 'swapInSlot';

const slotReferenceRules = jobSchema['x-slot-reference-rules'];
const actionSlotReferenceKeys = slotReferenceRules.actionReferenceKeys as ActionSlotReferenceKey[];
const anyServantReferenceKeys = new Set<string>(slotReferenceRules.anyServantReferenceKeys as string[]);
const memberSlotRequiredActions = new Set<string>(slotReferenceRules.memberSlotRequiredActions);

function locationLabel(diagnostic: JobDiagnostic) {
  if (diagnostic.wave !== undefined && diagnostic.turn !== undefined) return `第 ${diagnostic.wave} 面 · 回合 ${diagnostic.turn}`;
  if (diagnostic.wave !== undefined) return `第 ${diagnostic.wave} 面`;
  return '作业';
}

export function describeJobDiagnostic(diagnostic: JobDiagnostic) {
  return `${locationLabel(diagnostic)}：${diagnostic.message}${diagnostic.path ? `（${diagnostic.path}）` : ''}`;
}

export function dedupeJobDiagnostics(diagnostics: JobDiagnostic[]): JobDiagnostic[] {
  return diagnostics.filter((diagnostic, index, all) => (
    all.findIndex((item) => item.path === diagnostic.path && item.message === diagnostic.message) === index
  ));
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
  const supports = job.formation
    .map((entry, index) => ({ entry, index }))
    .filter(({ entry }) => entry.support);
  if (supports.length > 1) {
    const second = supports[1];
    add(`$.formation[${second.index}].support`,
      `编队只能有一个助战槽；出现 ${supports.length} 个（槽位 ${supports.map(({ entry }) => entry.slot).join('、')}）`);
  }
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
        if (memberSlotRequiredActions.has(action.kind) && action.memberSlot === undefined) {
          add(`${actionPath}.memberSlot`, action.kind === 'skill' ? '从者技能必须指定 memberSlot' : '宝具攻击必须指定 memberSlot', waveIndex + 1, turnIndex + 1);
        }
        actionSlotReferenceKeys.forEach((key) => {
          if (anyServantReferenceKeys.has(key)) return;
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
        const selection = action.secondarySelection;
        if (!selection) return;
        const base = `$.waves[${waveIndex}].turns[${turnIndex}].actions[${actionIndex}].secondarySelection`;
        const add = (path: string, message: string) => diagnostics.push({
          path, message, wave: waveIndex + 1, turn: turnIndex + 1,
        });
        if (action.kind !== 'skill') add(base, '技能追加选择只能挂在从者技能上');
        if (selection.type === 'criticalStarConsumption') add(`${base}.type`, '没有对应真实技能的追加选择类型');
        const options = selection.options ?? [];
        if (!options.length) {
          add(`${base}.options`, '技能追加选择缺少候选选项，请重新导入或编辑该技能');
          return;
        }
        if (selection.index < 0 || selection.index >= options.length) {
          add(`${base}.index`, `索引 ${selection.index} 没有对应候选选项`);
        } else if (options[selection.index].value !== selection.value) {
          add(`${base}.value`, '所选值与索引对应的候选选项不一致');
        }
        if (selection.type === 'noblePhantasmCardType'
          && options.some((option) => typeof option.value === 'string')) {
          add(`${base}.options`, '旧字符串卡色值不再受支持，请重新选择宝具变体');
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
  const diagnostics = dedupeJobDiagnostics([...remote.diagnostics, ...local]);
  return {
    ok: remote.ok && diagnostics.length === 0,
    summary: remote.ok
      ? (diagnostics.length ? `发现 ${diagnostics.length} 个执行能力问题` : remote.summary)
      : remote.summary,
    diagnostics,
  };
}
