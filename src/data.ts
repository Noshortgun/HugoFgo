import type { MysticCode, Servant, SkillDefinition, SkillTarget } from './types';

export const skillIcons = ['skill_00400', 'skill_00403', 'skill_00700'];
export const spellIcons = ['skill_00601', 'skill_00600'];

export function standardSkills(overrides: Partial<Record<number, Partial<SkillDefinition>>> = {}): SkillDefinition[] {
  return skillIcons.map((icon, index) => ({
    icon,
    target: (index === 1 ? 'ally' : 'allAllies') as SkillTarget,
    ...overrides[index],
  }));
}

export const team: Servant[] = [
  { memberInstanceId: 'member-1', svtId: 100100, name: '阿尔托莉雅', role: 'Saber', image: '/assets/servants/artoria.png', art: '/assets/cards/artoria.png', level: 'Lv. 100', ce: '星之王冠', ceImage: '/assets/ces/9300010.png', ceId: 9300010, skills: standardSkills({ 2: { target: 'ally' } }) },
  { memberInstanceId: 'member-2', svtId: 800100, name: '玛修·基列莱特', role: 'Shielder', image: '/assets/servants/mash.png', art: '/assets/cards/mash.png', level: 'Lv. 90', ce: '获赠的尊贵十字之盾面', ceImage: '/assets/ces/9309050.png', ceId: 9309050, skills: standardSkills() },
  { memberInstanceId: 'member-3', svtId: 200200, name: '吉尔伽美什', role: 'Archer', image: '/assets/servants/gilgamesh.png', art: '/assets/cards/gilgamesh.png', level: 'Lv. 100', ce: '', ceImage: '', support: true, supportRequired: false, grandRequired: false, skills: standardSkills({ 2: { target: 'self' } }) },
  { memberInstanceId: 'member-4', svtId: 200100, name: '卫宫', role: 'Archer', image: '/assets/servants/emiya.png', art: '/assets/cards/emiya.png', level: 'Lv. 90', ce: '赤原猎兵', ceImage: '/assets/ces/9300060.png', ceId: 9300060, skills: standardSkills() },
  { memberInstanceId: 'member-5', svtId: 300100, name: '库·丘林', role: 'Lancer', image: '/assets/servants/cu.png', art: '/assets/cards/cu.png', level: 'Lv. 90', ce: '预言之星', ceImage: '/assets/ces/9300450.png', ceId: 9300450, skills: standardSkills() },
  { memberInstanceId: 'member-6', svtId: 400100, name: '美杜莎', role: 'Rider', image: '/assets/servants/medusa.png', art: '/assets/cards/medusa.png', level: 'Lv. 80', ce: '无形之岛', ceImage: '/assets/ces/9300470.png', ceId: 9300470, skills: standardSkills() },
];

export const mysticCodes: MysticCode[] = [
  { id: 'default', name: '魔术礼装·迦勒底', figure: '/assets/mystic/default-figure.png', item: '/assets/mystic/default-item.png', maleFigure: '/assets/mystic/default-figure-male.png', maleItem: '/assets/mystic/default-item-male.png', skills: ['mc_00600', 'mc_00300', 'mc_00402'], skillTargets: ['ally', 'ally', 'ally'] },
  { id: 'order-change', name: '魔术礼装·迦勒底战斗服', figure: '/assets/mystic/order-change-figure.png', item: '/assets/mystic/order-change-item.png', maleFigure: '/assets/mystic/order-change-figure-male.png', maleItem: '/assets/mystic/order-change-item-male.png', skills: ['mc_00300', 'mc_00508', 'mc_00704'], skillTargets: ['allAllies', 'enemy', 'ally'], orderChangeSkill: 2 },
];

let nextEmptyInstanceId = 1;
export function emptyServant(support = false): Servant {
  return { memberInstanceId: `empty-${nextEmptyInstanceId++}`, name: '空位', role: '', image: '', art: '', level: '', ce: '', ceImage: '', skills: standardSkills(), support, supportRequired: false, grandRequired: false, empty: true };
}
