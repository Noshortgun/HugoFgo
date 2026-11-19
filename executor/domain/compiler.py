"""把作业 JSON 编译成执行器可直接消费的计划。"""
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
    TurnPlan,
    WavePlan,
)

SLOT_KEYS = ('memberSlot', 'playerTargetSlot', 'swapOutSlot', 'swapInSlot')
SUPPORTED_KINDS = frozenset({'skill', 'master', 'spell', 'attack', 'target', 'orderChange'})
SUPPORTED_SECONDARY_TYPES = frozenset({'selectAddInfo', 'noblePhantasmCardType', 'criticalStarConsumption'})
CARD_TYPES = frozenset({'buster', 'arts', 'quick'})
ATTACK_MODES = frozenset({'strategy', 'prefer', 'verify'})


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _diagnostic(diagnostics: list[Diagnostic], path: str, message: str,
                wave: int | None = None, turn: int | None = None) -> None:
    diagnostics.append(Diagnostic(path=path, message=message, wave=wave, turn=turn))


def _validate_top_level(raw: Any, diagnostics: list[Diagnostic]) -> bool:
    if not isinstance(raw, dict):
        _diagnostic(diagnostics, '$', '作业必须是 JSON 对象')
        return False
    if raw.get('version') != 1:
        _diagnostic(diagnostics, '$.version', f'不支持的作业版本：{raw.get("version")!r}；仅支持 1')
    if raw.get('enemyCount') not in (3, 6):
        _diagnostic(diagnostics, '$.enemyCount', f'敌人数量必须是 3 或 6，收到 {raw.get("enemyCount")!r}')
    if not isinstance(raw.get('mysticId'), str) or not raw.get('mysticId', '').strip():
        _diagnostic(diagnostics, '$.mysticId', 'mysticId 必须是非空字符串')
    if not isinstance(raw.get('strategy'), dict):
        _diagnostic(diagnostics, '$.strategy', 'strategy 必须是对象')
    waves = raw.get('waves')
    if not isinstance(waves, list) or not waves:
        _diagnostic(diagnostics, '$.waves', 'waves 必须是非空数组')
    return True


def _formation_slots(raw: dict[str, Any], diagnostics: list[Diagnostic]) -> dict[int, str]:
    formation = raw.get('formation')
    if not isinstance(formation, list):
        _diagnostic(diagnostics, '$.formation', 'formation 必须是数组')
        return {}
    slots: dict[int, str] = {}
    for index, entry in enumerate(formation):
        path = f'$.formation[{index}]'
        if not isinstance(entry, dict):
            _diagnostic(diagnostics, path, '编队槽位必须是对象')
            continue
        slot = entry.get('slot')
        if not _is_int(slot) or not 0 <= slot <= 5:
            _diagnostic(diagnostics, f'{path}.slot', f'编队槽位越界：{slot!r} 不在 0–5')
            continue
        if slot in slots:
            _diagnostic(diagnostics, f'{path}.slot', f'编队槽位重复：{slot}')
            continue
        slots[slot] = str(entry.get('name') or '').strip()
    return slots


def _check_slot(action: dict[str, Any], key: str, action_path: str, wave: int, turn: int,
                formation: dict[int, str], diagnostics: list[Diagnostic]) -> None:
    value = action.get(key)
    if value is None:
        return
    if not _is_int(value) or not 0 <= value <= 5:
        _diagnostic(diagnostics, f'{action_path}.{key}', f'槽位越界：{value!r} 不在 0–5', wave, turn)
    elif not formation.get(value):
        _diagnostic(diagnostics, f'{action_path}.{key}', f'引用了空槽位：{value} 号位没有从者', wave, turn)


def _check_enemy_target(action: dict[str, Any], key: str, action_path: str, wave: int, turn: int,
                        enemy_count: int | None, diagnostics: list[Diagnostic]) -> None:
    value = action.get(key)
    if value is None:
        return
    if not _is_int(value) or value < 1 or (enemy_count is not None and value > enemy_count):
        limit = f'1–{enemy_count}' if enemy_count is not None else '正整数'
        _diagnostic(diagnostics, f'{action_path}.{key}', f'敌方目标越界：{value!r}，应为 {limit}', wave, turn)


def _validate_secondary(action: dict[str, Any], action_path: str, wave: int, turn: int,
                        diagnostics: list[Diagnostic]) -> None:
    secondary = action.get('secondarySelection')
    if secondary is None:
        return
    if not isinstance(secondary, dict):
        _diagnostic(diagnostics, f'{action_path}.secondarySelection', 'secondarySelection 必须是对象', wave, turn)
    elif secondary.get('type') not in SUPPORTED_SECONDARY_TYPES:
        _diagnostic(diagnostics, f'{action_path}.secondarySelection.type',
                    f'不支持的二次选择类型：{secondary.get("type")!r}', wave, turn)


