<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import AppIcon from './AppIcon.vue';
import { isTauri } from '../device';
import {
  browserHistoryMessage,
  formatRunDuration,
  runHistoryApi,
  type RunArtifactEvent,
  type RunArtifacts,
  type RunRecord,
} from '../runHistory';
import { recoveryItemLabels } from '../runSettings';

const records = ref<RunRecord[]>([]);
const loading = ref(false);
const detailLoading = ref(false);
const error = ref('');
const message = ref('');
const selectedId = ref('');
const artifacts = ref<RunArtifacts | null>(null);
const resultLabels = { completed: '完成', stopped: '手动停止', failed: '失败' };
const eventLabels: Record<string, string> = {
  run_started: '运行开始',
  state_changed: '画面状态',
  wave_started: '进入新面',
  turn_started: '进入回合',
  action: '已发送输入',
  action_confirmed: '画面确认',
  card_batch_submitted: '卡批次已提交',
  warning: '警告',
  channel_changed: '通道变化',
  recovery_used: '恢复消耗',
  battle_completed: '完成一场',
  drain_started: '开始清 AP',
  finished: '完成',
  stopped: '停止',
  failed: '失败',
};
const selected = computed(() => records.value.find((record) => record.id === selectedId.value) ?? null);

async function refresh() {
  loading.value = true;
  error.value = '';
  try {
    records.value = await runHistoryApi.list();
    if (!records.value.some((record) => record.id === selectedId.value)) selectedId.value = records.value[0]?.id ?? '';
    if (selectedId.value) await loadArtifacts(selectedId.value);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

async function select(record: RunRecord) {
  selectedId.value = record.id;
  await loadArtifacts(record.id);
}

async function loadArtifacts(id: string) {
  detailLoading.value = true;
  artifacts.value = null;
  error.value = '';
  try {
    artifacts.value = await runHistoryApi.artifacts(id);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    detailLoading.value = false;
  }
}

async function openDirectory() {
  if (!selected.value) return;
  message.value = '';
  error.value = '';
  try {
    await runHistoryApi.openDirectory(selected.value.id);
    message.value = '已在文件管理器中打开证据目录';
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  }
}

async function copyDirectory() {
  if (!artifacts.value) return;
  message.value = '';
  error.value = '';
  try {
    await navigator.clipboard.writeText(artifacts.value.directory);
    message.value = '证据目录路径已复制';
  } catch {
    error.value = '无法访问剪贴板；请从下方路径手动复制';
  }
}

function recoveryText(record: RunRecord) {
  const entries = Object.entries(record.recoveryUsed);
  if (!entries.length) return '未使用恢复道具';
  return entries.map(([item, count]) => `${recoveryItemLabels[item as keyof typeof recoveryItemLabels] ?? item} × ${count}`).join(' · ');
}

function channelText(channel?: string) {
  return channel === 'scrcpy' ? 'scrcpy' : channel === 'adb' ? 'ADB' : '未记录';
}

function eventText(event: RunArtifactEvent) {
  const data = event.data ?? {};
  const text = (key: string) => typeof data[key] === 'string' ? data[key] as string : '';
  const number = (key: string) => typeof data[key] === 'number' ? data[key] as number : undefined;
  if (event.type === 'action') return [text('control'), number('wave') ? `第 ${number('wave')} 面 · 回合 ${number('turn')}` : '', text('reason')].filter(Boolean).join(' · ');
  if (event.type === 'card_batch_submitted') return `批次 ${number('batch_id') ?? '?'} · ${Array.isArray(data.controls) ? (data.controls as string[]).join(' / ') : ''}`;
  if (event.type === 'warning' || event.type === 'failed') return text('message') || text('reason') || '—';
  if (event.type === 'channel_changed') return text('message') || `切换到 ${text('to')}`;
  if (event.type === 'recovery_used') return `${recoveryItemLabels[text('item') as keyof typeof recoveryItemLabels] ?? text('item')} · ${number('count') ?? '?'}`;
  if (event.type === 'battle_completed') return `已完成 ${number('completed_runs') ?? 0} 场`;
  if (event.type === 'finished') return `完成 ${number('completed_runs') ?? 0} 场`;
  if (event.type === 'turn_started' || event.type === 'wave_started') return `第 ${number('wave') ?? '?'} 面 · 回合 ${number('turn') ?? 1}`;
  if (event.type === 'state_changed') return text('state');
  return Object.entries(data).slice(0, 2).map(([key, value]) => `${key}: ${String(value)}`).join(' · ');
}

function perceptionRows() {
  const perception = artifacts.value?.result?.perception;
  if (!perception || typeof perception !== 'object') return [];
  return Object.entries(perception as Record<string, unknown>).map(([key, value]) => ({ key, value: typeof value === 'object' ? JSON.stringify(value) : String(value) }));
}

onMounted(refresh);
</script>

<template>
  <section class="history-workspace">
    <header class="workspace-page-heading">
      <div><span>RUN ARCHIVE</span><h2>记录</h2><p>查看每次运行的实际结果、通道、事件与证据；重启后仍保留。</p></div>
      <button class="button-secondary" :disabled="loading" @click="refresh"><AppIcon name="history" :size="14" />{{ loading ? '读取中…' : '刷新' }}</button>
    </header>
    <p v-if="!isTauri" class="workspace-blocker">{{ browserHistoryMessage }}</p>
    <p v-if="error" class="history-error">{{ error }}</p>
    <p v-if="message" class="job-file-message">{{ message }}</p>
    <p v-if="loading && !records.length" class="history-empty">正在读取运行记录…</p>
    <p v-else-if="!records.length && isTauri" class="history-empty">还没有运行记录。完成、停止或失败一次运行后会显示在这里。</p>

    <div v-else class="history-layout">
      <aside class="history-list">
        <button v-for="record in records" :key="record.id" :class="{ active: selectedId === record.id }" @click="select(record)">
          <span class="history-result" :class="`history-${record.result}`"><AppIcon :name="record.result === 'completed' ? 'check' : 'close'" :size="14" /></span>
          <div><strong>{{ record.jobName || record.jobFile }}</strong><small>{{ new Date(record.startedAt).toLocaleString('zh-CN', { hour12: false }) }}</small><small>{{ formatRunDuration(record.durationMs) }} · {{ channelText(record.channel) }} · {{ resultLabels[record.result] }}</small></div>
        </button>
      </aside>

      <section v-if="selected" class="history-detail">
        <header><div><span :class="`history-result-label history-${selected.result}`">{{ resultLabels[selected.result] }}</span><h3>{{ selected.jobName }}</h3><p>{{ selected.reason }}</p></div><div><button class="button-primary" @click="openDirectory"><AppIcon name="folder" :size="14" /> 打开证据目录</button><button class="button-secondary" :disabled="!artifacts" @click="copyDirectory">复制路径</button></div></header>
        <div class="history-facts">
          <div><span>开始</span><strong>{{ new Date(selected.startedAt).toLocaleString('zh-CN', { hour12: false }) }}</strong></div>
          <div><span>用时</span><strong>{{ formatRunDuration(selected.durationMs) }}</strong></div>
          <div><span>完成场数</span><strong>{{ selected.battlesCompleted }}</strong></div>
          <div><span>设备通道</span><strong>{{ channelText(selected.channel) }}</strong></div>
          <div><span>设备</span><strong>{{ selected.serial }}</strong></div>
          <div><span>AP 恢复</span><strong>{{ recoveryText(selected) }}</strong></div>
        </div>
        <p v-if="selected.error" class="device-error">{{ selected.error }}</p>
        <p v-if="artifacts" class="history-directory">{{ artifacts.directory }}</p>
        <p v-if="detailLoading" class="history-empty">正在读取证据…</p>

        <div v-if="artifacts" class="history-detail-grid">
          <section class="history-evidence-card">
            <h4>证据文件</h4>
            <small>事件 {{ artifacts.eventCount }} 条，显示最近 {{ artifacts.events.length }} 条。</small>
            <ul><li>event：{{ artifacts.logFile }}</li><li>result：{{ artifacts.resultFile }}</li><li v-if="artifacts.observationFile">observation：{{ artifacts.observationFile }}</li><li v-for="frame in artifacts.screenshots" :key="frame">画面：{{ frame }}</li></ul>
            <div v-if="perceptionRows().length" class="history-perception"><h4>识别统计</h4><div v-for="row in perceptionRows()" :key="row.key"><span>{{ row.key }}</span><strong>{{ row.value }}</strong></div></div>
          </section>
          <section class="history-events-card">
            <h4>事件时间线</h4>
            <div class="history-event-list">
              <p v-if="!artifacts.events.length" class="history-empty">没有可读取的事件记录。</p>
              <article v-for="(event, index) in artifacts.events" :key="index" :class="event.type"><span>{{ eventLabels[event.type] ?? event.type }}</span><div><strong>{{ eventText(event) }}</strong><small>{{ event.type }}</small></div></article>
            </div>
          </section>
        </div>
      </section>
    </div>
  </section>
</template>
