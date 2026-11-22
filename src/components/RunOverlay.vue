<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue';
import type { RunState } from '../runState';
import AppIcon from './AppIcon.vue';

const props = defineProps<{ state: RunState }>();
const emit = defineEmits<{ close: []; stop: [] }>();
const now = ref(Date.now());
const timer = window.setInterval(() => {
  if (props.state.phase === 'running') now.value = Date.now();
}, 1000);
onUnmounted(() => window.clearInterval(timer));

const recoveryTotal = computed(() => Object.values(props.state.recoveryUsed).reduce((sum, count) => sum + count, 0));
const heading = computed(() => ({
  running: '正在自动战斗',
  stopping: '正在停止执行器',
  completed: '运行完成',
  stopped: '运行已停止',
  failed: '运行失败',
  idle: '等待运行',
}[props.state.phase]));
const elapsed = computed(() => {
  if (!props.state.startedAtMs) return '00:00';
  const seconds = Math.max(0, Math.floor((now.value - props.state.startedAtMs) / 1000));
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
});
const progress = computed(() => props.state.screen
  ? `${props.state.screen.toUpperCase()} · 第 ${props.state.wave} 面 · 回合 ${props.state.turn}`
  : '等待执行器状态');
const actionDetail = computed(() => props.state.actionDetail
  || (props.state.screen ? `当前画面：${props.state.screen}` : '执行器尚未发送动作'));
const active = computed(() => props.state.phase === 'running' || props.state.phase === 'stopping');
</script>

<template>
  <section class="run-overlay" aria-live="polite">
    <div class="run-overlay-heading"><div><span v-if="active" class="pulse-dot"></span><strong>{{ heading }}</strong></div><button title="收起运行面板" @click="emit('close')"><AppIcon name="close" :size="16" /></button></div>
    <p>{{ state.jobName || state.jobPath }}</p>
    <div class="run-overlay-progress"><span>{{ progress }}</span><strong>{{ elapsed }}</strong></div>
    <div class="run-recovery-count"><span>AP 恢复道具</span><strong>已用 {{ recoveryTotal }} 个</strong></div>
    <div class="run-current"><span class="action-icon servant"><AppIcon name="bolt" /></span><div><strong>{{ state.action || '等待执行器事件' }}</strong><small>{{ actionDetail }}</small></div></div>
    <p v-if="state.completedRuns" class="run-log-path">已完成 {{ state.completedRuns }} 场</p>
    <p class="run-log-path">设备通道：{{ state.channel === 'scrcpy' ? 'scrcpy 持续帧与输入' : 'ADB' }}</p>
    <p v-if="state.notice" class="device-error" role="alert">{{ state.notice }}</p>
    <p v-if="state.error" class="device-error">{{ state.error }}<span v-if="state.exitCode !== null">（退出码 {{ state.exitCode }}）</span></p>
    <p v-if="state.logDir" class="run-log-path">日志：{{ state.logDir }}</p>
    <div class="run-overlay-actions"><button v-if="active" class="stop" :disabled="state.phase === 'stopping'" @click="emit('stop')"><AppIcon name="stop" :size="13" />{{ state.phase === 'stopping' ? '停止中…' : '停止' }}</button><button v-else @click="emit('close')">关闭</button></div>
  </section>
</template>
