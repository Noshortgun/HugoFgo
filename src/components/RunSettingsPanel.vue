<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { isTauri } from '../device';
import { defaultRunSettings, loadRunSettings, saveRunSettings, type RecoveryItem, type RunSettings } from '../runSettings';

const settings = ref<RunSettings>(defaultRunSettings());
const loading = ref(true);
const saving = ref(false);
const message = ref('');
const error = ref('');
const items: { key: RecoveryItem; label: string }[] = [
  { key: 'saint_quartz', label: '圣晶石' },
  { key: 'gold', label: '黄金果实' },
  { key: 'silver', label: '白银果实' },
  { key: 'bronze', label: '青铜果实' },
  { key: 'copper', label: '赤铜果实' },
];

onMounted(async () => {
  try {
    settings.value = await loadRunSettings();
  } catch (value) {
    error.value = value instanceof Error ? value.message : '运行设置读取失败';
  } finally {
    loading.value = false;
  }
});

async function save() {
  saving.value = true;
  message.value = '';
  error.value = '';
  try {
    settings.value = await saveRunSettings(settings.value);
    message.value = isTauri ? '运行设置已保存到应用数据目录' : '运行设置已保存到浏览器';
  } catch (value) {
    error.value = value instanceof Error ? value.message : '运行设置保存失败';
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <section class="run-settings">
    <div class="run-settings-heading"><div><strong>运行设置</strong><small>固定场数与 AP 恢复白名单</small></div><button :disabled="loading || saving" @click="save">{{ saving ? '保存中…' : '保存设置' }}</button></div>
    <fieldset :disabled="loading || saving">
      <label class="run-settings-battles">连续出击场数<input v-model.number="settings.battles" type="number" min="1" step="1" required /></label>
      <label class="run-settings-channel">设备通道<select v-model="settings.device_channel"><option value="adb">ADB（默认）</option><option value="scrcpy">scrcpy 持续帧与输入（显式启用）</option></select></label>
      <small>scrcpy 使用随包固定服务端；通道设置只保存到运行设置，不写入作业。当前仅验证指定模拟器，不代表其他平台稳定。</small>
      <label v-if="settings.device_channel === 'scrcpy'" class="run-settings-drain"><input v-model="settings.allow_adb_fallback" type="checkbox" />我确认：scrcpy 启动失败时可见回退到 ADB</label>
      <small v-if="settings.device_channel === 'scrcpy'">未勾选时启动失败会直接停止；运行中视频或控制中断不会自动回退。</small>
      <div class="run-settings-recovery"><span>允许使用的恢复方式</span><label v-for="item in items" :key="item.key"><input v-model="settings.recovery.allowed" type="checkbox" :value="item.key" />{{ item.label }}</label></div>
      <label class="run-settings-drain"><input v-model="settings.recovery.drain_after_turns" type="checkbox" />达到场数后继续清空剩余 AP</label>
      <small>清 AP 阶段不会使用任何恢复道具；未勾选的道具不会点击。</small>
    </fieldset>
    <p v-if="message" class="run-settings-message">{{ message }}</p>
    <p v-if="error" class="device-error">{{ error }}</p>
  </section>
</template>
