import type {
  FallbackStrategy,
  HugoFgoJob,
  JobAction,
  JobFormationSlot,
  JobTurn,
  JobWave,
  MysticCode,
  NpPosition,
  PlanAction,
  PlanSnapshot,
  Servant,
} from './types';
import { emptyServant, standardSkills } from './data';
import { lookupAtlasById, type AtlasServant } from './atlas';
import type { EquipInfo } from './equips';

const fallbackIcons = ['skill_00400', 'skill_00403', 'skill_00700'];

function latestSkillByPriority(skills: AtlasServant['skills'], num: number, priority?: number) {
  const candidates = skills.filter((skill) => skill.num === num && (priority === undefined || skill.priority === priority));
  return candidates.sort((left, right) => (right.priority ?? 0) - (left.priority ?? 0))[0] ?? skills.find((skill) => skill.num === num);
}

export function activeSkillsForLimitCount(skills: AtlasServant['skills'], limitCount = 4, svtId?: number): AtlasServant['skills'] {
  if (svtId === 800100) {
    const priorities = limitCount <= 2 ? [1, 1, 1] : limitCount === 3 ? [3, 7, 8] : [7, 10, 7];
    return priorities.map((priority, index) => latestSkillByPriority(skills, index + 1, priority)).filter((skill): skill is NonNullable<typeof skill> => Boolean(skill));
  }
  if (svtId === 4000100) {
    const priority = limitCount < 3 ? 1 : 2;
    return [1, 2, 3].map((num) => latestSkillByPriority(skills, num, priority)).filter((skill): skill is NonNullable<typeof skill> => Boolean(skill));
  }
  const bySlot = new Map<number, AtlasServant['skills'][number]>();
  skills.forEach((skill) => {
    const previous = bySlot.get(skill.num);
    if (!previous || (skill.priority ?? 0) >= (previous.priority ?? 0)) bySlot.set(skill.num, skill);
  });
  return [...bySlot.values()].sort((left, right) => left.num - right.num).slice(0, 3);
}

export function activeNoblePhantasmForLimitCount(noblePhantasms: NonNullable<AtlasServant['noblePhantasms']>, limitCount = 4, svtId?: number) {
  if (svtId === 800100) {
    const npId = limitCount <= 2 ? 800104 : limitCount === 3 ? 800106 : 800197;
    return noblePhantasms.find((np) => np.id === npId) ?? noblePhantasms[0];
  }
  return [...noblePhantasms].sort((left, right) => (right.priority ?? 0) - (left.priority ?? 0))[0];
}

export function ascensionOptionsFor(member: Servant): number[] {
  if (member.svtId === 4000100) return [1, 3];
  if (member.svtId === 800100) return [1, 3, 4];
  const asset = lookupAtlasById(member.svtId);
  const skills = asset?.skills ?? [];
  const signatures = new Set<string>();
  return [4, 3, 2, 1].filter((limitCount) => {
    const npId = activeNoblePhantasmForLimitCount(asset?.noblePhantasms ?? [], limitCount, member.svtId)?.id ?? '';
    const signature = `${activeSkillsForLimitCount(skills, limitCount, member.svtId).map((skill) => skill.name).join('|')}#${npId}`;
    if (signatures.has(signature)) return false;
    signatures.add(signature);
    return true;
  });
}

function resolveEquip(equips: Map<number, EquipInfo> | undefined, id?: number, name?: string): EquipInfo | undefined {
  if (!equips) return undefined;
  if (id !== undefined) {
    const byId = equips.get(id);
    if (byId) return byId;
  }
  if (name) return [...equips.values()].find((equip) => equip.name === name);
  return undefined;
}

export function createEmptyPlanSnapshot(): PlanSnapshot {
  return {
    turnCountByWave: { 1: 1, 2: 1, 3: 1 },
    enemyCount: 3,
    targetByWave: { 1: 3, 2: 3, 3: 3 },
    actionsBySlot: {},
    npPositionsBySlot: {},
    attackBySlot: {},
    nextId: 1,
  };
}

