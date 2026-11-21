from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from executor.core.ap_policy import RecoveryItem, RecoveryPolicy, RunSettings
from executor.session.settings import load_settings, save_settings


class RunSettingsPersistenceTest(unittest.TestCase):
    def test_missing_file_uses_safe_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            actual = load_settings(Path(directory) / 'missing.json')

        self.assertEqual(RunSettings(), actual)

    def test_saved_settings_survive_a_new_load(self) -> None:
        expected = RunSettings(
            battles=3,
            recovery=RecoveryPolicy(
                allowed=frozenset({RecoveryItem.GOLD, RecoveryItem.SAINT_QUARTZ}),
                drain_after_turns=True,
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'run-settings.json'

            save_settings(path, expected)
            actual = load_settings(path)

        self.assertEqual(expected, actual)


if __name__ == '__main__':
    unittest.main()
