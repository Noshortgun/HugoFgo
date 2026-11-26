from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Protocol

from executor.core.ap_policy import CONTROL_NAMES, RecoveryItem, select_recovery
from executor.core.model import RunOptions, RunProgress, RunResult, RunStatus
from executor.core.strategy import select_cards, strategy_servant_ranks
from executor.device.base import Device, DeviceError, Frame
from executor.domain import ActionPlan, ExecutionPlan, FormationSlotPlan, TurnPlan
from executor.perception import (BasePage, CommandCardIdentity, Control, ControlKind, Observation, Overlay,
                                 SupportCandidate, Variant)
from executor.session import EventSink, RunControl, RunEvent
from executor.session.evidence import RunEvidence


SUPPORT_REFRESH_COOLDOWN_SECONDS = 5.0
SUPPORT_ROW_HASH_MAX_DISTANCE = 8
NP_CARD_CENTERS = (420, 635, 855)
NP_CARD_CENTER_TOLERANCE = 100
BATTLE_SKIP_POINT = (1240, 420)
ACTION_RECOVERY_SECONDS = 2.0
ATTACK_END_TIMEOUT_SECONDS = 90.0
JOB_ACTION_INTERVAL_SECONDS = 1.0
COMMAND_CARD_INTERVAL_SECONDS = 0.35
SUPPORT_CLASS_TAB_POINTS = {
    'saber': (161, 128), 'archer': (227, 128), 'lancer': (295, 128),
    'rider': (362, 128), 'caster': (430, 128), 'assassin': (497, 128), 'berserker': (565, 128),
    'shielder': (632, 128), 'ruler': (632, 128), 'avenger': (632, 128),
    'alterego': (632, 128), 'mooncancer': (632, 128), 'foreigner': (632, 128),
    'pretender': (632, 128), 'beast': (632, 128),
}

FLOW_OVERLAYS = frozenset({
    Overlay.FRIEND_REQUEST,
    Overlay.CLASS_INFO,
    Overlay.BATTLE_STATUS_CHANGE_DETAIL,
    Overlay.SUPPORT_PARTY_CONFIRM,
    Overlay.MASTER_EQUIPMENT,
    Overlay.SUPPORT_CONFIRM,
    Overlay.SUPPORT_REFRESH_COOLDOWN,
    Overlay.REPEAT_DIALOG,
    Overlay.AP_RECOVERY_CONFIRM,
    Overlay.ORDER_CHANGE,
    Overlay.COMMAND_SPELL_MENU,
    Overlay.COMMAND_SPELL_TARGET,
    Overlay.COMMAND_SPELL_CONFIRM,
})


def support_rows_same(left: tuple, right: tuple) -> bool:
    if len(left) != len(right):
        return False
    for previous, current in zip(left, right):
        if previous[:2] != current[:2]:
            return False
        previous_hash, current_hash = previous[4], current[4]
        if previous_hash == current_hash:
            continue
        if (not previous_hash or not current_hash or len(previous_hash) != len(current_hash)
                or sum(a != b for a, b in zip(previous_hash, current_hash)) > SUPPORT_ROW_HASH_MAX_DISTANCE):
            return False
    return True


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


def positioned_np_cards(observation: Observation, active_slots: list[int],
                        formation: tuple[FormationSlotPlan, ...]) -> tuple[Observation, str | None]:
    """宝具区域中心 x=420/635/855 固定对应左/中/右三位，与可见宝具数量无关。"""
    members = {member.slot: member for member in formation}
    controls = []
    seen: dict[int, str] = {}
    for control in observation.controls:
        is_np_region = (control.kind is ControlKind.CARD and control.card is None
                        and control.name.startswith('宝具卡') and control.reason.startswith('np_slot:'))
        if not is_np_region:
            controls.append(control)
            continue
        x, y = control.center
        positions = [index for index, expected_x in enumerate(NP_CARD_CENTERS)
                     if abs(x - expected_x) <= NP_CARD_CENTER_TOLERANCE and 40 <= y <= 400]
        if len(positions) != 1:
            return observation, f'宝具卡区域 {control.name} 不在已知三个固定位置，无法唯一确认从者'
        index = positions[0]
        if index >= len(active_slots):
            return observation, f'宝具卡区域 {control.name} 超出编队位置，无法按固定位置确认从者'
        if index in seen:
            return observation, (f'宝具卡区域重复（{seen[index]}、{control.name}），'
                                 '无法按固定位置唯一确认从者')
        seen[index] = control.name
        member = members.get(active_slots[index])
        if member is None:
            return observation, f'宝具卡区域 {control.name} 没有对应编队从者，无法按固定位置确认身份'
        controls.append(replace(control, card=CommandCardIdentity(
            servant_slot=member.slot, card_type='unknown', noble_phantasm=True,
            svt_id=member.svt_id, support=member.support)))
    return replace(observation, controls=tuple(controls)), None


def requested_card_controls(observation: Observation,
                            requests: tuple[CardRequest, ...]) -> tuple[Control, ...] | None:
    used: set[str] = set()
    controls = []
    for wanted in requests:
        control = matching_card_control(observation, wanted, used)
        if control is None:
            return None
        used.add(control.name)
        controls.append(control)
    return tuple(controls)


def complete_card_controls(observation: Observation, requests: tuple[CardRequest, ...]) -> bool:
    return requested_card_controls(observation, requests) is not None