function memberSlotOf(members: Servant[], id?: string): number | undefined {
  if (!id) return undefined;
  const index = members.findIndex((member) => member.memberInstanceId === id);
  return index === -1 ? undefined : index;
}

function toJobAction(members: Servant[], action: PlanAction): JobAction {
  const jobAction: JobAction = {
    kind: action.kind,
    title: action.title,
    detail: action.detail,
    icon: action.icon,
  };
  if (action.skill !== undefined) jobAction.skill = action.skill;
  if (action.enemyTarget !== undefined) jobAction.enemyTarget = action.enemyTarget;
  if (action.secondarySelection) jobAction.secondarySelection = action.secondarySelection;
  if (action.fromTarget !== undefined) jobAction.fromTarget = action.fromTarget;
  if (action.toTarget !== undefined) jobAction.toTarget = action.toTarget;
  const memberSlot = memberSlotOf(members, action.memberInstanceId);
  if (memberSlot !== undefined) jobAction.memberSlot = memberSlot;
  const playerTargetSlot = memberSlotOf(members, action.playerTarget);
  if (playerTargetSlot !== undefined) jobAction.playerTargetSlot = playerTargetSlot;
  const swapOutSlot = memberSlotOf(members, action.swapOutMemberInstanceId);
  if (swapOutSlot !== undefined) jobAction.swapOutSlot = swapOutSlot;
  const swapInSlot = memberSlotOf(members, action.swapInMemberInstanceId);
  if (swapInSlot !== undefined) jobAction.swapInSlot = swapInSlot;
  return jobAction;
}

function toJobFormation(members: Servant[]): JobFormationSlot[] {
  return members.map((member, slot) => {
    const entry: JobFormationSlot = { slot, name: member.empty ? '' : member.name, role: member.role };
    if (!member.empty && member.svtId !== undefined) entry.svtId = member.svtId;
    if (!member.empty) entry.limitCount = member.limitCount ?? 4;
    if (!member.empty && member.npId !== undefined) entry.npId = member.npId;
    if (member.support) entry.support = true;
    if (member.supportRequired) entry.supportRequired = true;
    if (member.grandRequired) entry.grandRequired = true;
    if (member.ce) entry.ce = member.ce;
    if (member.ceId !== undefined) entry.ceId = member.ceId;
    if (member.grandCeTop || member.grandCeMiddle || member.grandCeBottom) {
      entry.grandCe = {
        top: member.grandCeTop?.name ?? null,
        middle: member.grandCeMiddle?.name ?? null,
        bottom: member.grandCeBottom?.name ?? null,
      };
      entry.grandCeIds = {
        top: member.grandCeTop?.id ?? null,
        middle: member.grandCeMiddle?.id ?? null,
        bottom: member.grandCeBottom?.id ?? null,
      };
    }
    return entry;
  });
}

export function buildJobFile(input: {
  members: Servant[];
  mystic: MysticCode;
  fallback: FallbackStrategy;
  fallbackByWave: Record<number, FallbackStrategy | null>;
  snapshot: PlanSnapshot;
  name?: string;
  base?: HugoFgoJob;
}): HugoFgoJob {
  const { members, mystic, fallback, fallbackByWave, snapshot } = input;
  const waves: JobWave[] = [1, 2, 3].map((waveNumber) => {
    const turnCount = Math.max(1, snapshot.turnCountByWave[waveNumber] ?? 1);
    const turns: JobTurn[] = [];
    for (let turnNumber = 1; turnNumber <= turnCount; turnNumber++) {
      const key = `${waveNumber}-${turnNumber}`;
      const turn: JobTurn = {
        actions: (snapshot.actionsBySlot[key] ?? []).map((action) => toJobAction(members, action)),
      };
      const npPositions: Record<number, NpPosition> = {};
      Object.entries(snapshot.npPositionsBySlot[key] ?? {}).forEach(([memberInstanceId, position]) => {
        const slot = memberSlotOf(members, memberInstanceId);
        if (slot !== undefined) npPositions[slot] = position;
      });
      if (Object.keys(npPositions).length) turn.npPositions = npPositions;
      const attackPlan = snapshot.attackBySlot[key];
      if (attackPlan) {
        const cards = attackPlan.cards.flatMap((card) => {
          const slot = memberSlotOf(members, card.memberInstanceId);
          return slot === undefined ? [] : [{ slot, type: card.type, ...(card.deckIndex !== undefined ? { deckIndex: card.deckIndex } : {}) }];
        });
        turn.attack = { mode: attackPlan.mode, cards };
      }
      turns.push(turn);
    }
    return { turnCount, target: snapshot.targetByWave[waveNumber] ?? 3, turns };
  });

  const strategyByWave: Record<number, FallbackStrategy | null> = {};
  [1, 2, 3].forEach((waveNumber) => {
    if (fallbackByWave[waveNumber]) strategyByWave[waveNumber] = fallbackByWave[waveNumber];
  });

  return {
    ...input.base,
    version: 1,
    name: input.name,
    mysticId: mystic.id,
    enemyCount: snapshot.enemyCount,
    formation: toJobFormation(members),
    strategy: { ...input.base?.strategy, ...fallback },
    strategyByWave: Object.keys(strategyByWave).length ? strategyByWave : undefined,
    waves,
  };
}

