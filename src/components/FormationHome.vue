<script setup lang="ts">
import { computed, ref } from 'vue';
import type { FallbackStrategy, MysticCode, Servant } from '../types';
import { ascensionOptionsFor } from '../job';
import { recoveryItemLabels, type RecoveryItem, type RunSettings } from '../runSettings';
import AppIcon from './AppIcon.vue';

const props = withDefaults(defineProps<{
  members: Servant[];
  mystic: MysticCode;
  fallback: FallbackStrategy;
  gender: 'female' | 'male';
  settings: RunSettings;
  startDisabled?: boolean;
  startDisabledReason?: string;
  startLabel?: string;
  galleryEmpty?: boolean;
  resourceRegion?: string;
  jobName?: string;
}>(), { startDisabled: false, startDisabledReason: '', startLabel: '开始', galleryEmpty: false, resourceRegion: 'CN', jobName: '' });
const emit = defineEmits<{
  picker: [type: 'servant' | 'ce' | 'mystic', slot?: number];
  plan: [];
  support: [index: number];
  reorder: [source: number, target: number];
  remove: [index: number];
  ascension: [index: number, limitCount: number];

  start: [];
  library: [];
  prepare: [];
  updateSettings: [settings: RunSettings];
  gender: [value: 'female' | 'male'];
}>();
const ascensionServantIds = new Set([800100, 4000100]);
const draggingMember = ref<number | null>(null);
const appleOptions: { key: RecoveryItem; icon: string }[] = [
  { key: 'gold', icon: '/assets/items/gold.png' },
  { key: 'silver', icon: '/assets/items/silver.png' },
  { key: 'copper', icon: '/assets/items/copper.png' },
  { key: 'bronze', icon: '/assets/items/bronze.png' },
  { key: 'saint_quartz', icon: '/assets/items/quartz.png' },
];
const selectedApples = computed(() => props.settings.recovery.allowed.map((key) => appleOptions.find((item) => item.key === key)).filter((item): item is typeof appleOptions[number] => item !== undefined));
const orderedAppleOptions = computed(() => [
  ...selectedApples.value,
  ...appleOptions.filter((item) => !props.settings.recovery.allowed.includes(item.key)),
]);
const quartzConfirmOpen = ref(false);
const filledSlots = computed(() => props.members.filter((member) => !member.empty).length);
function updateBattles(event: Event) {
  const value = Math.max(1, Math.floor(Number((event.target as HTMLInputElement).value) || 1));
  emit('updateSettings', { ...props.settings, battles: value });
}
function toggleApple(key: RecoveryItem, checked: boolean) {
  if (key === 'saint_quartz' && checked) {
    quartzConfirmOpen.value = true;
    return;
  }
  const current = props.settings.recovery.allowed;
  const allowed = checked
    ? [...current.filter((item) => item !== key), key]
    : current.filter((item) => item !== key);
  emit('updateSettings', { ...props.settings, recovery: { ...props.settings.recovery, allowed } });
}
function confirmQuartz() {
  const allowed = props.settings.recovery.allowed.includes('saint_quartz')
    ? props.settings.recovery.allowed
    : [...props.settings.recovery.allowed, 'saint_quartz' as RecoveryItem];
  emit('updateSettings', { ...props.settings, recovery: { ...props.settings.recovery, allowed } });
  quartzConfirmOpen.value = false;
}
function cancelQuartz() {
  quartzConfirmOpen.value = false;
  emit('updateSettings', { ...props.settings });
}
function toggleDrain() {
  emit('updateSettings', { ...props.settings, recovery: { ...props.settings.recovery, drain_after_turns: !props.settings.recovery.drain_after_turns } });
}
function displayName(member: Servant) {
  if (member.support && !member.supportRequired) return '任意助战';
  if (member.empty) return '不限制';
  return member.name;
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
</script>

<template>
  <main class="formation-screen">
    <div class="game-background"></div>
    <section class="formation-area">
      <div class="formation-topline"><div><span class="formation-kicker">PARTY FORMATION</span><strong>{{ props.jobName || '未命名作业' }}</strong><button class="formation-library-button" @click="emit('library')"><AppIcon name="library" :size="12" /> 作业库</button></div><div v-if="props.galleryEmpty" class="empty-gallery-prompt"><span>{{ props.resourceRegion }} 图库为空，当前使用占位卡</span><button @click="emit('prepare')">前往准备页</button></div><div class="party-cost"><span>编队槽位</span><strong>{{ filledSlots }} <small>/ 6</small></strong></div></div>
      <div class="game-formation-grid">
        <article v-for="(member, index) in props.members" :key="member.memberInstanceId" class="game-member" :class="{ support: member.support, grand: member.grandRequired, back: index >= 3, empty: member.empty, placeholder: member.placeholder }" draggable="true" @dragstart="draggingMember = index" @dragover.prevent @drop="dropMember(index)">
          <button v-if="!member.empty && (!member.support || member.supportRequired)" class="member-delete" :title="`删除第 ${index + 1} 位从者`" @click.stop="emit('remove', index)"><AppIcon name="close" :size="13" /></button>
          <button class="member-art-button" @click="emit('picker', 'servant', index)" :title="`更换第 ${index + 1} 位从者`">
            <img v-if="!member.empty && member.art && (!member.support || member.supportRequired)" class="member-full-art" :src="member.art" :alt="member.name" />
            <span v-else-if="member.placeholder" class="placeholder-member-art"><AppIcon name="cards" :size="24" /><strong>{{ member.name }}</strong><small>无本地卡面</small></span>
            <span v-else class="empty-member-art" :class="{ 'support-empty-card': member.support }"><AppIcon :name="member.support ? 'shield' : 'plus'" :size="27" /><small>{{ member.support ? '任意助战' : '选择从者' }}</small></span>
            <span class="card-frame"></span><span class="member-slot">0{{ index + 1 }}</span>
            <span v-if="member.support" class="game-support-badge" role="button" tabindex="0" @click.stop="emit('support', index)" @keydown.enter.stop="emit('support', index)">{{ member.grandRequired ? '冠位助战' : '助战' }}</span>
            <span v-if="!member.empty && (!member.support || member.supportRequired)" class="member-class">{{ member.role }}</span>
            <span v-if="!member.empty" class="change-member">更换从者</span>
          </button>
          <button v-if="member.support" class="member-ce-button" :class="{ 'grand-ce-list': member.grandRequired }" @click="emit('support', index)" title="设置助战与概念礼装">
            <template v-if="member.grandRequired"><span v-for="(slot, slotIndex) in [member.grandCeTop, member.grandCeMiddle, member.grandCeBottom]" :key="slotIndex" class="grand-ce-row"><img v-if="slot?.image" :src="slot.image" :alt="slot.name" /><i v-else></i><small>{{ ['任意概念礼装', '牵绊 / NP50%', '仅限加羁绊'][slotIndex] }}</small><strong>{{ slot?.name || '不限' }}</strong></span></template>
            <template v-else-if="member.ceImage"><img :src="member.ceImage" :alt="member.ce" /><span><strong>{{ member.ce }}</strong></span></template>
            <template v-else><span class="support-ce-empty"><AppIcon name="plus" :size="13" /> 概念礼装不限</span></template>
          </button>
          <button class="member-name-button" @click="emit('picker', 'servant', index)"><span><strong>{{ displayName(member) }}</strong><small v-if="member.support">{{ member.supportRequired ? '指定助战' : '不指定身份，礼装可单独筛选' }}</small><small v-else-if="!member.empty">拖动卡片可调整顺序</small></span><AppIcon name="search" :size="15" /></button>
          <label v-if="!member.empty && member.svtId !== undefined && ascensionServantIds.has(member.svtId)" class="member-ascension" title="切换灵基阶段，仅编队阶段生效">{{ ascensionLabel(member) }} <select :value="member.limitCount ?? 4" @change.stop="emit('ascension', index, Number(($event.target as HTMLSelectElement).value))"><option v-for="stage in ascensionOptionsFor(member)" :key="stage" :value="stage">{{ stage }}</option></select></label>

        </article>
      </div>
      <div class="formation-lines"><span>FRONTLINE · 前排</span><span>BACKLINE · 候补</span></div>
    </section>

    <section class="mission-control-bar">
      <button class="mission-button primary" @click="emit('plan')"><AppIcon name="cards" :size="22" /><span><strong>操作序列</strong><small>支持每面多回合编辑</small></span><AppIcon name="chevron" :size="15" /></button>
      <div class="mission-button repeat-input-button"><AppIcon name="history" :size="21" /><span><strong>刷本次数</strong><small>保存到运行设置</small></span><label><input :value="props.settings.battles" type="number" min="1" max="999" aria-label="刷本次数" @change="updateBattles" /><small>次</small></label></div>
      <details class="apple-selector"><summary class="mission-button"><AppIcon name="plus" :size="21" /><span><strong>体力恢复</strong><small>只使用勾选道具</small></span><div class="apple-icon-row"><img v-for="item in selectedApples" :key="item.key" :src="item.icon" :alt="recoveryItemLabels[item.key]" :title="recoveryItemLabels[item.key]" /><span v-if="!selectedApples.length" class="apple-none">不使用</span></div></summary><div class="apple-picker"><div class="apple-priority-list"><label v-for="item in orderedAppleOptions" :key="item.key" :class="{ selected: props.settings.recovery.allowed.includes(item.key), 'requires-confirm': item.key === 'saint_quartz' }" :title="item.key === 'saint_quartz' ? '勾选后会弹出圣晶石消耗确认' : recoveryItemLabels[item.key]"><span v-if="props.settings.recovery.allowed.includes(item.key)" class="apple-order-index">{{ props.settings.recovery.allowed.indexOf(item.key) + 1 }}</span><input type="checkbox" :checked="props.settings.recovery.allowed.includes(item.key)" :aria-label="recoveryItemLabels[item.key]" @change="toggleApple(item.key, ($event.target as HTMLInputElement).checked)" /><img :src="item.icon" :alt="recoveryItemLabels[item.key]" /><span class="apple-label">{{ recoveryItemLabels[item.key] }}</span></label></div><button type="button" class="apple-drain-toggle" :class="{ active: props.settings.recovery.drain_after_turns }" @click="toggleDrain"><span class="apple-toggle-track"><i></i></span><span>场数完成后清空剩余 AP</span><small>{{ props.settings.recovery.drain_after_turns ? '已开启' : '已关闭' }}</small></button></div></details>
    </section>

    <section class="master-zone">
      <div class="master-figure-wrap"><button class="master-figure-button" @click="emit('picker', 'mystic')" title="选择御主礼装"><img :src="props.mystic.figure" :alt="props.mystic.name" /><span><small>御主礼装</small><strong>{{ props.mystic.name }}</strong><em>点击更换</em></span></button><div class="gender-switch" aria-label="御主性别"><button :class="{ active: props.gender === 'female' }" @click="emit('gender', 'female')">女</button><button :class="{ active: props.gender === 'male' }" @click="emit('gender', 'male')">男</button></div></div>
      <div class="master-actions"><div class="master-action-label"><span>御主技能</span></div><div class="master-skill-row"><button v-for="(skill, index) in props.mystic.skills" :key="index" :title="`御主技能 ${index + 1}`"><img :src="props.mystic.skillIconUrls?.[index] || `/assets/skills/${skill}.png`" :alt="`御主技能${index + 1}`" /><span>{{ index + 1 }}</span></button></div><p class="master-skill-note">在操作序列中编入技能</p></div>
    </section>

    <div class="start-zone"><div class="start-ring"></div><button class="game-start-button" :disabled="props.startDisabled" :title="props.startDisabledReason || '开始战斗'" @click="emit('start')"><span>{{ props.startLabel }}</span><small>BATTLE</small></button></div>
    <div class="game-version">HUGOFGO · DESKTOP</div>
    <div v-if="quartzConfirmOpen" class="quartz-confirm-backdrop" @click.self="cancelQuartz">
      <section class="quartz-confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="quartz-confirm-title">
        <span class="quartz-confirm-icon"><AppIcon name="shield" :size="24" /></span>
        <h3 id="quartz-confirm-title">允许使用圣晶石？</h3>
        <p>体力不足时，会使用圣晶石补充体力。你可以随时取消勾选。</p>
        <div class="quartz-confirm-actions"><button class="button-secondary" @click="cancelQuartz">取消</button><button class="button-primary" @click="confirmQuartz">允许使用</button></div>
      </section>
    </div>
  </main>
</template>
