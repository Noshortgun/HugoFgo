from __future__ import annotations

import unittest

from executor.core.tests.test_runner import single_wave_plan
from executor.device.base import Frame
from executor.perception import BasePage, Observation
from executor.testing import ScriptedPerception, require_controls, run_scenario


class ScenarioTest(unittest.TestCase):
    def test_scenario_rejects_mismatched_lengths(self) -> None:
        with self.assertRaisesRegex(ValueError, '观察数与时间数必须一致'):
            run_scenario([Observation()], [0.0, 1.0], single_wave_plan())

    def test_scripted_perception_assigns_a_new_hash_to_every_frame(self) -> None:
        observation = Observation(base_page=BasePage.BATTLE, frame_hash='same')
        perception = ScriptedPerception([observation, observation])

        first = perception.read(Frame(image=b'0', width=1280, height=720, captured_at=0.0))
        second = perception.read(Frame(image=b'1', width=1280, height=720, captured_at=0.5))

        self.assertNotEqual(first.frame_hash, second.frame_hash)

    def test_required_control_must_be_actionable(self) -> None:
        with self.assertRaisesRegex(ValueError, '攻击'):
            require_controls(Observation(), '攻击')

    def test_scenario_validates_required_controls_at_their_frame(self) -> None:
        with self.assertRaisesRegex(ValueError, '攻击'):
            run_scenario([Observation()], [0.0], single_wave_plan(),
                         required_controls={0: ('攻击',)})


if __name__ == '__main__':
    unittest.main()
