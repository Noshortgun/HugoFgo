<script setup lang="ts">
import { computed, ref } from 'vue';
import type { MysticCode, NpPosition, PlanAction, PlanSnapshot, Servant, SkillDefinition, SkillSecondaryOption, SelectedSkillOption } from '../types';
import AppIcon from './AppIcon.vue';
import OrderChangeDialog from './OrderChangeDialog.vue';
import { skillIcons, spellIcons } from '../data';
import { servantLabel } from '../servantLabel';

const props = defineProps<{ members: Servant[]; mystic: MysticCode; savedState: string | null; importWarnings?: string[]; strategyDirty?: boolean; saving?: boolean }>();
const warningsVisible = ref(true);
const emit = defineEmits<{ close: []; fallback: []; save: [state: string]; resetSaved: [] }>();
interface SkillWizard {
  member: Servant;
  skill: number;
  icon: string;
  skillKey: string;
  definition: SkillDefinition;
  stage: 'secondary' | 'target';
  option?: SelectedSkillOption;
}
const wave = ref(1);
const turn = ref(1);
const waveCount = ref(3);
const waveNumbers = computed(() => Array.from({ length: waveCount.value }, (_, index) => index + 1));
const turnCountByWave = ref<Record<number, number>>({ 1: 1, 2: 1, 3: 1 });
const enemyCount = ref<3 | 6>(3);
const targetByWave = ref<Record<number, number>>({ 1: 3, 2: 3, 3: 3 });
const actionsBySlot = ref<Record<string, PlanAction[]>>({});
const npPositionsBySlot = ref<Record<string, Record<string, NpPosition>>>({});
const usedSkillKeys = ref<Set<string>>(new Set());
type PendingSelection =
  | { kind: 'spell'; skill: number; icon: string }
  | { kind: 'master'; skill: number; icon: string; target: 'ally' | 'enemy' };
const pendingSelection = ref<PendingSelection | null>(null);
const skillWizard = ref<SkillWizard | null>(null);
const orderChangeOpen = ref(false);
const editingSecondaryId = ref<number | null>(null);
const refreshSkillKey = ref<string | null>(null);
const refreshSkillLabel = ref('');
const editWarning = ref('');
const nextId = ref(1);
const slotKey = computed(() => `${wave.value}-${turn.value}`);
const actions = computed(() => actionsBySlot.value[slotKey.value] || []);
const target = computed({ get: () => targetByWave.value[wave.value] ?? (enemyCount.value === 6 ? 6 : 3), set: (value: number) => { targetByWave.value[wave.value] = value; } });
const baseFrontline = computed(() => props.members.slice(0, 3));
const frontlineSlots = computed<Servant[]>(() => {
  const ids = baseFrontline.value.map((member) => member.memberInstanceId);
  Object.keys(actionsBySlot.value)
    .map((key) => {
      const [waveNumber, turnNumber] = key.split('-').map(Number);
      return { key, waveNumber, turnNumber };
    })
    .sort((left, right) => left.waveNumber - right.waveNumber || left.turnNumber - right.turnNumber)
    .forEach(({ key, waveNumber, turnNumber }) => {
      if (waveNumber > wave.value || (waveNumber === wave.value && turnNumber > turn.value)) return;
      const list = [...(actionsBySlot.value[key] || [])].sort((left, right) => left.id - right.id);
      list.forEach((action) => {
        if (action.kind !== 'orderChange' || !action.swapOutMemberInstanceId || !action.swapInMemberInstanceId) return;
        const index = ids.indexOf(action.swapOutMemberInstanceId);
        if (index !== -1) ids[index] = action.swapInMemberInstanceId;
      });
    });
  return ids
    .map((id) => props.members.find((member) => member.memberInstanceId === id))
    .filter((member): member is Servant => member !== undefined);
});
const frontline = computed(() => frontlineSlots.value.filter((member) => !member.empty));
const backline = computed(() => props.members.filter((member) => !member.empty && !frontline.value.some((front) => front.memberInstanceId === member.memberInstanceId)));
const pendingDescription = computed(() => {
  const pending = pendingSelection.value;
  if (!pending) return '';
  if (pending.kind === 'spell') return `令咒 · ${pending.skill === 0 ? '宝具解放' : '灵基修复'}`;
  return `${props.mystic.name} · 技能 ${pending.skill + 1}`;
});
const targetPickerDescription = computed(() => {
  const wizard = skillWizard.value;
  if (wizard?.stage === 'target') return `${wizard.member.name} · ${wizard.skill + 1} 技能${wizard.option ? ` · ${wizard.option.label}` : ''}`;
  const pending = pendingSelection.value;
  if (pending?.kind === 'master') return `${pendingDescription.value} · ${pending.target === 'enemy' ? '选择敌方目标' : '选择己方目标'}`;
  return pendingDescription.value;
});
const wizardShowing = computed(() => skillWizard.value !== null && skillWizard.value.stage === 'secondary');
const targetPickerShowing = computed(() => skillWizard.value?.stage === 'target' || pendingSelection.value !== null);
const masterEnemyPickerShowing = computed(() => pendingSelection.value?.kind === 'master' && pendingSelection.value.target === 'enemy');
const wizardSecondaryLabel = computed(() => skillWizard.value?.definition.secondarySelection?.label ?? '');
const wizardSecondaryOptions = computed<SkillSecondaryOption[]>(() => skillWizard.value?.definition.secondarySelection?.options ?? []);
const unsupportedSecondaryActions = computed(() => orderedActionEntries().filter((entry) => entry.action.secondarySelection));
function actionHasEmptyReference(action: PlanAction) {
  const ids = [action.memberInstanceId, action.playerTarget, action.swapOutMemberInstanceId, action.swapInMemberInstanceId];
  return ids.some((id) => id && props.members.find((member) => member.memberInstanceId === id)?.empty);
}
const emptyReferenceActions = computed(() => orderedActionEntries().filter((entry) => actionHasEmptyReference(entry.action)));
const emptyNpReferences = computed(() => Object.entries(npPositionsBySlot.value).flatMap(([key, positions]) => {
  const [waveNumber, turnNumber] = key.split('-').map(Number);
  return Object.keys(positions)
    .filter((id) => props.members.find((member) => member.memberInstanceId === id)?.empty)
    .map(() => ({ waveNumber, turnNumber }));
}));
function memberNameFor(memberInstanceId: string) {
  return props.members.find((member) => member.memberInstanceId === memberInstanceId)?.name ?? '未知从者';
}
/** 动作图标优先使用图库里的真实技能/宝具素材。 */
function actionIconSrc(action: PlanAction) {
  if (action.kind === 'master' || action.kind === 'orderChange') {
    const url = props.mystic.skillIconUrls?.[action.skill ?? props.mystic.orderChangeSkill ?? 0];
    if (url) return url;
  }
  const member = action.memberInstanceId
    ? props.members.find((item) => item.memberInstanceId === action.memberInstanceId)
    : undefined;
  if (action.kind === 'skill' && member && action.skill !== undefined) {
    const url = member.skills[action.skill]?.iconUrl;
    if (url) return url;
  }
  if (action.kind === 'attack' && member?.image) return member.image;
  return `/assets/skills/${action.icon}.png`;
}

