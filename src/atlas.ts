import { ref } from 'vue';
import type { MysticCode, SkillSecondarySelection, SkillTarget } from './types';

export interface AtlasSkill {
  num: number;
  name: string;
  icon: string | null;
  target?: SkillTarget;
  secondarySelection?: SkillSecondarySelection;
  condLimitCount?: number;
  condQuestId?: number;
  condQuestPhase?: number;
  priority?: number;
}

export interface AtlasNoblePhantasm {
  id: number;
  name: string;
  card: string;
  priority?: number;
  condLimitCount?: number;
}

export interface AtlasServant {
  id: number;
  name: string;
  classLabel: string;
  type: string;
  collectionNo: number;
  ascensionAssets?: Record<number, { face: string | null; card: string | null }>;
  face: string | null;
  card: string | null;
  noblePhantasms?: AtlasNoblePhantasm[];
  skills: AtlasSkill[];
}

/** 本地图库索引；静态部署没有该文件时保持为空，界面回落到占位卡。 */
export const atlasByServantName = ref<Map<string, AtlasServant>>(new Map());
export const atlasServants = ref<AtlasServant[]>([]);
export const atlasMysticCodes = ref<MysticCode[]>([]);
const byNameAndClass = new Map<string, AtlasServant>();
const byId = new Map<number, AtlasServant>();
let started = false;

interface AtlasMystic {
  id: number;
  name: string;
  item: string | null;
  maleItem: string | null;
  figure: string | null;
  maleFigure: string | null;
  skills: { num: number; name: string; detail: string; icon: string | null; target?: SkillTarget }[];
  orderChangeSkill?: number;
}

function toMysticCode(code: AtlasMystic): MysticCode {
  const icons = code.skills.map((skill) => skill.icon ?? '');
  return {
    id: `atlas-${code.id}`,
    name: code.name,
    figure: code.figure ?? code.item ?? '',
    maleFigure: code.maleFigure ?? code.figure ?? code.item ?? '',
    item: code.item ?? '',
    maleItem: code.maleItem ?? code.item ?? '',
    skills: code.skills.map(() => 'skill_00400'),
    skillIconUrls: icons,
    skillTargets: code.skills.map((skill) => skill.target ?? 'allAllies'),
    orderChangeSkill: code.orderChangeSkill,
  };
}

/** 同名从者（如美杜莎的 Saber / Lancer / Rider）按职阶区分。 */
export function lookupAtlas(name: string, role?: string): AtlasServant | undefined {
  if (role) {
    const exact = byNameAndClass.get(`${name}|${role}`);
    if (exact) return exact;
  }
  return atlasByServantName.value.get(name);
}

export function lookupAtlasById(id?: number): AtlasServant | undefined {
  return id === undefined ? undefined : byId.get(id);
}

export async function loadAtlas() {
  if (started) return;
  started = true;
  try {
    const response = await fetch('/data/assets/atlas/index.json');
    if (!response.ok) return;
    const data = (await response.json()) as { servants: AtlasServant[] };
    const map = new Map<string, AtlasServant>();
    data.servants.forEach((servant) => {
      if (!map.has(servant.name)) map.set(servant.name, servant);
      byNameAndClass.set(`${servant.name}|${servant.classLabel}`, servant);
      byId.set(servant.id, servant);
    });
    atlasByServantName.value = map;
    atlasServants.value = data.servants;
  } catch {
    // 分享版不包含图库，忽略即可。
  }
  try {
    const response = await fetch('/data/assets/atlas/mystic/index.json');
    if (!response.ok) return;
    const data = (await response.json()) as { mysticCodes: AtlasMystic[] };
    atlasMysticCodes.value = data.mysticCodes.filter((code) => code.figure || code.item).map(toMysticCode);
  } catch {
    // 同上。
  }
}
