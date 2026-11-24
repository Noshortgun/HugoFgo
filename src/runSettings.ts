import { ref } from 'vue';
import { invoke, isTauri } from './tauri';

export const recoveryItems = ['saint_quartz', 'gold', 'silver', 'bronze', 'copper'] as const;
export type RecoveryItem = (typeof recoveryItems)[number];

export interface RecoveryPolicy {
  allowed: RecoveryItem[];
  drain_after_turns: boolean;
}

export type DeviceChannel = 'adb' | 'scrcpy';

export interface RunSettings {
  battles: number;
  recovery: RecoveryPolicy;
  device_channel: DeviceChannel;
  allow_adb_fallback: boolean;
}

export const recoveryItemLabels: Record<RecoveryItem, string> = {
  saint_quartz: '圣晶石',
  gold: '黄金果实',
  silver: '白银果实',
  bronze: '青铜果实',
  copper: '赤铜果实',
};

export const supportedRecoveryItems: RecoveryItem[] = [...recoveryItems];

const storageKey = 'hugofgo.runSettings';

export function defaultRunSettings(): RunSettings {
  return {
    battles: 1,
    recovery: { allowed: ['bronze'], drain_after_turns: false },
    device_channel: 'scrcpy',
    allow_adb_fallback: true,
  };
}

export function normalizeRunSettings(value: unknown): RunSettings {
  const fallback = defaultRunSettings();
  if (!value || typeof value !== 'object') return fallback;
  const data = value as Partial<RunSettings>;
  const recovery = data.recovery;
  const allowed = Array.isArray(recovery?.allowed)
    ? [...new Set(recovery.allowed.filter((item): item is RecoveryItem => supportedRecoveryItems.includes(item as RecoveryItem)))]
    : fallback.recovery.allowed;
  const channel = data.device_channel === 'adb' || data.device_channel === 'scrcpy'
    ? data.device_channel
    : fallback.device_channel;
  return {
    battles: typeof data.battles === 'number' && Number.isInteger(data.battles) && data.battles >= 1 ? data.battles : fallback.battles,
    recovery: {
      allowed,
      drain_after_turns: typeof recovery?.drain_after_turns === 'boolean' ? recovery.drain_after_turns : fallback.recovery.drain_after_turns,
    },
    device_channel: channel,
    allow_adb_fallback: channel === 'scrcpy'
      ? (typeof data.allow_adb_fallback === 'boolean' ? data.allow_adb_fallback : fallback.allow_adb_fallback)
      : false,
  };
}

/** 首页、设置页和启动流程共用这一份状态，避免界面显示与实际运行参数分叉。 */
export const runSettings = ref<RunSettings>(defaultRunSettings());
export const runSettingsLoaded = ref(false);
let loadPromise: Promise<RunSettings> | null = null;

export async function loadRunSettings(force = false): Promise<RunSettings> {
  if (!force && runSettingsLoaded.value) return runSettings.value;
  if (!force && loadPromise) return loadPromise;
  loadPromise = (async () => {
    let value: unknown = null;
    if (isTauri) {
      value = await invoke<unknown>('get_run_settings');
    } else {
      try {
        value = JSON.parse(localStorage.getItem(storageKey) ?? 'null');
      } catch {
        value = null;
      }
    }
    runSettings.value = normalizeRunSettings(value);
    runSettingsLoaded.value = true;
    return runSettings.value;
  })();
  try {
    return await loadPromise;
  } finally {
    loadPromise = null;
  }
}

export async function saveRunSettings(value: RunSettings = runSettings.value): Promise<RunSettings> {
  const settings = normalizeRunSettings(value);
  if (isTauri) {
    runSettings.value = normalizeRunSettings(await invoke<unknown>('save_run_settings', { settings }));
  } else {
    localStorage.setItem(storageKey, JSON.stringify(settings));
    runSettings.value = settings;
  }
  runSettingsLoaded.value = true;
  return runSettings.value;
}

export function applyRunSettings(value: RunSettings) {
  runSettings.value = normalizeRunSettings(value);
  runSettingsLoaded.value = true;
}

export function patchRunSettings(patch: Partial<RunSettings>) {
  applyRunSettings({ ...runSettings.value, ...patch });
}
