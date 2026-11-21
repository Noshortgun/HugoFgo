import { ref } from 'vue';

import { isTauri } from './tauri';

export type Region = 'CN' | 'JP' | 'TW';

export const RESOURCE_REGIONS: { id: Region; label: string }[] = [
  { id: 'CN', label: '国服' },
  { id: 'JP', label: '日服' },
  { id: 'TW', label: '台服' },
];

const storageKey = 'hugofgo.resourceRegion';
const saved = localStorage.getItem(storageKey);
export const resourceRegion = ref<Region>(
  RESOURCE_REGIONS.some((region) => region.id === saved) ? (saved as Region) : 'CN',
);

export function setResourceRegion(region: Region) {
  resourceRegion.value = region;
  localStorage.setItem(storageKey, region);
}

function normalizeResourcePath(path: string): string {
  const clean = path
    .replace(/^\/+/, '')
    .replace(/^data\//, '')
    .replace(/^(CN|JP|TW)\//, '');
  return clean;
}

/** 浏览器预览读取 Vite 的 data/ 静态目录；桌面版读取应用数据目录。 */
export function resourceUrl(path: string): string {
  const clean = normalizeResourcePath(path);
  if (!isTauri) return `/data/${resourceRegion.value}/${clean}`;
  const base = /windows/i.test(navigator.userAgent)
    ? 'http://hugofgo-data.localhost'
    : 'hugofgo-data://localhost';
  return `${base}/${resourceRegion.value}/${clean}`;
}

export async function fetchResourceJson<T>(path: string): Promise<T> {
  const response = await fetch(resourceUrl(path), { cache: 'no-store' });
  if (!response.ok) throw new Error(`读取 ${path} 失败（HTTP ${response.status}）`);
  return (await response.json()) as T;
}