def _compile_action(action: Any, action_path: str, wave: int, turn_no: int, formation: dict[int, str],
                    enemy_count: int | None, diagnostics: list[Diagnostic]) -> ActionPlan | None:
    if not isinstance(action, dict):
        _diagnostic(diagnostics, action_path, '动作必须是对象', wave, turn_no)
        return None

    kind = action.get('kind')
    if kind not in SUPPORTED_KINDS:
        _diagnostic(diagnostics, f'{action_path}.kind', f'不支持的动作：{kind!r}', wave, turn_no)
    for key in ('title', 'detail', 'icon'):
        if not isinstance(action.get(key), str):
            _diagnostic(diagnostics, f'{action_path}.{key}', f'{key} 必须是字符串', wave, turn_no)
    if 'skill' in action and (not _is_int(action['skill']) or action['skill'] < 0):
        _diagnostic(diagnostics, f'{action_path}.skill', f'skill 必须是非负整数，收到 {action["skill"]!r}',
                    wave, turn_no)

    _validate_secondary(action, action_path, wave, turn_no, diagnostics)
    for key in SLOT_KEYS:
        _check_slot(action, key, action_path, wave, turn_no, formation, diagnostics)
    for key in ('enemyTarget', 'fromTarget', 'toTarget'):
        _check_enemy_target(action, key, action_path, wave, turn_no, enemy_count, diagnostics)

    if kind == 'orderChange':
        swap_out, swap_in = action.get('swapOutSlot'), action.get('swapInSlot')
        if swap_out is None or swap_in is None:
            _diagnostic(diagnostics, f'{action_path}.swapOutSlot', '换人动作必须同时给出 swapOutSlot 与 swapInSlot',
                        wave, turn_no)
        elif swap_out == swap_in:
            _diagnostic(diagnostics, f'{action_path}.swapInSlot', '换人动作的上下场槽位不能相同', wave, turn_no)

    return ActionPlan(
        kind=kind,
        title=action.get('title', ''),
        detail=action.get('detail', ''),
        icon=action.get('icon', ''),
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


def _compile_np_positions(value: Any, turn_path: str, wave: int, turn_no: int,
                          formation: dict[int, str], diagnostics: list[Diagnostic]) -> dict[int, int | str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        _diagnostic(diagnostics, f'{turn_path}.npPositions', 'npPositions 必须是对象', wave, turn_no)
        return {}
    positions: dict[int, int | str] = {}
    for slot_text, position in value.items():
        try:
            slot = int(slot_text)
        except (TypeError, ValueError):
            _diagnostic(diagnostics, f'{turn_path}.npPositions.{slot_text}',
                        f'宝具卡序槽位无效：{slot_text!r}', wave, turn_no)
            continue
        if not 0 <= slot <= 5:
            _diagnostic(diagnostics, f'{turn_path}.npPositions.{slot_text}',
                        f'宝具卡序槽位越界：{slot}', wave, turn_no)
            continue
        if not formation.get(slot):
            _diagnostic(diagnostics, f'{turn_path}.npPositions.{slot_text}',
                        f'宝具卡序引用了空槽位：{slot} 号位没有从者', wave, turn_no)
        if position != 'auto' and (not _is_int(position) or not 1 <= position <= 3):
            _diagnostic(diagnostics, f'{turn_path}.npPositions.{slot_text}',
                        f'宝具卡序必须是 auto 或 1–3，收到 {position!r}', wave, turn_no)
            continue
        positions[slot] = position
    return positions


def _compile_attack(value: Any, turn_path: str, wave: int, turn_no: int, formation: dict[int, str],
                    diagnostics: list[Diagnostic]) -> AttackPlan | None:
    if value is None:
        return None
    path = f'{turn_path}.attack'
    if not isinstance(value, dict):
        _diagnostic(diagnostics, path, 'attack 必须是对象', wave, turn_no)
        return None
    mode = value.get('mode')
    if mode not in ATTACK_MODES:
        _diagnostic(diagnostics, f'{path}.mode', f'不支持的攻击模式：{mode!r}', wave, turn_no)
    cards = value.get('cards', [])
    if not isinstance(cards, list):
        _diagnostic(diagnostics, f'{path}.cards', 'attack.cards 必须是数组', wave, turn_no)
        cards = []
    compiled_cards = []
    for index, card in enumerate(cards):
        card_path = f'{path}.cards[{index}]'
        if not isinstance(card, dict):
            _diagnostic(diagnostics, card_path, '指令卡必须是对象', wave, turn_no)
            continue
        slot = card.get('slot')
        if not _is_int(slot) or not 0 <= slot <= 5:
            _diagnostic(diagnostics, f'{card_path}.slot', f'指令卡槽位越界：{slot!r} 不在 0–5', wave, turn_no)
        elif not formation.get(slot):
            _diagnostic(diagnostics, f'{card_path}.slot', f'指令卡引用了空槽位：{slot} 号位没有从者', wave, turn_no)
        if card.get('type') not in CARD_TYPES:
            _diagnostic(diagnostics, f'{card_path}.type', f'不支持的指令卡颜色：{card.get("type")!r}', wave, turn_no)
        deck = card.get('deckIndex')
        if deck is not None and (not _is_int(deck) or deck < 0):
            _diagnostic(diagnostics, f'{card_path}.deckIndex', f'deckIndex 必须是非负整数，收到 {deck!r}', wave, turn_no)
        compiled_cards.append(AttackCardPlan(slot=slot, type=card.get('type'), deck_index=deck))
    return AttackPlan(mode=mode, cards=tuple(compiled_cards))


def _compile_turn(turn: Any, wave: int, turn_number: int, formation: dict[int, str],
                  enemy_count: int | None, diagnostics: list[Diagnostic]) -> TurnPlan:
    turn_path = f'$.waves[{wave - 1}].turns[{turn_number - 1}]'
    if not isinstance(turn, dict):
        _diagnostic(diagnostics, turn_path, '回合必须是对象', wave, turn_number)
        return TurnPlan()
    actions_value = turn.get('actions', [])
    if not isinstance(actions_value, list):
        _diagnostic(diagnostics, f'{turn_path}.actions', 'actions 必须是数组', wave, turn_number)
        actions_value = []
    actions = []
    for action_index, action in enumerate(actions_value):
        action_path = f'{turn_path}.actions[{action_index}]'
        compiled = _compile_action(action, action_path, wave, turn_number, formation, enemy_count, diagnostics)
        if compiled is not None:
            actions.append(compiled)
    np_positions = _compile_np_positions(turn.get('npPositions'), turn_path, wave, turn_number, formation, diagnostics)
    attack = _compile_attack(turn.get('attack'), turn_path, wave, turn_number, formation, diagnostics)
    return TurnPlan(actions=tuple(actions), np_positions=np_positions, attack=attack)


def _compile_wave(wave: Any, index: int, formation: dict[int, str], enemy_count: int | None,
                  diagnostics: list[Diagnostic]) -> WavePlan:
    wave_number = index + 1
    path = f'$.waves[{index}]'
    if not isinstance(wave, dict):
        _diagnostic(diagnostics, path, '面必须是对象', wave_number)
        return WavePlan(number=wave_number, target=0, turns=())
    turn_count = wave.get('turnCount')
    if not _is_int(turn_count) or turn_count < 1:
        _diagnostic(diagnostics, f'{path}.turnCount', f'turnCount 必须是正整数，收到 {turn_count!r}', wave_number)
    turns_value = wave.get('turns')
    if not isinstance(turns_value, list):
        _diagnostic(diagnostics, f'{path}.turns', 'turns 必须是数组', wave_number)
        turns_value = []
    elif _is_int(turn_count) and turn_count != len(turns_value):
        _diagnostic(diagnostics, f'{path}.turnCount',
                    f'回合数不一致：turnCount={turn_count}，turns 有 {len(turns_value)} 个', wave_number)
    target = wave.get('target')
    if not _is_int(target) or target < 1 or (enemy_count is not None and target > enemy_count):
        limit = f'1–{enemy_count}' if enemy_count is not None else '正整数'
        _diagnostic(diagnostics, f'{path}.target', f'攻击目标越界：{target!r}，应为 {limit}', wave_number)
    turns = tuple(
        _compile_turn(turn, wave_number, turn_index + 1, formation, enemy_count, diagnostics)
        for turn_index, turn in enumerate(turns_value)
    )
    return WavePlan(number=wave_number, target=target if _is_int(target) else 0, turns=turns)


def compile_job(text: str) -> CompileResult:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        return CompileResult(None, [Diagnostic('$', f'不是有效的 JSON：{error}')])

    diagnostics: list[Diagnostic] = []
    if not _validate_top_level(raw, diagnostics):
        return CompileResult(None, diagnostics)

    formation = _formation_slots(raw, diagnostics)
    enemy_count = raw.get('enemyCount') if raw.get('enemyCount') in (3, 6) else None
    waves_value = raw.get('waves') if isinstance(raw.get('waves'), list) else []
    waves = tuple(
        _compile_wave(wave, index, formation, enemy_count, diagnostics)
        for index, wave in enumerate(waves_value)
    )
    plan = ExecutionPlan(
        mystic_id=raw.get('mysticId', ''),
        enemy_count=raw.get('enemyCount', 0),
        waves=waves,
    )
    return CompileResult(None if diagnostics else plan, diagnostics)
