<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { atlasMysticCodes, atlasServants, loadAtlas, lookupAtlas, lookupAtlasById } from './atlas';
import { equipsById, equipsList, loadEquips } from './equips';
import type { FallbackScope, FallbackStrategy, HugoFgoJob, MysticCode, PlanSnapshot, Servant } from './types';
import { emptyServant, mysticCodes, standardSkills, team } from './data';
import { activeNoblePhantasmForLimitCount, activeSkillsForLimitCount, applyFormation, buildJobFile, createEmptyPlanSnapshot, jobToSnapshot, parseJobFile } from './job';
import { convertChaldeaTeam, fetchChaldeaTeam, loadChaldeaCatalog, loadSecondaryCatalog, parseChaldeaRef } from './chaldea';
import { deleteJobFromLibrary, jobLibraryReadOnly, jobNameForMembers, listSavedJobs, readJobFromLibrary, saveJobToLibrary, type SavedJobEntry } from './jobLibrary';
import { resourceRegion } from './resources';
import { browserRunDisabledReason, fakeRunRequested, runApi } from './run';
import { applyRunEvent, emptyRunState } from './runState';
import { loadRunSettings } from './runSettings';
import { isTauri } from './device';
import { defaultFallbackStrategy, normalizeServantOrder } from './servantOrder';
import FormationHome from './components/FormationHome.vue';
import BattlePlanDrawer from './components/BattlePlanDrawer.vue';
import PickerDialog from './components/PickerDialog.vue';
import RunOverlay from './components/RunOverlay.vue';
import RunHistory from './components/RunHistory.vue';
import RunSettingsPanel from './components/RunSettingsPanel.vue';
import SupportDialog from './components/SupportDialog.vue';
import SupportAssignDialog from './components/SupportAssignDialog.vue';
import DevicePanel from './components/DevicePanel.vue';
import FallbackDialog from './components/FallbackDialog.vue';
import ResourceCenter from './components/ResourceCenter.vue';
import AppIcon from './components/AppIcon.vue';

