export type SkillTarget = 'self' | 'ally' | 'allAllies' | 'enemy' | 'allEnemies';

export interface SkillSecondaryOption {
  value: string | number;
  label: string;
  imageUrl?: string;
  npId?: number;
  conditions?: { cond: string; value?: number }[];
}

export interface SkillSecondarySelection {
  type: string;
  label: string;
  shape?: 'buttons2' | 'buttons3';
  optionCount?: number;
  followUpTarget?: 'ally' | null;
  options: SkillSecondaryOption[];
}

export interface SkillDefinition {
  id?: number;
  name?: string;
  icon: string;
  /** 本地图库中的技能图标地址；缺省时使用 icon 对应的内置图片。 */
  iconUrl?: string;
  target: SkillTarget;
  secondarySelection?: SkillSecondarySelection;
}

export interface Servant {
  memberInstanceId: string;
  /** 游戏内从者 ID；同名同职阶的不同从者靠它区分。 */
  svtId?: number;
  limitCount?: number;
  npId?: number;
  noblePhantasm?: { id: number; name: string; card: string };
  name: string;
  role: string;
  image: string;
  art: string;
  level: string;
  ce: string;
  ceImage: string;
  ceId?: number;
  skills: SkillDefinition[];
  support?: boolean;
  supportRequired?: boolean;
  grandRequired?: boolean;
  empty?: boolean;
  /** 来自作业文件但没有本地卡面的从者，用占位卡显示。 */
  placeholder?: boolean;
  grandCeTop?: { name: string; image: string; id?: number } | null;
  grandCeMiddle?: { name: string; image: string; id?: number } | null;
  grandCeBottom?: { name: string; image: string; id?: number } | null;
}

export interface MysticCode {
  id: string;
  name: string;
  figure: string;
  item: string;
  maleFigure: string;
  maleItem: string;
  skills: string[];
  /** 图库中的御主技能图标，按技能顺序。 */
  skillIconUrls?: string[];
  /** 御主技能目标，按技能顺序；缺失时按全体处理。 */
  skillTargets?: SkillTarget[];
  orderChangeSkill?: number;
}

export interface SelectedSkillOption {
  type: string;
  index: number;
  value: string | number;
  label: string;
  imageUrl?: string;
  options?: SkillSecondaryOption[];
}

export interface PlanAction {
  id: number;
  kind: 'skill' | 'master' | 'spell' | 'attack' | 'target' | 'orderChange';
  title: string;
  detail: string;
  icon: string;
  skillKey?: string;
  skill?: number;
  memberInstanceId?: string;
  playerTarget?: string;
  enemyTarget?: number;
  secondarySelection?: SelectedSkillOption;
  swapOutMemberInstanceId?: string;
  swapInMemberInstanceId?: string;
  fromTarget?: number;
  toTarget?: number;
}

export type CardColor = 'buster' | 'arts' | 'quick';
export type StrategyCriterion = 'servant' | 'cards' | 'crit' | 'affinity';
export type NpPosition = 'auto' | 1 | 2 | 3;

/** 旧版作业文件的固定卡序字段；导入时忽略并改用选卡策略。 */
export type AttackCardMode = 'strategy' | 'prefer' | 'verify';

export interface PlanSnapshot {
  waveCount?: number;
  turnCountByWave: Record<number, number>;
  enemyCount: 3 | 6;
  targetByWave: Record<number, number>;
  actionsBySlot: Record<string, PlanAction[]>;
  npPositionsBySlot: Record<string, Record<string, NpPosition>>;
  nextId: number;
}

export interface FallbackStrategy {
  allowNp: boolean;
  servantOrder: string[];
  /** 与 servantOrder 对应的编队槽位；用于区分同名从者（例如两个娄希）。旧作业可省略。 */
  servantOrderSlots?: number[];
  cards: CardColor | 'balanced';
  colorPriority: CardColor[];
  strategyOrder: StrategyCriterion[];
}

export type FallbackScope = 'all' | 1 | 2 | 3;

/* ---- Portable job file. Runtime members are referenced by party slot (0-5),
   never by memberInstanceId, so a job can be shared between different sessions. ---- */

export interface JobFormationSlot {
  slot: number;
  /** 游戏内从者 ID；用于准确判断是否为同一位从者。 */
  svtId?: number;
  limitCount?: number;
  npId?: number;
  name: string;
  role?: string;
  support?: boolean;
  supportRequired?: boolean;
  grandRequired?: boolean;
  ce?: string;
  ceId?: number;
  grandCe?: { top?: string | null; middle?: string | null; bottom?: string | null };
  grandCeIds?: { top?: number | null; middle?: number | null; bottom?: number | null };
}

export interface JobAction {
  kind: PlanAction['kind'];
  title: string;
  detail: string;
  icon: string;
  skill?: number;
  memberSlot?: number;
  playerTargetSlot?: number;
  enemyTarget?: number;
  secondarySelection?: SelectedSkillOption;
  swapOutSlot?: number;
  swapInSlot?: number;
  fromTarget?: number;
  toTarget?: number;
}

export interface JobCard {
  slot: number;
  type: CardColor;
  deckIndex?: number;
}

export interface JobTurn {
  actions: JobAction[];
  npPositions?: Record<number, NpPosition>;
  attack?: { mode: AttackCardMode; cards: JobCard[] };
}

export interface JobWave {
  turnCount: number;
  target: number;
  turns: JobTurn[];
}

export interface HugoFgoJob {
  version: 1;
  name?: string;
  mysticId: string;
  enemyCount: 3 | 6;
  formation: JobFormationSlot[];
  strategy: FallbackStrategy;
  strategyByWave?: Record<number, FallbackStrategy | null>;
  waves: JobWave[];
  warnings?: string[];
  /** 测试用途：导入与再次保存时保留，但运行时不读取。 */
  provenance?: unknown;
}
