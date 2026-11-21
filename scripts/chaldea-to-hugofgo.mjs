#!/usr/bin/env node
/**
 * 把 Chaldea 公开队伍转换成 HugoFgo 作业文件（测试用转换器，不是正式导入器）。
 *
 * 用法：
 *   node scripts/chaldea-to-hugofgo.mjs <team.json> [out.json]
 *   node scripts/chaldea-to-hugofgo.mjs --id 79355 [out.json]
 *
 * 说明：
 * - 队伍成员按“前排 3 + 候补 3”映射到 HugoFgo 槽位 0-5。
 * - Chaldea 的 actions 是线性序列，遇到 attack 视为一个回合结束，全部放入第 1 面。
 * - delegate.replaceMemberIndexes 由御主礼装第 3 技能触发，转换为换人动作。
 * - 具体指令卡转换为 prefer 模式的固定卡序层，执行时仍需按发牌校验。
 * - provenance 仅供测试对照，HugoFgo 运行时和导出都不会读取它。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { resourcePaths } from './resource-paths.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const paths = resourcePaths(root);
const { region } = paths;
const catalog = JSON.parse(fs.readFileSync(paths.absolute(`data/${region}/servants-by-id.json`), 'utf8'));
const equipPath = paths.absolute(`data/${region}/equips-by-id.json`);
const equips = fs.existsSync(equipPath) ? JSON.parse(fs.readFileSync(equipPath, 'utf8')) : {};
const cardTypes = { 1: 'arts', 2: 'buster', 3: 'quick', arts: 'arts', buster: 'buster', quick: 'quick' };
const localIcons = ['skill_00400', 'skill_00403', 'skill_00700'];

function readTeam(args) {
  const idIndex = args.indexOf('--id');
  if (idIndex !== -1) {
    return fetch(`https://api.chaldea.center/api/v1/teams/${args[idIndex + 1]}`).then(async (res) => {
      if (!res.ok) throw new Error(`获取队伍失败：HTTP ${res.status}`);
      return res.json();
    });
  }
  const file = args[0];
  if (!file) throw new Error('请提供 team.json 路径，或使用 --id <队伍编号>');
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function closeTurn(current) {
  const turn = { actions: current.actions };
  if (current.cards.length) turn.attack = { mode: 'prefer', cards: current.cards };
  if (Object.keys(current.npPositions).length) turn.npPositions = current.npPositions;
  return turn;
}

function servantName(svtId) {
  return catalog[svtId]?.name ?? `未知从者 ${svtId}`;
}

function convert(team) {
  const data = team.data;
  const onField = data.team.onFieldSvts ?? [];
  const backup = data.team.backupSvts ?? [];
  const all = [...onField, ...backup];
  const formation = all.map((member, slot) => {
    if (!member) return { slot, name: '' };
    const info = catalog[member.svtId];
    const entry = {
      slot,
      svtId: member.svtId,
      name: servantName(member.svtId),
      role: info?.classLabel ?? '',
    };
    if (member.supportType && member.supportType !== 'none') {
      entry.support = true;
      entry.supportRequired = true;
    }
    if (member.grandSvt) entry.grandRequired = true;
    const ceId = member.ceId ?? member.equip1?.id;
    if (ceId) {
      entry.ceId = ceId;
      if (equips[ceId]) entry.ce = equips[ceId].name;
    }
    if (member.grandSvt) {
      const ids = [member.equip1?.id ?? null, member.equip2?.id ?? null, member.equip3?.id ?? null];
      entry.grandCeIds = { top: ids[0], middle: ids[1], bottom: ids[2] };
      entry.grandCe = {
        top: ids[0] ? equips[ids[0]]?.name ?? null : null,
        middle: ids[1] ? equips[ids[1]]?.name ?? null : null,
        bottom: ids[2] ? equips[ids[2]]?.name ?? null : null,
      };
    }
    return entry;
  });

  const frontline = [0, 1, 2];
  const replacements = [...(data.delegate?.replaceMemberIndexes ?? [])];
  const turnEntries = [];
  let current = { actions: [], npPositions: {}, cards: [] };
  let target = 3;
  const emitted = new Map();
  let rawIndex = 0;
  let currentSourceIndex = 0;

  const slotName = (slot) => formation[slot]?.name ?? `槽位 ${slot + 1}`;
  const pushAction = (action) => {
    current.actions.push(action);
    emitted.set(currentSourceIndex, action);
  };

  for (const action of data.actions ?? []) {
    currentSourceIndex = rawIndex++;
    if (action.type === 'skill') {
      const isMasterSkill = action.svt === undefined;
      if (isMasterSkill) {
        if (action.skill === 2 && replacements.length) {
          const [outIndex, inIndex] = replacements.shift();
          const outSlot = frontline[outIndex];
          const inSlot = 3 + inIndex;
          pushAction({
            kind: 'orderChange',
            title: `换人 · ${slotName(outSlot)} → ${slotName(inSlot)}`,
            detail: `${slotName(outSlot)} 下场，${slotName(inSlot)} 上场`,
            icon: 'skill_00306',
            skill: action.skill,
            swapOutSlot: outSlot,
            swapInSlot: inSlot,
          });
          frontline[outIndex] = inSlot;
          continue;
        }
        pushAction({
          kind: 'master',
          title: `御主礼装 · 技能 ${action.skill + 1}`,
          detail: '御主礼装技能',
          icon: 'skill_00400',
          skill: action.skill,
        });
        continue;
      }
      const targetSlot = frontline[action.options?.playerTarget ?? 0];
      const skillSlot = frontline[action.svt] ?? action.svt;
      pushAction({
        kind: 'skill',
        title: `${slotName(skillSlot)} · ${action.skill + 1} 技能`,
        detail: `目标：${slotName(targetSlot ?? frontline[0])}`,
        icon: localIcons[action.skill % localIcons.length],
        skill: action.skill,
        memberSlot: skillSlot,
        playerTargetSlot: targetSlot,
      });
      continue;
    }

    if (action.type !== 'attack') continue;
    target = Math.min(3, Math.max(1, (action.options?.enemyTarget ?? 0) + 1));
    const attacks = action.attacks ?? [];
    attacks.forEach((attack, index) => {
      const attackSlot = frontline[attack.svt] ?? attack.svt;
      if (attack.isTD) {
        pushAction({
          kind: 'attack',
          title: `${slotName(attackSlot)} · 宝具`,
          detail: `宝具释放卡序：${index + 1}`,
          icon: 'skill_00400',
          memberSlot: attackSlot,
        });
        current.npPositions[attackSlot] = index + 1;
        return;
      }
      const type = cardTypes[attack.cardType];
      if (type && attack.card >= 0) current.cards.push({ slot: attackSlot, type, deckIndex: attack.card });
    });
    turnEntries.push({ turn: closeTurn(current), target });
    current = { actions: [], npPositions: {}, cards: [] };
  }
  if (current.actions.length || current.cards.length) {
    turnEntries.push({ turn: closeTurn(current), target });
  }

  (data.delegate?.skillActSelectSelections ?? []).forEach((sourceIndex) => {
    const action = emitted.get(sourceIndex);
    if (!action) return;
    action.detail += ' · 原作业含二次选择（未记录选项）';
  });

  const hasOrderChange = (data.delegate?.replaceMemberIndexes ?? []).length > 0;
  const tdType = (data.delegate?.tdTypeChanges ?? [])[0];
  const firstTurn = turnEntries[0]?.turn;
  if (tdType && firstTurn?.actions) {
    const npAction = firstTurn.actions.find((action) => action.kind === 'attack');
    if (npAction) npAction.detail += ` · 已变更宝具卡色（${cardTypes[tdType] ?? tdType}）`;
  }

  // Chaldea 作业不记录分面，按常规「每面一个回合」分配：第 1/2/3 回合对应第 1/2/3 面，
  // 超过 3 个回合的部分留在第 3 面继续。
  const waves = [0, 1, 2].map((waveIndex) => {
    const entries = turnEntries.filter((_, index) => Math.min(index, 2) === waveIndex);
    return {
      turnCount: Math.max(1, entries.length),
      target: entries[0]?.target ?? turnEntries[0]?.target ?? 3,
      turns: entries.length ? entries.map((entry) => entry.turn) : [{ actions: [] }],
    };
  });

  const mysticCodeId = data.team?.mysticCode?.mysticCodeId;
  const job = {
    version: 1,
    name: `${(team.names ?? []).join(' / ')} · Chaldea #${team.id}`,
    mysticId: mysticCodeId ? `atlas-${mysticCodeId}` : (hasOrderChange ? 'order-change' : 'default'),
    enemyCount: 3,
    formation,
    strategy: {
      allowNp: true,
      servantOrder: formation.slice(0, 3).map((entry) => entry.name),
      cards: 'buster',
      colorPriority: ['buster', 'arts', 'quick'],
      strategyOrder: ['servant', 'cards', 'crit'],
    },
    waves,
    provenance: {
      source: 'chaldea',
      teamId: team.id,
      questId: team.questId,
      note: '测试用途：原始记录仅作对照，HugoFgo 运行时不读取，导出不会写回',
      actions: data.actions,
      delegate: data.delegate ?? {},
    },
  };
  return job;
}

export { convert };

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const team = await readTeam(args);
  const job = convert(team);
  const outIndex = args.indexOf('--id') === 2 ? 3 : 1;
  const outFile = args[outIndex] ?? path.join(root, `examples/chaldea-${team.id}.hugofgo.json`);
  fs.mkdirSync(path.dirname(outFile), { recursive: true });
  fs.writeFileSync(outFile, JSON.stringify(job, null, 2));

  console.log(`已生成 ${outFile}`);
  console.log(`队伍 #${team.id} · ${job.formation.map((entry) => entry.name).join(' | ')}`);
  console.log(`御主礼装：${job.mysticId}`);
  job.waves.forEach((wave, index) => {
    const stats = wave.turns.map((turn) => `${turn.actions.length} 动作${turn.attack ? ` / ${turn.attack.cards.length} 卡` : ''}`);
    console.log(`第 ${index + 1} 面：${wave.turnCount} 回合 · ${stats.join('，')}`);
  });
}
