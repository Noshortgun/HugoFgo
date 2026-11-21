<script setup lang="ts">
import { computed, ref } from 'vue';
import AppIcon from './AppIcon.vue';

const props = withDefaults(defineProps<{ recoveryUsed?: Record<string, number> }>(), { recoveryUsed: () => ({}) });
const emit = defineEmits<{ close: [] }>();
const paused = ref(false);
const recoveryTotal = computed(() => Object.values(props.recoveryUsed).reduce((sum, count) => sum + count, 0));
</script>

<template>
  <section class="run-overlay">
    <div class="run-overlay-heading"><div><span class="pulse-dot"></span><strong>{{ paused ? '任务已暂停' : '正在自动战斗' }}</strong></div><button title="收起" @click="emit('close')"><AppIcon name="close" :size="16" /></button></div>
    <p>终末之海 · 深蓝的航路</p>
    <div class="run-overlay-progress"><span>BATTLE 1/3 · 回合 1</span><strong>00:18</strong></div>
    <div class="run-recovery-count"><span>AP 恢复道具</span><strong>已用 {{ recoveryTotal }} 个</strong></div>
    <div class="run-current"><span class="action-icon servant"><AppIcon name="bolt" /></span><div><strong>{{ paused ? '设备操作已暂停' : '阿尔托莉雅 · 一技能' }}</strong><small>{{ paused ? '点击继续后恢复当前动作' : '等待技能动画结束并确认攻击按钮' }}</small></div></div>
    <div class="run-overlay-actions"><button @click="paused = !paused"><AppIcon :name="paused ? 'play' : 'pause'" :size="14" />{{ paused ? '继续' : '暂停' }}</button><button class="stop" @click="emit('close')"><AppIcon name="stop" :size="13" />停止</button></div>
  </section>
</template>
