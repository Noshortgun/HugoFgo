import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const root = fileURLToPath(new URL('../', import.meta.url));
const bundle = await build({
  stdin: {
    contents: "export * from './src/runSettings.ts';",
    resolveDir: root,
    sourcefile: 'run-settings.ts',
    loader: 'ts',
  },
  bundle: true,
  platform: 'node',
  format: 'esm',
  write: false,
  logLevel: 'silent',
});
globalThis.window = {};
const settings = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);

const defaults = settings.defaultRunSettings();
assert.equal(defaults.device_channel, 'scrcpy', '新配置默认使用 scrcpy');
assert.equal(defaults.allow_adb_fallback, true, '新配置默认允许启动失败时回退 ADB');
assert.deepEqual(settings.normalizeRunSettings({}), defaults);
assert.deepEqual(settings.normalizeRunSettings({
  device_channel: 'scrcpy', allow_adb_fallback: true,
}), { ...defaults, device_channel: 'scrcpy', allow_adb_fallback: true });
assert.deepEqual(settings.normalizeRunSettings({
  device_channel: 'adb', allow_adb_fallback: true,
}), { ...defaults, device_channel: 'adb', allow_adb_fallback: false }, '已保存的显式 ADB 选择不得被静默覆盖');
assert.deepEqual(settings.normalizeRunSettings({
  device_channel: 'invalid', allow_adb_fallback: true,
}), defaults, '未知通道和无效回退不得泄漏到执行器');
assert.deepEqual(settings.normalizeRunSettings({
  device_channel: 'scrcpy', allow_adb_fallback: false,
}), { ...defaults, device_channel: 'scrcpy', allow_adb_fallback: false }, '显式关闭回退必须保留');
const sanitized = settings.normalizeRunSettings({
  recovery: { allowed: ['saint_quartz', 'bronze', 'unknown'], drain_after_turns: false },
});
assert.deepEqual(sanitized.recovery.allowed, ['saint_quartz', 'bronze'], '显式确认的圣晶石可进入白名单，未知道具仍被过滤');
const ordered = settings.normalizeRunSettings({
  recovery: { allowed: ['silver', 'gold', 'bronze'], drain_after_turns: false },
});
assert.deepEqual(ordered.recovery.allowed, ['silver', 'gold', 'bronze'], '设置归一化必须保留用户勾选顺序');
console.log('运行设置默认通道、回退与白名单自检通过');
