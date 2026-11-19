<script setup lang="ts">
import { computed, ref } from 'vue';
import type { Servant } from '../types';
import AppIcon from './AppIcon.vue';

const props = defineProps<{
  members: Servant[];
  candidates: number[];
  required: boolean;
  duplicateNames: string[];
  initialSelection: number | null;
  jobName?: string;
  importing?: boolean;
}>();
const emit = defineEmits<{ confirm: [slot: number | null]; close: [] }>();

const choice = ref<number | null>(props.initialSelection ?? props.candidates[0] ?? null);
const canConfirm = computed(() => choice.value !== null);
const candidateSet = computed(() => new Set(props.candidates));

function selectable(index: number) {
  return candidateSet.value.has(index) || (!props.required && props.members[index]?.empty);
}
function pick(index: number) {
  if (!selectable(index)) return;
  choice.value = index;
}
function confirm() {
  if (choice.value === null) return;
  emit('confirm', props.members[choice.value]?.empty ? null : choice.value);
}
</script>

<template>
  <div class="support-assign-backdrop">
    <section class="support-assign-dialog" role="dialog" aria-modal="true" aria-labelledby="support-assign-title">
      <header>
        <div><span class="picker-kicker">SUPPORT SLOT</span><h2 id="support-assign-title">选择助战从者</h2></div>
        <button title="关闭" @click="emit('close')"><AppIcon name="close" /></button>
      </header>
      <div class="support-assign-body">
        <p v-if="props.required" class="support-assign-note warning">
          作业中有重复从者<span v-for="name in props.duplicateNames" :key="name" class="duplicate-name">{{ name }}</span>。自己的队伍不能放两名相同从者，所以其中一位必然是助战，请从这两名中选择。
        </p>
        <p v-else class="support-assign-note">游戏内每个队伍都需要 1 名助战。请选择作业中哪一位是助战；也可以选择空位，表示不指定助战。<template v-if="props.importing">确认后才会正式导入作业，取消不会改动当前编队。</template></p>
        <p v-if="props.jobName" class="support-assign-job">当前作业：{{ props.jobName }}</p>
        <div class="support-assign-grid">
          <button
            v-for="(member, index) in props.members"
            :key="member.memberInstanceId"
            class="support-assign-card"
            :class="{ selected: choice === index, disabled: !selectable(index) }"
            :disabled="!selectable(index)"
            @click="pick(index)"
          >
            <span class="support-assign-slot">{{ String(index + 1).padStart(2, '0') }}</span>
            <img v-if="!member.empty && member.image" :src="member.image" :alt="member.name" />
            <span v-else class="support-assign-placeholder"><AppIcon :name="member.empty ? 'plus' : 'shield'" :size="18" /></span>
            <span class="support-assign-text">
              <strong>{{ member.empty ? '空位' : member.name }}</strong>
              <small>{{ member.empty ? (selectable(index) ? '不指定助战' : '重复从者必须选一位') : member.role || '未知职阶' }}</small>
              <small v-if="!member.empty" class="support-assign-ce">
                <img v-if="member.ceImage" :src="member.ceImage" alt="" />
                <span>{{ member.ce || '礼装不限' }}</span>
              </small>
            </span>
            <span v-if="member.support" class="support-assign-badge">原助战</span>
            <span v-if="choice === index" class="support-assign-check"><AppIcon name="check" :size="12" /></span>
          </button>
        </div>
        <p v-if="props.required && !canConfirm" class="support-assign-hint">请先选择其中一位重复从者。</p>
      </div>
      <footer>
        <button class="button-secondary" @click="emit('close')">取消</button>
        <button class="button-primary" :disabled="!canConfirm" @click="confirm"><AppIcon name="check" :size="15" /> 确认助战</button>
      </footer>
    </section>
  </div>
</template>