export function parseJobFile(text: string): HugoFgoJob {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new Error('不是有效的 JSON 文件');
  }
  if (!raw || typeof raw !== 'object') throw new Error('作业文件内容为空');
  const job = raw as Partial<HugoFgoJob>;
  if (job.version !== 1) throw new Error(`不支持的作业版本：${String(job.version ?? '未标注')}`);
  if (!Array.isArray(job.formation) || !Array.isArray(job.waves)) throw new Error('作业文件缺少编队或关卡数据');
  if (!job.strategy || !Array.isArray(job.strategy.servantOrder)) throw new Error('作业文件缺少选卡策略');
  return job as HugoFgoJob;
}

function fromJobAction(
  jobAction: JobAction,
  members: Servant[],
  waveNumber: number,
  turnNumber: number,
  mystic: MysticCode,
  warnings: string[],
): Omit<PlanAction, 'id'> | null {
  const where = `第 ${waveNumber} 面 · 回合 ${turnNumber}`;
  const memberAt = (slot: number | undefined, label: string): Servant | undefined => {
    if (slot === undefined) return undefined;
    const member = members[slot];
    if (!member || member.empty) {
      warnings.push(`${where}：${label}指向空位（${slot + 1} 号位），动作已跳过`);
      return undefined;
    }
    return member;
  };

  const member = memberAt(jobAction.memberSlot, '动作所属从者');
  if (jobAction.memberSlot !== undefined && !member) return null;
  const playerTarget = memberAt(jobAction.playerTargetSlot, '技能目标');
  const swapOut = memberAt(jobAction.swapOutSlot, '换人下场从者');
  const swapIn = memberAt(jobAction.swapInSlot, '换人上场从者');
  if (jobAction.kind === 'orderChange' && (!swapOut || !swapIn)) {
    warnings.push(`${where}：换人动作缺少有效的双方，动作已跳过`);
    return null;
  }

  let skillKey: string | undefined;
  if (jobAction.kind === 'skill' && member && jobAction.skill !== undefined) skillKey = `servant-${member.memberInstanceId}-${jobAction.skill}`;
  else if (jobAction.kind === 'attack' && member) skillKey = `np-${waveNumber}-${turnNumber}-${member.memberInstanceId}`;
  else if (jobAction.kind === 'master') skillKey = `master-${jobAction.skill ?? 0}`;
  else if (jobAction.kind === 'orderChange') skillKey = `master-${mystic.orderChangeSkill ?? 0}`;
  const skillName = jobAction.kind === 'skill' && member && jobAction.skill !== undefined ? member.skills[jobAction.skill]?.name : undefined;
  const title = skillName && member
    ? `${member.name} · ${skillName}`
    : jobAction.kind === 'attack' && member?.noblePhantasm
      ? `${member.name} · ${member.noblePhantasm.name}`
      : jobAction.title;

  return {
    kind: jobAction.kind,
    title,
    detail: jobAction.detail,
    icon: jobAction.icon,
    skillKey,
    skill: jobAction.skill,
    memberInstanceId: member?.memberInstanceId,
    playerTarget: playerTarget?.memberInstanceId,
    enemyTarget: jobAction.enemyTarget,
    secondarySelection: jobAction.secondarySelection,
    swapOutMemberInstanceId: swapOut?.memberInstanceId,
    swapInMemberInstanceId: swapIn?.memberInstanceId,
    fromTarget: jobAction.fromTarget,
    toTarget: jobAction.toTarget,
  };
}

