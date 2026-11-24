import type { FallbackStrategy, Servant } from './types';

export interface ServantOrderEntry {
  slot: number;
  name: string;
}

/** 前排使用物理 0–2 槽；空槽不补入后排，也不进入候选。 */
export function frontlineEntries(members: Servant[]): ServantOrderEntry[] {
  return members.slice(0, 3).flatMap((member, slot) => (
    !member.empty && member.name.trim() ? [{ slot, name: member.name.trim() }] : []
  ));
}

/** 解析顺序：优先按槽位还原；旧作业没有槽位时按名字依次匹配。最后补齐缺失的前排。 */
export function resolveServantOrder(strategy: FallbackStrategy, members: Servant[]): ServantOrderEntry[] {
  const front = frontlineEntries(members);
  const result: ServantOrderEntry[] = [];
  const used = new Set<number>();
  const push = (slot: number) => {
    if (used.has(slot)) return;
    const entry = front.find((item) => item.slot === slot);
    if (!entry) return;
    used.add(slot);
    result.push(entry);
  };
  const slots = strategy.servantOrderSlots;
  if (Array.isArray(slots) && slots.length === strategy.servantOrder.length) {
    slots.forEach((slot, index) => {
      if (!Number.isInteger(slot)) return;
      const entry = front.find((item) => item.slot === slot);
      if (entry && entry.name === strategy.servantOrder[index]) push(slot);
    });
  }
  strategy.servantOrder.forEach((name) => {
    const entry = front.find((item) => !used.has(item.slot) && item.name === name);
    if (entry) push(entry.slot);
  });
  front.forEach((entry) => push(entry.slot));
  return result;
}

/** 默认选卡预设：按当前前排顺序、红卡优先。 */
export function defaultFallbackStrategy(members: Servant[]): FallbackStrategy {
  const entries = frontlineEntries(members);
  return {
    allowNp: false,
    servantOrder: entries.map((entry) => entry.name),
    servantOrderSlots: entries.map((entry) => entry.slot),
    cards: 'buster',
    colorPriority: ['buster', 'arts', 'quick'],
    strategyOrder: ['servant', 'cards', 'affinity', 'crit'],
  };
}

export function emptyServantOrderEntries(strategy: FallbackStrategy, members: Servant[]): ServantOrderEntry[] {
  const slots = strategy.servantOrderSlots;
  if (!Array.isArray(slots) || slots.length !== strategy.servantOrder.length) return [];
  return slots.flatMap((slot, index) => {
    const member = members[slot];
    return !member || member.empty || !member.name.trim()
      ? [{ slot, name: strategy.servantOrder[index] }]
      : [];
  });
}

export function normalizeServantOrder(strategy: FallbackStrategy, members: Servant[]): FallbackStrategy {
  const entries = [...resolveServantOrder(strategy, members), ...emptyServantOrderEntries(strategy, members)];
  return {
    ...strategy,
    servantOrder: entries.map((entry) => entry.name),
    servantOrderSlots: entries.map((entry) => entry.slot),
  };
}
