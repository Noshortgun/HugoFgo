<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { isTauri } from '../device';
import { browserHistoryMessage, formatRunDuration, runHistoryApi, type RunArtifacts, type RunRecord } from '../runHistory';
import AppIcon from './AppIcon.vue';

const records = ref<RunRecord[]>([]);
const loading = ref(false);
const error = ref('');
const selectedId = ref('');
const artifacts = ref<RunArtifacts | null>(null);
const resultLabels = { completed: '完成', stopped: '手动停止', failed: '失败' };
const recoveryLabels: Record<string, string> = {
  saint_quartz: '圣晶石', gold: '黄金果实', silver: '白银果实', bronze: '青铜果实', copper: '赤铜果实',
};

async function refresh() {
  loading.value = true;
  error.value = '';
  try {
    records.value = await runHistoryApi.list();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

async function showArtifacts(record: RunRecord) {
  error.value = '';
  selectedId.value = record.id;
  artifacts.value = null;
  try {
    artifacts.value = await runHistoryApi.artifacts(record.id);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  }
}

function recoveryText(record: RunRecord) {
  const entries = Object.entries(record.recoveryUsed);
  if (!entries.length) return '未使用恢复道具';
  return entries.map(([item, count]) => `${recoveryLabels[item] ?? item} × ${count}`).join(' · ');
}

onMounted(refresh);
</script>

<template>
  <div class="utility-content">
    <p v-if="!isTauri" class="job-file-message">{{ browserHistoryMessage }}</p>
    <template v-else>
      <div class="history-heading"><span>记录保存在桌面应用数据目录，重启后仍可查看。</span><button :disabled="loading" @click="refresh">刷新</button></div>
      <p v-if="error" class="history-error">{{ error }}</p>
      <p v-if="loading && !records.length" class="history-empty">正在读取运行记录…</p>
      <p v-else-if="!records.length" class="history-empty">还没有运行记录。完成、停止或失败一次运行后会显示在这里。</p>
      <div v-for="record in records" :key="record.id" class="history-row">
        <span class="history-result" :class="`history-${record.result}`"><AppIcon :name="record.result === 'completed' ? 'check' : 'close'" :size="15" /></span>
        <div class="history-record">
          <strong>{{ record.jobName }} · 完成 {{ record.battlesCompleted }} 场</strong>
          <small>开始 {{ new Date(record.startedAt).toLocaleString('zh-CN', { hour12: false }) }} · 结束 {{ new Date(record.finishedAt).toLocaleString('zh-CN', { hour12: false }) }}</small>
          <small>用时 {{ formatRunDuration(record.durationMs) }} · {{ record.serial }} · {{ record.region }}</small>
          <small>{{ recoveryText(record) }}</small>
          <small v-if="record.error" class="history-error">{{ record.error }}</small>
          <button class="history-artifacts-button" @click="showArtifacts(record)">定位日志{{ record.result === 'failed' ? '与失败截图' : '目录' }}</button>
          <div v-if="selectedId === record.id && artifacts" class="history-artifacts">
            <small>日志：{{ artifacts.logFile }}</small>
            <small v-for="screenshot in artifacts.screenshots" :key="screenshot">失败截图：{{ screenshot }}</small>
            <small v-if="record.result === 'failed' && !artifacts.screenshots.length">此记录没有已保存的失败截图；可查看日志与 result.json。</small>
          </div>
        </div>
        <span class="history-result-label" :class="`history-${record.result}`">{{ resultLabels[record.result] }}</span>
      </div>
    </template>
    <p class="utility-note">运行记录只在需要时从这里查看，不占主界面导航。</p>
  </div>
</template>
