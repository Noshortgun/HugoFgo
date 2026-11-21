"""后台 OCR：不阻塞快路径、按帧绑定、过期结果只进缓存。"""
from __future__ import annotations

import threading
import unittest

import numpy as np

from executor.perception import Field, FieldStatus, StaleFrameError
from executor.perception.ocr import OcrLine, OcrWorker
from executor.perception.tests.fixtures import isolated_perception, load_frame


class GatedReader:
    """可控的后台 OCR：第一次调用会卡住，直到测试放行。"""

    def __init__(self) -> None:
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, image):
        self.calls += 1
        self.started.set()
        self.release.wait(5)
        return [
            OcrLine('1/3', 910, 26, 0.99),
            OcrLine('2回合', 930, 96, 0.98),
            OcrLine('30%', 268, 668, 0.97),
            OcrLine('45%', 585, 668, 0.96),
            OcrLine('还剩1名', 924, 62, 0.98),
        ]


class FieldTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reader = GatedReader()
        self.perception = isolated_perception(self.addCleanup, ocr_reader=self.reader)
        self.battle = load_frame('captures/2026-11-20/key/battle-wave1.png')
        self.card = load_frame('captures/2026-11-20/key/card-select.png')

    def tearDown(self) -> None:
        self.reader.release.set()

    def test_fields_are_pending_until_ocr_finishes(self) -> None:
        self.perception.read(self.battle)
        pending = self.perception.read_fields(self.battle, ['wave'])

        self.assertIs(FieldStatus.PENDING, pending.field('wave').status)
        self.assertIs(FieldStatus.NOT_READ, pending.field('turn').status)

        self.reader.release.set()
        ready = self.perception.read_fields(self.battle, ['wave', 'turn', 'np', 'enemies'], timeout=5)

        self.assertIs(FieldStatus.CONFIRMED, ready.field('wave').status)
        self.assertEqual(1, ready.field('wave').value)
        self.assertEqual(2, ready.field('turn').value)
        self.assertEqual((30, 45), ready.field('np').value)
        self.assertEqual(1, ready.field('enemies').value)

    def test_ocr_only_runs_once_per_frame(self) -> None:
        self.perception.read(self.battle)
        self.perception.read_fields(self.battle, ['wave'])
        self.reader.release.set()
        self.perception.read_fields(self.battle, ['wave'], timeout=5)
        self.perception.read_fields(self.battle, ['turn'], timeout=5)

        self.assertEqual(1, self.reader.calls)

    def test_stale_result_is_cached_but_rejected(self) -> None:
        old = self.perception.read(self.battle)
        errors: list[Exception] = []

        def wait_for_fields() -> None:
            try:
                self.perception.read_fields(self.battle, ['wave'], timeout=5)
            except StaleFrameError as error:
                errors.append(error)

        worker = threading.Thread(target=wait_for_fields)
        worker.start()
        self.assertTrue(self.reader.started.wait(2))

        self.perception.read(self.card)
        self.reader.release.set()
        worker.join(5)

        self.assertTrue(errors, '画面变化后仍返回了旧帧字段')
        self.assertIs(FieldStatus.NOT_READ, old.field('wave').status)

        cached = self.perception.read(self.battle)
        self.assertIs(FieldStatus.CONFIRMED, cached.field('wave').status)
        self.assertEqual(1, self.reader.calls)

    def test_frame_not_just_read_is_rejected(self) -> None:
        self.perception.read(self.card)
        with self.assertRaises(StaleFrameError):
            self.perception.read_fields(self.battle, ['wave'])

    def test_unknown_field_name_is_rejected(self) -> None:
        self.perception.read(self.battle)
        with self.assertRaises(ValueError):
            self.perception.read_fields(self.battle, ['bond'])

    def test_field_not_applicable_on_page_stays_unread(self) -> None:
        result = load_frame('captures/2026-11-20/key/result.png')
        self.perception.read(result)
        observed = self.perception.read_fields(result, ['wave'])

        self.assertIs(FieldStatus.NOT_APPLICABLE, observed.field('wave').status)
        self.assertEqual(0, self.reader.calls)

    def test_cached_ocr_result_is_used_by_next_read(self) -> None:
        self.perception.read(self.battle)
        self.perception.read_fields(self.battle, ['wave'])
        self.reader.release.set()
        self.perception.read_fields(self.battle, ['wave'], timeout=5)

        fresh = self.perception.read(self.battle)
        self.assertIs(FieldStatus.CONFIRMED, fresh.field('wave').status)
        self.assertTrue(isinstance(fresh.field('wave'), Field))

    def test_unrequested_fields_stay_not_read(self) -> None:
        self.perception.read(self.battle)
        self.perception.read_fields(self.battle, ['wave'])
        self.reader.release.set()
        ready = self.perception.read_fields(self.battle, ['wave'], timeout=5)

        self.assertIs(FieldStatus.CONFIRMED, ready.field('wave').status)
        self.assertIs(FieldStatus.NOT_READ, ready.field('turn').status)
        self.assertIs(FieldStatus.NOT_READ, ready.field('np').status)

    def test_results_are_bound_to_frame_and_request(self) -> None:
        reader = GatedReader()
        worker = OcrWorker(reader)
        try:
            first = worker.submit('frame-a', np.zeros((4, 4, 3), dtype=np.uint8))
            again = worker.submit('frame-a', np.zeros((4, 4, 3), dtype=np.uint8))
            second = worker.submit('frame-b', np.zeros((4, 4, 3), dtype=np.uint8))

            self.assertEqual(first, again)
            self.assertNotEqual(first, second)

            reader.release.set()
            finished = worker.wait('frame-a', 5)
            self.assertEqual(first, finished[0])
            self.assertEqual(first, worker.request_id('frame-a'))
        finally:
            reader.release.set()
            worker.close()


if __name__ == '__main__':
    unittest.main()
