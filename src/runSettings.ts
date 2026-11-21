import { invoke, isTauri } from './tauri';

export const recoveryItems = ['saint_quartz', 'gold', 'silver', 'bronze', 'copper'] as const;
export type RecoveryItem = (typeof recoveryItems)[number];

export interface RecoveryPolicy {
  allowed: RecoveryItem[];
  drain_after_turns: boolean;
}

export interface RunSettings {
  battles: number;
  recovery: RecoveryPolicy;
}

const storageKey = 'hugofgo.runSettings';

export function defaultRunSettings(): RunSettings {
  return { battles: 1, recovery: { allowed: ['bronze'], drain_after_turns: false } };
}

export function normalizeRunSettings(value: unknown): RunSettings {
  if (!value || typeof value !== 'object') return defaultRunSettings();
  const data = value as Partial<RunSettings>;
  const recovery = data.recovery;
  const allowed = Array.isArray(recovery?.allowed)
    ? [...new Set(recovery.allowed.filter((item): item is RecoveryItem => recoveryItems.includes(item as RecoveryItem)))]
    : defaultRunSettings().recovery.allowed;
  return {
    battles: typeof data.battles === 'number' && Number.isInteger(data.battles) && data.battles >= 1 ? data.battles : 1,
    recovery: {
      allowed,
      drain_after_turns: typeof recovery?.drain_after_turns === 'boolean' ? recovery.drain_after_turns : false,
    },
  };
}

export async function loadRunSettings(): Promise<RunSettings> {
  if (isTauri) return normalizeRunSettings(await invoke<unknown>('get_run_settings'));
  try {
    return normalizeRunSettings(JSON.parse(localStorage.getItem(storageKey) ?? 'null'));
  } catch {
    return defaultRunSettings();
  }
}

export async function saveRunSettings(value: RunSettings): Promise<RunSettings> {
  const settings = normalizeRunSettings(value);
  if (isTauri) return normalizeRunSettings(await invoke<unknown>('save_run_settings', { settings }));
  localStorage.setItem(storageKey, JSON.stringify(settings));
  return settings;
}
