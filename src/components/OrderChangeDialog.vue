<script setup lang="ts">
import { computed, ref } from 'vue';
import type { Servant } from '../types';
import AppIcon from './AppIcon.vue';
import { servantLabel } from '../servantLabel';

const props = defineProps<{ frontline: Servant[]; backline: Servant[]; mysticName: string }>();
const emit = defineEmits<{ close: []; confirm: [outgoing: Servant, incoming: Servant] }>();
const outgoingId = ref<string | null>(null);
const incomingId = ref<string | null>(null);
const outgoing = computed(() => props.frontline.find((member) => member.memberInstanceId === outgoingId.value) ?? null);
const incoming = computed(() => props.backline.find((member) => member.memberInstanceId === incomingId.value) ?? null);
const ready = computed(() => Boolean(outgoing.value && incoming.value));

function chooseOutgoing(member: Servant) {
  outgoingId.value = member.memberInstanceId;
  if (incomingId.value === member.memberInstanceId) incomingId.value = null;
}
function confirm() {
  if (outgoing.value && incoming.value) emit('confirm', outgoing.value, incoming.value);
}
</script>

<template>
  <div class="order-change-backdrop" @click.self="emit('close')">
    <section class="order-change-dialog">
      <header><div><span class="picker-kicker">ORDER CHANGE</span><h2>换人</h2><p>{{ mysticName }} · 先选择下场的从者，再选择上场的候补从者。</p></div><button title="关闭" @click="emit('close')"><AppIcon name="close" /></button></header>
      <div class="order-change-flow">
        <div class="order-change-step">
          <div class="order-change-step-title"><span>STEP 1</span><strong>选择下场的从者</strong><small>当前前排 {{ frontline.length }} 人</small></div>
          <div class="order-change-grid">
            <button v-for="member in frontline" :key="member.memberInstanceId" :class="{ selected: outgoingId === member.memberInstanceId }" @click="chooseOutgoing(member)"><img v-if="member.image" :src="member.image" :alt="member.name" /><span v-else class="order-change-avatar-placeholder">{{ member.name.slice(0, 1) }}</span><strong>{{ servantLabel(member) }}</strong><small>{{ member.role }}</small><em v-if="outgoingId === member.memberInstanceId">下场</em></button>
            <p v-if="!frontline.length" class="order-change-empty">当前没有可下场的从者。</p>
          </div>
        </div>
        <div class="order-change-arc"><AppIcon name="chevron" :size="18" /></div>
        <div class="order-change-step">
          <div class="order-change-step-title"><span>STEP 2</span><strong>选择上场的候补从者</strong><small>候补席 {{ backline.length }} 人</small></div>
          <div class="order-change-grid">
            <button v-for="member in backline" :key="member.memberInstanceId" :class="{ selected: incomingId === member.memberInstanceId }" @click="incomingId = member.memberInstanceId"><img v-if="member.image" :src="member.image" :alt="member.name" /><span v-else class="order-change-avatar-placeholder">{{ member.name.slice(0, 1) }}</span><strong>{{ servantLabel(member) }}</strong><small>{{ member.role }}</small><em v-if="incomingId === member.memberInstanceId">上场</em></button>
            <p v-if="!backline.length" class="order-change-empty">没有可上场的候补从者。</p>
          </div>
        </div>
      </div>
      <div class="order-change-summary"><span>{{ outgoing ? servantLabel(outgoing) : '未选择下场' }} → {{ incoming ? servantLabel(incoming) : '未选择上场' }}</span><small>确认后写入一条换人动作；当前回合之后的前排阵容与可编辑从者会同步更新。</small></div>
      <footer><button class="button-secondary" @click="emit('close')">取消</button><button class="button-primary" :disabled="!ready" @click="confirm"><AppIcon name="check" :size="15" /> 确认换人</button></footer>
    </section>
  </div>
</template>
