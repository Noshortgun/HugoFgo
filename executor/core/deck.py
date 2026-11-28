"""指令卡牌组跟踪：只产生非阻塞识别告警，不改变选卡门槛。

普通阶段按同一完整十五卡周期统计前排指定从者的出现次数；锁卡技能生效后
只校验重抽的下一手。证据不完整（换面、换人、漏看手牌等）时丢弃当前统计段。
"""
from __future__ import annotations

from dataclasses import dataclass

from executor.domain import FormationSlotPlan

CARD_CYCLE = 15
HAND_SIZE = 5
# 十五卡牌组由三名前排各五张组成。
EXPECTED_COPIES = 5


@dataclass(frozen=True)
class HandCard:
    svt_id: int | None
    servant_slot: int | None


@dataclass(frozen=True)
class DeckWarning:
    reason: str
    message: str
    data: dict[str, object]


def hand_cards(cards) -> tuple[HandCard, ...]:
    return tuple(HandCard(svt_id=card.svt_id,
                          servant_slot=card.servant_slot if card.servant_slot is not None
                          and card.servant_slot >= 0 else None)
                 for card in cards)


class DeckTracker:
    def __init__(self, formation: tuple[FormationSlotPlan, ...],
                 lock_skills: dict[int, dict[int, int]] | None = None) -> None:
        self._names = {member.slot: member.name for member in formation if member.name.strip()}
        self._svt_ids = {member.slot: member.svt_id for member in formation if member.name.strip()}
        self._lock_skills = lock_skills or {}
        self._cycle: list[HandCard] = []
        self._last: tuple[int, int] | None = None
        self._frontline: tuple[int, ...] | None = None
        self._lock_svt: int | None = None
        self._lock_slot: int | None = None
        self.lock_checked = False

    def reset(self) -> None:
        self._cycle = []
        self._last = None
        self._frontline = None
        self._lock_svt = None
        self._lock_slot = None

    def lock_turns(self, slot: int, skill: int | None) -> int | None:
        """该槽位上的从者在指定技能上是否有锁卡效果；无法确认身份时返回 None。

        返回 Atlas 记录的效果持续手数，仅作说明；当前规格只校验重抽后的下一手。
        """
        svt_id = self._svt_ids.get(slot)
        if svt_id is None or skill is None:
            return None
        return self._lock_skills.get(svt_id, {}).get(skill)

    def begin_lock(self, slot: int) -> None:
        svt_id = self._svt_ids.get(slot)
        self.reset()
        if svt_id is None:
            return
        self._lock_svt = svt_id
        self._lock_slot = slot

    def record_hand(self, wave: int, turn: int, frontline: tuple[int, ...],
                    cards: tuple[HandCard, ...]) -> tuple[DeckWarning, ...]:
        self.lock_checked = False
        if self._lock_svt is not None:
            warnings = self._verify_lock(cards)
            self._lock_svt = None
            self._lock_slot = None
            self.reset()
            return warnings
        if len(cards) != HAND_SIZE:
            self.reset()
            return ()
        frontline = tuple(frontline)
        if self._frontline is not None and frontline != self._frontline:
            self.reset()
        self._frontline = frontline
        if self._last is not None and (wave, turn) != (self._last[0], self._last[1] + 1):
            self.reset()
            self._frontline = frontline
        self._last = (wave, turn)
        self._cycle.extend(cards)
        if len(self._cycle) < CARD_CYCLE:
            return ()
        cycle, self._cycle = self._cycle, []
        self._last = None
        return self._evaluate(cycle)

    def _verify_lock(self, cards: tuple[HandCard, ...]) -> tuple[DeckWarning, ...]:
        self.lock_checked = True
        expected = self._lock_svt
        unknown = sum(1 for card in cards if card.svt_id is None)
        others = sum(1 for card in cards if card.svt_id is not None and card.svt_id != expected)
        slot = self._lock_slot
        name = self._names.get(slot, '锁定从者') if slot is not None else '锁定从者'
        if unknown:
            return (DeckWarning(
                'lock_cards_unknown',
                f'锁卡校验：重抽手牌有 {unknown} 张卡无法识别，无法确认全部属于「{name}」；'
                '本次只告警，不改变选卡。',
                {'slot': slot, 'unknown': unknown}),)
        if others:
            return (DeckWarning(
                'lock_state_mismatch',
                f'锁卡校验：重抽手牌出现 {others} 张其他从者的卡，与「{name}」的锁卡效果不一致；'
                '本次只告警，不改变选卡。',
                {'slot': slot, 'other_cards': others}),)
        return ()

    def _evaluate(self, cycle: list[HandCard]) -> tuple[DeckWarning, ...]:
        unknown = sum(1 for card in cycle if card.svt_id is None)
        warnings: list[DeckWarning] = []
        if unknown:
            warnings.append(DeckWarning(
                'deck_unknown_cards',
                f'牌组周期内共有 {unknown} 张无法识别的指令卡；它们可能属于指定从者，'
                '本周期不做从者卡数告警。',
                {'unknown': unknown}))
        candidates = [(slot, svt_id) for slot, svt_id in self._svt_ids.items() if svt_id is not None]
        duplicate = {svt_id for slot, svt_id in candidates
                     if sum(1 for _slot, other in candidates if other == svt_id) > 1}
        for slot, svt_id in candidates:
            if svt_id in duplicate:
                continue
            count = (sum(1 for card in cycle if card.servant_slot == slot)
                     + sum(1 for card in cycle if card.svt_id == svt_id and card.servant_slot is None))
            if count == EXPECTED_COPIES or unknown:
                continue
            name = self._names.get(slot, f'{slot + 1} 号位')
            warnings.append(DeckWarning(
                'deck_servant_count_mismatch',
                f'牌组周期内指定从者「{name}」的指令卡应为 {EXPECTED_COPIES} 张，'
                f'实际识别到 {count} 张；自动选卡可能把该从者的卡当作任意从者卡。',
                {'slot': slot, 'count': count}))
        return tuple(warnings)