def complete_normal_hand(observation: Observation) -> bool:
    expected = {f'卡{index}' for index in range(1, 6)}
    controls = [control for control in observation.controls
                if control.kind is ControlKind.CARD and control.name in expected]
    return ({control.name for control in controls} == expected and len(controls) == 5
            and all(control.actionable and control.card is not None
                    and not control.card.noble_phantasm
                    and control.card.card_type in {'buster', 'arts', 'quick'}
                    for control in controls))


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
        self.last_observed_turn = 1
        self.last_page: BasePage | None = None
        self.unknown_since: float | None = None
        self.unknown_retries = 0
        self.loading_since: float | None = None
        self.pending_pages: frozenset[BasePage] | None = None
        self.pending_since: float | None = None
        self.pending_frame_hash: str | None = None
        self.pending_stage = 'page'
        self.pending_skill_skip = False
        self.pending_skill_recovery = False
        self.pending_skill_ready_seen = False
        self.skill_animation_skipped = False
        self.pending_recovery_control: str | None = None
        self.pending_recovery_used = False
        self.pending_variant: Variant | None = None
        self.pending_overlay: Overlay | None = None
        self.job_target_slot: int | None = None
        self.target_selection_since: float | None = None
        self.optional_target = False
        self.master_action: ActionPlan | None = None
        self.pending_master_action = False
        self.order_action: ActionPlan | None = None
        self.pending_order_action = False
        self.order_stage: str | None = None
        self.pending_swap: tuple[int, int] | None = None
        self.pending_frontline: tuple[int, ...] | None = None
        self.spell_action: ActionPlan | None = None
        self.pending_spell_action = False
        self.spell_stage: str | None = None
        self.incidental_overlay: Overlay | None = None
        self.incidental_since: float | None = None
        self.incidental_tapped = False
        self.stable_signature: tuple | None = None
        self.stable_since: float | None = None
        self.stable_box: tuple[int, int, int, int] | None = None
        self.card_batch_sequence = 0
        self.card_batch_controls: tuple[Control, ...] = ()
        self.card_recovery_used = False
        self.card_recovery_reading = False
        self.attack_submitted_at: float | None = None
        self.last_input_finished_at: float | None = None
        self.last_job_action_finished_at: float | None = None
        self.np_reservation_since: float | None = None
        self.np_slots_previous: frozenset[str] | None = None
        self.card_confirmation_since: float | None = None
        self.card_selection_since: float | None = None
        self.card_readings: list[tuple[tuple[str, int, str], ...]] = []
        self.active_slots = [0, 1, 2]
        self.reserve_slots = [member.slot for member in plan.formation
                              if member.slot not in self.active_slots]
        specified = {member.slot for member in plan.formation if member.name.strip()}
        self.priority_anchors: list[int | None] = [slot if slot in specified else None
                                                   for slot in self.active_slots]
        self.confirming_job_action = False
        self.run_settled = False
        self.draining = False
        self.recovery_scrolled = False
        self.recovery_scroll_since: float | None = None
        self.pending_recovery: RecoveryItem | None = None
        self.recovery_confirmation_tapped = False
        self.starting_next_run = False
        self.result_seen = False
        self.awaiting_attack_end = False
        self.last_attack_skip_at: float | None = None
        self.fallback_mode = False
        self.startup = True
        self.battle_since: float | None = None
        self.team_since: float | None = None
        self.formation_checked = False
        self.support_stage = 'idle'
        self.support_class_selected = False
        self.support_class_filter_pending = False
        self.support_refreshes = 0
        self.support_scrolls = 0
        self.support_confirm_since: float | None = None
        self.support_refresh_cooldown_until: float | None = None
        self.support_search_signature: tuple | None = None
        self.support_search_rows: tuple | None = None
        self.support_search_since: float | None = None
        self.support_previous_rows: tuple | None = None
        self.fallback_signature: tuple | None = None
        self.fallback_since: float | None = None
        self.skill_confirmation: tuple[str, int, int] | None = None
        self.skill_cooldown_since: float | None = None
        self.skill_available_since: float | None = None

    def skill_retry_allowed(self, observation: Observation, now: float) -> bool:
        if self.skill_confirmation is None:
            return True
        control = observation.control(self.skill_confirmation[0])
        if control is None or control.on_cooldown is not False:
            self.skill_available_since = None
            return False
        if self.skill_available_since is None:
            self.skill_available_since = now
            return False
        return now - self.skill_available_since >= 0.5

    def skill_effect_confirmed(self, observation: Observation) -> bool:
        if self.skill_confirmation is None:
            return True
        name, member_slot, position = self.skill_confirmation
        control = observation.control(name)
        if control is None or control.on_cooldown is None:
            return True
        if control.on_cooldown:
            return True
        frontline = observation.field('battle_servants')
        if frontline is not None and frontline.usable and isinstance(frontline.value, tuple) \
                and len(frontline.value) == 3:
            observed = frontline.value[position - 1]
            if isinstance(observed, int) and observed >= 0 and observed != member_slot:
                return True
        return False

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
        stats = getattr(self.perception, 'stats', None)
        return RunResult(status, reason, self.progress, perception_stats=stats() if stats is not None else None)

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

    def tap_action(self, observation: Observation, name: str, now: float) -> bool:
        control = observation.control(name)
        if control is None or not control.actionable:
            return False
        x, y = control.center
        if self.last_job_action_finished_at is not None:
            remaining = JOB_ACTION_INTERVAL_SECONDS - (time.time() - self.last_job_action_finished_at)
            if remaining > 0:
                time.sleep(remaining)
        self.last_job_action_finished_at = self.tap_point(
            observation, now, name, x, y, 'battle_action')
        self.reset_stability()
        return True

    def tap_point(self, observation: Observation, now: float, control: str,
                  x: int, y: int, reason: str, **data: object) -> float:
        self.check_stop()
        started = time.perf_counter()
        self.device.tap(x, y)
        input_finished_at = time.time()
        channel_ms = (time.perf_counter() - started) * 1000
        self.pending_frame_hash = observation.frame_hash
        self.last_input_finished_at = input_finished_at
        self.emit('action', now, control=control, x=x, y=y, reason=reason,
                  wave=self.progress.wave, turn=self.progress.turn,
                  action_index=self.progress.action_index, wait_ms=0.0, channel_ms=channel_ms,
                  input_finished_at=input_finished_at, **data)
        return input_finished_at

    def battle_controls_ready(self, observation: Observation) -> bool:
        attack = observation.control('攻击')
        menu = observation.control('战斗菜单')
        return (observation.base_page is BasePage.BATTLE
                and not observation.overlays
                and attack is not None and attack.actionable
                and menu is not None and menu.actionable)

    def battle_ready(self, observation: Observation) -> bool:
        return (self.battle_controls_ready(observation)
                and Variant.BATTLE_ANIMATION not in observation.variants)

    def wait_for_pages(self, now: float, *pages: BasePage) -> None:
        self.pending_pages = frozenset(pages)
        self.pending_since = now
        self.pending_variant = None
        self.pending_overlay = None
        self.pending_stage = 'page'
        self.pending_skill_skip = False
        self.pending_skill_recovery = False
        self.pending_skill_ready_seen = False
        self.pending_recovery_control = None
        self.pending_recovery_used = False

    def wait_for_page(self, page: BasePage, now: float) -> None:
        self.wait_for_pages(now, page)

    def wait_for_job_action(self, now: float, *, skill: bool = False) -> None:
        self.confirming_job_action = True
        self.wait_for_page(BasePage.BATTLE, now)
        self.pending_stage = 'battle_ready'
        self.pending_skill_skip = not (skill and self.skill_animation_skipped)
        self.pending_skill_recovery = skill

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
        self.support_search_rows = None
        self.support_search_since = None

    def support_search_stable(self, observation: Observation, rows: tuple, now: float) -> bool:
        signature = (observation.base_page, observation.variants, observation.overlays)
        if (signature != self.support_search_signature or self.support_search_rows is None
                or not support_rows_same(self.support_search_rows, rows)):
            self.support_search_signature = signature
            self.support_search_rows = rows
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
        if self.support_stage == 'cooldown':
            if (self.support_refresh_cooldown_until is not None
                    and now < self.support_refresh_cooldown_until):
                return None
            self.support_stage = 'idle'
            self.support_refresh_cooldown_until = None
            self.support_previous_rows = None
            self.reset_support_search()
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
        if not self.support_class_selected:
            role = (requirement.role or '').strip().lower().replace(' ', '').replace('-', '') \
                if requirement is not None else ''
            point = SUPPORT_CLASS_TAB_POINTS.get(role)
            current_class = observation.field('support_class')
            if current_class is not None and current_class.usable and current_class.value == role:
                self.support_class_selected = True
                self.support_stage = 'top'
            elif requirement is None:
                self.support_class_selected = True
            elif point is None:
                self.emit('failed', now, reason='support_role_unavailable', role=requirement.role)
                return self.result(RunStatus.STOPPED, 'support_role_unavailable')
            else:
                x, y = point
                self.tap_point(observation, now, f'职阶筛选:{role}', x, y, 'support_class_filter')
                self.support_class_selected = True
                self.support_class_filter_pending = True
                self.support_stage = 'top'
                self.reset_support_search()
                return None
        control = next((item for item in observation.controls
                        if item.support is not None and item.actionable
                        and (requirement is None or support_matches(requirement, item.support))), None)
        if control is not None:
            tapped = (self.tap_action(observation, control.name, now) if self.support_class_filter_pending
                      else self.tap_stable(observation, control.name, now))
            if tapped:
                self.support_stage = 'idle'
                self.support_class_selected = False
                self.support_class_filter_pending = False
                self.support_refreshes = 0
                self.support_scrolls = 0
                self.support_previous_rows = None
                self.reset_support_search()
                self.wait_for_pages(now, BasePage.TEAM, BasePage.BATTLE)
            return None
        rows = tuple((item.name, item.box,
                      item.support.svt_id if item.support else None,
                      item.support.ce_id if item.support else None,
                      item.visual_hash)
                     for item in observation.controls if item.kind is ControlKind.ROW)
        if self.support_stage == 'idle':
            if self.support_search_stable(observation, rows, now):
                if self.support_refreshes >= 2:
                    if not rows and self.support_search_since is not None \
                            and now - self.support_search_since < self.options.action_timeout:
                        return None
                    self.emit('failed', now, reason='support_not_found', refreshes=self.support_refreshes)
                    return self.result(RunStatus.STOPPED, 'support_not_found')
                self.support_swipe(observation, now, down=False)
        elif self.support_stage == 'top':
            if self.support_search_stable(observation, rows, now):
                self.support_previous_rows = rows
                self.support_scrolls = 1
                self.support_swipe(observation, now, down=True)
        elif self.support_stage == 'scroll':
            if not rows:
                self.support_search_stable(observation, rows, now)
                if (self.support_search_since is not None
                        and now - self.support_search_since >= self.options.action_timeout):
                    self.emit('failed', now, reason='support_candidates_unavailable')
                    return self.result(RunStatus.STOPPED, 'support_candidates_unavailable')
                return None
            if self.support_previous_rows is not None and support_rows_same(rows, self.support_previous_rows):
                at_bottom = observation.field('support_at_bottom')
                if at_bottom is not None and at_bottom.usable and at_bottom.value is True:
                    if self.tap_stable(observation, '列表更新', now):
                        self.support_stage = 'refresh_confirm'
                        self.reset_support_search()
                        self.wait_for_overlay(now, Overlay.SUPPORT_CONFIRM, BasePage.SUPPORT)
                elif self.support_search_stable(observation, rows, now):
                    self.support_scrolls += 1
                    self.support_swipe(observation, now, down=True)
            elif self.support_search_stable(observation, rows, now):
                self.support_previous_rows = rows
                self.support_scrolls += 1
                self.support_swipe(observation, now, down=True)
        return None

    def handle_team(self, observation: Observation, now: float) -> RunResult | None:
        members = {member.slot: member for member in self.plan.formation}

        def servants_match(field_name: str, slots: tuple[int, ...]) -> bool:
            required = [slot for slot in slots if members.get(slot) is not None and members[slot].name.strip()]
            if not required:
                return True
            field = observation.field(field_name)
            if field is None or not field.usable or not isinstance(field.value, tuple) or len(field.value) != 3:
                return False
            return all(field.value[slot % 3] == members[slot].svt_id for slot in required)

        support = next((member for member in self.plan.formation if member.support), None)
        support_ce = observation.field('team_support_ce')
        mystic = observation.field('team_mystic')
        support_valid = (support is None or support.ce_id is None
                         or (support_ce is not None and support_ce.usable
                             and support_ce.value == support.ce_id))
        valid = (self.formation_checked
                 or (servants_match('team_servants', (0, 1, 2))
                     and servants_match('team_backline_servants', (3, 4, 5))
                     and support_valid
                     and mystic is not None and mystic.usable and mystic.value == self.plan.mystic_id))
        if valid:
            self.formation_checked = True
            self.team_since = None
            if self.tap_stable(observation, '开始任务', now):
                self.wait_for_page(BasePage.BATTLE, now)
            elif self.fallback_tap(observation, now, '开始任务', 1190, 655, 'team_fallback'):
                self.wait_for_page(BasePage.BATTLE, now)
            return None
        if self.team_since is None:
            self.team_since = now
        elif now - self.team_since >= self.options.page_timeout:
            self.emit('failed', now, reason='team_mismatch')
            return self.result(RunStatus.STOPPED, 'team_mismatch')
        return None

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
            if self.tap_action(observation, '御主技能3', now):
                self.order_stage = 'swap_out'
                self.wait_for_overlay(now, Overlay.ORDER_CHANGE)
        elif self.order_stage == 'swap_out':
            if action.swap_out_slot not in self.active_slots:
                self.emit('failed', now, reason='order_change_invalid')
                return self.result(RunStatus.STOPPED, 'order_change_invalid')
            position = self.active_slots.index(action.swap_out_slot) + 1
            if self.tap_action(observation, f'先发第{position}位', now):
                self.order_stage = 'swap_in'
                self.wait_for_overlay(now, Overlay.ORDER_CHANGE)
        elif self.order_stage == 'swap_in':
            if action.swap_in_slot not in self.reserve_slots:
                self.emit('failed', now, reason='order_change_invalid')
                return self.result(RunStatus.STOPPED, 'order_change_invalid')
            position = self.reserve_slots.index(action.swap_in_slot) + 1
            if self.tap_action(observation, f'候补第{position}位', now):
                self.order_stage = 'confirm'
                self.wait_for_overlay(now, Overlay.ORDER_CHANGE)
        elif self.order_stage == 'confirm' and self.tap_action(observation, '进行更替', now):
            self.pending_swap = (action.swap_out_slot, action.swap_in_slot)
            self.order_action = None
            self.pending_order_action = True
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
            if self.tap_action(observation, name, now):
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
            if self.tap_action(observation, f'目标从者{position}', now):
                self.spell_stage = 'confirm'
                self.wait_for_overlay(now, Overlay.COMMAND_SPELL_CONFIRM)
        elif self.spell_stage == 'confirm' and self.tap_action(observation, '决定', now):
            self.spell_action = None
            self.pending_spell_action = True
            self.spell_stage = None
            self.wait_for_job_action(now)
        return None

    def current_servant_order(self, wave: int) -> tuple[int, ...]:
        effective = self.plan.strategy_by_wave.get(wave) or self.plan.strategy
        members = {member.slot: member for member in self.plan.formation}
        ranks = strategy_servant_ranks(effective, self.plan)

        def rank(item: tuple[int, int, int | None]) -> tuple[int, int]:
            position, slot, anchor = item
            return ranks.get(anchor, ranks.get(slot, len(ranks))), position

        positioned = [(position, slot, self.priority_anchors[position])
                      for position, slot in enumerate(self.active_slots)
                      if members.get(slot) is not None and members[slot].name.strip()]
        return tuple(slot for _position, slot, _anchor in sorted(positioned, key=rank))

    def confirm_auto_fill(self, observation: Observation, now: float) -> bool:
        if self.pending_swap is not None or not self.battle_ready(observation):
            self.pending_frontline = None
            return False
        field = observation.field('battle_servants')
        if field is None or not field.usable or not isinstance(field.value, tuple) or len(field.value) != 3:
            self.pending_frontline = None
            return False
        observed = field.value
        if any(not isinstance(slot, int) for slot in observed):
            self.pending_frontline = None
            return False
        members = {member.slot: member for member in self.plan.formation}

        def visible(slot: int) -> int:
            member = members.get(slot)
            return slot if member is not None and member.name.strip() else -1

        active = list(self.active_slots)
        reserves = list(self.reserve_slots)
        anchors = list(self.priority_anchors)
        changes: list[tuple[int, int, int]] = []
        for index, current in enumerate(active):
            replacement = observed[index]
            if visible(current) == replacement:
                continue
            if not reserves:
                self.pending_frontline = None
                return False
            candidate = reserves.pop(0)
            if visible(candidate) != replacement:
                self.pending_frontline = None
                return False
            active[index] = candidate
            anchors[index] = anchors[index] if visible(candidate) == candidate else None
            changes.append((current, candidate, index + 1))
        if not changes:
            self.pending_frontline = None
            return False
        if self.pending_frontline != observed:
            self.pending_frontline = observed
            self.emit('warning', now, reason='auto_fill_waiting', observed=observed)
            return True
        self.pending_frontline = None
        self.active_slots = active
        self.reserve_slots = reserves
        self.priority_anchors = anchors
        for out_slot, in_slot, position in changes:
            self.emit('auto_fill_confirmed', now, out_slot=out_slot, in_slot=in_slot,
                      frontline_position=position, active_slots=tuple(active),
                      reserve_slots=tuple(reserves))
        return False

    def handle_battle(self, observation: Observation, now: float) -> RunResult | None:
        if self.fallback_mode:
            if self.tap_action(observation, '攻击', now):
                self.attack_submitted_at = self.last_input_finished_at
                self.wait_for_page(BasePage.CARD, now)
            return None
        wave = self.plan.waves[self.progress.wave - 1]
        turn = wave.turns[self.progress.turn - 1]
        if self.progress.action_index < len(turn.actions):
            action = turn.actions[self.progress.action_index]
            if action.kind == 'attack':
                self.progress.action_index += 1
                return self.handle_battle(observation, now)
            if action.kind == 'spell':
                if self.tap_action(observation, '令咒', now):
                    self.spell_action = action
                    self.spell_stage = 'menu'
                    self.wait_for_overlay(now, Overlay.COMMAND_SPELL_MENU)
                return None
            if action.kind == 'target':
                target = action.enemy_target if action.enemy_target is not None else action.to_target
                if target is None:
                    self.emit('failed', now, reason='target_unavailable')
                    return self.result(RunStatus.STOPPED, 'target_unavailable')
                if self.tap_action(observation, f'敌方{target}', now):
                    self.confirming_job_action = True
                    self.wait_for_page(BasePage.BATTLE, now)
                return None
            if action.kind == 'orderChange':
                if self.tap_action(observation, '御主技能', now):
                    self.order_action = action
                    self.order_stage = 'master_menu'
                    self.wait_for_overlay(now, Overlay.MASTER_SKILLS)
                return None
            if action.kind == 'master':
                if self.tap_action(observation, '御主技能', now):
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
            control = observation.control(name)
            if control is not None and control.on_cooldown is True:
                if self.skill_cooldown_since is None:
                    self.skill_cooldown_since = now
                    self.emit('warning', now, reason='skill_cooldown', control=name,
                              wave=self.progress.wave, turn=self.progress.turn,
                              action_index=self.progress.action_index)
                elif now - self.skill_cooldown_since >= self.options.action_timeout:
                    self.emit('failed', now, reason='skill_unavailable', control=name)
                    return self.result(RunStatus.STOPPED, 'skill_unavailable')
                return None
            self.skill_cooldown_since = None
            if self.tap_action(observation, name, now):
                self.skill_animation_skipped = False
                if action.player_target_slot is not None:
                    self.job_target_slot = action.player_target_slot
                    self.target_selection_since = now
                    self.optional_target = action.member_slot == action.player_target_slot
                    self.confirming_job_action = self.optional_target
                    self.pending_pages = frozenset({BasePage.BATTLE})
                    self.pending_since = now
                    self.pending_variant = None if self.optional_target else Variant.SKILL_TARGET
                    self.pending_overlay = None
                    self.pending_recovery_control = name
                    self.pending_recovery_used = False
                    if not self.optional_target:
                        self.pending_stage = 'skill_target'
                    if self.optional_target:
                        self.pending_stage = 'battle_ready'
                        self.pending_skill_skip = True
                        self.pending_skill_recovery = True
                else:
                    self.wait_for_job_action(now, skill=True)
                    self.pending_recovery_control = name
                self.skill_confirmation = (name, action.member_slot, position)
            return None
        if self.tap_action(observation, '攻击', now):
            self.attack_submitted_at = self.last_input_finished_at
            self.wait_for_page(BasePage.CARD, now)
            self.pending_recovery_control = '攻击'
        return None

    def submit_card_batch(self, observation: Observation, controls: tuple[Control, ...],
                          now: float, *, recovery: bool = False, start_position: int = 1) -> None:
        if not recovery:
            self.card_batch_controls = controls
        self.card_batch_sequence += 1
        batch_id = self.card_batch_sequence
        attempt = 1 if recovery else 0
        previous_finished_at = None
        for index, control in enumerate(controls):
            if index:
                time.sleep(COMMAND_CARD_INTERVAL_SECONDS)
            x, y = control.center
            started_at = time.time()
            data: dict[str, object] = {
                'card_position': index + start_position,
                'batch_id': batch_id,
                'batch_attempt': attempt,
            }
            if control.card is not None:
                data.update(servant_slot=control.card.servant_slot, card_type=control.card.card_type,
                            deck_index=control.card.deck_index, noble_phantasm=control.card.noble_phantasm,
                            affinity=control.card.affinity)
            if index == 0 and start_position == 1 and self.attack_submitted_at is not None:
                data['attack_to_first_card_ms'] = (started_at - self.attack_submitted_at) * 1000
            if previous_finished_at is not None:
                data['interval_ms'] = (started_at - previous_finished_at) * 1000
            previous_finished_at = self.tap_point(
                observation, now, control.name, x, y, 'command_card_batch', **data)
        submitted_at = now + COMMAND_CARD_INTERVAL_SECONDS * (len(controls) - 1)
        self.emit('card_batch_submitted', submitted_at, batch_id=batch_id, batch_attempt=attempt,
                  controls=[control.name for control in controls],
                  configured_interval_ms=COMMAND_CARD_INTERVAL_SECONDS * 1000,
                  hand=[{'control': control.name, 'servant_slot': control.card.servant_slot,
                         'card_type': control.card.card_type}
                        for control in observation.controls
                        if control.card is not None and not control.card.noble_phantasm],
                  wave=self.progress.wave, turn=self.progress.turn,
                  action_index=self.progress.action_index)
        self.card_confirmation_since = None
        self.card_selection_since = None
        self.card_readings = []
        self.np_reservation_since = None
        self.card_recovery_used = recovery
        self.card_recovery_reading = False
        self.awaiting_attack_end = True
        self.last_attack_skip_at = None
        self.wait_for_pages(submitted_at, BasePage.BATTLE, BasePage.RESULT, BasePage.DROPS)
        self.pending_stage = 'attack_end'

    def remaining_card_controls(self, observation: Observation) -> tuple[tuple[Control, ...], int] | None:
        initial = self.card_batch_controls
        if len(initial) != 3 or any(control.card is None for control in initial):
            return None
        selected_count = 0
        for control in initial:
            current = observation.control(control.name)
            if current is not None and current.actionable:
                break
            selected_count += 1
        if not 1 <= selected_count < len(initial):
            return None
        if any((current := observation.control(control.name)) is None or not current.actionable
               for control in initial[selected_count:]):
            return None
        requests = tuple(CardRequest(control.card.servant_slot, control.card.card_type,
                                     control.card.deck_index, control.card.noble_phantasm)
                         for control in initial[selected_count:] if control.card is not None)
        controls = requested_card_controls(observation, requests)
        if controls is None:
            return None
        return controls, selected_count + 1

    def stable_np_cards(self, observation: Observation) -> Observation:
        present = frozenset(control.reason.split(':', 1)[-1] for control in observation.controls
                            if control.reason.startswith('np_slot:'))
        if len(present) < 2:
            self.np_slots_previous = present
            return observation
        stable = present & self.np_slots_previous if self.np_slots_previous is not None else frozenset()
        self.np_slots_previous = present
        if stable == present:
            return observation
        return replace(observation, controls=tuple(
            control for control in observation.controls
            if not control.reason.startswith('np_slot:')
            or control.reason.split(':', 1)[-1] in stable))

    def voted_normal_hand(self, observation: Observation) -> Observation | None:
        unknown_in_history = any(slot == -1 for reading in self.card_readings
                                 for _name, slot, _card_type in reading)
        minimum_readings = 3 if unknown_in_history else 2
        if len(self.card_readings) < minimum_readings:
            return None
        normals = sorted((control for control in observation.controls
                          if control.card is not None and not control.card.noble_phantasm),
                         key=lambda control: control.name)
        voted = []
        for index, control in enumerate(normals):
            values = [reading[index] for reading in self.card_readings]

            def majority(items, allowed=None):
                counts = {}
                for item in items:
                    if allowed is None or item in allowed:
                        counts[item] = counts.get(item, 0) + 1
                ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
                if not ranked or ranked[0][1] < 2 or len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
                    return None
                return ranked[0][0]

            slot = majority([item[1] for item in values])
            card_type = majority([item[2] for item in values], {'buster', 'arts', 'quick'})
            if slot is None or card_type is None:
                return None
            voted.append(replace(control, card=replace(control.card, servant_slot=slot, card_type=card_type)))
        replacements = {control.name: control for control in voted}
        return replace(observation, controls=tuple(replacements.get(control.name, control)
                                                    for control in observation.controls))

    def handle_card(self, observation: Observation, now: float) -> RunResult | None:
        observation = self.stable_np_cards(observation)
        observation, np_mapping_diagnostic = positioned_np_cards(
            observation, self.active_slots, self.plan.formation)
        turn = (None if self.fallback_mode else
                self.plan.waves[self.progress.wave - 1].turns[self.progress.turn - 1])
        normals = sorted((control for control in observation.controls
                          if control.card is not None and not control.card.noble_phantasm),
                         key=lambda control: control.name)
        if len(normals) == 5:
            self.card_readings.append(tuple((control.name, control.card.servant_slot, control.card.card_type)
                                            for control in normals))
            self.card_readings = self.card_readings[-4:]
            if len(self.card_readings) > 1 or any(control.card.servant_slot == -1 for control in normals):
                voted = self.voted_normal_hand(observation)
                if voted is None:
                    if self.card_selection_since is None:
                        self.card_selection_since = now
                        self.emit('warning', now, reason='card_batch_waiting',
                                  message='指令卡身份与颜色等待多帧确认')
                    if now - self.card_selection_since < self.options.action_timeout:
                        return None
                    self.emit('failed', now, reason='fallback_cards_unavailable',
                              message='指令卡身份与颜色持续不稳定')
                    return self.result(RunStatus.STOPPED, 'fallback_cards_unavailable')
                observation = voted
        if not complete_normal_hand(observation):
            self.card_selection_since = None
            if self.card_recovery_used:
                remaining = self.remaining_card_controls(observation)
                if remaining is not None:
                    controls, start_position = remaining
                    self.submit_card_batch(observation, controls, now, recovery=True,
                                           start_position=start_position)
                    return None
            if self.card_confirmation_since is None:
                self.card_confirmation_since = now
                self.emit('warning', now, reason='card_batch_waiting',
                          message='五张普通指令卡尚未完整确认')
            if now - self.card_confirmation_since < self.options.action_timeout:
                return None
            reason = ('card_mismatch' if turn is not None and turn.attack is not None
                      and turn.attack.mode in {'verify', 'prefer'} else 'fallback_cards_unavailable')
            self.emit('failed', now, reason=reason, message='五张普通指令卡尚未完整确认')
            return self.result(RunStatus.STOPPED, reason)
        self.card_confirmation_since = None
        use_strategy = self.fallback_mode or turn is None or turn.attack is None or turn.attack.mode == 'strategy'
        if turn is not None and turn.attack is not None and turn.attack.mode == 'prefer':
            preferred = card_requests(turn)
            use_strategy = len(preferred) != 3 or not complete_card_controls(observation, preferred)
        if use_strategy:
            reserved: list[tuple[int | None, Control]] = []
            if turn is not None:
                for position, wanted in scheduled_np_requests(turn):
                    reserved_control = matching_card_control(observation, wanted, set())
                    if reserved_control is None:
                        if self.np_reservation_since is None:
                            self.np_reservation_since = now
                            self.emit('warning', now, reason='np_reservation_waiting',
                                      card_position=position, servant_slot=wanted.servant_slot)
                        if now - self.np_reservation_since < self.options.action_timeout:
                            return None
                        mapped_slots = sorted({item.card.servant_slot for item in observation.controls
                                               if item.card is not None and item.card.noble_phantasm})
                        message = (np_mapping_diagnostic or
                                   '作业宝具卡未在当前手牌中确认：'
                                   f'按固定位置只能确认槽位 {mapped_slots}，'
                                   f'无法唯一确认作业要求的槽位 {wanted.servant_slot}')
                        self.emit('failed', now, reason='fallback_cards_unavailable',
                                  card_position=position, servant_slot=wanted.servant_slot,
                                  message=message)
                        return self.result(RunStatus.STOPPED, 'fallback_cards_unavailable')
                    reserved.append((position, reserved_control))
            self.np_reservation_since = None
            selection = select_cards(observation, self.plan.strategy,
                                     self.plan, self.progress.wave, tuple(reserved),
                                     current_order=self.current_servant_order(self.progress.wave),
                                     active_slots=tuple(self.active_slots))
            for warning in selection.warnings:
                self.emit('warning', now, reason='strategy_servant_slots', message=warning)
            if selection.diagnostic is not None or len(selection.controls) != 3:
                message = selection.diagnostic or '补刀策略未选出三张卡'
                if self.card_selection_since is None:
                    self.card_selection_since = now
                    self.emit('warning', now, reason='card_batch_waiting', message=message)
                if now - self.card_selection_since < self.options.action_timeout:
                    return None
                self.emit('failed', now, reason='fallback_cards_unavailable',
                          message=message, strategy_source=selection.source)
                return self.result(RunStatus.STOPPED, 'fallback_cards_unavailable')
            controls = selection.controls
        else:
            requests = card_requests(turn)
            if len(requests) != 3:
                self.emit('failed', now, reason='card_mismatch', message='作业没有提供可执行的三张指令卡')
                return self.result(RunStatus.STOPPED, 'card_mismatch')
            selected = requested_card_controls(observation, requests)
            missing_np = next((wanted for wanted in requests if wanted.noble_phantasm
                               and matching_card_control(observation, wanted, set()) is None), None)
            if selected is None and missing_np is not None:
                position = requests.index(missing_np) + 1
                if self.np_reservation_since is None:
                    self.np_reservation_since = now
                    self.emit('warning', now, reason='np_reservation_waiting',
                              card_position=position, servant_slot=missing_np.servant_slot)
                if now - self.np_reservation_since < self.options.action_timeout:
                    return None
                self.emit('failed', now, reason='card_mismatch', card_position=position,
                          servant_slot=missing_np.servant_slot,
                          message=np_mapping_diagnostic or '作业宝具卡未在当前手牌中确认')
                return self.result(RunStatus.STOPPED, 'card_mismatch')
            if selected is None:
                reason = 'fallback_required' if turn.attack.mode == 'prefer' else 'card_mismatch'
                self.emit('failed', now, reason=reason, card_position=1)
                return self.result(RunStatus.STOPPED, reason)
            self.np_reservation_since = None
            controls = selected
        self.submit_card_batch(observation, controls, now, recovery=self.card_recovery_used)
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
        if Overlay.AP_RECOVERY_CONFIRM in observation.overlays:
            item = self.pending_recovery
            if item is None or item not in self.options.recovery.allowed:
                self.emit('failed', now, reason='ap_recovery_confirm_not_allowed')
                return self.result(RunStatus.STOPPED, 'ap_recovery_confirm_not_allowed')
            items = {control.name for control in observation.controls
                     if control.kind is ControlKind.ITEM and control.actionable}
            if items != {CONTROL_NAMES[item]}:
                self.emit('failed', now, reason='ap_recovery_confirm_mismatch',
                          expected=CONTROL_NAMES[item], actual=sorted(items))
                return self.result(RunStatus.STOPPED, 'ap_recovery_confirm_mismatch')
            if self.recovery_confirmation_tapped:
                if self.pending_since is not None and now - self.pending_since >= self.options.action_timeout:
                    self.emit('failed', now, reason='action_confirmation_timeout', actual=observation.base_page.value)
                    return self.result(RunStatus.STOPPED, 'action_confirmation_timeout')
                return None
            if self.tap_stable(observation, '决定', now):
                self.recovery_confirmation_tapped = True
                self.wait_for_pages(now, BasePage.SUPPORT, BasePage.TEAM, BasePage.DROPS, BasePage.BATTLE)
            return None
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
            self.recovery_confirmation_tapped = False
            self.wait_for_pages(now, BasePage.SUPPORT, BasePage.TEAM, BasePage.DROPS, BasePage.BATTLE)
        return None

    def handle_drops(self, observation: Observation, now: float) -> RunResult | None:
        if Overlay.REPEAT_DIALOG in observation.overlays:
            if not self.run_settled:
                self.progress.completed_runs += 1
                self.run_settled = True
                self.emit('battle_completed', now,
                          completed_runs=self.progress.completed_runs,
                          wave=self.progress.wave, turn=self.progress.turn)
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
            (Overlay.SUPPORT_REFRESH_COOLDOWN, '关闭'),
            (Overlay.FRIEND_REQUEST, '结束'),
            (Overlay.CLASS_INFO, '关闭'),
            (Overlay.BATTLE_STATUS_CHANGE_DETAIL, '关闭'),
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
            valid = (wave is not None and boundary is not None
                     and wave.usable and boundary.usable and boundary.value is True
                     and isinstance(wave.value, int) and 1 <= wave.value <= len(self.plan.waves))
            if not valid:
                self.emit('failed', now, reason='resume_uncertain', actual=page.value)
                return observation, self.result(RunStatus.STOPPED, 'resume_uncertain')
            observed_turn = (turn.value if turn is not None and turn.usable
                             and isinstance(turn.value, int) and turn.value >= 1 else None)
            first_turn = wave.value == 1 and observed_turn == 1
            self.progress.wave = wave.value
            self.progress.turn = 1 if first_turn else len(self.plan.waves[wave.value - 1].turns)
            self.last_observed_turn = observed_turn
            self.progress.action_index = 0
            self.fallback_mode = not first_turn
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
            if Overlay.STORM_POD_INSUFFICIENT in observation.overlays:
                if self.tap_stable(observation, '关闭', now):
                    message = '风暴罐不足，已关闭弹窗并停止'
                    self.emit('stopped', now, reason='storm_pod_insufficient', message=message)
                    return self.result(RunStatus.STOPPED, 'storm_pod_insufficient')
                time.sleep(self.options.poll_interval)
                continue
            if page is BasePage.CARD and any(control.card is not None and not control.card.noble_phantasm
                                              and control.card.crit_rate is None
                                              for control in observation.controls):
                observation = self.perception.read_fields(frame, ('crit',), timeout=self.options.page_timeout)
                self.record_observation(frame, observation)
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
                if self.draining and Overlay.AP_RECOVERY_CONFIRM not in observation.overlays:
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
                self.recovery_confirmation_tapped = False
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
            if (Overlay.MASTER_SKILLS in unexpected
                    and (self.master_action or self.order_action or self.pending_master_action
                         or self.job_target_slot is not None)):
                unexpected = unexpected - {Overlay.MASTER_SKILLS}
            if (Overlay.ORDER_CHANGE in observation.overlays
                    and self.order_action is None and not self.pending_order_action):
                unexpected = unexpected | {Overlay.ORDER_CHANGE}
            if ((observation.overlays & {Overlay.COMMAND_SPELL_MENU, Overlay.COMMAND_SPELL_TARGET,
                                         Overlay.COMMAND_SPELL_CONFIRM})
                    and self.spell_action is None and not self.pending_spell_action):
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
                    if overlay is Overlay.SUPPORT_REFRESH_COOLDOWN:
                        self.support_stage = 'cooldown'
                        self.support_refresh_cooldown_until = now + SUPPORT_REFRESH_COOLDOWN_SECONDS
                        self.pending_pages = None
                        self.pending_since = None
                        self.pending_frame_hash = None
                        self.pending_variant = None
                        self.pending_overlay = None
                        self.reset_support_search()
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

            if (self.job_target_slot is not None and self.target_selection_since is not None
                    and now - self.target_selection_since >= self.options.action_timeout):
                self.emit('failed', now, reason='target_selection_timeout', slot=self.job_target_slot,
                          stage='skill_target', wait_ms=(now - self.target_selection_since) * 1000,
                          message='技能目标未在限定时间内变为可选择')
                return self.result(RunStatus.STOPPED, 'target_selection_timeout')

            if self.card_recovery_reading and page is not BasePage.CARD:
                if page not in {BasePage.BATTLE, BasePage.RESULT, BasePage.DROPS}:
                    self.emit('failed', now, reason='action_confirmation_timeout',
                              stage='attack_recovery', recovery_attempts=1, actual=page.value)
                    return self.result(RunStatus.STOPPED, 'action_confirmation_timeout')
                self.card_recovery_reading = False
                self.wait_for_pages(now, BasePage.BATTLE, BasePage.RESULT, BasePage.DROPS)
                self.pending_stage = 'attack_end'

            if page is BasePage.BATTLE and self.confirm_auto_fill(observation, now):
                time.sleep(self.options.poll_interval)
                continue

            if self.pending_pages is not None:
                if (self.awaiting_attack_end and page is BasePage.BATTLE
                        and Variant.BATTLE_ANIMATION in observation.variants and not observation.overlays
                        and not self.battle_controls_ready(observation)
                        and observation.frame_hash != self.pending_frame_hash
                        and (self.last_attack_skip_at is None
                             or now - self.last_attack_skip_at + 1e-9 >= 1.0)):
                    x, y = BATTLE_SKIP_POINT
                    self.tap_point(observation, now, '战斗跳过', x, y, 'attack_animation_skip')
                    self.last_attack_skip_at = now
                    time.sleep(self.options.poll_interval)
                    continue
                if (self.awaiting_attack_end and page is BasePage.CARD
                        and self.pending_since is not None
                        and now - self.pending_since >= ACTION_RECOVERY_SECONDS
                        and observation.frame_hash != self.pending_frame_hash):
                    if self.card_recovery_used:
                        self.emit('failed', now, reason='action_confirmation_timeout',
                                  stage=self.pending_stage, recovery_attempts=1,
                                  wait_ms=(now - self.pending_since) * 1000,
                                  actual=page.value)
                        return self.result(RunStatus.STOPPED, 'action_confirmation_timeout')
                    self.card_recovery_used = True
                    self.card_recovery_reading = True
                    self.emit('warning', now, reason='action_recovery', control='command_card_batch',
                              attempt=1, wave=self.progress.wave, turn=self.progress.turn,
                              action_index=self.progress.action_index)
                    self.pending_pages = None
                    self.pending_since = None
                    self.pending_frame_hash = None
                    result = self.handle_card(observation, now)
                    if result is not None:
                        return result
                    time.sleep(self.options.poll_interval)
                    continue
                if (self.pending_recovery_control is not None and not self.pending_recovery_used
                        and (self.pending_stage == 'skill_target' or self.battle_controls_ready(observation))
                        and (self.pending_variant is None or self.pending_variant not in observation.variants)
                        and self.pending_since is not None
                        and now - self.pending_since >= ACTION_RECOVERY_SECONDS
                        and observation.frame_hash != self.pending_frame_hash
                        and self.skill_retry_allowed(observation, now)):
                    control = self.pending_recovery_control
                    if self.tap_action(observation, control, now):
                        self.pending_recovery_used = True
                        self.pending_since = now
                        self.emit('warning', now, reason='action_recovery', control=control, attempt=1,
                                  wave=self.progress.wave, turn=self.progress.turn,
                                  action_index=self.progress.action_index)
                        time.sleep(self.options.poll_interval)
                        continue
                if (self.pending_skill_skip and self.pending_stage == 'battle_ready'
                        and page is BasePage.BATTLE and not observation.overlays
                        and Variant.SKILL_TARGET not in observation.variants
                        and observation.frame_hash != self.pending_frame_hash
                        and not (self.optional_target and self.battle_controls_ready(observation))):
                    x, y = BATTLE_SKIP_POINT
                    self.tap_point(observation, now, '战斗跳过', x, y, 'skill_animation_skip')
                    self.pending_skill_skip = False
                    if self.pending_skill_recovery:
                        self.skill_animation_skipped = True
                    time.sleep(self.options.poll_interval)
                    continue
                if (self.pending_skill_recovery and self.optional_target and self.skill_animation_skipped
                        and Variant.SKILL_TARGET in observation.variants):
                    self.emit('failed', now, reason='skill_target_after_animation_skip',
                              wave=self.progress.wave, turn=self.progress.turn,
                              action_index=self.progress.action_index)
                    return self.result(RunStatus.STOPPED, 'skill_target_after_animation_skip')
                if (self.awaiting_attack_end and page is BasePage.BATTLE
                        and Variant.BATTLE_ANIMATION not in observation.variants):
                    observation = self.perception.read_fields(
                        frame, ('wave', 'turn'), timeout=self.options.page_timeout)
                    self.record_observation(frame, observation)
                skill_recovery_confirmed = True
                if (self.pending_skill_recovery and self.pending_stage == 'battle_ready'
                        and not self.pending_skill_skip):
                    if self.battle_ready(observation):
                        skill_recovery_confirmed = self.pending_skill_ready_seen
                        self.pending_skill_ready_seen = True
                    else:
                        self.pending_skill_ready_seen = False
                confirmed = (page in self.pending_pages
                             and observation.frame_hash != self.pending_frame_hash
                             and skill_recovery_confirmed
                             and (self.pending_variant is None or self.pending_variant in observation.variants)
                             and (self.pending_overlay is None or self.pending_overlay in observation.overlays)
                             and (self.pending_stage != 'battle_ready' or self.battle_ready(observation)
                                  or (self.optional_target and Variant.SKILL_TARGET in observation.variants))
                             and (not self.pending_skill_recovery or not self.pending_skill_skip
                                  or (self.optional_target and Variant.SKILL_TARGET in observation.variants))
                             and not (page is BasePage.BATTLE
                                      and Variant.BATTLE_ANIMATION in observation.variants)
                             and self.skill_effect_confirmed(observation))
                if confirmed and self.awaiting_attack_end and page is BasePage.BATTLE:
                    wave_field = observation.field('wave')
                    turn_field = observation.field('turn')
                    if wave_field is None or not wave_field.usable:
                        confirmed = False
                    else:
                        wave = wave_field.value
                        turn = (turn_field.value if turn_field is not None and turn_field.usable
                                and isinstance(turn_field.value, int) and turn_field.value >= 1 else None)
                        current_wave = self.progress.wave
                        if not isinstance(wave, int) or wave < current_wave:
                            confirmed = False
                        elif wave > current_wave + 1 or wave > len(self.plan.waves):
                            self.emit('failed', now, reason='progress_uncertain',
                                      observed_wave=wave, observed_turn=turn)
                            return self.result(RunStatus.STOPPED, 'progress_uncertain')
                        elif self.last_observed_turn is not None:
                            if turn is None or turn <= self.last_observed_turn:
                                confirmed = False
                            elif turn != self.last_observed_turn + 1:
                                self.emit('failed', now, reason='progress_uncertain',
                                          observed_wave=wave, observed_turn=turn)
                                return self.result(RunStatus.STOPPED, 'progress_uncertain')
                        if confirmed:
                            if turn is not None:
                                self.last_observed_turn = turn
                            if wave == current_wave:
                                self.progress.turn += 1
                                self.fallback_mode = self.progress.turn > len(self.plan.waves[wave - 1].turns)
                                self.progress.action_index = 0
                                self.emit('turn_started', now, wave=wave, turn=self.progress.turn)
                            else:
                                self.progress.wave = wave
                                self.progress.turn = 1
                                self.progress.action_index = 0
                                self.fallback_mode = False
                                self.emit('wave_started', now, wave=wave, turn=1)
                                self.emit('turn_started', now, wave=wave, turn=1)
                if confirmed and self.pending_swap is not None:
                    swap_out, swap_in = self.pending_swap
                    expected = list(self.active_slots)
                    expected[expected.index(swap_out)] = swap_in
                    frontline = observation.field('battle_servants')
                    confirmed = (frontline is not None and frontline.usable
                                 and frontline.value == tuple(expected))
                if confirmed:
                    self.emit('action_confirmed', now, stage=self.pending_stage,
                              wait_ms=((now - self.pending_since) * 1000
                                       if self.pending_since is not None else 0.0),
                              wave=self.progress.wave, turn=self.progress.turn,
                              action_index=self.progress.action_index)
                    self.pending_pages = None
                    self.pending_since = None
                    self.pending_frame_hash = None
                    self.pending_stage = 'page'
                    self.pending_skill_skip = False
                    self.pending_skill_recovery = False
                    self.pending_skill_ready_seen = False
                    self.pending_recovery_control = None
                    self.pending_recovery_used = False
                    self.pending_variant = None
                    self.pending_overlay = None
                    self.awaiting_attack_end = False
                    self.last_attack_skip_at = None
                    self.card_recovery_used = False
                    self.card_recovery_reading = False
                    self.skill_confirmation = None
                    self.skill_cooldown_since = None
                    self.skill_available_since = None
                    if self.starting_next_run:
                        self.progress.wave = 1
                        self.progress.turn = 1
                        self.last_observed_turn = 1
                        self.progress.action_index = 0
                        self.fallback_mode = False
                        self.active_slots = [0, 1, 2]
                        self.reserve_slots = [member.slot for member in self.plan.formation
                                              if member.slot not in self.active_slots]
                        specified = {member.slot for member in self.plan.formation if member.name.strip()}
                        self.priority_anchors = [slot if slot in specified else None
                                                 for slot in self.active_slots]
                        self.pending_frontline = None
                        self.run_settled = False
                        self.starting_next_run = False
                        self.result_seen = False
                    if self.job_target_slot is not None and self.optional_target:
                        if Variant.SKILL_TARGET in observation.variants:
                            self.confirming_job_action = False
                        else:
                            self.job_target_slot = None
                            self.target_selection_since = None
                        self.optional_target = False
                    if self.pending_swap is not None:
                        swap_out, swap_in = self.pending_swap
                        active_position = self.active_slots.index(swap_out)
                        reserve_position = self.reserve_slots.index(swap_in)
                        self.active_slots[active_position] = swap_in
                        self.reserve_slots[reserve_position] = swap_out
                        self.pending_swap = None
                        self.emit('order_change_confirmed', now, out_slot=swap_out, in_slot=swap_in,
                                  active_slots=tuple(self.active_slots),
                                  reserve_slots=tuple(self.reserve_slots))
                    if self.confirming_job_action:
                        self.progress.action_index += 1
                        self.confirming_job_action = False
                        self.pending_master_action = False
                        self.pending_order_action = False
                        self.pending_spell_action = False
                elif (self.pending_since is not None
                      and now - self.pending_since >= (ATTACK_END_TIMEOUT_SECONDS
                                                     if self.pending_stage == 'attack_end'
                                                     and page is BasePage.BATTLE
                                                     else self.options.action_timeout)):
                    self.emit('failed', now, reason='action_confirmation_timeout', stage=self.pending_stage,
                              wait_ms=(now - self.pending_since) * 1000,
                              expected=sorted(item.value for item in self.pending_pages), actual=page.value)
                    return self.result(RunStatus.STOPPED, 'action_confirmation_timeout')
                else:
                    self.pending_frame_hash = observation.frame_hash
                    time.sleep(self.options.poll_interval)
                    continue

            if page is BasePage.SUPPORT:
                result = self.handle_support(observation, now)
                if result is not None:
                    return result
            elif page is BasePage.TEAM:
                result = self.handle_team(observation, now)
                if result is not None:
                    return result
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
                if self.tap_action(observation, f'御主技能{action.skill + 1}', now):
                    self.master_action = None
                    self.pending_master_action = True
                    self.skill_animation_skipped = False
                    if action.player_target_slot is not None:
                        self.job_target_slot = action.player_target_slot
                        self.target_selection_since = now
                        self.pending_pages = frozenset({BasePage.BATTLE})
                        self.pending_since = now
                        self.pending_variant = Variant.SKILL_TARGET
                        self.pending_overlay = None
                        self.pending_recovery_control = f'御主技能{action.skill + 1}'
                        self.pending_recovery_used = False
                        self.pending_stage = 'skill_target'
                    else:
                        self.wait_for_job_action(now, skill=True)
            elif self.job_target_slot is not None:
                if self.job_target_slot not in self.active_slots:
                    self.emit('failed', now, reason='target_unavailable', slot=self.job_target_slot)
                    return self.result(RunStatus.STOPPED, 'target_unavailable')
                position = self.active_slots.index(self.job_target_slot) + 1
                if self.tap_action(observation, f'目标从者{position}', now):
                    self.job_target_slot = None
                    self.target_selection_since = None
                    self.wait_for_job_action(now, skill=True)
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
        bind_mystic = getattr(perception, 'bind_mystic', None)
        if bind_mystic is not None:
            bind_mystic(plan.mystic_id)
        result = Runner(plan, device, perception, evidence or events, run_options, control).run()
        return evidence.finish(result) if evidence is not None else result
    finally:
        if evidence is not None:
            evidence.close()
