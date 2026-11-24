<script setup lang="ts">
import AppIcon from './AppIcon.vue';
import type { RunPhase } from '../runState';

const props = defineProps<{
  active: 'prepare' | 'jobs' | 'run' | 'history';
  runPhase: RunPhase;
  deviceOnline: boolean;
  deviceLabel: string;
}>();
const emit = defineEmits<{ navigate: [value: 'prepare' | 'jobs' | 'run' | 'history'] }>();

const items = [
  { key: 'prepare', label: '准备', detail: '设备与资源', icon: 'device' },
  { key: 'jobs', label: '编队', detail: '从者与编排', icon: 'cards' },
  { key: 'run', label: '运行', detail: '启动与监控', icon: 'play' },
  { key: 'history', label: '记录', detail: '历史与证据', icon: 'history' },
] as const;
const runLabel = () => {
  if (props.runPhase === 'running') return '运行中';
  if (props.runPhase === 'stopping') return '停止中';
  if (props.runPhase === 'failed') return '有失败';
  if (props.runPhase === 'completed') return '已完成';
  if (props.runPhase === 'stopped') return '已停止';
  return '空闲';
};
</script>

<template>
  <aside class="app-sidebar">
    <div class="sidebar-brand" title="HugoFgo"><span>F</span><strong>HUGO<br />FGO</strong></div>
    <nav aria-label="工作区">
      <button v-for="item in items" :key="item.key" :class="{ active: props.active === item.key, running: item.key === 'run' && (props.runPhase === 'running' || props.runPhase === 'stopping') }" :title="`${item.label} · ${item.detail}`" @click="emit('navigate', item.key)">
        <AppIcon :name="item.icon" :size="20" />
        <span>{{ item.label }}</span>
        <small v-if="item.key === 'run'">{{ runLabel() }}</small>
      </button>
    </nav>
    <div class="sidebar-status" :class="{ online: props.deviceOnline }" :title="props.deviceLabel">
      <span class="status-dot"></span>
      <small>{{ props.deviceOnline ? '设备在线' : '未连接设备' }}</small>
    </div>
    <button class="sidebar-settings" title="运行设置与设备准备" @click="emit('navigate', 'prepare')"><AppIcon name="settings" :size="18" /><span>设置</span></button>
  </aside>
</template>
