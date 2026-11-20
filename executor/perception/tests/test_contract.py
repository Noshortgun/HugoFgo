"""感知契约：同步快路径不碰 OCR，字段状态区分“不需要读”和“没读”。"""
from __future__ import annotations

import unittest
from unittest import mock

from executor.device.base import Frame, FrameFormat
from executor.perception import BasePage, FieldStatus, Perception, evidence
from executor.perception.tests.fixtures import ROOT, load_frame
from executor.perception.observation import ControlKind


class RecordingReader:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, image):
        self.calls += 1
        raise AssertionError('同步 read() 不允许调用 OCR')


class ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.reader = RecordingReader()
        cls.perception = Perception(ocr_reader=cls.reader)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.perception.close()

    def test_read_never_calls_ocr(self) -> None:
        for relative in (
            'captures/2026-11-20/key/battle-wave1.png',
            'captures/2026-11-20/key/card-select.png',
            'captures/2026-11-20/key/result.png',
            'captures/2026-11-20/key/12-battle-start.png',
        ):
            with self.subTest(relative=relative):
                self.perception.read(load_frame(relative))

        self.assertEqual(0, self.reader.calls)

    def test_field_status_separates_not_applicable_from_not_read(self) -> None:
        result = self.perception.read(load_frame('captures/2026-11-20/key/result.png'))
        battle = self.perception.read(load_frame('captures/2026-11-20/key/battle-wave1.png'))

        self.assertIs(FieldStatus.NOT_APPLICABLE, result.field('wave').status)
        self.assertIs(FieldStatus.NOT_READ, battle.field('wave').status)
        self.assertIs(FieldStatus.NOT_READ, battle.field('np').status)

    def test_unknown_and_loading_expose_no_controls(self) -> None:
        for relative, expected in (
            ('captures/2026-11-20/key/12-battle-start.png', BasePage.LOADING),
            ('captures/2026-11-19-ember/34-grand-support-confirm.png', BasePage.UNKNOWN),
        ):
            with self.subTest(relative=relative):
                observation = self.perception.read(load_frame(relative))
                self.assertIs(expected, observation.base_page)
                self.assertEqual((), observation.controls)
                self.assertFalse(observation.actionable)

    def test_identical_frame_comes_from_cache(self) -> None:
        sample = load_frame('captures/2026-11-20/key/team-confirm.png')
        original = evidence.match_templates
        with mock.patch.object(evidence, 'match_templates', wraps=original) as matcher:
            first = self.perception.read(sample)
            second = self.perception.read(sample)

        self.assertEqual(1, matcher.call_count)
        self.assertEqual(first.frame_hash, second.frame_hash)
        self.assertIs(first.base_page, second.base_page)

    def test_frame_hash_tracks_content_and_format(self) -> None:
        battle = load_frame('captures/2026-11-20/key/battle-wave1.png')
        card = load_frame('captures/2026-11-20/key/card-select.png')
        self.assertNotEqual(evidence.frame_hash(battle), evidence.frame_hash(card))

        raw = Frame(image=bytes(range(12)), width=2, height=2, captured_at=0.0,
                    format=FrameFormat.BGR24)
        self.assertNotEqual(evidence.frame_hash(raw),
                            evidence.frame_hash(Frame(image=raw.image, width=2, height=2,
                                                      captured_at=0.0)))

    def test_observation_is_immutable(self) -> None:
        observation = self.perception.read(load_frame('captures/2026-11-20/key/battle-wave1.png'))
        with self.assertRaises(TypeError):
            observation.fields['wave'] = None  # type: ignore[index]

        updated = observation.with_fields({})
        self.assertIsNot(observation, updated)
        self.assertIs(FieldStatus.NOT_READ, observation.field('wave').status)

    def test_controls_carry_base_coordinates(self) -> None:
        card = self.perception.read(load_frame('captures/2026-11-20/key/card-select.png'))
        first = card.control('卡1')
        self.assertIsNotNone(first)
        self.assertIs(ControlKind.CARD, first.kind)
        self.assertEqual((128, 535), first.center)
        self.assertTrue(all(0 <= value <= 1280 for value in first.box))


if __name__ == '__main__':
    unittest.main()