function addAction(action: Omit<PlanAction, 'id'>) {
  if (!actionsBySlot.value[slotKey.value]) actionsBySlot.value[slotKey.value] = [];
  actionsBySlot.value[slotKey.value].push({ ...action, id: nextId.value++ });
  if (action.skillKey) usedSkillKeys.value = new Set(usedSkillKeys.value).add(action.skillKey);
}
function skillKeyFor(member: Servant, skill: number) {
  return `servant-${member.memberInstanceId}-${skill}`;
}
function npKeyFor(member: Servant) {
  return `np-${slotKey.value}-${member.memberInstanceId}`;
}
function skillDetail(definition: SkillDefinition, targetMember?: Servant) {
  if (definition.target === 'self') return '目标：自身';
  if (definition.target === 'ally') return `目标：${targetMember?.name ?? '未选择'}`;
  if (definition.target === 'enemy') return `敌方 ${target.value}`;
  if (definition.target === 'allEnemies') return '敌方全体';
  return '己方全体';
}
function finalizeSkill(wizard: SkillWizard, targetMember?: Servant) {
  const resolvedTarget = targetMember ?? (wizard.definition.target === 'self' ? wizard.member : undefined);
  addAction({
    kind: 'skill',
    title: `${wizard.member.name} · ${wizard.definition.name ?? `${wizard.skill + 1} 技能`}`,
    detail: skillDetail(wizard.definition, resolvedTarget),
    icon: wizard.icon,
    skillKey: wizard.skillKey,
    memberInstanceId: wizard.member.memberInstanceId,
    skill: wizard.skill,
    playerTarget: resolvedTarget?.memberInstanceId,
    enemyTarget: wizard.definition.target === 'enemy' ? target.value : undefined,
    secondarySelection: wizard.option,
  });
  skillWizard.value = null;
}
function useSkill(member: Servant, skill: number) {
  const skillKey = skillKeyFor(member, skill);
  if (usedSkillKeys.value.has(skillKey)) return;
  const definition: SkillDefinition = member.skills[skill] ?? { icon: skillIcons[skill] ?? skillIcons[0], target: 'allAllies', secondarySelection: undefined };
  const wizard: SkillWizard = {
    member,
    skill,
    icon: definition.icon,
    skillKey,
    definition,
    stage: definition.secondarySelection ? 'secondary' : 'target',
  };
  if (definition.secondarySelection) {
    skillWizard.value = wizard;
    return;
  }
  if (definition.target === 'ally') {
    skillWizard.value = wizard;
    return;
  }
  finalizeSkill(wizard);
}
function chooseSecondaryOption(option: SkillSecondaryOption, index: number) {
  const wizard = skillWizard.value;
  const secondary = wizard?.definition.secondarySelection;
  if (!wizard || !secondary) return;
  wizard.option = { type: secondary.type, index, value: option.value, label: option.label, imageUrl: option.imageUrl, options: secondary.options };
  if (wizard.definition.target === 'ally') wizard.stage = 'target';
  else finalizeSkill(wizard);
}
function chooseAlly(servant: Servant) {
  if (skillWizard.value?.stage === 'target') {
    finalizeSkill(skillWizard.value, servant);
    return;
  }
  const pending = pendingSelection.value;
  if (!pending) return;
  if (pending.kind === 'master') {
    addMasterAction(pending.skill, servant);
    pendingSelection.value = null;
    return;
  }
  addAction({ kind: 'spell', title: `令咒 · ${pending.skill === 0 ? '宝具解放' : '灵基修复'}`, detail: `目标：${servant.name}`, icon: pending.icon, playerTarget: servant.memberInstanceId });
  pendingSelection.value = null;
}
function chooseMasterEnemy(enemy: number) {
  const pending = pendingSelection.value;
  if (pending?.kind !== 'master' || pending.target !== 'enemy') return;
  addMasterAction(pending.skill, undefined, enemy);
  pendingSelection.value = null;
}
function cancelSelection() {
  skillWizard.value = null;
  pendingSelection.value = null;
}
function addMasterAction(skill: number, targetMember?: Servant, enemyTarget?: number) {
  const detail = targetMember ? `目标：${targetMember.name}` : enemyTarget ? `敌方 ${enemyTarget}` : '御主礼装技能';
  addAction({
    kind: 'master',
    title: `${props.mystic.name} · ${skill + 1} 技能`,
    detail,
    icon: props.mystic.skills[skill],
    skillKey: `master-${skill}`,
    skill,
    playerTarget: targetMember?.memberInstanceId,
    enemyTarget,
  });
}
function useMaster(skill: number) {
  const skillKey = `master-${skill}`;
  if (usedSkillKeys.value.has(skillKey)) return;
  if (props.mystic.orderChangeSkill === skill) {
    orderChangeOpen.value = true;
    return;
  }
  const targetType = props.mystic.skillTargets?.[skill] ?? 'allAllies';
  if (targetType === 'ally' || targetType === 'enemy') {
    pendingSelection.value = { kind: 'master', skill, icon: props.mystic.skills[skill], target: targetType };
    return;
  }
  addMasterAction(skill);
}
function confirmOrderChange(outgoing: Servant, incoming: Servant) {
  const skill = props.mystic.orderChangeSkill;
  if (skill === undefined) return;
  addAction({
    kind: 'orderChange',
    title: `换人 · ${outgoing.name} → ${incoming.name}`,
    detail: `${outgoing.name} 下场，${incoming.name} 上场`,
    icon: props.mystic.skills[skill],
    skillKey: `master-${skill}`,
    swapOutMemberInstanceId: outgoing.memberInstanceId,
    swapInMemberInstanceId: incoming.memberInstanceId,
  });
  orderChangeOpen.value = false;
}
function useSpell(index: number) {
  pendingSelection.value = { kind: 'spell', skill: index, icon: spellIcons[index] };
}
function openRefresh(key: string, label: string) {
  refreshSkillKey.value = key;
  refreshSkillLabel.value = label;
}
function refreshSkill() {
  if (!refreshSkillKey.value) return;
  const next = new Set(usedSkillKeys.value);
  next.delete(refreshSkillKey.value);
  usedSkillKeys.value = next;
  refreshSkillKey.value = null;
}
function npPositionFor(memberId: string) {
  return npPositionsBySlot.value[slotKey.value]?.[memberId] ?? 'auto';
}
function setNpPosition(memberId: string, position: NpPosition) {
  if (!npPositionsBySlot.value[slotKey.value]) npPositionsBySlot.value[slotKey.value] = {};
  npPositionsBySlot.value[slotKey.value][memberId] = position;
  const action = actions.value.find((item) => item.skillKey === `np-${slotKey.value}-${memberId}`);
  if (action) action.detail = `宝具释放卡序：${position === 'auto' ? '自动' : position}`;
}
function addNoblePhantasm(member: Servant) {
  const skillKey = npKeyFor(member);
  if (usedSkillKeys.value.has(skillKey)) return;
  const position = npPositionFor(member.memberInstanceId);
  addAction({ kind: 'attack', title: `${member.name} · ${member.noblePhantasm?.name ?? '宝具'}`, detail: `宝具释放卡序：${position === 'auto' ? '自动' : position}`, icon: skillIcons[0], skillKey, memberInstanceId: member.memberInstanceId });
}
function rebuildUsedSkills() {
  const used = new Set<string>();
  Object.values(actionsBySlot.value).flat().forEach((action) => { if (action.skillKey) used.add(action.skillKey); });
  usedSkillKeys.value = used;
}
function secondaryOptionsFor(action: PlanAction): SkillSecondaryOption[] {
  if (action.secondarySelection?.options?.length) return action.secondarySelection.options;
  const member = props.members.find((item) => item.memberInstanceId === action.memberInstanceId);
  const definition = member && action.skill !== undefined ? member.skills[action.skill]?.secondarySelection : undefined;
  return definition?.options ?? [];
}
function toggleSecondaryEdit(action: PlanAction) {
  editingSecondaryId.value = editingSecondaryId.value === action.id ? null : action.id;
}
function updateSecondary(action: PlanAction, option: SkillSecondaryOption, index: number) {
  if (!action.secondarySelection) return;
  action.secondarySelection = { ...action.secondarySelection, index, value: option.value, label: option.label, imageUrl: option.imageUrl };
  editingSecondaryId.value = null;
}
function targetActionsForWave() {
  return Object.entries(actionsBySlot.value)
    .filter(([key]) => Number(key.split('-')[0]) === wave.value)
    .flatMap(([key, list]) => list.filter((action) => action.kind === 'target').map((action) => ({ action, turn: Number(key.split('-')[1]) })))
    .sort((left, right) => left.turn - right.turn || left.action.id - right.action.id)
    .map((entry) => entry.action);
}
function orderedActionEntries() {
  return Object.entries(actionsBySlot.value).flatMap(([key, list]) => {
    const [waveNumber, turnNumber] = key.split('-').map(Number);
    return list.map((action) => ({ key, waveNumber, turnNumber, action }));
  }).sort((left, right) => left.waveNumber - right.waveNumber || left.turnNumber - right.turnNumber || left.action.id - right.action.id);
}
function hasOrderChangeDependents(action: PlanAction) {
  const entries = orderedActionEntries();
  const index = entries.findIndex((entry) => entry.action.id === action.id);
  if (index < 0 || entries.length > index + 1) return true;
  const [actionWave, actionTurn] = slotKey.value.split('-').map(Number);
  return Object.entries(npPositionsBySlot.value).some(([key, positions]) => {
    const [waveNumber, turnNumber] = key.split('-').map(Number);
    return Object.keys(positions).length && (waveNumber > actionWave || (waveNumber === actionWave && turnNumber >= actionTurn));
  });
}
function canRemoveAction(action: PlanAction) {
  if (action.kind === 'orderChange') return !hasOrderChangeDependents(action);
  if (action.kind !== 'target') return true;
  return targetActionsForWave().at(-1)?.id === action.id;
}
function restoredTargetValue(action?: PlanAction) {
  if (!action) return undefined;
  if (action.toTarget !== undefined) return action.toTarget;
  const match = action.detail.match(/敌方\s*(\d+)/);
  return match ? Number(match[1]) : undefined;
}
function restoreTargetFromActions() {
  target.value = restoredTargetValue(targetActionsForWave().at(-1)) ?? (enemyCount.value === 6 ? 6 : 3);
}
function removeAction(id: number) {
  const action = actions.value.find((item) => item.id === id);
  if (!action) return;
  if (!canRemoveAction(action)) {
    if (action.kind === 'orderChange') editWarning.value = '请先删除此换人之后的动作和宝具卡序，再删除换人。';
    return;
  }
  editWarning.value = '';
  actionsBySlot.value[slotKey.value] = actions.value.filter((item) => item.id !== id);
  if (action.kind === 'target') restoreTargetFromActions();
  if (editingSecondaryId.value === id) editingSecondaryId.value = null;
  rebuildUsedSkills();
}
function clearTurn() {
  if (actions.value.some((action) => action.kind === 'orderChange')) {
    const [currentWave, currentTurn] = slotKey.value.split('-').map(Number);
    const hasLaterActions = orderedActionEntries().some((entry) => entry.waveNumber > currentWave || (entry.waveNumber === currentWave && entry.turnNumber > currentTurn));
    const hasLaterNpPositions = Object.keys(npPositionsBySlot.value).some((key) => {
      const [waveNumber, turnNumber] = key.split('-').map(Number);
      return waveNumber > currentWave || (waveNumber === currentWave && turnNumber > currentTurn);
    });
    if (hasLaterActions || hasLaterNpPositions) {
      editWarning.value = '请先清空后续回合，再清空包含换人的回合，避免后续动作失去上场从者。';
      return;
    }
  }
  editWarning.value = '';
  actionsBySlot.value[slotKey.value] = [];
  delete npPositionsBySlot.value[slotKey.value];
  editingSecondaryId.value = null;
  restoreTargetFromActions();
  rebuildUsedSkills();
}
function setEnemyCount(count: 3 | 6) {
  enemyCount.value = count;
  target.value = count === 6 ? 6 : 3;
}
function selectTarget(enemy: number) {
  if (target.value === enemy) return;
  const fromTarget = target.value;
  target.value = enemy;
  addAction({ kind: 'target', title: '切换敌方目标', detail: `敌方 ${enemy}`, icon: skillIcons[0], fromTarget, toTarget: enemy });
}
function selectWave(value: number) {
  wave.value = value;
  turn.value = 1;
  cancelSelection();
  editingSecondaryId.value = null;
  // 浏览分面不应改动该面的攻击目标，否则会被误判为有未保存修改。
}
function removeTurn() {
  const count = turnCountByWave.value[wave.value];
  if (count <= 1) return;
  delete actionsBySlot.value[`${wave.value}-${count}`];
  delete npPositionsBySlot.value[`${wave.value}-${count}`];
  turnCountByWave.value[wave.value] -= 1;
  turn.value = Math.min(turn.value, turnCountByWave.value[wave.value]);
  restoreTargetFromActions();
  rebuildUsedSkills();
}
function addTurn() {
  turnCountByWave.value[wave.value] += 1;
  turn.value = turnCountByWave.value[wave.value];
}
function addWave() {
  if (waveCount.value >= 3) return;
  waveCount.value += 1;
  turnCountByWave.value[waveCount.value] = 1;
  targetByWave.value[waveCount.value] = enemyCount.value === 6 ? 6 : 3;
  wave.value = waveCount.value;
  turn.value = 1;
}
function removeWave() {
  if (waveCount.value <= 1) return;
  const last = waveCount.value;
  const hasContent = Object.keys(actionsBySlot.value).some((key) => Number(key.split('-')[0]) === last)
    || Object.keys(npPositionsBySlot.value).some((key) => Number(key.split('-')[0]) === last);
  if (hasContent && !window.confirm(`删除第 ${last} 面及其动作和宝具卡序？`)) return;
  delete turnCountByWave.value[last];
  delete targetByWave.value[last];
  Object.keys(actionsBySlot.value).forEach((key) => { if (Number(key.split('-')[0]) === last) delete actionsBySlot.value[key]; });
  Object.keys(npPositionsBySlot.value).forEach((key) => { if (Number(key.split('-')[0]) === last) delete npPositionsBySlot.value[key]; });
  waveCount.value -= 1;
  wave.value = Math.min(wave.value, waveCount.value);
  turn.value = Math.min(turn.value, turnCountByWave.value[wave.value] ?? 1);
  rebuildUsedSkills();
}
function captureState(): string {
  const snapshot: PlanSnapshot = {
    waveCount: waveCount.value,
    turnCountByWave: turnCountByWave.value,
    enemyCount: enemyCount.value,
    targetByWave: targetByWave.value,
    actionsBySlot: actionsBySlot.value,
    npPositionsBySlot: npPositionsBySlot.value,
    nextId: nextId.value,
  };
  return JSON.stringify(snapshot);
}
function restoreState(serialized: string) {
  const snapshot = JSON.parse(serialized) as PlanSnapshot;
  const countKeys = [
    ...Object.keys(snapshot.turnCountByWave ?? {}),
    ...Object.keys(snapshot.targetByWave ?? {}),
    ...Object.keys(snapshot.actionsBySlot ?? {}).map((key) => key.split('-')[0]),
  ].map(Number).filter(Number.isFinite);
  waveCount.value = Math.max(1, snapshot.waveCount ?? 3, ...countKeys);
  turnCountByWave.value = { ...(snapshot.turnCountByWave ?? {}) };
  for (let item = 1; item <= waveCount.value; item++) turnCountByWave.value[item] ||= 1;
  enemyCount.value = snapshot.enemyCount;
  targetByWave.value = { ...(snapshot.targetByWave ?? {}) };
  for (let item = 1; item <= waveCount.value; item++) targetByWave.value[item] ||= snapshot.enemyCount === 6 ? 6 : 3;
  actionsBySlot.value = snapshot.actionsBySlot;
  npPositionsBySlot.value = snapshot.npPositionsBySlot || {};
  nextId.value = snapshot.nextId;
  pendingSelection.value = null;
  skillWizard.value = null;
  orderChangeOpen.value = false;
  editingSecondaryId.value = null;
  refreshSkillKey.value = null;
  wave.value = 1;
  turn.value = 1;
  rebuildUsedSkills();
}
const savedSnapshot = ref(props.savedState || captureState());
if (props.savedState) restoreState(props.savedState);
const planDirty = computed(() => captureState() !== savedSnapshot.value);
const dirty = computed(() => planDirty.value || Boolean(props.strategyDirty));
function savePlan() {
  const state = captureState();
  savedSnapshot.value = state;
  emit('save', state);
}
function resetPlan() {
  restoreState(savedSnapshot.value);
  emit('resetSaved');
}
function markSaved() {
  savedSnapshot.value = captureState();
}
defineExpose({ getSnapshot: captureState, markSaved });
</script>