const members = ref<Servant[]>(team.map((member) => ({ ...member })));
const mystic = ref<MysticCode>(mysticCodes[0]);
const gender = ref<'female' | 'male'>('female');
const currentMystic = computed<MysticCode>(() => ({
  ...mystic.value,
  figure: gender.value === 'female' ? mystic.value.figure : mystic.value.maleFigure,
  item: gender.value === 'female' ? mystic.value.item : mystic.value.maleItem,
}));
const fallback = ref<FallbackStrategy>(defaultFallbackStrategy(members.value));
const fallbackByWave = ref<Record<number, FallbackStrategy | null>>({ 1: null, 2: null, 3: null });
function cloneStrategy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
const savedFallback = ref<FallbackStrategy>(cloneStrategy(fallback.value));
const savedFallbackByWave = ref<Record<number, FallbackStrategy | null>>(cloneStrategy(fallbackByWave.value));
const strategyRevision = ref(0);
const savedStrategyRevision = ref(0);
const strategyDirty = computed(() => strategyRevision.value !== savedStrategyRevision.value);
const servantChoices = computed<Servant[]>(() => {
  if (!atlasServants.value.length) return team.map((member) => ({ ...member, image: '', art: '', ceImage: '', placeholder: true }));
  return atlasServants.value.filter((servant) => (servant.type === 'normal' || servant.type === 'heroine') && servant.collectionNo > 0).map((servant) => {
    const assetSkills = activeSkillsForLimitCount(servant.skills, 4);
    const ascension = servant.ascensionAssets?.[4];
    return {
      memberInstanceId: `catalog-${servant.id}`,
      svtId: servant.id,
      limitCount: 4,
      name: servant.name,
      role: servant.classLabel,
      image: ascension?.face ?? servant.face ?? '',
      art: ascension?.card ?? servant.card ?? '',
      level: '',
      ce: '',
      ceImage: '',
      skills: standardSkills().map((definition, index) => ({
        ...definition,
        iconUrl: assetSkills[index]?.icon ?? undefined,
        target: assetSkills[index]?.target ?? definition.target,
        name: assetSkills[index]?.name,
        secondarySelection: assetSkills[index]?.secondarySelection,
      })),
    };
  });
});
/** 选择器优先展示完整图库；没有图库时回落到内置两套。 */
const allMysticCodes = computed<MysticCode[]>(() => (atlasMysticCodes.value.length ? atlasMysticCodes.value : mysticCodes));
/** 导入作业时两套 id 都要能解析（旧作业用 default / order-change）。 */
const lookupMysticCodes = computed<MysticCode[]>(() => [...mysticCodes, ...atlasMysticCodes.value]);
const picker = ref<{ type: 'servant' | 'ce' | 'mystic'; slot?: number } | null>(null);
const supportSlot = ref<number | null>(null);
const supportPick = ref<{ candidates: number[]; required: boolean; duplicateNames: string[]; selected: number | null } | null>(null);
const pendingImport = ref<{ job: HugoFgoJob; members: Servant[]; warnings: string[] } | null>(null);
const planOpen = ref(false);
const savedPlan = ref<string | null>(null);
const planDrawer = ref<{ getSnapshot: () => string; markSaved: () => void } | null>(null);
const fallbackOpen = ref(false);
const runState = ref(emptyRunState());
const runOverlayVisible = ref(false);
const runStarting = ref(false);
const selectedSerial = ref('');
const currentJobPath = ref('');
const utility = ref<'jobs' | 'history' | 'settings' | null>(null);
const importSource = ref('');
const jobFileInput = ref<HTMLInputElement | null>(null);
const jobMessage = ref('');
const jobWarnings = ref<string[]>([]);
const jobName = computed(() => jobNameForMembers(members.value));
const runActive = computed(() => runStarting.value || runState.value.phase === 'running' || runState.value.phase === 'stopping');
const runDisabledReason = computed(() => {
  if (!isTauri) return browserRunDisabledReason;
  if (runActive.value) return '已有作业正在运行，不能重复启动';
  if (!selectedSerial.value && !fakeRunRequested) return '请先在“设置与资源”中检测并选择设备';
  return '';
});
const runStartLabel = computed(() => runStarting.value ? '启动中' : runActive.value ? '运行中' : '开始');
const jobLoading = ref(false);
const jobLibrary = ref<SavedJobEntry[]>([]);
const jobLibraryError = ref('');
const jobLibraryLoading = ref(false);
const jobSaving = ref(false);
let nextMemberInstanceId = team.length + 1;
let unsubscribeRun: (() => void) | undefined;

onMounted(async () => {
  try {
    unsubscribeRun = await runApi.subscribe((event) => {
      runState.value = applyRunEvent(runState.value, event);
      runOverlayVisible.value = true;
    });
    const restored = await runApi.getState();
    if (!runState.value.runId) {
      runState.value = restored;
      runOverlayVisible.value = restored.phase === 'running' || restored.phase === 'stopping';
    }
  } catch (error) {
    runState.value = { ...runState.value, phase: 'failed', error: error instanceof Error ? error.message : String(error) };
  }
  await Promise.all([loadAtlas(), loadEquips()]);
  members.value = members.value.map((member) => resolveMemberAssets(member, member.limitCount ?? 4));
});
onUnmounted(() => unsubscribeRun?.());

async function reloadResources(force = false) {
  await Promise.all([loadAtlas(resourceRegion.value, force), loadEquips(resourceRegion.value, force)]);
  members.value = members.value.map((member) => resolveMemberAssets(member, member.limitCount ?? 4));
}

watch(resourceRegion, () => reloadResources());

