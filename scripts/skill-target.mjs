export function servantSkillTarget(skill) {
  const funcs = skill.functions ?? [];
  const func = funcs.find((item) => ['ptOne', 'ptOneOther', 'enemy'].includes(item.funcTargetType)) ?? funcs[0];
  const target = func?.funcTargetType;
  const team = func?.funcTargetTeam ?? '';
  // ptOne / ptOneOther 都需要先选择己方单体；ptOther 则是除自身外的己方全体。
  if (target === 'ptOneOther') return 'ally';
  if (target === 'ptOne') return team.includes('player') ? 'ally' : 'enemy';
  return ({ self: 'self', ptOther: 'allAllies', ptAll: 'allAllies', ptOtherAll: 'allAllies', enemy: 'enemy', enemyAll: 'allEnemies' })[target] ?? 'allAllies';
}
