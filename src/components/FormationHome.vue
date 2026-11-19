<script setup lang="ts">
import { computed, ref } from 'vue';
import type { FallbackStrategy, MysticCode, Servant } from '../types';
import { ascensionOptionsFor } from '../job';
import AppIcon from './AppIcon.vue';

const props = defineProps<{ members: Servant[]; mystic: MysticCode; fallback: FallbackStrategy; gender: 'female' | 'male' }>();
const emit = defineEmits<{
  picker: [type: 'servant' | 'ce' | 'mystic', slot?: number];
  plan: [];
  support: [index: number];
  reorder: [source: number, target: number];
  remove: [index: number];
  ascension: [index: number, limitCount: number];

  start: [];
  utility: [type: 'jobs' | 'history' | 'settings'];
  gender: [value: 'female' | 'male'];
}>();
const ascensionServantIds = new Set([800100, 4000100]);
type AppleKey = 'gold' | 'silver' | 'copper' | 'bronze' | 'quartz';
const repeat = ref(10);
const draggingMember = ref<number | null>(null);
const draggingApple = ref<AppleKey | null>(null);
const apples = ref<Record<AppleKey, boolean>>({ gold: true, silver: true, copper: false, bronze: false, quartz: false });
const appleOrder = ref<AppleKey[]>(['gold', 'silver', 'copper', 'bronze', 'quartz']);
const appleOptions: { key: AppleKey; name: string; icon: string }[] = [
  { key: 'gold', name: '金苹果', icon: '/assets/items/gold.png' },
  { key: 'silver', name: '银苹果', icon: '/assets/items/silver.png' },
  { key: 'copper', name: '铜苹果', icon: '/assets/items/copper.png' },
  { key: 'bronze', name: '青铜苹果', icon: '/assets/items/bronze.png' },
  { key: 'quartz', name: '圣晶石', icon: '/assets/items/quartz.png' },
];
const orderedAppleOptions = computed(() => appleOrder.value.map((key) => appleOptions.find((item) => item.key === key)!));
const selectedApples = computed(() => orderedAppleOptions.value.filter((item) => apples.value[item.key]));
function displayName(member: Servant) {
  if (member.empty) return '空位';
  return member.support && !member.supportRequired ? '任意助战' : member.name;
}
function ascensionLabel(member: Servant) {
  if (member.svtId === 800100) return ({ 1: '基础', 3: '奥特瑙斯', 4: '帕拉丁' })[member.limitCount ?? 4] ?? '基础';
  if (member.svtId === 4000100) return member.limitCount === 3 ? '第 3 再临' : '第 1/2 再临';
  return `灵基 ${member.limitCount ?? 4}`;
}
function dropMember(index: number) {
  if (draggingMember.value !== null) emit('reorder', draggingMember.value, index);
  draggingMember.value = null;
}
function dropApple(key: AppleKey) {
  const source = draggingApple.value;
  if (!source || source === key) return;
  const order = appleOrder.value.filter((item) => item !== source);
  order.splice(order.indexOf(key), 0, source);
  appleOrder.value = order;
  draggingApple.value = null;
}
</script>

