from __future__ import annotations

import unittest

from executor.core.ap_policy import RecoveryItem, RecoveryPolicy, RunSettings, select_recovery


class RecoveryPolicyTest(unittest.TestCase):
    def test_default_run_settings_allow_only_bronze_for_one_battle(self) -> None:
        settings = RunSettings()

        self.assertEqual(1, settings.battles)
        self.assertEqual(frozenset({RecoveryItem.BRONZE}), settings.recovery.allowed)
        self.assertFalse(settings.recovery.drain_after_turns)

    def test_selects_only_an_available_whitelisted_item(self) -> None:
        policy = RecoveryPolicy(allowed=frozenset({RecoveryItem.BRONZE}))

        selected = select_recovery(policy, ('圣晶石', '黄金果实', '青铜果实'))

        self.assertEqual(RecoveryItem.BRONZE, selected)


if __name__ == '__main__':
    unittest.main()
