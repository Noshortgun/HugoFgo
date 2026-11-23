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
assert.equal(defaults.device_channel, 'adb', '实机验收前不得默认启用 scrcpy');
assert.equal(defaults.allow_adb_fallback, false, 'ADB 回退必须由用户明确确认');
assert.deepEqual(settings.normalizeRunSettings({}), defaults);
assert.deepEqual(settings.normalizeRunSettings({
  device_channel: 'scrcpy', allow_adb_fallback: true,
}), { ...defaults, device_channel: 'scrcpy', allow_adb_fallback: true });
assert.deepEqual(settings.normalizeRunSettings({
  device_channel: 'invalid', allow_adb_fallback: true,
}), defaults, '未知通道和无效回退不得泄漏到执行器');
assert.deepEqual(settings.normalizeRunSettings({
  device_channel: 'scrcpy', allow_adb_fallback: false,
}), { ...defaults, device_channel: 'scrcpy' }, '显式 scrcpy 不应暗中打开 ADB 回退');
console.log('运行设置通道与回退自检通过');
