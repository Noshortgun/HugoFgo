"""把作业 JSON 编译成执行器可直接消费的计划。

结构由 schemas/job-v1.schema.json 统一校验；这里只补充 Schema 无法表达的业务语义。
"""
from __future__ import annotations

import json
from typing import Any

from executor.domain.model import (
    ActionPlan,
    AttackCardPlan,
    AttackPlan,
    CompileResult,
    Diagnostic,
    ExecutionPlan,
    FallbackStrategy,
    FormationSlotPlan,
    TurnPlan,
    WavePlan,
)
from executor.domain.schema import validate_job

SLOT_KEYS = ('memberSlot', 'playerTargetSlot', 'swapOutSlot', 'swapInSlot')


def _diagnostic(diagnostics: list[Diagnostic], path: str, message: str,
                wave: int | None = None, turn: int | None = None) -> None:
    diagnostics.append(Diagnostic(path=path, message=message, wave=wave, turn=turn))


def _formation_slots(raw: dict[str, Any], diagnostics: list[Diagnostic]) -> dict[int, str]:
    slots: dict[int, str] = {}
    for index, entry in enumerate(raw['formation']):
        slot = entry['slot']
        if slot in slots:
            _diagnostic(diagnostics, f'$.formation[{index}].slot', f'编队槽位重复：{slot}')
            continue
        slots[slot] = entry['name'].strip()
    return slots


def _compile_strategy(raw: dict[str, Any]) -> FallbackStrategy:
    slots = raw.get('servantOrderSlots')
    return FallbackStrategy(
        allow_np=raw['allowNp'],
        servant_order=tuple(raw['servantOrder']),
        servant_order_slots=tuple(slots) if slots is not None else None,
        cards=raw['cards'],
        color_priority=tuple(raw['colorPriority']),
        strategy_order=tuple(raw['strategyOrder']),
    )


def _compile_formation(raw: dict[str, Any]) -> tuple[FormationSlotPlan, ...]:
    return tuple(
        FormationSlotPlan(
            slot=entry['slot'],
            name=entry['name'].strip(),
            svt_id=entry.get('svtId'),
            limit_count=entry.get('limitCount'),
            np_id=entry.get('npId'),
            role=entry.get('role'),
            support=entry.get('support', False),
            support_required=entry.get('supportRequired', False),
            grand_required=entry.get('grandRequired', False),
            ce=entry.get('ce'),
            ce_id=entry.get('ceId'),
            grand_ce=entry.get('grandCe'),
            grand_ce_ids=entry.get('grandCeIds'),
        )
        for entry in raw['formation']
    )


def _check_strategy_slots(strategy: dict[str, Any], path: str, formation: dict[int, str],
                          diagnostics: list[Diagnostic], wave: int | None = None) -> None:
    for index, slot in enumerate(strategy.get('servantOrderSlots') or []):
        if not formation.get(slot):
            _diagnostic(diagnostics, f'{path}.servantOrderSlots[{index}]',
                        f'补刀策略引用了空槽位：{slot} 号位没有从者', wave)


def _check_slot(action: dict[str, Any], key: str, action_path: str, wave: int, turn: int,
                formation: dict[int, str], diagnostics: list[Diagnostic]) -> None:
    value = action.get(key)
    if value is not None and not formation.get(value):
        _diagnostic(diagnostics, f'{action_path}.{key}', f'引用了空槽位：{value} 号位没有从者', wave, turn)


def _compile_action(action: dict[str, Any], action_path: str, wave: int, turn_no: int,
                    formation: dict[int, str], diagnostics: list[Diagnostic]) -> ActionPlan:
    for key in SLOT_KEYS:
        _check_slot(action, key, action_path, wave, turn_no, formation, diagnostics)
    if action['kind'] == 'orderChange' and action['swapOutSlot'] == action['swapInSlot']:
        _diagnostic(diagnostics, f'{action_path}.swapInSlot', '换人动作的上下场槽位不能相同', wave, turn_no)
    return ActionPlan(
        kind=action['kind'],
        title=action['title'],
        detail=action['detail'],
        icon=action['icon'],
        skill=action.get('skill'),
        member_slot=action.get('memberSlot'),
        player_target_slot=action.get('playerTargetSlot'),
        enemy_target=action.get('enemyTarget'),
        secondary_selection=action.get('secondarySelection'),
        swap_out_slot=action.get('swapOutSlot'),
        swap_in_slot=action.get('swapInSlot'),
        from_target=action.get('fromTarget'),
        to_target=action.get('toTarget'),
    )


