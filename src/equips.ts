import { ref } from 'vue';

import { fetchResourceJson, resourceRegion, resourceUrl, type Region } from './resources';

export interface EquipInfo {
  id: number;
  name: string;
  rarity: number;
  icon: string | null;
  commonDefault?: boolean;
}

export const equipsById = ref<Map<number, EquipInfo>>(new Map());
export const equipsList = ref<EquipInfo[]>([]);
let loadedRegion: Region | null = null;
let started: Promise<void> | null = null;
let startedRegion: Region | null = null;

export function loadEquips(region: Region = resourceRegion.value, force = false): Promise<void> {
  if (!force && loadedRegion === region) return Promise.resolve();
  if (started && startedRegion === region) return started;
  loadedRegion = null;
  startedRegion = region;
  started = (async () => {
    try {
      const data = await fetchResourceJson<Record<string, EquipInfo>>('equips-by-id.json');
      const map = new Map<number, EquipInfo>();
      Object.values(data).forEach((equip) => map.set(equip.id, { ...equip, icon: equip.icon ? resourceUrl(equip.icon) : null }));
      equipsById.value = map;
      equipsList.value = [...map.values()].sort((left, right) => right.rarity - left.rarity || left.id - right.id);
      loadedRegion = region;
    } catch {
      equipsById.value = new Map();
      equipsList.value = [];
    } finally {
      started = null;
      startedRegion = null;
    }
  })();
  return started;
}

export function findEquip(id?: number, name?: string): EquipInfo | undefined {
  if (id !== undefined) {
    const byId = equipsById.value.get(id);
    if (byId) return byId;
  }
  if (name) return equipsList.value.find((equip) => equip.name === name);
  return undefined;
}
