import { ref } from 'vue';

export interface EquipInfo {
  id: number;
  name: string;
  rarity: number;
  icon: string | null;
  commonDefault?: boolean;
}

export const equipsById = ref<Map<number, EquipInfo>>(new Map());
export const equipsList = ref<EquipInfo[]>([]);
let started: Promise<void> | null = null;

export function loadEquips(): Promise<void> {
  if (started) return started;
  started = (async () => {
    try {
      const response = await fetch('/data/equips-by-id.json');
      if (!response.ok) return;
      const data = (await response.json()) as Record<string, EquipInfo>;
      const map = new Map<number, EquipInfo>();
      Object.values(data).forEach((equip) => map.set(equip.id, equip));
      equipsById.value = map;
      equipsList.value = [...map.values()].sort((left, right) => right.rarity - left.rarity || left.id - right.id);
    } catch {
      // 分享版不含礼装图库，保持为空。
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