function selectServant(index: number, servant: Servant) {
  const current = members.value[index];
  const limitCount = current.svtId === servant.svtId ? current.limitCount ?? servant.limitCount ?? 4 : servant.limitCount ?? 4;
  members.value[index] = { ...current, ...servant, limitCount, memberInstanceId: `member-${nextMemberInstanceId++}`, support: current.support, supportRequired: current.support ? true : false, grandRequired: current.grandRequired, empty: false };
  picker.value = null;
  syncFallbackOrders();
}
function resolveMemberAssets(member: Servant, limitCount: number): Servant {
  const asset = lookupAtlasById(member.svtId);
  const assetSkills = activeSkillsForLimitCount(asset?.skills ?? [], limitCount, member.svtId);
  const ascension = asset?.ascensionAssets?.[limitCount];
  const noblePhantasm = activeNoblePhantasmForLimitCount(asset?.noblePhantasms ?? [], limitCount, member.svtId);
  return {
    ...member,
    limitCount,
    npId: noblePhantasm?.id,
    noblePhantasm,
    image: atlasServants.value.length ? ascension?.face ?? asset?.face ?? member.image : '',
    art: atlasServants.value.length ? ascension?.card ?? asset?.card ?? member.art : '',
    ceImage: atlasServants.value.length ? member.ceImage : '',
    placeholder: !atlasServants.value.length && !member.empty,
    skills: assetSkills.length
      ? assetSkills.map((skill, skillIndex) => ({
          icon: member.skills[skillIndex]?.icon ?? standardSkills()[skillIndex].icon,
          iconUrl: skill.icon ?? undefined,
          target: skill.target ?? member.skills[skillIndex]?.target ?? standardSkills()[skillIndex].target,
          name: skill.name,
          secondarySelection: skill.secondarySelection,
        }))
      : member.skills,
  };
}
function changeAscension(index: number, limitCount: number) {
  members.value[index] = resolveMemberAssets(members.value[index], limitCount);
}

function selectCe(index: number, name: string, image: string) {
  members.value[index] = { ...members.value[index], ce: name, ceImage: image };
  picker.value = null;
}
function selectMystic(code: MysticCode) {
  mystic.value = code;
  picker.value = null;
}
function openPicker(type: 'servant' | 'ce' | 'mystic', slot?: number) {
  picker.value = { type, slot };
}
function reorderMembers(source: number, target: number) {
  if (source === target) return;
  const next = [...members.value];
  [next[source], next[target]] = [next[target], next[source]];
  members.value = next;
  syncFallbackOrders();
}
function removeMember(index: number) {
  members.value[index] = emptyServant(members.value[index].support);
  syncFallbackOrders();
}
function saveSupport(index: number, member: Servant) {
  members.value[index] = { ...member };
  supportSlot.value = null;
  syncFallbackOrders();
}
function updateFallback(payload: { scope: FallbackScope; value: FallbackStrategy }) {
  if (payload.scope === 'all') fallback.value = payload.value;
  else fallbackByWave.value[payload.scope] = payload.value;
  strategyRevision.value++;
}
function resetFallback(wave: number) {
  fallbackByWave.value[wave] = null;
  strategyRevision.value++;
}
function resetStrategy() {
  fallback.value = defaultFallbackStrategy(members.value);
  fallbackByWave.value = { 1: null, 2: null, 3: null };
  strategyRevision.value++;
}
function syncFallbackOrders() {
  fallback.value = normalizeServantOrder(fallback.value, members.value);
  Object.entries(fallbackByWave.value).forEach(([wave, strategy]) => {
    if (strategy) fallbackByWave.value[Number(wave)] = normalizeServantOrder(strategy, members.value);
  });
  strategyRevision.value++;
}
function currentJob(snapshot: PlanSnapshot) {
  const strategy = normalizeServantOrder(fallback.value, members.value);
  const strategyByWave = Object.fromEntries(
    Object.entries(fallbackByWave.value).map(([wave, value]) => [wave, value ? normalizeServantOrder(value, members.value) : null]),
  );
  return buildJobFile({ members: members.value, mystic: mystic.value, fallback: strategy, fallbackByWave: strategyByWave, snapshot, name: jobName.value });
}
async function refreshJobLibrary() {
  jobLibraryLoading.value = true;
  jobLibraryError.value = jobLibraryReadOnly
    ? '浏览器预览为只读，作业列表与保存仅在桌面版可用。'
    : '';
  try {
    jobLibrary.value = await listSavedJobs();
  } catch (error) {
    jobLibraryError.value = error instanceof Error ? error.message : '读取作业文件夹失败';
  } finally {
    jobLibraryLoading.value = false;
  }
}
function currentPlanSnapshot() {
  return planDrawer.value?.getSnapshot() ?? savedPlan.value ?? JSON.stringify(createEmptyPlanSnapshot());
}
async function persistCurrentJob(snapshotText = currentPlanSnapshot()): Promise<boolean> {
  const snapshot: PlanSnapshot = snapshotText ? (JSON.parse(snapshotText) as PlanSnapshot) : createEmptyPlanSnapshot();
  jobSaving.value = true;
  let ok = false;
  try {
    const saved = await saveJobToLibrary(currentJob(snapshot), jobName.value);
    currentJobPath.value = `jobs/${saved.file}`;
    jobMessage.value = `已保存到 jobs/${saved.file}`;
    jobWarnings.value = [];
    savedPlan.value = snapshotText;
    savedFallback.value = cloneStrategy(fallback.value);
    savedFallbackByWave.value = cloneStrategy(fallbackByWave.value);
    savedStrategyRevision.value = strategyRevision.value;
    planDrawer.value?.markSaved();
    await refreshJobLibrary();
    ok = true;
  } catch (error) {
    jobMessage.value = `保存到 jobs 文件夹失败：${error instanceof Error ? error.message : '未知错误'}`;
  } finally {
    jobSaving.value = false;
  }
  return ok;
}
function savePlanSnapshot(snapshot: string) {
  savedPlan.value = snapshot;
  void persistCurrentJob(snapshot);
}
function applyStrategyAndClose() {
  fallbackOpen.value = false;
}
function resetStrategyToSaved() {
  fallback.value = cloneStrategy(savedFallback.value);
  fallbackByWave.value = { 1: null, 2: null, 3: null, ...cloneStrategy(savedFallbackByWave.value) };
  strategyRevision.value = savedStrategyRevision.value;
}
function openUtility(type: 'jobs' | 'history' | 'settings') {
  utility.value = type;
  if (type === 'jobs') void refreshJobLibrary();
}
async function openSavedJob(entry: SavedJobEntry) {
  try {
    const job = await readJobFromLibrary(entry.file);
    utility.value = null;
    currentJobPath.value = `jobs/${entry.file}`;
    loadJob(job);
  } catch (error) {
    jobLibraryError.value = `打开作业失败：${error instanceof Error ? error.message : '未知错误'}`;
  }
}

