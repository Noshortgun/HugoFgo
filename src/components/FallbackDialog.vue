<script setup lang="ts">
import { computed, ref } from 'vue';
import type { CardColor, FallbackScope, FallbackStrategy, Servant, StrategyCriterion } from '../types';
import AppIcon from './AppIcon.vue';
import { servantLabel } from '../servantLabel';
import { resolveServantOrder } from '../servantOrder';

const props = defineProps<{ fallback: FallbackStrategy; fallbackByWave: Record<number, FallbackStrategy | null>; members: Servant[]; dirty?: boolean }>();
const emit = defineEmits<{ close: []; update: [payload: { scope: FallbackScope; value: FallbackStrategy }]; reset: [wave: number]; resetAll: []; save: [] }>();
const scope = ref<FallbackScope>('all');
const activeFallback = computed(() => scope.value === 'all' ? props.fallback : props.fallbackByWave[scope.value] || props.fallback);
const hasOverride = computed(() => scope.value !== 'all' && props.fallbackByWave[scope.value] !== null);
/** 顺序项带编队槽位，同名从者也能分别移动并区分助战。 */
const servantEntries = computed(() => resolveServantOrder(activeFallback.value, props.members).map((entry, index) => {
  const member = props.members[entry.slot];
  return { ...entry, index, label: member ? servantLabel(member) : entry.name };
}));
const criterionMeta: Record<StrategyCriterion, { title: string; icon: string }> = {
  servant: { title: '从者顺序', icon: 'cards' },
  cards: { title: '选卡倾向', icon: 'cards' },
  crit: { title: '暴击率', icon: 'bolt' },
};
const colorNames: Record<CardColor, string> = { buster: '红', arts: '蓝', quick: '绿' };

function update(value: Partial<FallbackStrategy>) {
  const entries = resolveServantOrder(activeFallback.value, props.members);
  emit('update', {
    scope: scope.value,
    value: {
      ...activeFallback.value,
      servantOrder: entries.map((entry) => entry.name),
      servantOrderSlots: entries.map((entry) => entry.slot),
      ...value,
    },
  });
}
function criterionDetail(criterion: StrategyCriterion) {
  if (criterion === 'servant') return servantEntries.value.map((entry) => entry.label).join(' → ');
  if (criterion === 'cards') return activeFallback.value.cards === 'balanced' ? '3 色卡' : `3 ${colorNames[activeFallback.value.cards]}`;
  return '始终参与';
}
function moveCriterion(criterion: StrategyCriterion, delta: number) {
  const order = [...activeFallback.value.strategyOrder];
  const index = order.indexOf(criterion);
  const destination = index + delta;
  if (index < 0 || destination < 0 || destination >= order.length) return;
  [order[index], order[destination]] = [order[destination], order[index]];
  update({ strategyOrder: order });
}
function moveServant(index: number, delta: number) {
  const entries = [...servantEntries.value];
  const destination = index + delta;
  if (destination < 0 || destination >= entries.length) return;
  [entries[index], entries[destination]] = [entries[destination], entries[index]];
  update({ servantOrder: entries.map((entry) => entry.name), servantOrderSlots: entries.map((entry) => entry.slot) });
}
function moveColor(color: CardColor, delta: number) {
  const order = [...activeFallback.value.colorPriority];
  const index = order.indexOf(color);
  const destination = index + delta;
  if (destination < 0 || destination >= order.length) return;
  [order[index], order[destination]] = [order[destination], order[index]];
  update({ colorPriority: order });
}
</script>

