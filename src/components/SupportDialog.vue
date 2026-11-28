<script setup lang="ts">
import { computed, ref } from 'vue';
import type { Servant } from '../types';
import type { EquipInfo } from '../equips';
import AppIcon from './AppIcon.vue';
import EquipPicker from './EquipPicker.vue';

const props = defineProps<{ member: Servant; slot: number; equips: EquipInfo[]; members: Servant[] }>();
const emit = defineEmits<{ close: []; save: [index: number, member: Servant]; relocate: [] }>();
const canRelocate = computed(() => props.members.some((member, index) => index !== props.slot && !member.empty));
const canSpecify = computed(() => !props.member.empty && Boolean(props.member.name.trim()));
const draft = ref<Servant>({ ...props.member });
const ceEnabled = ref(Boolean(props.member.ceImage));
const picking = ref<'ce' | 'grandCeTop' | 'grandCeMiddle' | 'grandCeBottom' | null>(null);
const pickerTitle = computed(() => {
  if (picking.value === 'ce') return '选择助战概念礼装';
  if (picking.value === 'grandCeTop') return '选择上方礼装';
  if (picking.value === 'grandCeMiddle') return '选择中间礼装';
  if (picking.value === 'grandCeBottom') return '选择下方礼装';
  return '选择概念礼装';
});
const grandRows = [
  { key: 'grandCeTop' as const, title: '上方礼装', detail: '任意概念礼装，可不指定' },
  { key: 'grandCeMiddle' as const, title: '中间礼装', detail: '牵绊礼装或 NP +50%（如果有）' },
  { key: 'grandCeBottom' as const, title: '下方礼装', detail: '仅限增加牵绊的礼装，可不指定' },
];

function toggleSpecify() {
  if (canSpecify.value) draft.value.supportRequired = !draft.value.supportRequired;
}
function chooseEquip(equip: EquipInfo) {
  const target = picking.value;
  if (target === 'ce') {
    draft.value.ceId = equip.id;
    draft.value.ce = equip.name;
    draft.value.ceImage = equip.icon ?? '';
  } else if (target) {
    draft.value[target] = { name: equip.name, image: equip.icon ?? '', id: equip.id };
  }
  picking.value = null;
}
function clearEquip() {
  const target = picking.value;
  if (target === 'ce') {
    draft.value.ceId = undefined;
    draft.value.ce = '';
    draft.value.ceImage = '';
    ceEnabled.value = false;
  } else if (target) {
    draft.value[target] = null;
  }
  picking.value = null;
}
</script>

<template>
  <div class="support-backdrop" @click.self="emit('close')">
    <section class="support-dialog">
      <header><div><span class="picker-kicker">SUPPORT RULES</span><h2>助战与礼装要求</h2></div><button title="关闭" @click="emit('close')"><AppIcon name="close" /></button></header>
      <div class="support-dialog-content">
        <div class="support-toggle-row"><div><strong>指定助战从者</strong><p>{{ canSpecify ? '关闭后不指定名称，系统选择任意可用助战。' : '当前为任意助战：从者身份不限，只保留下方概念礼装条件。先选择从者才能指定身份。' }}</p></div><button class="switch-control" :class="{ active: draft.supportRequired && canSpecify }" :disabled="!canSpecify" @click="toggleSpecify"><i></i><span>{{ draft.supportRequired && canSpecify ? '指定' : '任意' }}</span></button></div>
        <div class="support-toggle-row grand-toggle"><div><strong>必须为冠位从者</strong><p>开启后使用游戏中的蓝色冠位边框，并按冠位助战区域查找。</p></div><button class="switch-control" :class="{ active: draft.grandRequired }" @click="draft.grandRequired = !draft.grandRequired"><i></i><span>{{ draft.grandRequired ? '冠位' : '普通' }}</span></button></div>

        <div v-if="!draft.grandRequired" class="ordinary-support-ce"><div class="support-section-heading"><div><strong>概念礼装</strong><p>礼装也可以不指定，默认不进行礼装筛选。</p></div><button class="switch-control" :class="{ active: ceEnabled }" @click="ceEnabled = !ceEnabled; if (!ceEnabled) { draft.ce = ''; draft.ceImage = ''; draft.ceId = undefined; }"><i></i><span>{{ ceEnabled ? '指定' : '不限' }}</span></button></div><button v-if="ceEnabled" class="support-ce-choice" @click="picking = 'ce'"><img v-if="draft.ceImage" :src="draft.ceImage" :alt="draft.ce" /><span><strong>{{ draft.ce || '选择概念礼装' }}</strong><small>点击选择礼装</small></span><AppIcon name="chevron" :size="15" /></button></div>

        <div v-else class="grand-support-ce"><div class="support-section-heading"><div><strong>冠位助战 · 三个礼装槽位</strong><p>三个位置均允许不指定；只在有条件时进行识别筛选。</p></div></div><div v-for="row in grandRows" :key="row.key" class="grand-support-row"><div class="grand-rule-label"><strong>{{ row.title }}</strong><small>{{ row.detail }}</small></div><button class="grand-ce-choice" @click="picking = row.key"><img v-if="draft[row.key]?.image" :src="draft[row.key]?.image" :alt="draft[row.key]?.name" /><span><strong>{{ draft[row.key]?.name || '不指定' }}</strong><small>点击选择 / 切换</small></span></button><button class="clear-grand-ce" :disabled="!draft[row.key]" @click="draft[row.key] = null">不限</button></div></div>
      </div>
      <footer><button v-if="canRelocate" class="button-secondary support-relocate" @click="emit('relocate')"><AppIcon name="swap" :size="15" /> 更换助战位置</button><button class="button-secondary" @click="emit('close')">取消</button><button class="button-primary" @click="emit('save', props.slot, draft)"><AppIcon name="check" :size="15" /> 保存助战要求</button></footer>
    </section>
    <EquipPicker v-if="picking" :equips="props.equips" :title="pickerTitle" :allow-clear="true" @close="picking = null" @select="chooseEquip" @clear="clearEquip" />
  </div>
</template>
