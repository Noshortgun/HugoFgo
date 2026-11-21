from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from executor.core.ap_policy import CONTROL_NAMES, RecoveryItem, select_recovery
from executor.core.model import RunOptions, RunProgress, RunResult, RunStatus
from executor.core.strategy import CardSelection, select_cards
from executor.device.base import Device, DeviceError, Frame
from executor.domain import ActionPlan, ExecutionPlan, FormationSlotPlan, TurnPlan
from executor.perception import BasePage, Control, ControlKind, Observation, Overlay, SupportCandidate, Variant
from executor.session import EventSink, RunControl, RunEvent
from executor.session.evidence import RunEvidence


FLOW_OVERLAYS = frozenset({
    Overlay.FRIEND_REQUEST,
    Overlay.CLASS_INFO,
    Overlay.SUPPORT_PARTY_CONFIRM,
    Overlay.MASTER_EQUIPMENT,
    Overlay.SUPPORT_CONFIRM,
    Overlay.REPEAT_DIALOG,
    Overlay.ORDER_CHANGE,
    Overlay.COMMAND_SPELL_MENU,
    Overlay.COMMAND_SPELL_TARGET,
    Overlay.COMMAND_SPELL_CONFIRM,
})


class FramePerception(Protocol):
    def bind_formation(self, formation) -> None: ...

    def read(self, frame: Frame) -> Observation: ...

    def read_fields(self, frame: Frame, names, *, timeout: float | None = None) -> Observation: ...


@dataclass(frozen=True)
class CardRequest:
    servant_slot: int
    card_type: str | None
    deck_index: int | None
    noble_phantasm: bool


def card_requests(turn: TurnPlan) -> tuple[CardRequest, ...]:
    requests: list[CardRequest | None] = [None, None, None]
    automatic: list[int] = []

    def add_auto(slot: int) -> None:
        if slot not in automatic:
            automatic.append(slot)

    for action in turn.actions:
        if action.kind == 'attack' and action.member_slot is not None:
            add_auto(action.member_slot)
    for slot, position in turn.np_positions.items():
        if position == 'auto':
            add_auto(slot)
            continue
        if not isinstance(position, int) or not 1 <= position <= 3 or requests[position - 1] is not None:
            return ()
        requests[position - 1] = CardRequest(slot, None, None, True)
        if slot in automatic:
            automatic.remove(slot)
    for slot in automatic:
        if None not in requests:
            return ()
        requests[requests.index(None)] = CardRequest(slot, None, None, True)
    if turn.attack is not None:
        for card in turn.attack.cards:
            if None not in requests:
                break
            requests[requests.index(None)] = CardRequest(card.slot, card.type, card.deck_index, False)
    if any(item is None for item in requests):
        return ()
    return tuple(item for item in requests if item is not None)


def scheduled_np_requests(turn: TurnPlan) -> tuple[tuple[int | None, CardRequest], ...]:
    requests: list[CardRequest | None] = [None, None, None]
    automatic: list[int] = []
    for action in turn.actions:
        if action.kind == 'attack' and action.member_slot is not None and action.member_slot not in automatic:
            automatic.append(action.member_slot)
    for slot, position in turn.np_positions.items():
        if position == 'auto':
            if slot not in automatic:
                automatic.append(slot)
        elif isinstance(position, int) and 1 <= position <= 3:
            requests[position - 1] = CardRequest(slot, None, None, True)
            if slot in automatic:
                automatic.remove(slot)
    for slot in automatic:
        if None in requests:
            requests[requests.index(None)] = CardRequest(slot, None, None, True)
    return tuple((index + 1, request) for index, request in enumerate(requests) if request is not None)


def matching_card_control(observation: Observation, wanted: CardRequest,
                          used: set[str]) -> Control | None:
    return next((item for item in observation.controls
                 if item.name not in used and item.actionable and item.card is not None
                 and item.card.servant_slot == wanted.servant_slot
                 and (wanted.card_type is None or item.card.card_type == wanted.card_type)
                 and item.card.deck_index == wanted.deck_index
                 and item.card.noble_phantasm == wanted.noble_phantasm), None)


def complete_card_controls(observation: Observation, requests: tuple[CardRequest, ...]) -> bool:
    used: set[str] = set()
    for wanted in requests:
        control = matching_card_control(observation, wanted, used)
        if control is None:
            return False
        used.add(control.name)
    return True


def support_matches(requirement: FormationSlotPlan, candidate: SupportCandidate) -> bool:
    if requirement.svt_id is not None and candidate.svt_id != requirement.svt_id:
        return False
    if requirement.name and candidate.name != requirement.name:
        return False
    if requirement.role and candidate.role != requirement.role:
        return False
    if requirement.ce_id is not None and candidate.ce_id != requirement.ce_id:
        return False
    if requirement.ce_id is None and requirement.ce and candidate.ce != requirement.ce:
        return False
    if requirement.grand_required and not candidate.grand:
        return False
    for expected, actual in ((requirement.grand_ce, candidate.grand_ce),
                             (requirement.grand_ce_ids, candidate.grand_ce_ids)):
        if expected and any(actual is None or actual.get(key) != value
                            for key, value in expected.items() if value is not None):
            return False
    return True


class StopRequested(Exception):
    pass


