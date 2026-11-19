import { ref } from 'vue';
import type { EquipInfo } from './equips';
import type { Servant } from './types';

function readList(key: string): (string | number)[] {
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? '[]');
    return Array.isArray(value) ? value.filter((item) => typeof item === 'string' || typeof item === 'number') : [];
  } catch {
    return [];
  }
}

function writeList(key: string, value: (string | number)[]) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 隐私模式或存储已满时保留当前会话状态。
  }
}

export const favoriteServantKeys = ref<string[]>(readList('hugofgo.favoriteServants') as string[]);
const customCommonEquipIds = ref<number[]>(readList('hugofgo.commonEquips') as number[]);
const excludedDefaultEquipIds = ref<number[]>(readList('hugofgo.uncommonDefaultEquips') as number[]);

export function servantPreferenceKey(servant: Pick<Servant, 'svtId' | 'name' | 'role'>): string {
  return servant.svtId === undefined ? `${servant.name}|${servant.role}` : String(servant.svtId);
}

export function isFavoriteServant(servant: Pick<Servant, 'svtId' | 'name' | 'role'>): boolean {
  return favoriteServantKeys.value.includes(servantPreferenceKey(servant));
}

export function toggleFavoriteServant(servant: Pick<Servant, 'svtId' | 'name' | 'role'>) {
  const key = servantPreferenceKey(servant);
  favoriteServantKeys.value = isFavoriteServant(servant)
    ? favoriteServantKeys.value.filter((item) => item !== key)
    : [...favoriteServantKeys.value, key];
  writeList('hugofgo.favoriteServants', favoriteServantKeys.value);
}

export function isCommonEquip(equip: EquipInfo): boolean {
  if (excludedDefaultEquipIds.value.includes(equip.id)) return false;
  return Boolean(equip.commonDefault) || customCommonEquipIds.value.includes(equip.id);
}

export function toggleCommonEquip(equip: EquipInfo) {
  if (isCommonEquip(equip)) {
    if (equip.commonDefault) {
      excludedDefaultEquipIds.value = [...excludedDefaultEquipIds.value, equip.id];
      writeList('hugofgo.uncommonDefaultEquips', excludedDefaultEquipIds.value);
    } else {
      customCommonEquipIds.value = customCommonEquipIds.value.filter((id) => id !== equip.id);
      writeList('hugofgo.commonEquips', customCommonEquipIds.value);
    }
    return;
  }
  if (equip.commonDefault) {
    excludedDefaultEquipIds.value = excludedDefaultEquipIds.value.filter((id) => id !== equip.id);
    writeList('hugofgo.uncommonDefaultEquips', excludedDefaultEquipIds.value);
  } else {
    customCommonEquipIds.value = [...customCommonEquipIds.value, equip.id];
    writeList('hugofgo.commonEquips', customCommonEquipIds.value);
  }
}