async function removeSavedJob(entry: SavedJobEntry) {
  if (!window.confirm(`删除作业「${entry.file.replace(/\.json$/, '')}」？`)) return;
  try {
    await deleteJobFromLibrary(entry.file);
    await refreshJobLibrary();
  } catch (error) {
    jobLibraryError.value = `删除作业失败：${error instanceof Error ? error.message : '未知错误'}`;
  }
}
function savedJobLineup(entry: SavedJobEntry) {
  const names = entry.job.formation
    .slice(0, 3)
    .filter((slot) => Boolean(slot.name))
    .map((slot) => (slot.support ? `${slot.name}（助战）` : slot.name));
  return names.length ? names.join('、') : '空编队';
}
function formatSavedAt(value: number) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false });
}
function exportCurrentJob() {
  const snapshot: PlanSnapshot = savedPlan.value ? (JSON.parse(savedPlan.value) as PlanSnapshot) : createEmptyPlanSnapshot();
  const job = currentJob(snapshot);
  const blob = new Blob([JSON.stringify(job, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `${jobName.value}.json`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
  jobMessage.value = savedPlan.value ? '已导出当前作业，包含已保存的操作序列与选卡策略。' : '尚未保存操作序列，已导出仅含编队与策略的作业。';
  jobWarnings.value = [];
}
function buildSupportPick(list: Servant[]) {
  const entries = list.map((member, index) => ({ member, index })).filter(({ member }) => !member.empty && member.name);
  if (!entries.length) return null;
  // 先按名称 + 职阶分组，再在组内用 svtId 区分同名同职阶的不同从者。
  const byNameClass = new Map<string, { member: Servant; index: number }[]>();
  entries.forEach((entry) => {
    const key = `${entry.member.name}|${entry.member.role}`;
    const group = byNameClass.get(key);
    if (group) group.push(entry);
    else byNameClass.set(key, [entry]);
  });
  const duplicates: number[][] = [];
  byNameClass.forEach((group) => {
    const bySvt = new Map<number | null, number[]>();
    group.forEach(({ member, index }) => {
      const key = member.svtId ?? null;
      const list = bySvt.get(key);
      if (list) list.push(index);
      else bySvt.set(key, [index]);
    });
    const knownIds = [...bySvt.keys()].filter((key): key is number => key !== null);
    if (knownIds.length > 1) {
      // 同名同职阶但 svtId 不同：是不同从者，分别判断。
      bySvt.forEach((list) => {
        if (list.length > 1) duplicates.push(list);
      });
    } else if (group.length > 1) {
      duplicates.push(group.map(({ index }) => index));
    }
  });
  const required = duplicates.length > 0;
  const candidates = required ? duplicates.flat() : entries.map(({ index }) => index);
  const marked = entries.find(({ member }) => member.support)?.index ?? null;
  const selected = marked !== null && candidates.includes(marked) ? marked : candidates[0] ?? null;
  return {
    candidates,
    required,
    duplicateNames: [...new Set(duplicates.map((group) => list[group[0]].name))],
    selected,
  };
}
// 选中空位表示不指定助战；此时清空所有助战标记。
function assignSupport(list: Servant[], slot: number | null): Servant[] {
  const target = slot !== null && !list[slot]?.empty ? slot : null;
  return list.map((member, index) => {
    if (index === target) {
      return { ...member, support: true, supportRequired: member.support ? member.supportRequired : true };
    }
    if (!member.support && !member.grandRequired) return member;
    return { ...member, support: false, supportRequired: false, grandRequired: false, grandCeTop: null, grandCeMiddle: null, grandCeBottom: null };
  });
}
function applySupportSlot(slot: number | null) {
  members.value = assignSupport(members.value, slot);
}
function commitImport(pending: { job: HugoFgoJob; members: Servant[]; warnings: string[] }, supportNote = '') {
  const { job } = pending;
  members.value = pending.members;
  const warnings = [...pending.warnings, ...(job.warnings ?? [])];
  const code = lookupMysticCodes.value.find((item) => item.id === job.mysticId);
  if (code) mystic.value = code;
  else warnings.push(`作业使用的御主礼装「${job.mysticId}」不存在，保留当前礼装`);
  const applied = jobToSnapshot(job, members.value, mystic.value);
  savedPlan.value = JSON.stringify(applied.snapshot);
  fallback.value = { ...job.strategy };
  fallbackByWave.value = { 1: null, 2: null, 3: null, ...(job.strategyByWave ?? {}) };
  syncFallbackOrders();
  savedFallback.value = cloneStrategy(fallback.value);
  savedFallbackByWave.value = cloneStrategy(fallbackByWave.value);
  savedStrategyRevision.value = strategyRevision.value;
  jobWarnings.value = [...warnings, ...applied.warnings];
  const turnTotal = job.waves.reduce((sum, wave) => sum + (wave.turnCount || 0), 0);
  jobMessage.value = `已导入${job.name ? `「${job.name}」` : '作业'}：${job.waves.length} 面 / ${turnTotal} 个回合${supportNote}`;
  pendingImport.value = null;
  supportPick.value = null;
  planOpen.value = true;
  utility.value = null;
}
function confirmSupportAssignment(slot: number | null) {
  if (pendingImport.value) {
    pendingImport.value.members = assignSupport(pendingImport.value.members, slot);
    commitImport(pendingImport.value);
    return;
  }
  applySupportSlot(slot);
  supportPick.value = null;
}
function cancelSupportPick() {
  supportPick.value = null;
  pendingImport.value = null;
}
function relocateSupport() {
  supportSlot.value = null;
  pendingImport.value = null;
  supportPick.value = buildSupportPick(members.value);
}
function loadJob(job: HugoFgoJob) {
  const formation = applyFormation(job, members.value, team, () => `member-${nextMemberInstanceId++}`, lookupAtlas, equipsById.value);
  const pending = { job, members: formation.members, warnings: formation.warnings };
  const pick = buildSupportPick(pending.members);
  if (pick?.required) {
    const slot = pick.selected ?? pick.candidates[0] ?? null;
    let supportNote = '';
    if (slot !== null) {
      pending.members = assignSupport(pending.members, slot);
      supportNote = `；重复从者已默认 ${slot + 1} 号位为助战`;
    }
    commitImport(pending, supportNote);
  } else if (pick) {
    // 选完助战并确认后才真正导入，取消不会改动当前编队。
    pendingImport.value = pending;
    supportPick.value = pick;
  } else {
    commitImport(pending);
  }
}
async function importFromLink() {
  if (!importSource.value.trim() || jobLoading.value) return;
  if (location.protocol === 'file:') {
    jobMessage.value = '离线分享版无法访问 Chaldea 服务器，请改用本地 JSON 导入';
    return;
  }
  jobLoading.value = true;
  jobMessage.value = '';
  jobWarnings.value = [];
  try {
    const id = parseChaldeaRef(importSource.value);
    const [catalog, team] = await Promise.all([loadChaldeaCatalog(), fetchChaldeaTeam(id)]);
    const hasSecondary = Boolean(team.data.delegate?.skillActSelectSelections?.length || team.data.delegate?.tdTypeChanges?.length);
    const secondaryCatalog = hasSecondary ? await loadSecondaryCatalog() : {};
    loadJob(convertChaldeaTeam(team, catalog, equipsById.value, secondaryCatalog));
  } catch (error) {
    jobMessage.value = error instanceof Error ? error.message : '读取作业失败';
  } finally {
    jobLoading.value = false;
  }
}
async function handleJobFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file) return;
  try {
    loadJob(parseJobFile(await file.text()));
  } catch (error) {
    jobMessage.value = error instanceof Error ? error.message : '读取作业文件失败';
    jobWarnings.value = [];
  }
}

async function startRun() {
  if (runDisabledReason.value) return;
  runStarting.value = true;
  runOverlayVisible.value = true;
  runState.value = { ...emptyRunState(), jobName: jobName.value, serial: selectedSerial.value };
  try {
    if (!await persistCurrentJob()) throw new Error(jobMessage.value || '当前作业保存失败');
    const settings = await loadRunSettings();
    runState.value = await runApi.start({
      serial: selectedSerial.value || (fakeRunRequested ? 'fake' : ''),
      jobPath: currentJobPath.value,
      settings,
      region: resourceRegion.value,
    });
  } catch (error) {
    runState.value = { ...runState.value, phase: 'failed', error: error instanceof Error ? error.message : String(error) };
  } finally {
    runStarting.value = false;
  }
}

async function stopRun() {
  if (runState.value.phase !== 'running') return;
  runState.value = { ...runState.value, phase: 'stopping' };
  try {
    runState.value = await runApi.stop();
  } catch (error) {
    runState.value = { ...runState.value, phase: 'failed', error: `停止执行器失败：${error instanceof Error ? error.message : String(error)}` };
  }
}
</script>

<template>
  <FormationHome :members="members" :mystic="currentMystic" :fallback="fallback" :gender="gender" :gallery-empty="!atlasServants.length" :resource-region="resourceRegion" @gender="gender = $event" @picker="openPicker" @plan="planOpen = true" @fallback="fallbackOpen = true" @support="supportSlot = $event" @reorder="reorderMembers" @remove="removeMember" @ascension="changeAscension"
 @start="startRun" @utility="openUtility" :start-disabled="Boolean(runDisabledReason)" :start-disabled-reason="runDisabledReason" :start-label="runStartLabel" />
  <BattlePlanDrawer v-if="planOpen" ref="planDrawer" :members="members" :mystic="currentMystic" :saved-state="savedPlan" :import-warnings="jobWarnings" :strategy-dirty="strategyDirty" :saving="jobSaving" @close="planOpen = false; jobWarnings = []" @fallback="fallbackOpen = true" @save="savePlanSnapshot" @reset-saved="resetStrategyToSaved" />
  <PickerDialog v-if="picker" :type="picker.type" :slot="picker.slot" :servants="servantChoices" :mystic-codes="allMysticCodes" @close="picker = null" @servant="selectServant" @ce="selectCe" @mystic="selectMystic" />
  <SupportDialog v-if="supportSlot !== null" :slot="supportSlot" :member="members[supportSlot]" :members="members" :equips="equipsList" @close="supportSlot = null" @save="saveSupport" @relocate="relocateSupport" />
  <SupportAssignDialog v-if="supportPick" :members="pendingImport ? pendingImport.members : members" :candidates="supportPick.candidates" :required="supportPick.required" :duplicate-names="supportPick.duplicateNames" :initial-selection="supportPick.selected" :job-name="pendingImport ? jobNameForMembers(pendingImport.members) : jobName" :importing="Boolean(pendingImport)" @confirm="confirmSupportAssignment" @close="cancelSupportPick" />
  <FallbackDialog v-if="fallbackOpen" :fallback="fallback" :fallback-by-wave="fallbackByWave" :members="members" :dirty="strategyDirty" @close="fallbackOpen = false" @update="updateFallback" @reset="resetFallback" @reset-all="resetStrategy" @save="applyStrategyAndClose" />
  <RunOverlay v-if="runOverlayVisible" :state="runState" @close="runOverlayVisible = false" @stop="stopRun" />

  <div v-if="utility" class="utility-backdrop">
    <section class="utility-dialog">
      <header><div><span class="picker-kicker">HUGOFGO</span><h2>{{ { jobs: '作业库', history: '运行记录', settings: '设置与资源' }[utility] }}</h2></div><button title="关闭" @click="utility = null"><AppIcon name="close" /></button></header>
      <div v-if="utility === 'jobs'" class="utility-content"><p v-if="jobLibraryReadOnly" class="job-file-message">浏览器预览为只读：作业可以导入、导出与查看，保存、打开、删除和资源任务请使用桌面版。</p><p v-else>作业保存在桌面应用数据目录的 jobs/ 文件夹（开发期为项目 jobs/），并以首发三名队员命名。</p><div class="utility-import"><AppIcon name="link" :size="16" /><input v-model="importSource" :disabled="jobLoading" placeholder="粘贴 Chaldea 分享链接或 Team ID" @keydown.enter="importFromLink" /><button :disabled="jobLoading || !importSource.trim()" @click="importFromLink">{{ jobLoading ? '读取中…' : '读取' }}</button></div><div class="job-library-head"><div><strong>jobs 文件夹</strong><small>{{ jobName }}.json</small></div><button :disabled="jobLibraryLoading || jobLibraryReadOnly" @click="persistCurrentJob()"><AppIcon name="folder" :size="15" /> 保存当前作业</button></div><p v-if="jobLibraryError" class="job-file-message">{{ jobLibraryError }}</p><ul v-else class="job-library-list"><li v-for="entry in jobLibrary" :key="entry.file"><div><strong>{{ entry.file.replace(/\.json$/, '') }}</strong><small>{{ savedJobLineup(entry) }}</small><small>{{ formatSavedAt(entry.savedAt) }}</small></div><div class="job-library-actions"><button @click="openSavedJob(entry)">打开</button><button @click="removeSavedJob(entry)">删除</button></div></li><li v-if="!jobLibrary.length && !jobLibraryLoading" class="job-library-empty">还没有作业，点击“保存当前作业”写入 jobs/。</li></ul><div class="job-file-row"><div><strong>本地作业文件</strong><small>编队 + 选卡策略 + 可选固定卡序；按编队槽位保存，可跨设备分享</small></div><div class="job-file-actions"><button @click="exportCurrentJob"><AppIcon name="folder" :size="15" /> 导出 JSON</button><button @click="jobFileInput?.click()"><AppIcon name="folder" :size="15" /> 导入 JSON</button></div></div><p v-if="jobMessage" class="job-file-message">{{ jobMessage }}</p><ul v-if="jobWarnings.length" class="job-file-warnings"><li v-for="(warning, index) in jobWarnings" :key="index">{{ warning }}</li></ul><input ref="jobFileInput" class="job-file-input" type="file" accept="application/json,.json" @change="handleJobFileChange" /></div>
      <RunHistory v-else-if="utility === 'history'" />
      <div v-else class="utility-content"><RunSettingsPanel /><DevicePanel v-model="selectedSerial" /><div class="resource-row"><div><strong>图像识别运行时</strong><small>Python CV · RapidOCR · 本地运行</small></div><span class="ready-text">未接入</span></div><div class="resource-row"><div><strong>编队检查方式</strong><small>开始前检测一次 · 不自动配队 · 自有礼装不校验</small></div><span class="ready-text">手动编队</span></div><ResourceCenter @updated="reloadResources(true)" /></div>
    </section>
  </div>
</template>
