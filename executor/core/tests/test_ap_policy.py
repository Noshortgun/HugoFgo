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

    def test_selects_available_items_in_user_priority_order(self) -> None:
        policy = RecoveryPolicy(
            allowed=frozenset({RecoveryItem.GOLD, RecoveryItem.SILVER, RecoveryItem.BRONZE}),
            priority=(RecoveryItem.SILVER, RecoveryItem.GOLD, RecoveryItem.BRONZE),
        )

        selected = select_recovery(policy, ('青铜果实', '黄金果实', '白银果实'))

        self.assertEqual(RecoveryItem.SILVER, selected)
        self.assertEqual(RecoveryItem.GOLD,
                         select_recovery(policy, ('青铜果实', '黄金果实')))

    def test_priority_cannot_bypass_the_whitelist(self) -> None:
        policy = RecoveryPolicy(
            allowed=frozenset({RecoveryItem.BRONZE}),
            priority=(RecoveryItem.SAINT_QUARTZ, RecoveryItem.BRONZE),
        )

        self.assertEqual((RecoveryItem.BRONZE,), policy.priority)
        self.assertEqual(RecoveryItem.BRONZE,
                         select_recovery(policy, ('圣晶石', '青铜果实')))


if __name__ == '__main__':
    unittest.main()