<template>
  <main class="formation-screen">
    <div class="game-background"></div>
    <header class="game-header">
      <div class="game-brand"><div class="game-brand-mark">F</div><div><strong>HugoFgo</strong><small>AUTOMATION CONSOLE</small></div></div>
      <div class="header-context"><strong>编队与自动化</strong><small>导入作业生成执行脚本，游戏内编队由玩家手动完成</small></div>
      <div class="header-utilities"><button title="作业库" @click="emit('utility', 'jobs')"><AppIcon name="library" :size="18" /><span>作业库</span></button><button title="运行记录" @click="emit('utility', 'history')"><AppIcon name="history" :size="18" /><span>记录</span></button><button title="设置与资源" @click="emit('utility', 'settings')"><AppIcon name="settings" :size="18" /><span>设置</span></button><div class="game-device"><span class="status-dot"></span><div><strong>MuMu 模拟器</strong><small>演示设备 · 未接入</small></div></div></div>
    </header>

    <section class="formation-area">
      <div class="formation-topline"><div><span class="formation-kicker">PARTY FORMATION</span><strong>出战编队</strong></div><div class="party-cost"><span>编队 COST</span><strong>112 <small>/ 117</small></strong></div></div>
      <div class="game-formation-grid">
        <article v-for="(member, index) in props.members" :key="member.memberInstanceId" class="game-member" :class="{ support: member.support, grand: member.grandRequired, back: index >= 3, empty: member.empty, placeholder: member.placeholder }" draggable="true" @dragstart="draggingMember = index" @dragover.prevent @drop="dropMember(index)">
          <button v-if="!member.empty && (!member.support || member.supportRequired)" class="member-delete" :title="`删除第 ${index + 1} 位从者`" @click.stop="emit('remove', index)"><AppIcon name="close" :size="13" /></button>
          <button class="member-art-button" @click="emit('picker', 'servant', index)" :title="`更换第 ${index + 1} 位从者`">
            <img v-if="!member.empty && member.art && (!member.support || member.supportRequired)" class="member-full-art" :src="member.art" :alt="member.name" />
            <span v-else-if="member.placeholder" class="placeholder-member-art"><AppIcon name="cards" :size="24" /><strong>{{ member.name }}</strong><small>无本地卡面</small></span>
            <span v-else class="empty-member-art" :class="{ 'support-empty-card': member.support && !member.empty }"><AppIcon :name="member.support && !member.empty ? 'shield' : 'plus'" :size="27" /><small>{{ member.support && !member.empty ? '任意助战' : '选择从者' }}</small></span>
            <span class="card-frame"></span><span class="member-slot">0{{ index + 1 }}</span>
            <span v-if="member.support" class="game-support-badge" role="button" tabindex="0" @click.stop="emit('support', index)" @keydown.enter.stop="emit('support', index)">{{ member.grandRequired ? '冠位助战' : '助战' }}</span>
            <span v-if="!member.empty && (!member.support || member.supportRequired)" class="member-class">{{ member.role }}</span>
            <span v-if="!member.empty" class="change-member">更换从者</span>
          </button>
          <button v-if="member.support && !member.empty" class="member-ce-button" :class="{ 'grand-ce-list': member.grandRequired }" @click="emit('support', index)" title="设置助战与概念礼装">
            <template v-if="member.grandRequired"><span v-for="(slot, slotIndex) in [member.grandCeTop, member.grandCeMiddle, member.grandCeBottom]" :key="slotIndex" class="grand-ce-row"><img v-if="slot?.image" :src="slot.image" :alt="slot.name" /><i v-else></i><small>{{ ['任意概念礼装', '牵绊 / NP50%', '仅限加羁绊'][slotIndex] }}</small><strong>{{ slot?.name || '不限' }}</strong></span></template>
            <template v-else-if="member.ceImage"><img :src="member.ceImage" :alt="member.ce" /><span><strong>{{ member.ce }}</strong></span></template>
            <template v-else><span class="support-ce-empty"><AppIcon name="plus" :size="13" /> 概念礼装不限</span></template>
          </button>
          <button class="member-name-button" @click="emit('picker', 'servant', index)"><span><strong>{{ displayName(member) }}</strong><small v-if="member.support && !member.empty">{{ member.supportRequired ? '指定助战' : '不指定时随机选择' }}</small><small v-else-if="!member.empty">拖动卡片可调整顺序</small></span><AppIcon name="search" :size="15" /></button>
          <label v-if="!member.empty && member.svtId !== undefined && ascensionServantIds.has(member.svtId)" class="member-ascension" title="切换灵基阶段，仅编队阶段生效">{{ ascensionLabel(member) }} <select :value="member.limitCount ?? 4" @change.stop="emit('ascension', index, Number(($event.target as HTMLSelectElement).value))"><option v-for="stage in ascensionOptionsFor(member)" :key="stage" :value="stage">{{ stage }}</option></select></label>

        </article>
      </div>
      <div class="formation-lines"><span>FRONTLINE · 前排</span><span>BACKLINE · 候补</span></div>
    </section>

    <section class="mission-control-bar">
      <button class="mission-button primary" @click="emit('plan')"><AppIcon name="cards" :size="22" /><span><strong>操作序列</strong><small>支持每面多回合编辑</small></span><AppIcon name="chevron" :size="15" /></button>
      <div class="mission-button repeat-input-button"><AppIcon name="history" :size="21" /><span><strong>刷本次数</strong><small>可随时修改</small></span><label><input v-model.number="repeat" type="number" min="1" max="999" aria-label="刷本次数" /><small>次</small></label></div>
      <details class="apple-selector"><summary class="mission-button"><AppIcon name="plus" :size="21" /><span><strong>体力恢复</strong><small>选择道具与使用顺序</small></span><div class="apple-icon-row"><img v-for="item in selectedApples" :key="item.key" :src="item.icon" :alt="item.name" :title="item.name" /></div></summary><div class="apple-picker"><p>按顺序优先使用 · 拖动图标排序</p><div class="apple-priority-list"><label v-for="item in orderedAppleOptions" :key="item.key" :class="{ selected: apples[item.key] }" :title="item.name" draggable="true" @dragstart="draggingApple = item.key" @dragover.prevent @drop="dropApple(item.key)"><input v-model="apples[item.key]" type="checkbox" :aria-label="item.name" /><span>{{ appleOrder.indexOf(item.key) + 1 }}</span><img :src="item.icon" :alt="item.name" /></label></div></div></details>
    </section>

    <section class="master-zone">
      <div class="master-figure-wrap"><button class="master-figure-button" @click="emit('picker', 'mystic')" title="选择御主礼装"><img :src="props.mystic.figure" :alt="props.mystic.name" /><span><small>御主礼装</small><strong>{{ props.mystic.name }}</strong><em>点击更换</em></span></button><div class="gender-switch" aria-label="御主性别"><button :class="{ active: props.gender === 'female' }" @click="emit('gender', 'female')">女</button><button :class="{ active: props.gender === 'male' }" @click="emit('gender', 'male')">男</button></div></div>
      <div class="master-actions"><div class="master-action-label"><span>御主技能</span></div><div class="master-skill-row"><button v-for="(skill, index) in props.mystic.skills" :key="index" :title="`御主技能 ${index + 1}`"><img :src="props.mystic.skillIconUrls?.[index] || `/assets/skills/${skill}.png`" :alt="`御主技能${index + 1}`" /><span>{{ index + 1 }}</span></button></div><p class="master-skill-note">在操作序列中编入技能</p></div>
    </section>

    <div class="start-zone"><div class="start-ring"></div><button class="game-start-button" @click="emit('start')"><span>开始</span><small>START</small></button><p>开始前检测编队 · 不自动修改游戏编队</p></div>
    <div class="game-version">HUGOFGO · PROTOTYPE 0.5</div>
  </main>
</template>