export function jobToSnapshot(job: HugoFgoJob, members: Servant[], mystic: MysticCode): { snapshot: PlanSnapshot; warnings: string[] } {
  const warnings: string[] = [];
  const snapshot = createEmptyPlanSnapshot();
  snapshot.enemyCount = job.enemyCount === 6 ? 6 : 3;
  let nextId = 1;

  job.waves.slice(0, 3).forEach((wave, waveIndex) => {
    const waveNumber = waveIndex + 1;
    const turnCount = Math.min(9, Math.max(1, wave.turnCount ?? 1));
    snapshot.turnCountByWave[waveNumber] = turnCount;
    snapshot.targetByWave[waveNumber] = wave.target ?? (snapshot.enemyCount === 6 ? 6 : 3);

    for (let turnNumber = 1; turnNumber <= turnCount; turnNumber++) {
      const jobTurn = wave.turns?.[turnNumber - 1];
      if (!jobTurn) continue;
      const key = `${waveNumber}-${turnNumber}`;
      const actions: PlanAction[] = [];
      (jobTurn.actions ?? []).forEach((jobAction) => {
        const action = fromJobAction(jobAction, members, waveNumber, turnNumber, mystic, warnings);
        if (action) actions.push({ ...action, id: nextId++ });
      });
      snapshot.actionsBySlot[key] = actions;

      if (jobTurn.npPositions) {
        const positions: Record<string, NpPosition> = {};
        Object.entries(jobTurn.npPositions).forEach(([slotText, position]) => {
          const member = members[Number(slotText)];
          if (!member || member.empty) {
            warnings.push(`第 ${waveNumber} 面 · 回合 ${turnNumber}：宝具卡序指向不存在的 ${Number(slotText) + 1} 号位，已忽略`);
            return;
          }
          positions[member.memberInstanceId] = position;
        });
        if (Object.keys(positions).length) snapshot.npPositionsBySlot[key] = positions;
      }

      if (jobTurn.attack && jobTurn.attack.mode !== 'strategy') {
        const cards = (jobTurn.attack.cards ?? []).flatMap((card) => {
          const member = members[card.slot];
          if (!member || member.empty) {
            warnings.push(`第 ${waveNumber} 面 · 回合 ${turnNumber}：固定卡序指向空位（${card.slot + 1} 号位），该卡已忽略`);
            return [];
          }
          return [{ memberInstanceId: member.memberInstanceId, type: card.type, ...(card.deckIndex !== undefined ? { deckIndex: card.deckIndex } : {}) }];
        });
        if (cards.length) snapshot.attackBySlot[key] = { mode: jobTurn.attack.mode, cards };
      }
    }
  });

  snapshot.nextId = nextId;
  return { snapshot, warnings };
}

