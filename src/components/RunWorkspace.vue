<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue';
import AppIcon from './AppIcon.vue';
import type { RunEvent, RunState } from '../runState';
import { recoveryItemLabels, type RunSettings } from '../runSettings';
import type { JobDiagnostic } from '../jobValidation';

const props = defineProps<{
  state: RunState;
  events: RunEvent[];
  settings: RunSettings;
  jobName: string;
  selectedSerial: string;
  deviceLabel: string;
  startDisabledReason: string;
  startLabel: string;
  starting: boolean;
  diagnostics: JobDiagnostic[];
}>();
const emit = defineEmits<{
  start: [];
  stop: [];
  history: [];
  prepare: [];
  jobs: [];
}>();

const filter = ref<'all' | 'input' | 'confirm' | 'warning' | 'result'>('all');
const now = ref(Date.now());
const timer = window.setInterval(() => {
  if (props.state.phase === 'running' || props.state.phase === 'stopping') now.value = Date.now();
}, 1000);
onUnmounted(() => window.clearInterval(timer));

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
const eventKinds: Record<string, 'input' | 'confirm' | 'warning' | 'result'> = {
  action: 'input',
  card_batch_submitted: 'input',
  action_confirmed: 'confirm',
  warning: 'warning',
  channel_changed: 'warning',
  battle_completed: 'confirm',
  drain_started: 'confirm',
  finished: 'result',
  stopped: 'result',
  failed: 'result',
};
const phaseLabel = computed(() => ({
  idle: '等待启动',
  running: '运行中',
  stopping: '停止中',
  completed: '已完成',
  stopped: '已停止',
  failed: '运行失败',
}[props.state.phase]));
const active = computed(() => props.state.phase === 'running' || props.state.phase === 'stopping');
const elapsed = computed(() => {
  if (!props.state.startedAtMs) return '00:00';
  const seconds = Math.max(0, Math.floor((now.value - props.state.startedAtMs) / 1000));
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
});
const recoveryText = computed(() => {
  const entries = Object.entries(props.state.recoveryUsed);
  if (!entries.length) return '未使用';
  return entries.map(([item, count]) => `${recoveryItemLabels[item as keyof typeof recoveryItemLabels] ?? item} × ${count}`).join(' · ');
});
const filteredEvents = computed(() => {
  if (filter.value === 'all') return props.events;
  if (filter.value === 'input') return props.events.filter((event) => eventKinds[event.type] === 'input');
  if (filter.value === 'confirm') return props.events.filter((event) => eventKinds[event.type] === 'confirm');
  if (filter.value === 'warning') return props.events.filter((event) => eventKinds[event.type] === 'warning');
  return props.events.filter((event) => eventKinds[event.type] === 'result');
});
const startedAt = computed(() => props.events[0]?.timestamp ?? 0);

function eventTitle(event: RunEvent) {
  return eventLabels[event.type] ?? event.type;
}
function eventText(event: RunEvent) {
  const data = event.data ?? {};
  const value = (key: string) => typeof data[key] === 'string' ? data[key] as string : '';
  const number = (key: string) => typeof data[key] === 'number' ? data[key] as number : undefined;
  if (event.type === 'action') {
    const control = value('control') || '动作';
    const where = number('wave') && number('turn') ? `第 ${number('wave')} 面 · 回合 ${number('turn')}` : '';
    const wait = number('channel_ms');
    return [control, where, value('reason'), wait !== undefined ? `输入 ${wait.toFixed(1)} ms` : ''].filter(Boolean).join(' · ');
  }
  if (event.type === 'action_confirmed') return [value('stage'), number('wait_ms') !== undefined ? `等待 ${Math.round(number('wait_ms')!)} ms` : ''].filter(Boolean).join(' · ') || '后续画面已确认';
  if (event.type === 'card_batch_submitted') return `批次 ${number('batch_id') ?? '?'} · ${Array.isArray(data.controls) ? (data.controls as string[]).join(' / ') : ''} · 间隔 ${Math.round(number('configured_interval_ms') ?? 0)} ms`;
  if (event.type === 'warning') return value('message') || value('reason') || '需要留意';
  if (event.type === 'channel_changed') return value('message') || `通道切换为 ${value('to')}`;
  if (event.type === 'recovery_used') return `${recoveryItemLabels[value('item') as keyof typeof recoveryItemLabels] ?? value('item')} · 已用 ${number('count') ?? '?'}`;
  if (event.type === 'battle_completed') return `已完成 ${number('completed_runs') ?? 0} 场`;
  if (event.type === 'finished') return `完成 ${number('completed_runs') ?? 0} 场`;
  if (event.type === 'drain_started') return `已固定完成 ${number('completed_runs') ?? 0} 场，转入清 AP`;
  if (event.type === 'failed') return value('message') || value('reason') || '执行器报告失败';
  if (event.type === 'stopped') return value('reason') || '已停止';
  if (event.type === 'state_changed') return value('state') || '画面变化';
  if (event.type === 'turn_started' || event.type === 'wave_started') return `第 ${number('wave') ?? '?'} 面 · 回合 ${number('turn') ?? 1}`;
  if (event.type === 'run_started') return [value('serial'), value('channel')].filter(Boolean).join(' · ');
  return Object.entries(data).slice(0, 3).map(([key, item]) => `${key}: ${String(item)}`).join(' · ');
}
function eventTime(event: RunEvent) {
  if (!startedAt.value || event.timestamp < 1_000_000_000) return `+${Math.max(0, event.timestamp - startedAt.value).toFixed(1)} s`;
  const offset = Math.max(0, event.timestamp - startedAt.value);
  return `+${offset.toFixed(1)} s`;
}
</script>