class Runner:
    def __init__(self, plan: ExecutionPlan, device: Device, perception: FramePerception,
                 events: EventSink, options: RunOptions, control: RunControl | None = None) -> None:
        self.plan = plan
        self.device = device
        self.perception = perception
        self.events = events
        self.options = options
        self.control = control
        self.progress = RunProgress()
        self.last_page: BasePage | None = None
        self.unknown_since: float | None = None
        self.unknown_retries = 0
        self.loading_since: float | None = None
        self.pending_pages: frozenset[BasePage] | None = None
        self.pending_since: float | None = None
        self.pending_frame_hash: str | None = None
        self.pending_animation_required = False
        self.pending_animation_seen = False
        self.pending_variant: Variant | None = None
        self.pending_overlay: Overlay | None = None
        self.job_target_slot: int | None = None
        self.optional_target = False
        self.master_action: ActionPlan | None = None
        self.order_action: ActionPlan | None = None
        self.order_stage: str | None = None
        self.pending_swap: tuple[int, int] | None = None
        self.spell_action: ActionPlan | None = None
        self.spell_stage: str | None = None
        self.incidental_overlay: Overlay | None = None
        self.incidental_since: float | None = None
        self.incidental_tapped = False
        self.stable_signature: tuple | None = None
        self.stable_since: float | None = None
        self.stable_box: tuple[int, int, int, int] | None = None
        self.card_index = 0
        self.card_selection: CardSelection | None = None
        self.confirming_card = False
        self.active_slots = [0, 1, 2]
        self.confirming_job_action = False
        self.run_settled = False
        self.draining = False
        self.recovery_scrolled = False
        self.recovery_scroll_since: float | None = None
        self.pending_recovery: RecoveryItem | None = None
        self.starting_next_run = False
        self.result_seen = False
        self.awaiting_attack_end = False
        self.fallback_mode = False
        self.startup = True
        self.battle_since: float | None = None
        self.initial_target_pending = bool(plan.waves and plan.waves[0].target != 1)
        self.confirming_initial_target = False
        self.support_stage = 'idle'
        self.support_refreshes = 0
        self.support_scrolls = 0
        self.support_confirm_since: float | None = None
        self.support_search_signature: tuple | None = None
        self.support_search_since: float | None = None
        self.support_previous_rows: tuple | None = None
        self.fallback_signature: tuple | None = None
        self.fallback_since: float | None = None

    def check_stop(self) -> None:
        if self.control is not None and self.control.stop_requested:
            raise StopRequested

    def emit(self, event_type: str, timestamp: float, **data: object) -> None:
        self.events.emit(RunEvent(event_type, timestamp, data))

    def record_observation(self, frame: Frame, observation: Observation) -> None:
        observe = getattr(self.events, 'observe', None)
        if observe is not None:
            observe(frame, observation)

    def result(self, status: RunStatus, reason: str) -> RunResult:
        return RunResult(status, reason, self.progress)

    def reset_stability(self) -> None:
        self.stable_signature = None
        self.stable_since = None
        self.stable_box = None

    def tap_stable(self, observation: Observation, name: str, now: float) -> bool:
        control = observation.control(name)
        if control is None or not control.actionable:
            self.reset_stability()
            return False
        signature = (
            observation.base_page,
            tuple(sorted(item.value for item in observation.variants)),
            tuple(sorted(item.value for item in observation.overlays)),
            control.name,
            control.kind,
            control.card,
            control.support,
        )
        moved = (self.stable_box is None
                 or max(abs(a - b) for a, b in zip(control.box, self.stable_box)) > self.options.coordinate_tolerance)
        if signature != self.stable_signature or moved:
            self.stable_signature = signature
            self.stable_since = now
            self.stable_box = control.box
            return False
        self.stable_box = control.box
        if self.stable_since is None or now - self.stable_since + 1e-9 < self.options.stable_seconds:
            return False
        x, y = control.center
        self.check_stop()
        self.device.tap(x, y)
        self.pending_frame_hash = observation.frame_hash
        self.emit('action', now, control=name, x=x, y=y, reason='stable_actionable',
                  wave=self.progress.wave, turn=self.progress.turn,
                  action_index=self.progress.action_index)
        self.reset_stability()
        return True

    def wait_for_pages(self, now: float, *pages: BasePage) -> None:
        self.pending_pages = frozenset(pages)
        self.pending_since = now
        self.pending_variant = None
        self.pending_overlay = None
        self.pending_animation_required = False
        self.pending_animation_seen = False

    def wait_for_page(self, page: BasePage, now: float) -> None:
        self.wait_for_pages(now, page)

    def wait_for_job_action(self, now: float) -> None:
        self.confirming_job_action = True
        self.wait_for_page(BasePage.BATTLE, now)
        self.pending_animation_required = True

    def fallback_tap(self, observation: Observation, now: float, control: str,
                     x: int, y: int, reason: str) -> bool:
        if observation.overlays:
            self.fallback_signature = None
            self.fallback_since = None
            return False
        signature = (observation.base_page, observation.variants, control)
        if signature != self.fallback_signature:
            self.fallback_signature = signature
            self.fallback_since = now
            return False
        if self.fallback_since is None or now - self.fallback_since + 1e-9 < self.options.stable_seconds:
            return False
        self.check_stop()
        self.device.tap(x, y)
        self.pending_frame_hash = observation.frame_hash
        self.emit('action', now, control=control, x=x, y=y, reason=reason,
                  wave=self.progress.wave, turn=self.progress.turn,
                  action_index=self.progress.action_index)
        self.fallback_signature = None
        self.fallback_since = None
        self.reset_stability()
        return True

    def reset_support_search(self) -> None:
        self.support_search_signature = None
        self.support_search_since = None

    def support_search_stable(self, observation: Observation, rows: tuple, now: float) -> bool:
        signature = (observation.base_page, observation.variants, observation.overlays, rows)
        if signature != self.support_search_signature:
            self.support_search_signature = signature
            self.support_search_since = now
            return False
        return (self.support_search_since is not None
                and now - self.support_search_since + 1e-9 >= self.options.stable_seconds)

    def support_swipe(self, observation: Observation, now: float, *, down: bool) -> None:
        y1, y2 = (620, 260) if down else (260, 620)
        control = '助战列表向下' if down else '助战列表回顶'
        self.check_stop()
        self.device.swipe(640, y1, 640, y2, 200)
        self.pending_frame_hash = observation.frame_hash
        self.emit('action', now, control=control, x=640, y=y1, x2=640, y2=y2,
                  reason='support_search')
        self.support_stage = 'scroll' if down else 'top'
        self.reset_support_search()
        self.wait_for_page(BasePage.SUPPORT, now)

    def handle_support(self, observation: Observation, now: float) -> RunResult | None:
        if self.support_stage == 'refresh_confirm' and Overlay.SUPPORT_CONFIRM in observation.overlays:
            if self.tap_stable(observation, '是', now):
                self.support_refreshes += 1
                self.support_stage = 'refresh_wait'
                self.support_confirm_since = now
                self.reset_support_search()
            return None
        if self.support_stage == 'refresh_wait':
            if Overlay.SUPPORT_CONFIRM in observation.overlays:
                if (self.support_confirm_since is not None
                        and now - self.support_confirm_since >= self.options.action_timeout):
                    self.emit('failed', now, reason='support_refresh_timeout')
                    return self.result(RunStatus.STOPPED, 'support_refresh_timeout')
                return None
            self.support_stage = 'idle'
            self.support_previous_rows = None
            self.reset_support_search()
            self.wait_for_page(BasePage.SUPPORT, now)
        requirement = next((slot for slot in self.plan.formation if slot.support_required), None)
        control = next((item for item in observation.controls
                        if item.support is not None and item.actionable
                        and (requirement is None or support_matches(requirement, item.support))), None)
        if control is not None:
            if self.tap_stable(observation, control.name, now):
                self.support_stage = 'idle'
                self.support_refreshes = 0
                self.support_scrolls = 0
                self.support_previous_rows = None
                self.reset_support_search()
                self.wait_for_page(BasePage.TEAM, now)
            return None
        rows = tuple((item.name, item.box,
                      item.support.svt_id if item.support else None,
                      item.support.ce_id if item.support else None)
                     for item in observation.controls if item.support is not None)
        if self.support_stage == 'idle':
            if self.support_refreshes >= 2:
                self.emit('failed', now, reason='support_not_found', refreshes=self.support_refreshes)
                return self.result(RunStatus.STOPPED, 'support_not_found')
            if self.support_search_stable(observation, rows, now):
                self.support_swipe(observation, now, down=False)
        elif self.support_stage == 'top':
            if self.support_search_stable(observation, rows, now):
                self.support_previous_rows = rows
                self.support_scrolls = 1
                self.support_swipe(observation, now, down=True)
        elif self.support_stage == 'scroll':
            if rows == self.support_previous_rows or not rows or self.support_scrolls >= 10:
                if self.tap_stable(observation, '列表更新', now):
                    self.support_stage = 'refresh_confirm'
                    self.reset_support_search()
                    self.wait_for_overlay(now, Overlay.SUPPORT_CONFIRM, BasePage.SUPPORT)
            elif self.support_search_stable(observation, rows, now):
                self.support_previous_rows = rows
                self.support_scrolls += 1
                self.support_swipe(observation, now, down=True)
        return None

    def handle_team(self, observation: Observation, now: float) -> None:
        if self.tap_stable(observation, '开始任务', now):
            self.wait_for_page(BasePage.BATTLE, now)
        elif self.fallback_tap(observation, now, '开始任务', 1190, 655, 'team_fallback'):
            self.wait_for_page(BasePage.BATTLE, now)

    def wait_for_overlay(self, now: float, overlay: Overlay,
                         page: BasePage = BasePage.BATTLE) -> None:
        self.pending_pages = frozenset({page})
        self.pending_since = now
        self.pending_variant = None
        self.pending_overlay = overlay

    def handle_order_change(self, observation: Observation, now: float) -> RunResult | None:
        action = self.order_action
        if action is None or action.swap_out_slot is None or action.swap_in_slot is None:
            self.emit('failed', now, reason='order_change_invalid')
            return self.result(RunStatus.STOPPED, 'order_change_invalid')
        if self.order_stage == 'master_menu':
            if self.tap_stable(observation, '御主技能3', now):
                self.order_stage = 'swap_out'
                self.wait_for_overlay(now, Overlay.ORDER_CHANGE)
        elif self.order_stage == 'swap_out':
            if action.swap_out_slot not in self.active_slots:
                self.emit('failed', now, reason='order_change_invalid')
                return self.result(RunStatus.STOPPED, 'order_change_invalid')
            position = self.active_slots.index(action.swap_out_slot) + 1
            if self.tap_stable(observation, f'先发第{position}位', now):
                self.order_stage = 'swap_in'
                self.wait_for_overlay(now, Overlay.ORDER_CHANGE)
        elif self.order_stage == 'swap_in':
            position = action.swap_in_slot - 2
            if not 1 <= position <= 3:
                self.emit('failed', now, reason='order_change_invalid')
                return self.result(RunStatus.STOPPED, 'order_change_invalid')
            if self.tap_stable(observation, f'候补第{position}位', now):
                self.order_stage = 'confirm'
                self.wait_for_overlay(now, Overlay.ORDER_CHANGE)
        elif self.order_stage == 'confirm' and self.tap_stable(observation, '进行更替', now):
            self.pending_swap = (action.swap_out_slot, action.swap_in_slot)
            self.order_action = None
            self.order_stage = None
            self.wait_for_job_action(now)
        return None

    def handle_spell(self, observation: Observation, now: float) -> RunResult | None:
        action = self.spell_action
        if action is None:
            self.emit('failed', now, reason='spell_invalid')
            return self.result(RunStatus.STOPPED, 'spell_invalid')
        if self.spell_stage == 'menu':
            name = '灵基修复' if action.skill == 1 else '宝具解放'
            if self.tap_stable(observation, name, now):
                target = action.player_target_slot if action.player_target_slot is not None else action.member_slot
                if action.skill == 1 and target is not None:
                    self.spell_stage = f'target:{target}'
                    self.wait_for_overlay(now, Overlay.COMMAND_SPELL_TARGET)
                else:
                    self.spell_stage = 'confirm'
                    self.wait_for_overlay(now, Overlay.COMMAND_SPELL_CONFIRM)
        elif self.spell_stage is not None and self.spell_stage.startswith('target:'):
            target = int(self.spell_stage.split(':', 1)[1])
            if target not in self.active_slots:
                self.emit('failed', now, reason='target_unavailable', slot=target)
                return self.result(RunStatus.STOPPED, 'target_unavailable')
            position = self.active_slots.index(target) + 1
            if self.tap_stable(observation, f'目标从者{position}', now):
                self.spell_stage = 'confirm'
                self.wait_for_overlay(now, Overlay.COMMAND_SPELL_CONFIRM)
        elif self.spell_stage == 'confirm' and self.tap_stable(observation, '决定', now):
            self.spell_action = None
            self.spell_stage = None
            self.wait_for_job_action(now)
        return None

    def handle_battle(self, observation: Observation, now: float) -> RunResult | None:
        if self.fallback_mode:
            if self.tap_stable(observation, '攻击', now):
                self.wait_for_page(BasePage.CARD, now)
            return None
        wave = self.plan.waves[self.progress.wave - 1]
        if self.initial_target_pending:
            if self.tap_stable(observation, f'敌方{wave.target}', now):
                self.confirming_initial_target = True
                self.wait_for_page(BasePage.BATTLE, now)
            return None
        turn = wave.turns[self.progress.turn - 1]
        if self.progress.action_index < len(turn.actions):
            action = turn.actions[self.progress.action_index]
            if action.kind == 'attack':
                self.progress.action_index += 1
                return self.handle_battle(observation, now)
            if action.kind == 'spell':
                if self.tap_stable(observation, '令咒', now):
                    self.spell_action = action
                    self.spell_stage = 'menu'
                    self.wait_for_overlay(now, Overlay.COMMAND_SPELL_MENU)
                return None
            if action.kind == 'target':
                target = action.enemy_target if action.enemy_target is not None else action.to_target
                if target is None:
                    self.emit('failed', now, reason='target_unavailable')
                    return self.result(RunStatus.STOPPED, 'target_unavailable')
                if self.tap_stable(observation, f'敌方{target}', now):
                    self.confirming_job_action = True
                    self.wait_for_page(BasePage.BATTLE, now)
                return None
            if action.kind == 'orderChange':
                if self.tap_stable(observation, '御主技能', now):
                    self.order_action = action
                    self.order_stage = 'master_menu'
                    self.wait_for_overlay(now, Overlay.MASTER_SKILLS)
                return None
            if action.kind == 'master':
                if self.tap_stable(observation, '御主技能', now):
                    self.master_action = action
                    self.pending_pages = frozenset({BasePage.BATTLE})
                    self.pending_since = now
                    self.pending_variant = None
                    self.pending_overlay = Overlay.MASTER_SKILLS
                return None
            if action.kind != 'skill' or action.member_slot not in self.active_slots:
                self.emit('failed', now, reason='unsupported_action', action_kind=action.kind)
                return self.result(RunStatus.STOPPED, 'unsupported_action')
            position = self.active_slots.index(action.member_slot) + 1
            name = f'从者{position}技能{action.skill + 1}'
            if self.tap_stable(observation, name, now):
                if action.player_target_slot is not None:
                    self.job_target_slot = action.player_target_slot
                    self.optional_target = action.member_slot == action.player_target_slot
                    self.confirming_job_action = self.optional_target
                    self.pending_pages = frozenset({BasePage.BATTLE})
                    self.pending_since = now
                    self.pending_variant = None if self.optional_target else Variant.SKILL_TARGET
                    self.pending_overlay = None
                    self.pending_animation_required = self.optional_target
                    self.pending_animation_seen = False
                else:
                    self.wait_for_job_action(now)
            return None
        if self.tap_stable(observation, '攻击', now):
            self.wait_for_page(BasePage.CARD, now)
        return None

    def handle_card(self, observation: Observation, now: float) -> RunResult | None:
        turn = (None if self.fallback_mode else
                self.plan.waves[self.progress.wave - 1].turns[self.progress.turn - 1])
        use_strategy = self.fallback_mode or self.card_selection is not None or turn.attack is None or (
            turn.attack is not None and turn.attack.mode == 'strategy')
        if (turn is not None and turn.attack is not None and turn.attack.mode == 'prefer'
                and self.card_index == 0):
            preferred = card_requests(turn)
            use_strategy = len(preferred) != 3 or not complete_card_controls(observation, preferred)
        if use_strategy:
            if self.card_selection is None:
                reserved: list[tuple[int | None, Control]] = []
                if turn is not None:
                    for position, wanted in scheduled_np_requests(turn):
                        reserved_control = matching_card_control(observation, wanted, set())
                        if reserved_control is None:
                            self.emit('failed', now, reason='fallback_cards_unavailable',
                                      card_position=position, servant_slot=wanted.servant_slot,
                                      message='作业宝具卡未在当前手牌中确认')
                            return self.result(RunStatus.STOPPED, 'fallback_cards_unavailable')
                        reserved.append((position, reserved_control))
                self.card_selection = select_cards(observation, self.plan.strategy,
                                                   self.plan, self.progress.wave, tuple(reserved))
                for warning in self.card_selection.warnings:
                    self.emit('warning', now, reason='strategy_servant_slots', message=warning)
            selection = self.card_selection
            if selection.diagnostic is not None or len(selection.controls) != 3:
                self.emit('failed', now, reason='fallback_cards_unavailable',
                          message=selection.diagnostic or '补刀策略未选出三张卡',
                          strategy_source=selection.source)
                return self.result(RunStatus.STOPPED, 'fallback_cards_unavailable')
            wanted_control = selection.controls[self.card_index]
            control = observation.control(wanted_control.name)
            reason = 'fallback_cards_unavailable'
            if control is None or not control.actionable or control.card != wanted_control.card:
                self.emit('failed', now, reason=reason, card_position=self.card_index + 1,
                          message='已选指令卡在当前画面不可确认')
                return self.result(RunStatus.STOPPED, reason)
        else:
            requests = card_requests(turn)
            if len(requests) != 3:
                self.emit('failed', now, reason='card_mismatch', message='作业没有提供可执行的三张指令卡')
                return self.result(RunStatus.STOPPED, 'card_mismatch')
            reason = ('fallback_required' if turn.attack is not None and turn.attack.mode == 'prefer'
                      else 'card_mismatch')
            if self.card_index == 0 and not complete_card_controls(observation, requests):
                self.emit('failed', now, reason=reason, card_position=1)
                return self.result(RunStatus.STOPPED, reason)
            wanted = requests[self.card_index]
            control = matching_card_control(observation, wanted, set())
            if control is None:
                self.emit('failed', now, reason=reason, card_position=self.card_index + 1,
                          servant_slot=wanted.servant_slot, card_type=wanted.card_type,
                          deck_index=wanted.deck_index, noble_phantasm=wanted.noble_phantasm)
                return self.result(RunStatus.STOPPED, reason)
        if not self.tap_stable(observation, control.name, now):
            return None
        if self.card_index == 2:
            self.card_index = 0
            self.card_selection = None
            self.awaiting_attack_end = True
            self.wait_for_pages(now, BasePage.BATTLE, BasePage.RESULT, BasePage.DROPS)
        else:
            self.confirming_card = True
            self.wait_for_page(BasePage.CARD, now)
        return None

    def handle_result(self, observation: Observation, now: float) -> None:
        if self.tap_stable(observation, '结算推进', now):
            self.wait_for_pages(now, BasePage.RESULT, BasePage.DROPS)
        elif self.fallback_tap(observation, now, '结算推进', 1130, 660, 'result_fallback'):
            self.wait_for_pages(now, BasePage.RESULT, BasePage.DROPS)

    def scroll_recovery(self, observation: Observation, now: float) -> bool:
        if self.recovery_scroll_since is None:
            self.recovery_scroll_since = now
            return False
        if now - self.recovery_scroll_since + 1e-9 < self.options.stable_seconds:
            return False
        self.check_stop()
        self.device.swipe(640, 560, 640, 260, 200)
        self.emit('action', now, control='AP 恢复列表向下', x=640, y=560, x2=640, y2=260,
                  reason='recovery_scroll')
        self.recovery_scrolled = True
        self.recovery_scroll_since = None
        self.reset_stability()
        return True

    def handle_ap_recovery(self, observation: Observation, now: float) -> RunResult | None:
        if self.pending_recovery is not None:
            if self.pending_since is not None and now - self.pending_since >= self.options.action_timeout:
                self.emit('failed', now, reason='action_confirmation_timeout', actual=observation.base_page.value)
                return self.result(RunStatus.STOPPED, 'action_confirmation_timeout')
            return None
        available = tuple(control.name for control in observation.controls
                          if control.kind is ControlKind.ITEM and control.actionable)
        item = select_recovery(self.options.recovery, available)
        if item is None:
            offscreen = {RecoveryItem.BRONZE, RecoveryItem.COPPER}
            if (available and not self.recovery_scrolled
                    and self.options.recovery.allowed & offscreen):
                self.scroll_recovery(observation, now)
                return None
            self.emit('failed', now, reason='ap_recovery_no_allowed_item', available=available)
            return self.result(RunStatus.STOPPED, 'ap_recovery')
        if self.tap_stable(observation, CONTROL_NAMES[item], now):
            self.pending_recovery = item
            self.wait_for_pages(now, BasePage.SUPPORT, BasePage.TEAM, BasePage.DROPS, BasePage.BATTLE)
        return None

    def handle_drops(self, observation: Observation, now: float) -> RunResult | None:
        if Overlay.REPEAT_DIALOG in observation.overlays:
            if not self.run_settled:
                self.progress.completed_runs += 1
                self.run_settled = True
            if not self.draining and self.progress.completed_runs >= self.options.run_count:
                if not self.options.recovery.drain_after_turns:
                    self.emit('finished', now, completed_runs=self.progress.completed_runs)
                    return self.result(RunStatus.COMPLETED, 'completed')
                self.draining = True
                self.emit('drain_started', now, completed_runs=self.progress.completed_runs)
            if self.tap_stable(observation, '连续出击', now):
                self.starting_next_run = True
                self.wait_for_pages(now, BasePage.TEAM, BasePage.SUPPORT)
            return None
        if self.tap_stable(observation, '下一步', now):
            self.wait_for_page(BasePage.DROPS, now)
        elif self.fallback_tap(observation, now, '下一步', 1130, 660, 'drops_fallback'):
            self.wait_for_page(BasePage.DROPS, now)
        return None

    def incidental_control(self, observation: Observation) -> tuple[Overlay, str] | None:
        for overlay, name in (
            (Overlay.FRIEND_REQUEST, '结束'),
            (Overlay.CLASS_INFO, '关闭'),
            (Overlay.SUPPORT_PARTY_CONFIRM, '关闭'),
            (Overlay.MASTER_EQUIPMENT, '关闭'),
        ):
            if overlay in observation.overlays:
                return overlay, name
        return None

    def resume(self, observation: Observation, frame: Frame,
               now: float) -> tuple[Observation, RunResult | None]:
        page = observation.base_page
        if page is BasePage.CARD:
            self.emit('failed', now, reason='resume_uncertain', actual=page.value)
            return observation, self.result(RunStatus.STOPPED, 'resume_uncertain')
        if page is BasePage.BATTLE:
            observation = self.perception.read_fields(frame, ('wave', 'turn'), timeout=self.options.page_timeout)
            wave = observation.field('wave')
            turn = observation.field('turn')
            boundary = observation.field('turn_boundary')
            valid = (wave is not None and turn is not None and boundary is not None
                     and wave.usable and turn.usable and boundary.usable and boundary.value is True
                     and isinstance(wave.value, int) and isinstance(turn.value, int)
                     and 1 <= wave.value <= len(self.plan.waves)
                     and 1 <= turn.value <= len(self.plan.waves[wave.value - 1].turns))
            if not valid:
                self.emit('failed', now, reason='resume_uncertain', actual=page.value)
                return observation, self.result(RunStatus.STOPPED, 'resume_uncertain')
            self.progress.wave = wave.value
            self.progress.turn = turn.value
            self.progress.action_index = 0
            self.initial_target_pending = self.plan.waves[wave.value - 1].target != 1
        elif page not in {BasePage.SUPPORT, BasePage.TEAM, BasePage.RESULT, BasePage.DROPS}:
            self.emit('failed', now, reason='unsupported_start', actual=page.value)
            return observation, self.result(RunStatus.STOPPED, 'unsupported_start')
        self.startup = False
        return observation, None

    def unsupported_job(self) -> tuple[int, int, str] | None:
        for wave in self.plan.waves:
            for turn_number, turn in enumerate(wave.turns, start=1):
                if any(action.secondary_selection for action in turn.actions):
                    return wave.number, turn_number, '二次选择的画面证据尚未接入执行器'
                if turn.attack is None or turn.attack.mode != 'verify':
                    continue
                if len(card_requests(turn)) != 3:
                    return wave.number, turn_number, '作业没有提供无需补刀策略的三张卡'
        return None

    def run(self) -> RunResult:
        unsupported = self.unsupported_job()
        if unsupported is not None:
            wave, turn, message = unsupported
            self.emit('failed', time.time(), reason='unsupported_job', wave=wave, turn=turn, message=message)
            return self.result(RunStatus.FAILED, 'unsupported_job')
        try:
            return self.run_loop()
        except StopRequested:
            self.emit('stopped', time.time(), reason='manual_stop')
            return self.result(RunStatus.STOPPED, 'manual_stop')
        except DeviceError as error:
            self.emit('failed', time.time(), reason='device_error', message=str(error))
            return self.result(RunStatus.FAILED, 'device_error')

    def run_loop(self) -> RunResult:
        while True:
            self.check_stop()
            try:
                frame = self.device.frame()
                observation = self.perception.read(frame)
                self.record_observation(frame, observation)
            except DeviceError as error:
                self.emit('failed', time.time(), reason='device_error', message=str(error))
                return self.result(RunStatus.FAILED, 'device_error')
            now = frame.captured_at
            page = observation.base_page
            if page is not BasePage.AP_RECOVERY:
                self.recovery_scrolled = False
                self.recovery_scroll_since = None
            if page in {BasePage.RESULT, BasePage.DROPS}:
                self.result_seen = True
            if page is BasePage.BATTLE and self.battle_since is None:
                self.battle_since = now
            elif page in {BasePage.RESULT, BasePage.DROPS}:
                self.battle_since = None
            if self.battle_since is not None and now - self.battle_since >= self.options.battle_timeout:
                self.emit('failed', now, reason='battle_timeout')
                return self.result(RunStatus.STOPPED, 'battle_timeout')
            if page != self.last_page:
                self.emit('state_changed', now, state=page.value, wave=self.progress.wave,
                          turn=self.progress.turn, action_index=self.progress.action_index)
                self.last_page = page
                self.reset_stability()

            if page is BasePage.UNKNOWN:
                self.unknown_retries += 1
                if self.unknown_since is None:
                    self.unknown_since = now
                    self.emit('warning', now, reason='unknown_waiting', retries=self.unknown_retries)
                if now - self.unknown_since >= self.options.page_timeout:
                    self.emit('failed', now, reason='unknown_timeout', retries=self.unknown_retries)
                    return self.result(RunStatus.STOPPED, 'unknown_timeout')
                time.sleep(self.options.poll_interval)
                continue
            self.unknown_since = None

            if page is BasePage.LOADING:
                if self.loading_since is None:
                    self.loading_since = now
                    self.emit('warning', now, reason='loading_waiting')
                if now - self.loading_since >= self.options.page_timeout:
                    self.emit('failed', now, reason='loading_timeout')
                    return self.result(RunStatus.STOPPED, 'loading_timeout')
                time.sleep(self.options.poll_interval)
                continue
            self.loading_since = None

            if page is BasePage.AP_RECOVERY:
                if self.draining:
                    self.emit('finished', now, completed_runs=self.progress.completed_runs,
                              reason='ap_drained', recovery_used=dict(self.progress.recovery_used))
                    return self.result(RunStatus.COMPLETED, 'ap_drained')
                result = self.handle_ap_recovery(observation, now)
                if result is not None:
                    return result
                time.sleep(self.options.poll_interval)
                continue

            if (self.pending_recovery is not None and self.pending_pages is not None
                    and page in self.pending_pages and observation.frame_hash != self.pending_frame_hash):
                item = self.pending_recovery
                self.pending_recovery = None
                count = self.progress.recovery_used.get(item.value, 0) + 1
                self.progress.recovery_used[item.value] = count
                self.emit('recovery_used', now, item=item.value, count=count)
                if Overlay.REPEAT_DIALOG in observation.overlays:
                    self.pending_pages = None
                    self.pending_since = None
                    self.pending_frame_hash = None
                    self.starting_next_run = False

            if self.startup:
                observation, result = self.resume(observation, frame, now)
                self.record_observation(frame, observation)
                if result is not None:
                    return result

            unexpected = observation.overlays - FLOW_OVERLAYS
            if (Overlay.REPEAT_DIALOG in observation.overlays
                    and (not self.result_seen or page not in {BasePage.BATTLE, BasePage.DROPS, BasePage.UNKNOWN})):
                unexpected = unexpected | {Overlay.REPEAT_DIALOG}
            if Overlay.MASTER_SKILLS in unexpected and (self.master_action or self.order_action):
                unexpected = unexpected - {Overlay.MASTER_SKILLS}
            if Overlay.ORDER_CHANGE in observation.overlays and self.order_action is None:
                unexpected = unexpected | {Overlay.ORDER_CHANGE}
            if (observation.overlays & {Overlay.COMMAND_SPELL_MENU, Overlay.COMMAND_SPELL_TARGET,
                                        Overlay.COMMAND_SPELL_CONFIRM}) and self.spell_action is None:
                unexpected = unexpected | (observation.overlays & {Overlay.COMMAND_SPELL_MENU,
                                                                   Overlay.COMMAND_SPELL_TARGET,
                                                                   Overlay.COMMAND_SPELL_CONFIRM})
            if (Overlay.SUPPORT_CONFIRM in observation.overlays
                    and self.support_stage not in {'refresh_confirm', 'refresh_wait'}):
                unexpected = unexpected | {Overlay.SUPPORT_CONFIRM}
            if unexpected:
                self.emit('failed', now, reason='unexpected_overlay',
                          overlays=sorted(item.value for item in unexpected))
                return self.result(RunStatus.STOPPED, 'unexpected_overlay')

            incidental = self.incidental_control(observation)
            if incidental is not None:
                overlay, control = incidental
                if self.incidental_overlay is not overlay:
                    self.incidental_overlay = overlay
                    self.incidental_since = now
                    self.incidental_tapped = False
                if (self.incidental_since is not None
                        and now - self.incidental_since >= self.options.action_timeout):
                    reason = 'overlay_confirmation_timeout' if self.incidental_tapped else 'overlay_recovery_unavailable'
                    self.emit('failed', now, reason=reason, overlay=overlay.value)
                    return self.result(RunStatus.STOPPED, reason)
                if not self.incidental_tapped and self.tap_stable(observation, control, now):
                    self.incidental_tapped = True
                time.sleep(self.options.poll_interval)
                continue
            self.incidental_overlay = None
            self.incidental_since = None
            self.incidental_tapped = False

            if Overlay.REPEAT_DIALOG in observation.overlays:
                if not self.starting_next_run:
                    result = self.handle_drops(observation, now)
                    if result is not None:
                        return result
                time.sleep(self.options.poll_interval)
                continue

            if self.pending_pages is not None:
                if (self.awaiting_attack_end and page is BasePage.BATTLE
                        and Variant.BATTLE_ANIMATION not in observation.variants):
                    observation = self.perception.read_fields(
                        frame, ('wave', 'turn'), timeout=self.options.page_timeout)
                    self.record_observation(frame, observation)
                if self.pending_animation_required and Variant.BATTLE_ANIMATION in observation.variants:
                    self.pending_animation_seen = True
                confirmed = (page in self.pending_pages
                             and observation.frame_hash != self.pending_frame_hash
                             and (not self.pending_animation_required or self.pending_animation_seen
                                  or (self.optional_target and Variant.SKILL_TARGET in observation.variants))
                             and (self.pending_variant is None or self.pending_variant in observation.variants)
                             and (self.pending_overlay is None or self.pending_overlay in observation.overlays)
                             and not (page is BasePage.BATTLE
                                      and Variant.BATTLE_ANIMATION in observation.variants))
                if confirmed and self.awaiting_attack_end and page is BasePage.BATTLE:
                    wave_field = observation.field('wave')
                    turn_field = observation.field('turn')
                    if wave_field is None or turn_field is None or not wave_field.usable or not turn_field.usable:
                        confirmed = False
                    else:
                        wave = wave_field.value
                        turn = turn_field.value
                        current = (self.progress.wave, self.progress.turn)
                        if not isinstance(wave, int) or not isinstance(turn, int) or (wave, turn) <= current:
                            confirmed = False
                        elif wave == current[0] and turn == current[1] + 1:
                            self.fallback_mode = turn > len(self.plan.waves[wave - 1].turns)
                            self.progress.turn = turn
                            self.progress.action_index = 0
                            self.emit('turn_started', now, wave=wave, turn=turn)
                        elif wave == current[0] + 1 and turn == 1 and wave <= len(self.plan.waves):
                            self.progress.wave = wave
                            self.progress.turn = 1
                            self.progress.action_index = 0
                            self.fallback_mode = False
                            self.initial_target_pending = self.plan.waves[wave - 1].target != 1
                            self.emit('wave_started', now, wave=wave, turn=1)
                            self.emit('turn_started', now, wave=wave, turn=1)
                        else:
                            self.emit('failed', now, reason='progress_uncertain',
                                      observed_wave=wave, observed_turn=turn)
                            return self.result(RunStatus.STOPPED, 'progress_uncertain')
                if confirmed:
                    self.pending_pages = None
                    self.pending_since = None
                    self.pending_frame_hash = None
                    self.pending_animation_required = False
                    self.pending_animation_seen = False
                    self.pending_variant = None
                    self.pending_overlay = None
                    self.awaiting_attack_end = False
                    if self.starting_next_run:
                        self.progress.wave = 1
                        self.progress.turn = 1
                        self.progress.action_index = 0
                        self.fallback_mode = False
                        self.active_slots = [0, 1, 2]
                        self.initial_target_pending = self.plan.waves[0].target != 1
                        self.run_settled = False
                        self.starting_next_run = False
                        self.result_seen = False
                    if self.confirming_card:
                        self.card_index += 1
                        self.confirming_card = False
                    if self.confirming_initial_target:
                        self.initial_target_pending = False
                        self.confirming_initial_target = False
                    if self.job_target_slot is not None and self.optional_target:
                        if Variant.SKILL_TARGET in observation.variants:
                            self.confirming_job_action = False
                        else:
                            self.job_target_slot = None
                        self.optional_target = False
                    if self.pending_swap is not None:
                        swap_out, swap_in = self.pending_swap
                        self.active_slots[self.active_slots.index(swap_out)] = swap_in
                        self.pending_swap = None
                    if self.confirming_job_action:
                        self.progress.action_index += 1
                        self.confirming_job_action = False
                elif self.pending_since is not None and now - self.pending_since >= self.options.action_timeout:
                    self.emit('failed', now, reason='action_confirmation_timeout',
                              expected=sorted(item.value for item in self.pending_pages), actual=page.value)
                    return self.result(RunStatus.STOPPED, 'action_confirmation_timeout')
                else:
                    time.sleep(self.options.poll_interval)
                    continue

            if page is BasePage.SUPPORT:
                result = self.handle_support(observation, now)
                if result is not None:
                    return result
            elif page is BasePage.TEAM:
                self.handle_team(observation, now)
            elif self.spell_action is not None:
                result = self.handle_spell(observation, now)
                if result is not None:
                    return result
            elif self.order_action is not None:
                result = self.handle_order_change(observation, now)
                if result is not None:
                    return result
            elif self.master_action is not None:
                action = self.master_action
                if self.tap_stable(observation, f'御主技能{action.skill + 1}', now):
                    self.master_action = None
                    if action.player_target_slot is not None:
                        self.job_target_slot = action.player_target_slot
                        self.pending_pages = frozenset({BasePage.BATTLE})
                        self.pending_since = now
                        self.pending_variant = Variant.SKILL_TARGET
                        self.pending_overlay = None
                    else:
                        self.wait_for_job_action(now)
            elif self.job_target_slot is not None:
                if self.job_target_slot not in self.active_slots:
                    self.emit('failed', now, reason='target_unavailable', slot=self.job_target_slot)
                    return self.result(RunStatus.STOPPED, 'target_unavailable')
                position = self.active_slots.index(self.job_target_slot) + 1
                if self.tap_stable(observation, f'目标从者{position}', now):
                    self.job_target_slot = None
                    self.wait_for_job_action(now)
            elif page is BasePage.BATTLE:
                result = self.handle_battle(observation, now)
                if result is not None:
                    return result
            elif page is BasePage.CARD:
                result = self.handle_card(observation, now)
                if result is not None:
                    return result
            elif page is BasePage.RESULT:
                self.handle_result(observation, now)
            elif page is BasePage.DROPS:
                result = self.handle_drops(observation, now)
                if result is not None:
                    return result
            time.sleep(self.options.poll_interval)


def run(plan: ExecutionPlan, device: Device, perception: FramePerception, events: EventSink,
        *, options: RunOptions | None = None, control: RunControl | None = None) -> RunResult:
    run_options = options or RunOptions()
    run_dir = run_options.run_dir
    if run_options.write_evidence and run_dir is None:
        run_dir = str(Path('logs/farm') / datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    evidence = RunEvidence(run_dir, plan, device, events) if run_options.write_evidence else None
    try:
        bind_formation = getattr(perception, 'bind_formation', None)
        if bind_formation is not None:
            bind_formation(plan.formation)
        result = Runner(plan, device, perception, evidence or events, run_options, control).run()
        return evidence.finish(result) if evidence is not None else result
    finally:
        if evidence is not None:
            evidence.close()
