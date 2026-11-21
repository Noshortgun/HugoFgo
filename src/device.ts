import { invoke as invokeTauri } from './tauri';

export { isTauri } from './tauri';

export function invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  return invokeTauri<T>(command, args);
}

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

function invokeDevice<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  return invokeTauri<T>(
    command,
    args,
    '设备控制仅在 Tauri 桌面版中可用，当前是浏览器预览',
  );
}

export const deviceApi = {
  listDevices: () => invokeDevice<DeviceInfo[]>('list_devices'),
  getDeviceInfo: (serial: string) => invokeDevice<DeviceMetrics>('get_device_info', { serial }),
  captureFrame: (serial: string) => invokeDevice<CapturedFrame>('capture_frame', { serial }),
  tapNormalized: (serial: string, x: number, y: number, width?: number, height?: number) =>
    invokeDevice<TapResult>('tap_normalized', { serial, x, y, width, height }),
};
