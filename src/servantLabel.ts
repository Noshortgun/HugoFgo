import type { Servant } from './types';

/** 列表中的从者名；助战追加“（助战）”避免同名从者混淆。 */
export function servantLabel(member: Servant): string {
  if (member.empty || !member.name) return member.name;
  return member.support ? `${member.name}（助战）` : member.name;
}