def _compile_np_positions(value: dict[str, Any] | None, turn_path: str, wave: int, turn_no: int,
                          formation: dict[int, str], diagnostics: list[Diagnostic]) -> dict[int, int | str]:
    positions: dict[int, int | str] = {}
    used_positions: dict[int, int] = {}
    for slot_text, position in (value or {}).items():
        slot = int(slot_text)
        if not formation.get(slot):
            _diagnostic(diagnostics, f'{turn_path}.npPositions.{slot_text}',
                        f'宝具卡序引用了空槽位：{slot} 号位没有从者', wave, turn_no)
        if isinstance(position, int) and position in used_positions:
            _diagnostic(diagnostics, f'{turn_path}.npPositions.{slot_text}',
                        f'宝具卡序冲突：槽位 {used_positions[position]} 与槽位 {slot} 都在第 {position} 张',
                        wave, turn_no)
        elif isinstance(position, int):
            used_positions[position] = slot
        positions[slot] = position
    return positions


def _compile_attack(value: dict[str, Any] | None, turn_path: str, wave: int, turn_no: int,
                    formation: dict[int, str], diagnostics: list[Diagnostic]) -> AttackPlan | None:
    if value is None:
        return None
    cards = []
    for index, card in enumerate(value['cards']):
        if not formation.get(card['slot']):
            _diagnostic(diagnostics, f'{turn_path}.attack.cards[{index}].slot',
                        f'指令卡引用了空槽位：{card["slot"]} 号位没有从者', wave, turn_no)
        cards.append(AttackCardPlan(
            slot=card['slot'],
            type=card['type'],
            deck_index=card.get('deckIndex'),
        ))
    return AttackPlan(mode=value['mode'], cards=tuple(cards))


def _compile_turn(turn: dict[str, Any], wave: int, turn_number: int, formation: dict[int, str],
                  diagnostics: list[Diagnostic]) -> TurnPlan:
    turn_path = f'$.waves[{wave - 1}].turns[{turn_number - 1}]'
    actions = tuple(
        _compile_action(action, f'{turn_path}.actions[{index}]', wave, turn_number, formation, diagnostics)
        for index, action in enumerate(turn['actions'])
    )
    np_positions = _compile_np_positions(turn.get('npPositions'), turn_path, wave, turn_number, formation, diagnostics)
    attack = _compile_attack(turn.get('attack'), turn_path, wave, turn_number, formation, diagnostics)
    return TurnPlan(actions=actions, np_positions=np_positions, attack=attack)


def _compile_wave(wave: dict[str, Any], index: int, formation: dict[int, str],
                  diagnostics: list[Diagnostic]) -> WavePlan:
    wave_number = index + 1
    path = f'$.waves[{index}]'
    if wave['turnCount'] != len(wave['turns']):
        _diagnostic(diagnostics, f'{path}.turnCount',
                    f'回合数不一致：turnCount={wave["turnCount"]}，turns 有 {len(wave["turns"])} 个',
                    wave_number)
    turns = tuple(
        _compile_turn(turn, wave_number, turn_index + 1, formation, diagnostics)
        for turn_index, turn in enumerate(wave['turns'])
    )
    return WavePlan(number=wave_number, target=wave['target'], turns=turns)


def compile_job(text: str) -> CompileResult:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        return CompileResult(None, [Diagnostic('$', f'不是有效的 JSON：{error}')])

    diagnostics = validate_job(raw)
    if diagnostics:
        return CompileResult(None, diagnostics)
    if not 1 <= len(raw['waves']) <= 3:
        _diagnostic(diagnostics, '$.waves', f'作业面数必须为 1～3 面；当前为 {len(raw["waves"])} 面')
        return CompileResult(None, diagnostics)

    formation = _formation_slots(raw, diagnostics)
    _check_strategy_slots(raw['strategy'], '$.strategy', formation, diagnostics)
    for wave_text, strategy in (raw.get('strategyByWave') or {}).items():
        if strategy is not None:
            _check_strategy_slots(strategy, f'$.strategyByWave.{wave_text}', formation, diagnostics, int(wave_text))
    waves = tuple(
        _compile_wave(wave, index, formation, diagnostics)
        for index, wave in enumerate(raw['waves'])
    )
    plan = ExecutionPlan(
        mystic_id=raw['mysticId'],
        enemy_count=raw['enemyCount'],
        waves=waves,
        formation=_compile_formation(raw),
        strategy=_compile_strategy(raw['strategy']),
        strategy_by_wave={int(wave): _compile_strategy(strategy) if strategy is not None else None
                          for wave, strategy in (raw.get('strategyByWave') or {}).items()},
    )
    return CompileResult(None if diagnostics else plan, diagnostics)
