<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { EquipInfo } from '../equips';
import { isCommonEquip, toggleCommonEquip } from '../preferences';
import AppIcon from './AppIcon.vue';

const props = defineProps<{ equips: EquipInfo[]; title?: string; allowClear?: boolean }>();
const emit = defineEmits<{ close: []; select: [equip: EquipInfo]; clear: [] }>();
const search = ref('');
const tab = ref<'common' | 'all'>('common');
const page = ref(1);
const pageSize = 100;
const commonCount = computed(() => props.equips.filter(isCommonEquip).length);
const filtered = computed(() => {
  const keyword = search.value.trim();
  return props.equips
    .filter((equip) => (tab.value === 'all' || isCommonEquip(equip)) && (!keyword || equip.name.includes(keyword) || String(equip.id) === keyword))
    .sort((left, right) => Number(isCommonEquip(right)) - Number(isCommonEquip(left)) || right.rarity - left.rarity || left.id - right.id);
});
const pageCount = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize)));
const visible = computed(() => filtered.value.slice((page.value - 1) * pageSize, page.value * pageSize));
watch([search, tab], () => { page.value = 1; });
watch(pageCount, () => { page.value = Math.min(page.value, pageCount.value); });
</script>

<template>
  <div class="equip-picker-backdrop" @click.self="emit('close')">
    <section class="equip-picker-dialog">
      <header><div><span class="picker-kicker">CRAFT ESSENCE</span><h2>{{ props.title || '选择概念礼装' }}</h2></div><button title="关闭" @click="emit('close')"><AppIcon name="close" /></button></header>
      <div class="equip-picker-tabs"><button :class="{ active: tab === 'common' }" @click="tab = 'common'">常用礼装 <span>{{ commonCount }}</span></button><button :class="{ active: tab === 'all' }" @click="tab = 'all'">全部礼装 <span>{{ props.equips.length }}</span></button></div>
      <label class="picker-search"><AppIcon name="search" :size="16" /><input v-model="search" placeholder="搜索当前分类的礼装名称或 ID" /></label>
      <div class="equip-picker-grid">
        <article v-for="equip in visible" :key="equip.id" class="equip-picker-card">
          <button class="equip-select" :title="equip.name" @click="emit('select', equip)">
            <img v-if="equip.icon" :src="equip.icon" :alt="equip.name" />
            <span v-else class="equip-icon-placeholder">{{ equip.name.slice(0, 1) }}</span>
            <strong>{{ equip.name }}</strong>
            <small>{{ equip.rarity }}★ · ID {{ equip.id }}</small>
          </button>
          <button class="equip-common" :class="{ active: isCommonEquip(equip) }" :aria-label="isCommonEquip(equip) ? `取消常用${equip.name}` : `设为常用${equip.name}`" :title="isCommonEquip(equip) ? '取消常用' : '设为常用'" @click="toggleCommonEquip(equip)">{{ isCommonEquip(equip) ? '★' : '☆' }}</button>
        </article>
        <p v-if="!visible.length" class="equip-picker-empty">{{ !props.equips.length ? '当前版本没有礼装图库，请使用在线版或从作业文件导入礼装' : tab === 'common' && !search ? '暂无常用礼装，可在“全部礼装”中点击星标添加' : '没有匹配的礼装' }}</p>
      </div>
      <div v-if="filtered.length > pageSize" class="equip-pagination"><button :disabled="page <= 1" @click="page--">上一页</button><span>第 {{ page }} / {{ pageCount }} 页 · {{ filtered.length }} 张</span><button :disabled="page >= pageCount" @click="page++">下一页</button></div>
      <footer><span>默认包含五星牵绊礼装、万华镜和黑之圣杯</span><button v-if="props.allowClear" class="equip-clear" @click="emit('clear')">设为不限</button><button class="button-primary" @click="emit('close')">关闭</button></footer>
    </section>
  </div>
</template>
