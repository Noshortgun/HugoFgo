<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import AppIcon from './AppIcon.vue';

interface ResourceManifest {
  region: string;
  version: { hash: string; timestamp: number };
  updatedAt: string;
  counts: { servants: number; equips: number; mysticCodes: number; files: number };
}

const manifest = ref<ResourceManifest | null>(null);
const checking = ref(false);
const runningAction = ref<'update' | 'verify' | 'refresh' | null>(null);
const taskOutput = ref('');
const message = ref('读取本地资源清单…');
const latestHash = ref('');
const hasUpdate = computed(() => Boolean(manifest.value && latestHash.value && manifest.value.version.hash !== latestHash.value));

async function loadManifest() {
  try {
    const response = await fetch('/data/resource-manifest.json', { cache: 'no-store' });
    if (!response.ok) throw new Error();
    manifest.value = await response.json();
  } catch {
    manifest.value = null;
    if (!runningAction.value) message.value = '尚未建立资源清单，请先执行一次增量更新。';
  }
}

async function checkLatest() {
  if (checking.value) return;
  checking.value = true;
  try {
    const response = await fetch('https://api.atlasacademy.io/info', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const info = await response.json();
    latestHash.value = info.CN.hash;
    if (!manifest.value) message.value = `远端 CN 版本为 ${info.CN.hash}，本地尚未建立清单。`;
    else if (hasUpdate.value) message.value = `发现新版本：${manifest.value.version.hash} → ${info.CN.hash}`;
    else message.value = `资源已是最新：${info.CN.hash}`;
  } catch (error) {
    message.value = `检查失败：${error instanceof Error ? error.message : '无法连接 Atlas Academy'}`;
  } finally {
    checking.value = false;
  }
}

async function runResource(action: 'update' | 'verify' | 'refresh') {
  if (runningAction.value) return;
  runningAction.value = action;
  taskOutput.value = '';
  message.value = action === 'update' ? '正在增量下载并校验…' : action === 'verify' ? '正在校验本地文件…' : '正在校准完整元数据…';
  try {
    const response = await fetch(`/api/resources/${action}`, { method: 'POST' });
    const result = await response.json() as { success: boolean; output: string };
    taskOutput.value = result.output;
    if (!result.success) throw new Error(result.output.split('\n').find((line) => line.trim()) || '资源任务失败');
    await loadManifest();
    await checkLatest();
    message.value = action === 'verify' ? '本地资源校验通过，可展开日志查看。' : `${action === 'refresh' ? '完整校准' : '增量更新'}完成：${message.value}`;
  } catch (error) {
    message.value = `执行失败：${error instanceof Error ? error.message : '无法连接本地资源服务'}`;
  } finally {
    runningAction.value = null;
  }
}

onMounted(() => {
  message.value = '本地清单包含每个文件的 SHA-256，可检测缺失与损坏。';
  loadManifest();
});
</script>

<template>
  <section class="resource-center">
    <div class="resource-center-heading"><div><span class="picker-kicker">RESOURCE MANAGER</span><h3>资源下载与校验</h3></div><button :disabled="checking || Boolean(runningAction)" @click="checkLatest"><AppIcon name="history" :size="14" />{{ checking ? '检查中…' : '检查更新' }}</button></div>
    <div class="resource-version-grid">
      <div><span>当前版本</span><strong>{{ manifest?.version.hash ?? '未初始化' }}</strong></div>
      <div><span>远端版本</span><strong>{{ latestHash || '尚未检查' }}</strong></div>
      <div><span>本地清单</span><strong>{{ manifest ? `${manifest.counts.files} 个文件` : '无' }}</strong></div>
    </div>
    <p v-if="manifest" class="resource-catalog-counts">从者 {{ manifest.counts.servants }} · 礼装 {{ manifest.counts.equips }} · 御主礼装 {{ manifest.counts.mysticCodes }}</p>
    <p class="resource-center-message" :class="{ update: hasUpdate }">{{ message }}</p>
    <div class="resource-action-row">
      <button :disabled="Boolean(runningAction)" @click="runResource('update')">{{ runningAction === 'update' ? '更新中…' : '增量更新' }}</button>
      <button :disabled="Boolean(runningAction)" @click="runResource('verify')">{{ runningAction === 'verify' ? '校验中…' : '校验资源' }}</button>
      <button :disabled="Boolean(runningAction)" title="低频操作：重新校准旧礼装等元数据" @click="runResource('refresh')">{{ runningAction === 'refresh' ? '校准中…' : '完整校准' }}</button>
    </div>
    <details v-if="taskOutput" class="resource-task-log"><summary>查看执行日志</summary><pre>{{ taskOutput }}</pre></details>
    <small class="resource-center-note">更新只下载新增、变更或损坏的文件，并原子替换。完整校准用于低频同步旧元数据。任务由本地开发服务真实执行。</small>
  </section>
</template>
