<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';
import AppIcon from './AppIcon.vue';
import { fetchResourceJson, RESOURCE_REGIONS, resourceRegion, setResourceRegion, type Region } from '../resources';
import { invoke, isTauri } from '../tauri';

interface ResourceManifest {
  region: string;
  version: { hash: string; timestamp: number };
  updatedAt: string;
  counts: { servants: number; equips: number; mysticCodes: number; files: number };
}

interface ResourceTaskResult {
  success: boolean;
  code: number | null;
  output: string;
}

interface ResourceTaskEvent {
  region: string;
  action: string;
  stream: string;
  text: string;
}

type ResourceAction = 'check' | 'update' | 'verify' | 'refresh';

const emit = defineEmits<{ updated: [region: Region] }>();
const manifest = ref<ResourceManifest | null>(null);
const runningAction = ref<ResourceAction | null>(null);
const taskOutput = ref('');
const message = ref('读取本地资源清单…');
const latestHash = ref('');
let unlisten: UnlistenFn | null = null;
const hasUpdate = computed(() => Boolean(manifest.value && latestHash.value && manifest.value.version.hash !== latestHash.value));

async function loadManifest() {
  try {
    manifest.value = await fetchResourceJson<ResourceManifest>('resource-manifest.json');
  } catch {
    manifest.value = null;
    if (!runningAction.value) message.value = `${resourceRegion.value} 尚未建立资源清单，请先执行一次增量更新。`;
  }
}

function firstLine(output: string) {
  return output.split('\n').find((line) => line.trim()) ?? '资源任务失败';
}

function remoteHash(output: string) {
  return output.match(/^远端 (?:CN|JP|TW)：(\S+)/m)?.[1] ?? '';
}

async function runTask(action: ResourceAction) {
  if (runningAction.value) return;
  if (!isTauri) {
    message.value = '浏览器预览为只读：资源检查、更新与校验仅在桌面版可用。';
    return;
  }
  runningAction.value = action;
  taskOutput.value = '';
  message.value = action === 'check'
    ? `正在检查 ${resourceRegion.value} 远端版本…`
    : action === 'update'
      ? '正在增量下载并校验…'
      : action === 'verify'
        ? '正在校验本地文件…'
        : '正在校准完整元数据…';
  try {
    const command = action === 'check' ? 'check_resources' : action === 'verify' ? 'verify_resources' : 'update_resources';
    const args = action === 'update' || action === 'refresh'
      ? { region: resourceRegion.value, refresh: action === 'refresh' }
      : { region: resourceRegion.value };
    const result = await invoke<ResourceTaskResult>(command, args);
    if (!taskOutput.value && result.output) taskOutput.value = result.output;
    if (!result.success) throw new Error(firstLine(result.output));
    if (action === 'check') {
      latestHash.value = remoteHash(result.output);
      if (latestHash.value && manifest.value && hasUpdate.value) {
        message.value = `发现新版本：${manifest.value.version.hash} → ${latestHash.value}`;
      } else if (latestHash.value) {
        message.value = `资源已是最新：${latestHash.value}`;
      } else {
        message.value = '资源检查完成，可展开日志查看结果。';
      }
      return;
    }
    await loadManifest();
    if (action === 'update' || action === 'refresh') emit('updated', resourceRegion.value);
    if (action !== 'verify') latestHash.value = manifest.value?.version.hash ?? '';
    message.value = action === 'verify'
      ? `${resourceRegion.value} 本地资源校验通过，可展开日志查看。`
      : `${action === 'refresh' ? '完整校准' : '增量更新'}完成：${manifest.value?.version.hash ?? resourceRegion.value}`;
  } catch (error) {
    message.value = `执行失败：${error instanceof Error ? error.message : '无法启动资源任务'}`;
  } finally {
    runningAction.value = null;
  }
}

function changeRegion(event: Event) {
  if (runningAction.value) return;
  const region = (event.target as HTMLSelectElement).value as Region;
  setResourceRegion(region);
  manifest.value = null;
  latestHash.value = '';
  taskOutput.value = '';
  message.value = `正在读取 ${region} 资源清单…`;
  void loadManifest();
}

onMounted(async () => {
  message.value = isTauri
    ? '本地清单包含每个文件的 SHA-256，可检测缺失与损坏。'
    : '浏览器预览为只读：这里只显示本地资源状态，检查、更新与校验请使用桌面版。';
  await loadManifest();
  if (isTauri) {
    unlisten = await listen<ResourceTaskEvent>('resource-task-output', (event) => {
      if (event.payload.region !== resourceRegion.value) return;
      taskOutput.value += event.payload.text;
    });
  }
});

onUnmounted(() => {
  unlisten?.();
});
</script>

<template>
  <section class="resource-center">
    <div class="resource-center-heading"><div><span class="picker-kicker">RESOURCE MANAGER</span><h3>资源下载与校验</h3></div><div class="resource-region-picker"><select :value="resourceRegion" :disabled="Boolean(runningAction)" aria-label="资源区服" @change="changeRegion"><option v-for="region in RESOURCE_REGIONS" :key="region.id" :value="region.id">{{ region.label }} · {{ region.id }}</option></select><button :disabled="runningAction !== null" @click="runTask('check')"><AppIcon name="history" :size="14" />{{ runningAction === 'check' ? '检查中…' : '检查更新' }}</button></div></div>
    <div class="resource-version-grid">
      <div><span>当前区服</span><strong>{{ resourceRegion }}</strong></div>
      <div><span>当前版本</span><strong>{{ manifest?.version.hash ?? '未初始化' }}</strong></div>
      <div><span>远端版本</span><strong>{{ latestHash || '尚未检查' }}</strong></div>
      <div><span>本地清单</span><strong>{{ manifest ? `${manifest.counts.files} 个文件` : '无' }}</strong></div>
    </div>
    <p v-if="manifest" class="resource-catalog-counts">从者 {{ manifest.counts.servants }} · 礼装 {{ manifest.counts.equips }} · 御主礼装 {{ manifest.counts.mysticCodes }}</p>
    <p class="resource-center-message" :class="{ update: hasUpdate }">{{ message }}</p>
    <div class="resource-action-row">
      <button :disabled="runningAction !== null" @click="runTask('update')">{{ runningAction === 'update' ? '更新中…' : '增量更新' }}</button>
      <button :disabled="runningAction !== null" @click="runTask('verify')">{{ runningAction === 'verify' ? '校验中…' : '校验资源' }}</button>
      <button :disabled="runningAction !== null" title="低频操作：重新校准旧礼装等元数据" @click="runTask('refresh')">{{ runningAction === 'refresh' ? '校准中…' : '完整校准' }}</button>
    </div>
    <details v-if="taskOutput" class="resource-task-log"><summary>查看执行日志</summary><pre>{{ taskOutput }}</pre></details>
    <small class="resource-center-note">更新只下载新增、变更或损坏的文件，并原子替换。完整校准用于低频同步旧元数据。任务由桌面壳执行；浏览器预览只读。</small>
  </section>
</template>
