export interface DeviceInfo {
  serial: string;
  state: string;
  model?: string;
  product?: string;
  device?: string;
}

export interface DeviceMetrics {
  serial: string;
  width: number;
  height: number;
  density?: number;
  model?: string;
}

export interface CapturedFrame {
  base64: string;
  path: string;
  width: number;
  height: number;
  captured_at_ms: number;
}

export interface TapResult {
  serial: string;
  x: number;
  y: number;
  norm_x: number;
  norm_y: number;
}

type TauriInvoke = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

function tauriInvoke(): TauriInvoke | undefined {
  const internals = (window as unknown as { __TAURI_INTERNALS__?: { invoke?: TauriInvoke } })
    .__TAURI_INTERNALS__;
  return typeof internals?.invoke === 'function' ? internals.invoke : undefined;
}

export const isTauri = Boolean(tauriInvoke());

export async function invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  const run = tauriInvoke();
  if (!run) throw new Error('此命令仅在 Tauri 桌面版中可用，当前是浏览器预览');
  return (await run(command, args)) as T;
}

export const deviceApi = {
  listDevices: () => invoke<DeviceInfo[]>('list_devices'),
  getDeviceInfo: (serial: string) => invoke<DeviceMetrics>('get_device_info', { serial }),
  captureFrame: (serial: string) => invoke<CapturedFrame>('capture_frame', { serial }),
  tapNormalized: (serial: string, x: number, y: number, width?: number, height?: number) =>
    invoke<TapResult>('tap_normalized', { serial, x, y, width, height }),
};
