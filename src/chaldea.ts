import type { FallbackStrategy, HugoFgoJob, JobAction, JobCard, JobFormationSlot, JobTurn, JobWave, NpPosition, SelectedSkillOption } from './types';
import type { EquipInfo } from './equips';

interface SecondarySkill {
  num: number;
  script?: { SelectAddInfo?: { title?: string; btn?: { name?: string; imageUrl?: string }[] }[] };
  functions?: { buffs?: { type?: string }[] }[];
}
interface SecondaryServant {
  id: number;
  skills: SecondarySkill[];
  noblePhantasms?: { card: string }[];
}
export type SecondaryCatalog = Record<number, SecondaryServant>;

export interface ChaldeaTeam {
  id: number;
  questId?: number;
  names?: string[];
  data: ChaldeaData;
}

interface ChaldeaServant {
  svtId: number;
  limitCount?: number;
  tdId?: number;
  supportType?: string;
  grandSvt?: boolean;
  ceId?: number;
  equip1?: { id: number };
  equip2?: { id: number };
  equip3?: { id: number };
}

interface ChaldeaAction {
  type: string;
  svt?: number;
  skill?: number;
  options?: { playerTarget?: number; enemyTarget?: number };
  attacks?: { svt: number; card: number; isTD: boolean; cardType: number }[];
}

interface ChaldeaData {
  team?: {
    mysticCode?: { mysticCodeId?: number };
    onFieldSvts?: (ChaldeaServant | null)[];
    backupSvts?: (ChaldeaServant | null)[];
  };
  actions?: ChaldeaAction[];
  delegate?: {
    replaceMemberIndexes?: [number, number][];
    skillActSelectSelections?: (number | null)[];
    tdTypeChanges?: number[];
  };
}

export interface ChaldeaServantInfo {
  id: number;
  name: string;
  classLabel: string;
}

// Chaldea 的卡色在不同版本里可能是数字（1/2/3）或字符串（arts/buster/quick）。
const cardTypes: Record<string | number, JobCard['type']> = {
  1: 'arts',
  2: 'buster',
  3: 'quick',
  arts: 'arts',
  buster: 'buster',
  quick: 'quick',
};
const localIcons = ['skill_00400', 'skill_00403', 'skill_00700'];

export function parseChaldeaRef(input: string): number {
  const text = input.trim();
  if (/^\d+$/.test(text)) return Number(text);
  const match =
    text.match(/(?:[?&](?:id|team|teamId)=|\/share\/|\/teams\/)(\d+)/i) ??
    text.match(/(\d{3,})/);
  if (!match) throw new Error('未能从链接中识别队伍编号，请粘贴分享链接或直接输入 Team ID');
  return Number(match[1]);
}

async function decodePayload(raw: unknown): Promise<ChaldeaData> {
  if (raw && typeof raw === 'object') return raw as ChaldeaData;
  if (typeof raw !== 'string') throw new Error('作业数据为空');
  let content = raw.startsWith('G') ? raw.slice(1) : raw;
  if (!content.startsWith('H4s')) {
    try {
      return JSON.parse(raw) as ChaldeaData;
    } catch {
      throw new Error('不支持的作业编码格式');
    }
  }
  // 分享数据可能是 base64url（含 - 和 _），atob 不接受，先转成标准 base64 并补齐。
  let base64 = content.replace(/-/g, '+').replace(/_/g, '/');
  while (base64.length % 4) base64 += '=';
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
  if (typeof DecompressionStream === 'undefined') throw new Error('当前浏览器不支持解压作业数据，请使用最新版 Chrome / Edge');
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
  const text = await new Response(stream).text();
  return JSON.parse(text) as ChaldeaData;
}

