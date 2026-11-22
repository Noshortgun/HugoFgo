from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from executor.domain import ExecutionPlan, FallbackStrategy
from executor.perception import BasePage, Control, ControlKind, Observation


@dataclass(frozen=True)
class CardSelection:
    controls: tuple[Control, ...]
    coordinates: tuple[tuple[int, int], ...]
    reason: str
    source: str
    warnings: tuple[str, ...] = ()
    diagnostic: str | None = None


def _empty(reason: str, source: str, warnings: tuple[str, ...] = ()) -> CardSelection:
    return CardSelection((), (), reason, source, warnings, reason)


def select_cards(observation: Observation, strategy: FallbackStrategy,
                 plan: ExecutionPlan, wave: int,
                 reserved: tuple[tuple[int | None, Control], ...] = ()) -> CardSelection:
    override = plan.strategy_by_wave.get(wave)
    effective = override or strategy
    source = 'wave' if override is not None else 'global'
    warnings: list[str] = []
    if observation.base_page is not BasePage.CARD:
        return _empty('当前画面不是指令卡选择页', source)

    reserved_names = {control.name for _, control in reserved}
    known_slots = {member.slot for member in plan.formation}
    affinity_ranks = {'resist': 0, 'disadvantage': 0, '抵抗': 0,
                      'neutral': 1, 'advantage': 2, '克制': 2}
    candidates = [control for control in observation.controls
                  if control.name not in reserved_names
                  and control.kind is ControlKind.CARD and control.actionable
                  and control.card is not None and control.card.servant_slot in known_slots
                  and control.card.card_type in {'buster', 'arts', 'quick'}
                  and ((not control.card.noble_phantasm and 450 < control.center[1] < 650)
                       or (effective.allow_np and control.card.noble_phantasm and control.center[1] < 450))]
    if not candidates and not reserved:
        return _empty('没有识别到可用指令卡', source)
    needed = 3 - len(reserved)
    if needed < 0 or len(candidates) < needed:
        return _empty('可用指令卡不足三张', source)
    if any(control.card is None or control.card.servant_slot not in known_slots
           or control.card.card_type not in {'buster', 'arts', 'quick'}
           for _, control in reserved):
        return _empty('指令卡身份不确定', source, tuple(warnings))

    ranked = (*candidates, *(control for _, control in reserved))
    if any(control.card.crit_rate is None for control in ranked):
        warnings.append('缺少暴击率，按 0 处理')
    if any(control.card.affinity not in affinity_ranks for control in ranked):
        warnings.append('缺少克制信息，按中立处理')

    ranks: dict[int, int] = {}
    used: set[int] = set()
    slots = effective.servant_order_slots
    if slots is not None and len(slots) == len(effective.servant_order):
        for name, slot in zip(effective.servant_order, slots):
            member = next((item for item in plan.formation if item.slot == slot), None)
            if member is not None and member.name == name and slot not in used:
                used.add(slot)
                ranks[slot] = len(ranks)
    elif effective.servant_order:
        warnings.append('补刀策略缺少槽位信息，按从者名称匹配')
    for name in effective.servant_order:
        member = next((item for item in plan.formation
                       if item.slot not in used and item.name == name), None)
        if member is not None:
            used.add(member.slot)
            ranks[member.slot] = len(ranks)
    for member in plan.formation:
        ranks.setdefault(member.slot, len(ranks))

    color_ranks = {color: rank for rank, color in enumerate(effective.color_priority)}

    def key(indexed: tuple[tuple[int, Control], ...]) -> tuple:
        cards = [control.card for _, control in reserved] + [control.card for _, control in indexed]
        values = []
        for criterion in effective.strategy_order:
            if criterion == 'servant':
                values.append(tuple(sorted(ranks.get(card.servant_slot, len(ranks)) for card in cards)))
            elif criterion == 'cards':
                if effective.cards == 'balanced':
                    preference = -len({card.card_type for card in cards})
                else:
                    preference = -sum(card.card_type == effective.cards for card in cards)
                values.append((preference,
                               tuple(sorted(color_ranks.get(card.card_type, len(color_ranks))
                                            for card in cards))))
            elif criterion == 'crit':
                values.append(tuple(sorted((-(card.crit_rate or 0) for card in cards))))
        values.append(tuple(sorted((-affinity_ranks.get(card.affinity, affinity_ranks['neutral'])
                                    for card in cards))))
        return (*values, tuple(index for index, _ in indexed))

    chosen_indexed = min(combinations(enumerate(candidates), needed), key=key)
    chosen = {id(control) for _, control in chosen_indexed}
    available = [control for control in candidates if id(control) in chosen]
    positioned: list[Control | None] = [None, None, None]
    automatic: list[Control] = []
    for position, control in reserved:
        if position is None:
            automatic.append(control)
        else:
            positioned[position - 1] = control
    for control in (*automatic, *available):
        positioned[positioned.index(None)] = control
    controls = tuple(control for control in positioned if control is not None)
    return CardSelection(controls, tuple(control.center for control in controls),
                         '按补刀策略顺序选择三张指令卡', source, tuple(warnings))
