<script setup lang="ts">
import { computed, ref } from 'vue';
import type { MysticCode, Servant } from '../types';
import { favoriteServantKeys, isFavoriteServant, servantPreferenceKey, toggleFavoriteServant } from '../preferences';
import { resourceUrl } from '../resources';
import AppIcon from './AppIcon.vue';

const props = defineProps<{ type: 'servant' | 'ce' | 'mystic'; servants: Servant[]; mysticCodes: MysticCode[]; slot?: number }>();
const emit = defineEmits<{ close: []; servant: [index: number, servant: Servant]; ce: [index: number, name: string, image: string]; mystic: [code: MysticCode] }>();
const search = ref('');
const classFilter = ref('全部');
const title = computed(() => props.type === 'servant' ? '选择从者' : props.type === 'ce' ? '选择概念礼装' : '选择御主礼装');
const classOrder = ['Saber', 'Archer', 'Lancer', 'Rider', 'Caster', 'Assassin', 'Berserker', 'Ruler', 'Avenger', 'Moon Cancer', 'Alter Ego', 'Foreigner', 'Pretender', 'Shielder', 'Beast'];
const classIconIds: Record<string, number> = { Saber: 1, Archer: 2, Lancer: 3, Rider: 4, Caster: 5, Assassin: 6, Berserker: 7, Shielder: 8, Ruler: 9, 'Alter Ego': 10, Avenger: 11, 'Moon Cancer': 23, Foreigner: 25, Pretender: 28, Beast: 33 };
const classes = computed(() => classOrder.filter((role) => props.servants.some((servant) => servant.role === role)));
const classIcon = (role: string) => resourceUrl(`assets/classes/${classIconIds[role]}.png`);
const visibleServants = computed(() => props.servants
  .filter((item) => (classFilter.value === '全部' || item.role === classFilter.value) && item.name.includes(search.value.trim()))
  .sort((left, right) => Number(isFavoriteServant(right)) - Number(isFavoriteServant(left)) || left.name.localeCompare(right.name, 'zh-CN')));
</script>

<template>
  <div class="picker-backdrop" @click.self="emit('close')">
    <section class="picker-dialog servant-picker-dialog">
      <div class="picker-heading"><div><span class="picker-kicker">FORMATION</span><h2>{{ title }}</h2></div><button title="关闭" @click="emit('close')"><AppIcon name="close" /></button></div>
      <template v-if="type === 'servant'">
        <label class="picker-search"><AppIcon name="search" :size="16" /><input v-model="search" placeholder="搜索从者名称" /></label>
        <div class="servant-picker-tools">
          <div class="servant-class-filter" aria-label="职阶筛选">
            <button :class="{ active: classFilter === '全部' }" @click="classFilter = '全部'">全部</button>
            <button v-for="role in classes" :key="role" class="class-icon-button" :class="{ active: classFilter === role }" :title="role" :aria-label="`筛选${role}`" @click="classFilter = role"><img :src="classIcon(role)" :alt="role" /></button>
          </div>
          <span>{{ favoriteServantKeys.length }} 位中意 · {{ visibleServants.length }} 位结果</span>
        </div>
        <div class="servant-picker-grid">
          <article v-for="servant in visibleServants" :key="servantPreferenceKey(servant)" class="servant-picker-card">
            <button class="servant-select" @click="emit('servant', props.slot || 0, servant)">
              <img v-if="servant.image" :src="servant.image" :alt="servant.name" />
              <strong>{{ servant.name }}</strong>
              <small>{{ servant.role }}</small>
            </button>
            <button class="servant-favorite" :class="{ active: isFavoriteServant(servant) }" :aria-label="isFavoriteServant(servant) ? `取消中意${servant.name}` : `设为中意${servant.name}`" :title="isFavoriteServant(servant) ? '取消中意' : '设为中意并优先显示'" @click="toggleFavoriteServant(servant)">{{ isFavoriteServant(servant) ? '★' : '☆' }}</button>
          </article>
          <p v-if="!visibleServants.length" class="servant-picker-empty">没有符合搜索和职阶条件的从者</p>
        </div>
      </template>
      <div v-else-if="type === 'ce'" class="ce-picker-grid">
        <button v-for="servant in props.servants" :key="servant.ce" @click="emit('ce', props.slot || 0, servant.ce, servant.ceImage)"><img :src="servant.ceImage" :alt="servant.ce" /><strong>{{ servant.ce }}</strong><small>概念礼装 · 满破</small></button>
      </div>
      <div v-else class="mystic-picker-grid">
        <button v-for="code in props.mysticCodes" :key="code.id" :aria-label="code.name" :title="code.name" @click="emit('mystic', code)"><img class="mystic-item" :src="code.item" :alt="code.name" /></button>
      </div>
      <p class="picker-footnote">{{ type === 'servant' ? '点击星标设置中意从者；中意从者优先显示并保存在本机。' : '选择后立即更新当前编队，不会跳转到其他页面。' }}</p>
    </section>
  </div>
</template>