export async function fetchChaldeaTeam(id: number): Promise<ChaldeaTeam> {
  let response: Response;
  try {
    response = await fetch(`https://api.chaldea.center/api/v1/teams/${id}`, { headers: { Accept: 'application/json' } });
  } catch {
    throw new Error('无法连接 Chaldea 服务器，请检查网络或稍后再试');
  }
  if (response.status === 404) throw new Error(`没有找到编号为 ${id} 的公开作业`);
  if (!response.ok) throw new Error(`读取失败：HTTP ${response.status}`);
  const row = (await response.json()) as {
    id?: number;
    quest_id?: number;
    questId?: number;
    data?: unknown;
    decoded_data?: ChaldeaData | null;
  };
  const data = row.decoded_data ?? (await decodePayload(row.data));
  return { id: row.id ?? id, questId: row.quest_id ?? row.questId, data };
}

let catalogPromise: Promise<Record<number, ChaldeaServantInfo>> | null = null;

export function loadChaldeaCatalog(): Promise<Record<number, ChaldeaServantInfo>> {
  if (!catalogPromise) {
    catalogPromise = fetch('/data/servants-by-id.json')
      .then((response) => (response.ok ? (response.json() as Promise<Record<number, ChaldeaServantInfo>>) : {}))
      .catch(() => ({}));
  }
  return catalogPromise;
}

let secondaryCatalogPromise: Promise<SecondaryCatalog> | null = null;
export function loadSecondaryCatalog(): Promise<SecondaryCatalog> {
  if (!secondaryCatalogPromise) {
    secondaryCatalogPromise = fetch('/data/atlas-nice-cn.json')
      .then((response) => (response.ok ? (response.json() as Promise<SecondaryServant[]>) : []))
      .then((servants) => Object.fromEntries(servants.map((servant) => [servant.id, servant])))
      .catch(() => ({}));
  }
  return secondaryCatalogPromise;
}

function closeTurn(current: { actions: JobAction[]; npPositions: Record<number, NpPosition>; cards: JobCard[] }): JobTurn {
  const turn: JobTurn = { actions: current.actions };
  if (current.cards.length) turn.attack = { mode: 'prefer', cards: current.cards };
  if (Object.keys(current.npPositions).length) turn.npPositions = current.npPositions;
  return turn;
}

