import { ref } from 'vue';
import { fetchResourceJson, resourceRegion, resourceUrl, type Region } from './resources';
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
let byNameAndClass = new Map<string, AtlasServant>();
let byId = new Map<number, AtlasServant>();
let loadedRegion: Region | null = null;
let loading: Promise<void> | null = null;
let loadingRegion: Region | null = null;

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

function mapUrl(url: string | null | undefined) {
  return url ? resourceUrl(url) : url ?? null;
}

function mapServant(servant: AtlasServant): AtlasServant {
  return {
    ...servant,
    face: mapUrl(servant.face),
    card: mapUrl(servant.card),
    ascensionAssets: servant.ascensionAssets
      ? Object.fromEntries(
          Object.entries(servant.ascensionAssets).map(([stage, assets]) => [
            stage,
            { face: mapUrl(assets.face), card: mapUrl(assets.card) },
          ]),
        )
      : undefined,
    skills: servant.skills.map((skill) => ({ ...skill, icon: mapUrl(skill.icon) })),
  };
}

function toMysticCode(code: AtlasMystic): MysticCode {
  const icons = code.skills.map((skill) => mapUrl(skill.icon) ?? '');
  return {
    id: `atlas-${code.id}`,
    name: code.name,
    figure: mapUrl(code.figure ?? code.maleFigure ?? code.item ?? code.maleItem) ?? '',
    maleFigure: mapUrl(code.maleFigure ?? code.figure ?? code.maleItem ?? code.item) ?? '',
    item: mapUrl(code.item ?? code.maleItem ?? code.figure ?? code.maleFigure) ?? '',
    maleItem: mapUrl(code.maleItem ?? code.item ?? code.maleFigure ?? code.figure) ?? '',
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

export async function loadAtlas(region: Region = resourceRegion.value, force = false): Promise<void> {
  if (!force && loadedRegion === region) return;
  if (loading && loadingRegion === region) return loading;
  loadedRegion = null;
  loadingRegion = region;
  loading = (async () => {
    try {
      const data = await fetchResourceJson<{ servants: AtlasServant[] }>('assets/atlas/index.json');
      const map = new Map<string, AtlasServant>();
      byNameAndClass = new Map();
      byId = new Map();
      const servants = data.servants.map(mapServant);
      servants.forEach((servant) => {
        if (!map.has(servant.name)) map.set(servant.name, servant);
        byNameAndClass.set(`${servant.name}|${servant.classLabel}`, servant);
        byId.set(servant.id, servant);
      });
      atlasByServantName.value = map;
      atlasServants.value = servants;
      loadedRegion = region;
    } catch {
      atlasByServantName.value = new Map();
      atlasServants.value = [];
    }
    try {
      const data = await fetchResourceJson<{ mysticCodes: AtlasMystic[] }>('assets/atlas/mystic/index.json');
      atlasMysticCodes.value = data.mysticCodes.filter((code) => code.figure || code.maleFigure || code.item || code.maleItem).map(toMysticCode);
    } catch {
      atlasMysticCodes.value = [];
    } finally {
      loading = null;
      loadingRegion = null;
    }
  })();
  return loading;
}
