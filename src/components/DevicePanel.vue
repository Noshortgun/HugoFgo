<script setup lang="ts">
import { computed, ref } from 'vue';
import AppIcon from './AppIcon.vue';
import { deviceApi, isTauri } from '../device';
import type { CapturedFrame, DeviceInfo, DeviceMetrics } from '../device';

const devices = ref<DeviceInfo[]>([]);
const serial = ref('');
const metrics = ref<DeviceMetrics | null>(null);
const frame = ref<CapturedFrame | null>(null);
const busy = ref('');
const message = ref('');
const error = ref('');

const current = computed(() => devices.value.find((device) => device.serial === serial.value));
const statusLabel = computed(() => {
  if (!isTauri) return '浏览器预览';
  if (!serial.value) return '未检测';
  return current.value?.state === 'device' ? '已连接' : current.value?.state ?? '未知';
});
const frameSrc = computed(() => (frame.value ? `data:image/png;base64,${frame.value.base64}` : ''));

function describe(errorValue: unknown) {
  return errorValue instanceof Error ? errorValue.message : String(errorValue);
}

async function loadInfo() {
  if (!serial.value) return;
  metrics.value = await deviceApi.getDeviceInfo(serial.value);
}

async function task(label: string, action: () => Promise<void>) {
  if (busy.value) return;
  busy.value = label;
  error.value = '';
  message.value = '';
  try {
    await action();
  } catch (errorValue) {
    error.value = describe(errorValue);
  } finally {
    busy.value = '';
  }
}

function detect() {
  return task('检测设备', async () => {
    const list = await deviceApi.listDevices();
    devices.value = list;
    const usable = list.find((device) => device.state === 'device') ?? list[0];
    serial.value = usable?.serial ?? '';
    metrics.value = null;
    frame.value = null;
    if (serial.value) {
      await loadInfo();
      message.value = `发现 ${list.length} 台设备`;
    } else {
      message.value = '没有发现设备，请先启动模拟器并确认 adb 可用';
    }
  });
}

function chooseDevice() {
  return task('读取设备信息', async () => {
    metrics.value = null;
    frame.value = null;
    await loadInfo();
  });
}

function capture() {
  return task('截取画面', async () => {
    frame.value = await deviceApi.captureFrame(serial.value);
    if (metrics.value) {
      frame.value = { ...frame.value, width: frame.value.width || metrics.value.width, height: frame.value.height || metrics.value.height };
    }
    message.value = `截图已保存到 ${frame.value.path}`;
  });
}

async function tapAt(x: number, y: number) {
  await task('测试点击', async () => {
    const result = await deviceApi.tapNormalized(serial.value, x, y, metrics.value?.width, metrics.value?.height);
    message.value = `已点击归一化坐标 (${x.toFixed(3)}, ${y.toFixed(3)}) → 像素 (${result.x}, ${result.y})`;
  });
}

function tapCenter() {
  return tapAt(0.5, 0.5);
}

function tapFrame(event: MouseEvent) {
  const target = event.currentTarget as HTMLImageElement;
  const rect = target.getBoundingClientRect();
  const x = (event.clientX - rect.left) / rect.width;
  const y = (event.clientY - rect.top) / rect.height;
  return tapAt(x, y);
}
</script>

<template>
  <section class="device-panel">
    <div class="device-panel-head">
      <div><strong>设备与触控</strong><small>ADB 截图与归一化点击 · 仅 Tauri 桌面版</small></div>
      <span class="device-status" :class="{ ready: current?.state === 'device' }">{{ statusLabel }}</span>
    </div>

    <p v-if="!isTauri" class="device-panel-note"><AppIcon name="device" :size="15" /> 当前是浏览器预览。设备命令需要在 Tauri 桌面窗口中运行。</p>
    <template v-else>
      <div class="device-panel-controls">
        <button :disabled="!!busy" @click="detect"><AppIcon name="device" :size="14" /> {{ busy === '检测设备' ? '检测中…' : '检测设备' }}</button>
        <select v-model="serial" :disabled="!devices.length || !!busy" @change="chooseDevice">
          <option value="">未选择设备</option>
          <option v-for="device in devices" :key="device.serial" :value="device.serial">{{ device.model || device.serial }} · {{ device.state }}</option>
        </select>
        <button :disabled="!serial || !!busy" @click="chooseDevice">读取信息</button>
        <button :disabled="!serial || !!busy" @click="capture"><AppIcon name="cards" :size="14" /> {{ busy === '截取画面' ? '截图中…' : '截取画面' }}</button>
        <button :disabled="!serial || !!busy" @click="tapCenter">测试点击中心</button>
      </div>

      <div v-if="metrics" class="device-metrics">
        <span>{{ metrics.width }} × {{ metrics.height }}</span>
        <span v-if="metrics.density">DPI {{ metrics.density }}</span>
        <span v-if="metrics.model">{{ metrics.model }}</span>
        <span class="device-serial">{{ metrics.serial }}</span>
      </div>
      <div v-if="frame" class="device-frame">
        <img :src="frameSrc" alt="设备截图" title="点击画面可测试归一化点击" @click="tapFrame" />
        <small>点击画面测试归一化坐标；截图 {{ frame.width }} × {{ frame.height }}</small>
      </div>
      <p v-if="message" class="device-message">{{ message }}</p>
      <p v-if="error" class="device-error">{{ error }}</p>
    </template>
  </section>
</template>