<template>
  <div class="plan-backdrop">
    <section class="plan-shell">
      <header class="plan-header"><div><span class="plan-kicker">BATTLE PROGRAM</span><h2>操作序列</h2><p>默认每面一个回合，需要时可在右侧增加回合。</p></div><div class="plan-header-right"><span class="plan-save-status" :class="{ dirty }"><AppIcon :name="dirty ? 'edit' : 'check'" :size="14" />{{ dirty ? '有未保存修改' : '已保存' }}</span><button class="reset-plan-button" :disabled="!dirty" title="恢复到最近保存的操作序列与选卡策略" @click="resetPlan">重置</button><button class="save-plan-button" :disabled="!dirty || props.saving" @click="savePlan"><AppIcon name="check" :size="14" /> {{ props.saving ? '保存中…' : '保存' }}</button><button title="关闭" @click="emit('close')"><AppIcon name="close" /></button></div></header>
      <div v-if="props.importWarnings?.length && warningsVisible" class="plan-import-warnings"><AppIcon name="edit" :size="15" /><div><strong>导入时有 {{ props.importWarnings.length }} 处需要留意</strong><ul><li v-for="(warning, index) in props.importWarnings" :key="index">{{ warning }}</li></ul></div><button title="关闭提示" @click="warningsVisible = false"><AppIcon name="close" :size="14" /></button></div>
      <div v-if="editWarning" class="plan-import-warnings" role="alert"><AppIcon name="edit" :size="15" /><div><strong>{{ editWarning }}</strong></div><button title="关闭提示" @click="editWarning = ''"><AppIcon name="close" :size="14" /></button></div>
      <div v-if="unsupportedSecondaryActions.length" class="plan-import-warnings plan-capability-warning" role="alert"><AppIcon name="edit" :size="15" /><div><strong>此作业包含需要技能追加选择的动作</strong><p>{{ unsupportedSecondaryActions.map((entry) => `第 ${entry.waveNumber} 面 · 回合 ${entry.turnNumber}：${entry.action.title}`).join('；') }}</p><small>技能使用后还需要额外选择，当前无法自动执行；动作会保留，启动前会被拦截。</small></div></div>
      <div v-if="emptyReferenceActions.length || emptyNpReferences.length" class="plan-import-warnings plan-capability-warning" role="alert"><AppIcon name="edit" :size="15" /><div><strong>空槽位不能承载显式引用</strong><p>{{ emptyReferenceActions.map((entry) => `第 ${entry.waveNumber} 面 · 回合 ${entry.turnNumber}：${entry.action.title}`).join('；') }}</p><p v-if="emptyNpReferences.length">{{ emptyNpReferences.map((entry) => `第 ${entry.waveNumber} 面 · 回合 ${entry.turnNumber}：宝具卡序`).join('；') }}</p><small>引用已保留。请为对应槽位重新选择从者，或删除引用后再战斗。</small></div></div>
      <div class="plan-wave-row"><div class="plan-wave-tabs" :style="{ gridTemplateColumns: `repeat(${waveCount}, minmax(120px, 1fr))` }"><button v-for="item in waveNumbers" :key="item" :class="{ active: wave === item }" @click="selectWave(item)"><span>BATTLE {{ item }}/{{ waveCount }}</span><strong>第 {{ item }} 面</strong><small>{{ turnCountByWave[item] }} 个回合</small></button></div><div class="plan-wave-controls"><button :disabled="waveCount >= 3" title="游戏关卡最多三面" @click="addWave"><AppIcon name="plus" :size="12" /> 增加一面</button><button :disabled="waveCount <= 1" title="游戏关卡最少一面" @click="removeWave">删除最后一面</button></div></div>
      <div class="plan-content">
        <div class="battle-editor-stage">
          <div class="battlefield">
            <div class="enemy-target-area">
              <div class="battlefield-label"><span>选择敌方目标 <small>未选择时默认攻击最前方</small></span><button @click="setEnemyCount(enemyCount === 3 ? 6 : 3)"><AppIcon name="cards" :size="13" /> 切换 {{ enemyCount === 3 ? 6 : 3 }} 敌人阵型</button></div>
              <div class="enemy-targets" :class="{ six: enemyCount === 6 }"><button v-for="enemy in enemyCount" :key="enemy" :class="{ selected: target === enemy }" @click="selectTarget(enemy)"><span class="enemy-figure"><i></i></span><strong>敌方 {{ enemy }}</strong><em v-if="target === enemy">当前目标</em></button></div>
            </div>
            <div class="ally-area">
              <div v-for="member in frontlineSlots" :key="member.memberInstanceId" class="ally-editor-card" :class="{ empty: member.empty }">
                <div class="ally-identity"><img v-if="member.image" :src="member.image" :alt="member.name" /><span v-else class="ally-avatar-placeholder">{{ member.empty ? '任' : member.name.slice(0, 1) }}</span><div><strong>{{ member.empty ? '不限制' : servantLabel(member) }}</strong></div></div>
                <div v-if="member.empty" class="ally-empty-slot"><small>实际从者仅参与自动选卡</small></div>
                <div v-else class="ally-skill-buttons"><button v-for="(definition, skill) in member.skills" :key="`${definition.icon}-${skill}`" :class="{ used: usedSkillKeys.has(skillKeyFor(member, skill)) }" :title="`${servantLabel(member)} ${definition.name ?? `${skill + 1} 技能`} · 右键可刷新冷却`" @click="useSkill(member, skill)" @contextmenu.prevent="openRefresh(skillKeyFor(member, skill), `${servantLabel(member)} · ${skill + 1} 技能`)"><img :src="definition.iconUrl || `/assets/skills/${definition.icon}.png`" alt="技能" /><span>{{ skill + 1 }}</span><i v-if="usedSkillKeys.has(skillKeyFor(member, skill))">已使用</i></button></div>
                <div v-if="!member.empty" class="ally-np-row"><button class="np-button" :class="{ used: usedSkillKeys.has(npKeyFor(member)) }" :title="usedSkillKeys.has(npKeyFor(member)) ? '该从者本回合已使用宝具' : '加入宝具'" @click="addNoblePhantasm(member)">宝具</button><label>宝具释放卡序<select :value="npPositionFor(member.memberInstanceId)" @change="setNpPosition(member.memberInstanceId, ($event.target as HTMLSelectElement).value === 'auto' ? 'auto' : Number(($event.target as HTMLSelectElement).value) as NpPosition)"><option value="auto">自动</option><option :value="1">1</option><option :value="2">2</option><option :value="3">3</option></select></label></div>
              </div>
            </div>
            <div v-if="wizardShowing" class="ally-target-picker skill-option-picker">
              <div><span>SKILL OPTION</span><strong>{{ skillWizard ? servantLabel(skillWizard.member) : '' }} · {{ (skillWizard?.skill ?? 0) + 1 }} 技能</strong><p>{{ wizardSecondaryLabel }}</p></div>
              <div><button v-for="(option, index) in wizardSecondaryOptions" :key="index" @click="chooseSecondaryOption(option, index)"><img v-if="option.imageUrl" class="skill-secondary-image" :src="option.imageUrl" :alt="option.label" /><span v-else>{{ option.label }}</span></button></div>
              <button class="cancel-target" @click="cancelSelection">取消</button>
            </div>
            <div v-if="targetPickerShowing" class="ally-target-picker">
              <div><span>SELECT TARGET</span><strong>选择使用对象</strong><p>{{ targetPickerDescription }}</p></div>
              <div v-if="masterEnemyPickerShowing"><button v-for="enemy in enemyCount" :key="enemy" class="master-enemy-target" @click="chooseMasterEnemy(enemy)"><span>敌方 {{ enemy }}</span></button></div>
              <div v-else><button v-for="member in frontline" :key="member.memberInstanceId" @click="chooseAlly(member)"><img v-if="member.image" :src="member.image" :alt="member.name" /><span v-else class="target-avatar-placeholder">{{ member.name.slice(0, 1) }}</span><span>{{ servantLabel(member) }}</span></button></div>
              <button class="cancel-target" @click="cancelSelection">取消</button>
            </div>
          </div>
          <div class="master-command-bar"><div class="master-command-title"><img :src="props.mystic.item" :alt="props.mystic.name" /><span><strong>{{ props.mystic.name }}</strong><small>右键技能可刷新冷却</small></span></div><button v-for="(skill, index) in props.mystic.skills" :key="index" :class="{ used: usedSkillKeys.has(`master-${index}`) }" :title="props.mystic.orderChangeSkill === index ? '换人：选择交换双方' : `御主技能 ${index + 1}`" @click="useMaster(index)" @contextmenu.prevent="openRefresh(`master-${index}`, `御主技能 ${index + 1}`)"><img :src="props.mystic.skillIconUrls?.[index] || `/assets/skills/${skill}.png`" alt="御主技能" /><span>技能 {{ index + 1 }}</span></button><i></i><span class="spell-label">令咒 · 选择对象</span><button v-for="(spell, index) in spellIcons" :key="spell" @click="useSpell(index)"><img :src="`/assets/skills/${spell}.png`" alt="令咒" /><span>{{ index === 0 ? '宝具解放' : '灵基修复' }}</span></button><button class="command-fallback-button" @click="emit('fallback')"><AppIcon name="cards" :size="16" /> 后续回合与选卡策略</button></div>
        </div>

        <aside class="sequence-panel"><div class="turn-selector"><button v-for="item in turnCountByWave[wave]" :key="item" :class="{ active: turn === item }" @click="turn = item">回合 {{ item }}</button><button class="add-turn-inline" title="增加回合" @click="addTurn"><AppIcon name="plus" :size="13" /></button><button class="remove-turn-inline" title="删除最后一个回合" :disabled="turnCountByWave[wave] <= 1" @click="removeTurn">−</button></div><div class="sequence-panel-heading"><div><span>CURRENT TURN</span><h3>第 {{ wave }} 面 · 回合 {{ turn }}</h3></div><button @click="clearTurn">清空</button></div><div class="sequence-target">攻击目标：<strong>敌方 {{ target }}</strong><small>{{ enemyCount }} 敌人阵型 · 无血量预设</small></div><div class="sequence-list"><div v-if="actions.length === 0" class="sequence-empty"><AppIcon name="cards" :size="27" /><strong>点击技能或从者宝具</strong><p>普通指令卡由补刀策略按实际发牌处理</p></div><div v-for="(action, index) in actions" :key="action.id" class="sequence-item"><span>{{ index + 1 }}</span><img :src="actionIconSrc(action)" alt="" /><div><strong>{{ action.title }}</strong><small>{{ action.detail }}</small><div v-if="action.secondarySelection" class="sequence-secondary"><button class="secondary-value" @click="toggleSecondaryEdit(action)"><img v-if="action.secondarySelection.imageUrl" class="skill-secondary-image" :src="action.secondarySelection.imageUrl" :alt="action.secondarySelection.label" /><span v-else>{{ action.secondarySelection.label }}</span> <AppIcon name="chevron" :size="11" /></button><div v-if="editingSecondaryId === action.id" class="secondary-option-list"><button v-for="(option, optionIndex) in secondaryOptionsFor(action)" :key="optionIndex" :class="{ active: action.secondarySelection.index === optionIndex }" @click="updateSecondary(action, option, optionIndex)"><img v-if="option.imageUrl" class="skill-secondary-image" :src="option.imageUrl" :alt="option.label" /><span v-else>{{ option.label }}</span></button></div></div><small v-if="actionHasEmptyReference(action)" class="sequence-action-error">引用空槽位，修复前无法战斗</small></div><button :disabled="!canRemoveAction(action)" :title="action.kind === 'orderChange' && !canRemoveAction(action) ? '请先删除此换人之后的动作和宝具卡序' : action.kind === 'target' && !canRemoveAction(action) ? '只能删除最新的选敌动作' : '删除'" @click="removeAction(action.id)"><AppIcon name="close" :size="14" /></button></div></div><div class="np-position-note"><AppIcon name="check" :size="13" /> 宝具卡序按从者、回合独立设置</div><div class="attack-count"><span>本回合动作</span><strong>{{ actions.length }}</strong><small>使用过的技能变灰；右键图标可刷新冷却</small></div></aside>
      </div>
    </section>

    <div v-if="refreshSkillKey" class="skill-refresh-backdrop" @click.self="refreshSkillKey = null"><section class="skill-refresh-dialog"><span class="dialog-icon"><AppIcon name="history" /></span><h3>刷新技能冷却？</h3><p>{{ refreshSkillLabel }}</p><small>用于游戏内冷却转好或减 CD 技能生效的情况。确认后图标重新亮起，可以再次编入操作序列。</small><div><button @click="refreshSkillKey = null">取消</button><button class="button-primary" @click="refreshSkill">是，刷新技能</button></div></section></div>
    <OrderChangeDialog v-if="orderChangeOpen" :frontline="frontline" :backline="backline" :mystic-name="props.mystic.name" @close="orderChangeOpen = false" @confirm="confirmOrderChange" />
  </div>
</template>
