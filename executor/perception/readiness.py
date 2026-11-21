from executor.perception import BasePage, ControlKind, Observation


def readiness_gaps(observation: Observation) -> list[str]:
    page = observation.base_page
    if page is BasePage.UNKNOWN:
        return ['known_screen']
    missing = []
    if page is BasePage.SUPPORT:
        candidates = [control for control in observation.controls
                      if control.kind is ControlKind.ROW and control.actionable]
        if not candidates or any(
            control.support is None
            or not (control.support.name or control.support.svt_id)
            or not (control.support.ce or control.support.ce_id)
            for control in candidates
        ):
            missing.append('support_identity')
    elif page is BasePage.CARD:
        cards = [control for control in observation.controls if control.kind is ControlKind.CARD]
        if not cards or any(not control.actionable or control.card is None
                            or not control.card.card_type for control in cards):
            missing.append('command_card_identity')
    elif page is BasePage.BATTLE:
        wave = observation.field('wave')
        turn = observation.field('turn')
        boundary = observation.field('turn_boundary')
        if wave is None or turn is None or not wave.usable or not turn.usable:
            missing.append('wave_turn')
        if boundary is None or not boundary.usable or boundary.value is not True:
            missing.append('turn_boundary')
    elif page is BasePage.RESULT:
        control = observation.control('结算推进')
        if control is None or not control.actionable:
            missing.append('result_control')
    return missing