export function convertChaldeaTeam(
  team: ChaldeaTeam,
  catalog: Record<number, ChaldeaServantInfo>,
  equips?: Map<number, EquipInfo>,
  secondaryCatalog: SecondaryCatalog = {},
): HugoFgoJob {
  const data = team.data ?? {};
  const onField = data.team?.onFieldSvts ?? [];
  const backup = data.team?.backupSvts ?? [];
  const all = [...onField, ...backup];
  const servantName = (svtId: number) => catalog[svtId]?.name ?? `未知从者 ${svtId}`;

  const formation = all.map((member, slot) => {
    if (!member) return { slot, name: '', role: '' };
    const info = catalog[member.svtId];
    const entry: JobFormationSlot = {
      slot,
      svtId: member.svtId,
      limitCount: member.limitCount ?? 4,
      npId: member.tdId,
      name: servantName(member.svtId),
      role: info?.classLabel ?? '',
    };
    if (member.supportType && member.supportType !== 'none') {
      entry.support = true;
      entry.supportRequired = true;
    }
    if (member.grandSvt) entry.grandRequired = true;
    const ceId = member.ceId ?? member.equip1?.id;
    if (ceId) {
      entry.ceId = ceId;
      const equip = equips?.get(ceId);
      if (equip) entry.ce = equip.name;
    }
    if (member.grandSvt) {
      const ids = [member.equip1?.id ?? null, member.equip2?.id ?? null, member.equip3?.id ?? null];
      entry.grandCeIds = { top: ids[0], middle: ids[1], bottom: ids[2] };
      entry.grandCe = {
        top: ids[0] ? equips?.get(ids[0])?.name ?? null : null,
        middle: ids[1] ? equips?.get(ids[1])?.name ?? null : null,
        bottom: ids[2] ? equips?.get(ids[2])?.name ?? null : null,
      };
    }
    return entry;
  });

  const frontline = [0, 1, 2];
  const replacements = [...(data.delegate?.replaceMemberIndexes ?? [])];
  const turnEntries: { turn: JobTurn; target: number }[] = [];
  let current: { actions: JobAction[]; npPositions: Record<number, NpPosition>; cards: JobCard[] } = { actions: [], npPositions: {}, cards: [] };
  let target = 3;

  const slotName = (slot: number) => formation[slot]?.name || `槽位 ${slot + 1}`;
  const pushAction = (action: JobAction) => {
    current.actions.push(action);
  };

  (data.actions ?? []).forEach((action) => {
    if (action.type === 'skill') {
      if (action.svt === undefined) {
        if (action.skill === 2 && replacements.length) {
          const [outIndex, inIndex] = replacements.shift()!;
          const outSlot = frontline[outIndex];
          const inSlot = 3 + inIndex;
          pushAction({
            kind: 'orderChange',
            title: `换人 · ${slotName(outSlot)} → ${slotName(inSlot)}`,
            detail: `${slotName(outSlot)} 下场，${slotName(inSlot)} 上场`,
            icon: 'skill_00306',
            skill: action.skill,
            swapOutSlot: outSlot,
            swapInSlot: inSlot,
          });
          frontline[outIndex] = inSlot;
          return;
        }
        pushAction({
          kind: 'master',
          title: `御主礼装 · 技能 ${(action.skill ?? 0) + 1}`,
          detail: '御主礼装技能',
          icon: 'skill_00400',
          skill: action.skill ?? 0,
        });
        return;
      }
      const targetSlot = frontline[action.options?.playerTarget ?? 0];
      const skillSlot = frontline[action.svt] ?? action.svt;
      pushAction({
        kind: 'skill',
        title: `${slotName(skillSlot)} · ${(action.skill ?? 0) + 1} 技能`,
        detail: `目标：${slotName(targetSlot ?? frontline[0])}`,
        icon: localIcons[(action.skill ?? 0) % localIcons.length],
        skill: action.skill,
        memberSlot: skillSlot,
        playerTargetSlot: targetSlot,
      });
      return;
    }

    if (action.type !== 'attack') return;
    target = Math.min(3, Math.max(1, (action.options?.enemyTarget ?? 0) + 1));
    const attacks = action.attacks ?? [];
    attacks.forEach((attack, index) => {
      const attackSlot = frontline[attack.svt] ?? attack.svt;
      if (attack.isTD) {
        pushAction({
          kind: 'attack',
          title: `${slotName(attackSlot)} · 宝具`,
          detail: `宝具释放卡序：${index + 1}`,
          icon: 'skill_00400',
          memberSlot: attackSlot,
        });
        current.npPositions[attackSlot] = Math.min(3, index + 1) as NpPosition;
        return;
      }
      const type = cardTypes[attack.cardType];
      if (type && attack.card >= 0) current.cards.push({ slot: attackSlot, type, deckIndex: attack.card });
    });
    turnEntries.push({ turn: closeTurn(current), target });
    current = { actions: [], npPositions: {}, cards: [] };
  });
  if (current.actions.length || current.cards.length) turnEntries.push({ turn: closeTurn(current), target });

  const warnings: string[] = [];
  const skillActions = turnEntries.flatMap((entry) => entry.turn.actions).filter((action) => action.kind === 'skill');
  const skillDefinition = (action: JobAction) => {
    const svtId = action.memberSlot === undefined ? undefined : formation[action.memberSlot]?.svtId;
    return svtId === undefined ? undefined : secondaryCatalog[svtId]?.skills.filter((skill) => skill.num === (action.skill ?? 0) + 1).at(-1);
  };
  const setSecondary = (action: JobAction, selection: SelectedSkillOption) => {
    action.secondarySelection = selection;
    action.detail += ` · ${selection.label}`;
  };

  let selectionActionIndex = 0;
  (data.delegate?.skillActSelectSelections ?? []).forEach((selected) => {
    let actionIndex = -1;
    let definition: SecondarySkill | undefined;
    for (let index = selectionActionIndex; index < skillActions.length; index++) {
      const candidate = skillDefinition(skillActions[index]);
      if (candidate?.script?.SelectAddInfo?.length) {
        actionIndex = index;
        definition = candidate;
        break;
      }
    }
    if (actionIndex < 0) {
      actionIndex = Math.min(selectionActionIndex, skillActions.length - 1);
      warnings.push(`${skillActions[actionIndex]?.title ?? '技能'}：Chaldea 未提供选项文本，请核对二次选择`);
    }
    const action = skillActions[actionIndex];
    if (!action) return;
    selectionActionIndex = actionIndex + 1;
    const select = definition?.script?.SelectAddInfo?.[0];
    const options = select?.btn?.length
      ? select.btn.map((button, index) => ({ value: index, label: button.name || `选项 ${index + 1}`, ...(button.imageUrl ? { imageUrl: button.imageUrl } : {}) }))
      : Array.from({ length: Math.max(2, (selected ?? 0) + 1) }, (_, index) => ({ value: index, label: `选项 ${index + 1}（待核对）` }));
    const optionIndex = selected !== null && options[selected] ? selected : 0;
    setSecondary(action, {
      type: 'selectAddInfo',
      index: optionIndex,
      value: options[optionIndex].value,
      label: options[optionIndex].label,
      options,
    });
  });

  let tdActionIndex = 0;
  (data.delegate?.tdTypeChanges ?? []).forEach((selected) => {
    let actionIndex = -1;
    for (let index = tdActionIndex; index < skillActions.length; index++) {
      const definition = skillDefinition(skillActions[index]);
      if (definition?.functions?.some((func) => func.buffs?.some((buff) => buff.type === 'tdTypeChange'))) {
        actionIndex = index;
        break;
      }
    }
    if (actionIndex < 0) {
      actionIndex = Math.min(tdActionIndex, skillActions.length - 1);
      warnings.push(`${skillActions[actionIndex]?.title ?? '技能'}：宝具卡色变更技能需要人工核对`);
    }
    const action = skillActions[actionIndex];
    if (!action) return;
    tdActionIndex = actionIndex + 1;
    const type = cardTypes[selected];
    if (!type) {
      warnings.push(`${action.title}：无法识别宝具卡色 ${selected}`);
      return;
    }
    const options: { value: JobCard['type']; label: string }[] = [
      { value: 'arts', label: '蓝卡宝具' },
      { value: 'buster', label: '红卡宝具' },
      { value: 'quick', label: '绿卡宝具' },
    ];
    const optionIndex = options.findIndex((option) => option.value === type);
    setSecondary(action, { type: 'noblePhantasmCardType', index: optionIndex, value: type, label: options[optionIndex].label, options });
  });

  // Chaldea 不记录分面：第 1/2/3 个回合分别放到第 1/2/3 面，超出的留在第 3 面。
  const defaultTarget = turnEntries[0]?.target ?? 3;
  const waves: JobWave[] = [0, 1, 2].map((waveIndex) => {
    const entries = turnEntries.filter((_, index) => Math.min(index, 2) === waveIndex);
    return {
      turnCount: Math.max(1, entries.length),
      target: entries[0]?.target ?? defaultTarget,
      turns: entries.length ? entries.map((entry) => entry.turn) : [{ actions: [] }],
    };
  });

  const names = formation.filter((entry) => entry.name).map((entry) => entry.name).slice(0, 3);
  const mysticCodeId = data.team?.mysticCode?.mysticCodeId;
  const fallbackStrategy: FallbackStrategy = {
    allowNp: true,
    servantOrder: formation.slice(0, 3).map((entry) => entry.name).filter(Boolean),
    cards: 'buster',
    colorPriority: ['buster', 'arts', 'quick'],
    strategyOrder: ['servant', 'cards', 'crit'],
  };

  return {
    version: 1,
    name: names.length ? `${names.join(' / ')} · Chaldea #${team.id}` : `Chaldea #${team.id}`,
    mysticId: mysticCodeId ? `atlas-${mysticCodeId}` : 'default',
    enemyCount: 3,
    formation,
    strategy: fallbackStrategy,
    waves,
    ...(warnings.length ? { warnings } : {}),
    provenance: {
      source: 'chaldea',
      teamId: team.id,
      questId: team.questId,
      note: '测试用途：原始记录仅作对照，HugoFgo 运行时不读取，导出不会写回',
      actions: data.actions,
      delegate: data.delegate ?? {},
    },
  };
}
