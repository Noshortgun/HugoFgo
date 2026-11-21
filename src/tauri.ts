type TauriInvoke = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

function tauriInvoke(): TauriInvoke | undefined {
  const internals = (window as unknown as { __TAURI_INTERNALS__?: { invoke?: TauriInvoke } })
    .__TAURI_INTERNALS__;
  return typeof internals?.invoke === 'function' ? internals.invoke : undefined;
}

export const isTauri = Boolean(tauriInvoke());

export async function invoke<T>(
  command: string,
  args?: Record<string, unknown>,
  unavailable = '此功能仅在 Tauri 桌面版中可用，当前是浏览器预览',
): Promise<T> {
  const run = tauriInvoke();
  if (!run) throw new Error(unavailable);
  return (await run(command, args)) as T;
}