export function applyFormation(
  job: HugoFgoJob,
  current: Servant[],
  catalog: Servant[],
  nextInstanceId: () => string,
  assets?: (name: string, role?: string) => AtlasServant | undefined,
  equips?: Map<number, EquipInfo>,
): { members: Servant[]; warnings: string[] } {
  const members = current.map((member) => ({ ...member }));
  const warnings: string[] = [];
  const grandSlot = (id?: number | null, name?: string | null) => {
    const equip = resolveEquip(equips, id ?? undefined, name ?? undefined);
    if (!equip && !name) return null;
    return { name: equip?.name ?? name ?? '', image: equip?.icon ?? '', id: equip?.id };
  };
  job.formation.forEach((entry) => {
    if (!Number.isInteger(entry.slot) || entry.slot < 0 || entry.slot >= members.length) return;
    const previous = members[entry.slot];
    if (!entry.name) {
      members[entry.slot] = emptyServant(entry.support ?? false);
    } else {
      const known = catalog.find((servant) => servant.name === entry.name && (!entry.role || servant.role === entry.role));
      const asset = assets?.(entry.name, entry.role);
      const assetSkills = activeSkillsForLimitCount(asset?.skills ?? [], entry.limitCount ?? 4, entry.svtId);
      const ascension = asset?.ascensionAssets?.[entry.limitCount ?? 4];
      const noblePhantasm = asset?.noblePhantasms?.find((np) => np.id === entry.npId) ?? activeNoblePhantasmForLimitCount(asset?.noblePhantasms ?? [], entry.limitCount ?? 4, entry.svtId);
      if (known) {
        members[entry.slot] = {
          ...known,
          skills: known.skills.map((skill, index) => ({ ...skill, iconUrl: assetSkills[index]?.icon ?? skill.iconUrl, target: assetSkills[index]?.target ?? skill.target, name: assetSkills[index]?.name, secondarySelection: assetSkills[index]?.secondarySelection ?? skill.secondarySelection })),
          image: ascension?.face ?? asset?.face ?? known.image,
          art: ascension?.card ?? asset?.card ?? known.art,
          memberInstanceId: nextInstanceId(),
          limitCount: entry.limitCount ?? 4,
          npId: noblePhantasm?.id ?? entry.npId,
          noblePhantasm,

          support: entry.support ?? false,
          supportRequired: entry.support ? (entry.supportRequired ?? true) : false,
          grandRequired: entry.grandRequired ?? false,
        };
      } else if (!(previous.name === entry.name && !previous.placeholder)) {
        const skills = assetSkills.length
          ? assetSkills.map((skill, index) => ({
              icon: fallbackIcons[index % fallbackIcons.length],
              iconUrl: skill.icon ?? undefined,
              target: skill.target ?? 'allAllies',
              name: skill.name,
              secondarySelection: skill.secondarySelection,
            }))
          : standardSkills();
        members[entry.slot] = {
          memberInstanceId: nextInstanceId(),
          limitCount: entry.limitCount ?? 4,
          npId: noblePhantasm?.id ?? entry.npId,
          noblePhantasm,

          name: entry.name,
          role: entry.role ?? '',
          image: ascension?.face ?? asset?.face ?? '',
          art: ascension?.card ?? asset?.card ?? '',
          level: '',
          ce: '',
          ceImage: '',
          skills,
          support: entry.support ?? false,
          supportRequired: entry.support ? (entry.supportRequired ?? true) : false,
          grandRequired: entry.grandRequired ?? false,
          placeholder: true,
        };
      }
    }

    const member = members[entry.slot];
    if (member.empty) return;
    if (entry.svtId !== undefined) member.svtId = entry.svtId;
    const equip = resolveEquip(equips, entry.ceId, entry.ce);
    member.ce = equip?.name ?? entry.ce ?? '';
    member.ceImage = equip?.icon ?? '';
    member.ceId = equip?.id ?? entry.ceId;
    if (entry.grandCe || entry.grandCeIds) {
      member.grandCeTop = grandSlot(entry.grandCeIds?.top, entry.grandCe?.top);
      member.grandCeMiddle = grandSlot(entry.grandCeIds?.middle, entry.grandCe?.middle);
      member.grandCeBottom = grandSlot(entry.grandCeIds?.bottom, entry.grandCe?.bottom);
    }
  });
  job.waves.forEach((wave) => wave.turns.forEach((turn) => turn.actions.forEach((action) => {
    if (!action.secondarySelection || action.memberSlot === undefined || action.skill === undefined) return;
    const definition = members[action.memberSlot]?.skills[action.skill];
    if (!definition) return;
    definition.secondarySelection = {
      type: action.secondarySelection.type,
      label: action.secondarySelection.type === 'noblePhantasmCardType' ? '选择宝具卡色' : '技能二次选择',
      options: action.secondarySelection.options ?? [],
    };
  })));
  return { members, warnings };
}