<template>
  <div class="fallback-backdrop" @click.self="emit('close')">
    <section class="fallback-dialog">
      <header><div><span class="picker-kicker">FALLBACK STRATEGY</span><h2>后续回合与选卡策略</h2></div><button title="关闭" @click="emit('close')"><AppIcon name="close" /></button></header>
      <div class="fallback-dialog-body">
        <div class="fallback-scope-bar"><strong>设置范围</strong><div class="fallback-scope-tabs"><button :class="{ active: scope === 'all' }" @click="scope = 'all'">全部面</button><button v-for="wave in 3" :key="wave" :class="{ active: scope === wave }" @click="scope = wave as FallbackScope">第 {{ wave }} 面<span v-if="props.fallbackByWave[wave]">·</span></button></div><button v-if="hasOverride" class="reset-scope" @click="emit('reset', scope as number)">恢复默认</button></div>

        <div class="fallback-dialog-option"><strong>宝具使用</strong><div class="fallback-segment"><button :class="{ active: !activeFallback.allowNp }" @click="update({ allowNp: false })">不用宝具</button><button :class="{ active: activeFallback.allowNp }" @click="update({ allowNp: true })">允许宝具</button></div></div>
        <div class="fallback-order-hint">使用左右按钮调整策略顺序，左侧优先。</div>
        <div class="strategy-card-grid">
          <section v-for="criterion in activeFallback.strategyOrder" :key="criterion" class="strategy-config-card">
            <header><AppIcon :name="criterionMeta[criterion].icon" :size="17" /><strong>{{ criterionMeta[criterion].title }}</strong><div class="criterion-arrows"><button :disabled="activeFallback.strategyOrder[0] === criterion" :title="`${criterionMeta[criterion].title}左移`" @click="moveCriterion(criterion, -1)">←</button><button :disabled="activeFallback.strategyOrder.at(-1) === criterion" :title="`${criterionMeta[criterion].title}右移`" @click="moveCriterion(criterion, 1)">→</button></div></header>
            <p class="strategy-current">{{ criterionDetail(criterion) }}</p>
            <div v-if="criterion === 'servant'" class="strategy-card-content"><div class="ordered-chip-list"><div v-for="entry in servantEntries" :key="`${entry.name}-${entry.index}`"><span>{{ entry.label }}</span><div><button :disabled="entry.index === 0" :title="`${entry.label} 左移`" @click="moveServant(entry.index, -1)">←</button><button :disabled="entry.index === activeFallback.servantOrder.length - 1" :title="`${entry.label} 右移`" @click="moveServant(entry.index, 1)">→</button></div></div></div></div>
            <div v-else-if="criterion === 'cards'" class="strategy-card-content"><div class="fallback-segment card-preference"><button v-for="item in ([['buster','3 红'],['quick','3 绿'],['arts','3 蓝'],['balanced','3 色卡']] as const)" :key="item[0]" :class="{ active: activeFallback.cards === item[0] }" @click="update({ cards: item[0] })">{{ item[1] }}</button></div><label>无法组成倾向时</label><div class="ordered-chip-list color-order"><div v-for="color in activeFallback.colorPriority" :key="color" :class="color"><span>{{ colorNames[color] }}</span><div><button :disabled="activeFallback.colorPriority[0] === color" :title="`${colorNames[color]}卡左移`" @click="moveColor(color, -1)">←</button><button :disabled="activeFallback.colorPriority.at(-1) === color" :title="`${colorNames[color]}卡右移`" @click="moveColor(color, 1)">→</button></div></div></div></div>
            <div v-else class="strategy-card-content"><div class="crit-always"><AppIcon name="bolt" :size="17" /><strong>暴击率更高者优先</strong></div></div>
          </section>
        </div>
      </div>
      <footer><span :class="{ 'fallback-dirty': props.dirty }">{{ props.dirty ? '修改尚未保存到作业' : '策略与作业一致' }}</span><div class="fallback-footer-actions"><button class="button-secondary fallback-reset-strategy" title="恢复默认选卡预设，并清除每面覆盖" @click="emit('resetAll')">恢复默认预设</button><button class="button-secondary" @click="emit('close')">关闭</button><button class="button-primary" title="仅应用到当前编辑，仍需在操作序列点击“保存”才会写入作业" @click="emit('save')"><AppIcon name="check" :size="16" /> 应用策略</button></div></footer>
    </section>
  </div>
</template>