<template>
  <section class="run-workspace">
    <header class="workspace-page-heading">
      <div><span>MISSION CONTROL</span><h2>运行</h2><p>启动前检查作业与设备；运行中只看真实事件与画面确认。</p></div>
      <div class="run-heading-actions"><button class="button-secondary" @click="emit('history')"><AppIcon name="history" :size="14" /> 运行记录</button><button v-if="active" class="button-danger" :disabled="state.phase === 'stopping'" @click="emit('stop')"><AppIcon name="stop" :size="14" />{{ state.phase === 'stopping' ? '停止中…' : '停止' }}</button><button v-else class="button-primary" :disabled="Boolean(startDisabledReason) || starting" :title="startDisabledReason" @click="emit('start')"><AppIcon name="play" :size="14" />{{ starting ? '启动中…' : startLabel }}</button></div>
    </header>

    <p v-if="startDisabledReason" class="workspace-blocker"><AppIcon name="help" :size="15" />{{ startDisabledReason }} <button v-if="!selectedSerial" @click="emit('prepare')">去准备</button></p>
    <div v-if="diagnostics.length" class="run-diagnostics" role="alert">
      <strong>作业当前不可执行</strong>
      <p v-for="diagnostic in diagnostics" :key="`${diagnostic.path}-${diagnostic.message}`">第 {{ diagnostic.wave ?? '?' }} 面 · 回合 {{ diagnostic.turn ?? '?' }}：{{ diagnostic.message }}</p>
      <button @click="emit('jobs')">返回编队修改</button>
    </div>

    <div class="run-layout">
      <section class="run-summary-card">
        <div class="run-summary-head"><div><span>{{ phaseLabel }}</span><h3>{{ state.jobName || jobName || '当前作业' }}</h3></div><strong>{{ elapsed }}</strong></div>
        <div class="run-progress-grid">
          <div><span>当前画面</span><strong>{{ state.screen || '等待事件' }}</strong></div>
          <div><span>进度</span><strong>第 {{ state.wave }} 面 · 回合 {{ state.turn }}</strong></div>
          <div><span>完成场数</span><strong>{{ state.completedRuns }} / {{ settings.battles }} 场</strong></div>
          <div><span>设备通道</span><strong>{{ state.channel === 'scrcpy' ? 'scrcpy' : 'ADB' }}</strong></div>
          <div><span>AP 恢复</span><strong>{{ recoveryText }}</strong></div>
          <div><span>设备</span><strong>{{ deviceLabel || selectedSerial || '未选择' }}</strong></div>
        </div>
        <div class="run-last-action"><span class="action-icon"><AppIcon name="bolt" :size="17" /></span><div><strong>{{ state.action || '等待执行器动作' }}</strong><small>{{ state.actionDetail || (state.screen ? `当前画面：${state.screen}` : '尚未收到动作事件') }}</small></div></div>
        <p v-if="state.notice" class="run-notice"><AppIcon name="help" :size="14" />{{ state.notice }}</p>
        <p v-if="state.error" class="device-error">{{ state.error }}<span v-if="state.exitCode !== null">（退出码 {{ state.exitCode }}）</span></p>
        <p v-if="state.logDir" class="run-log-path">证据目录：{{ state.logDir }}</p>
        <div class="run-summary-links"><button @click="emit('jobs')">查看编队</button><button @click="emit('prepare')">检查设备与设置</button><button v-if="state.logDir" @click="emit('history')">打开历史详情</button></div>
      </section>

      <section class="run-timeline-card">
        <header><div><strong>事件时间线</strong><small>“已发送输入”不等于游戏受理；以后续画面确认为准。</small></div><div class="run-event-filter"><button v-for="item in ([['all','全部'],['input','输入'],['confirm','确认'],['warning','警告'],['result','结果']] as const)" :key="item[0]" :class="{ active: filter === item[0] }" @click="filter = item[0]">{{ item[1] }}</button></div></header>
        <div class="run-timeline-list">
          <p v-if="!filteredEvents.length" class="run-timeline-empty">启动后会在这里按时间显示真实事件。</p>
          <article v-for="(event, index) in filteredEvents" :key="`${event.timestamp}-${index}`" :class="eventKinds[event.type] ?? 'state'">
            <span>{{ eventTime(event) }}</span>
            <div><strong>{{ eventTitle(event) }}</strong><small>{{ eventText(event) }}</small></div>
          </article>
        </div>
      </section>
    </div>
  </section>
</template>
