<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { isTauri } from '../device';
import { loadRunSettings, runSettings, runSettingsLoaded, saveRunSettings } from '../runSettings';

const loading = ref(!runSettingsLoaded.value);
const saving = ref(false);
const message = ref('');
const error = ref('');
const settings = runSettings;

onMounted(async () => {
  loading.value = !runSettingsLoaded.value;
  try {
    await loadRunSettings();
  } catch (value) {
    error.value = value instanceof Error ? value.message : '通道设置读取失败';
  } finally {
    loading.value = false;
  }
});

async function save() {
  saving.value = true;
  message.value = '';
  error.value = '';
  try {
    await saveRunSettings(settings.value);
    message.value = isTauri ? '通道与回退设置已保存' : '通道与回退设置已保存到浏览器';
  } catch (value) {
    error.value = value instanceof Error ? value.message : '通道与回退设置保存失败';
  } finally {
    saving.value = false;
  }
}

function useScrcpy() {
  settings.value = { ...settings.value, device_channel: 'scrcpy', allow_adb_fallback: true };
}
</script>

<template>
  <section class="run-settings">
    <div class="run-settings-heading"><div><strong>通道与回退</strong><small>环境准备；刷本次数与 AP 在编队页编辑</small></div><button :disabled="loading || saving" @click="save">{{ saving ? '保存中…' : '保存设置' }}</button></div>
    <fieldset :disabled="loading || saving">
      <label class="run-settings-channel">设备通道<select v-model="settings.device_channel"><option value="scrcpy">scrcpy（默认）</option><option value="adb">ADB（保底）</option></select></label>
      <small>scrcpy 使用随包固定服务端；若启动失败且已允许回退，运行面板会显示“已回退到 ADB”和原因。运行中通道中断只安全停止，不中途切换。</small>
      <button v-if="settings.device_channel === 'adb'" class="settings-use-scrcpy" type="button" @click="useScrcpy">改用推荐的 scrcpy</button>
      <label v-if="settings.device_channel === 'scrcpy'" class="run-settings-drain"><input v-model="settings.allow_adb_fallback" type="checkbox" />允许 scrcpy 启动失败时回退 ADB</label>
    </fieldset>
    <p v-if="message" class="run-settings-message">{{ message }}</p>
    <p v-if="error" class="device-error">{{ error }}</p>
  </section>
</template>
