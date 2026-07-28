#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from data_loader import OwnedOperator
from search_upgrades import selected_upgrade_requirements


class UpgradeSearchTests(unittest.TestCase):
    def test_selected_ceiling_skill_yields_minimum_unlock(self):
        roster = [OwnedOperator(name="巫恋", elite=1, level=1, recruited=True, morale=24)]
        selected = {
            "plan": {
                "segments": {
                    "segment_1": {
                        "hours": 12,
                        "rooms": {
                            "trading_post_1": {
                                "facility_id": "trading_post",
                                "product_id": "lmd_order",
                                "operators": [{"name": "巫恋"}],
                            }
                        },
                    }
                }
            }
        }
        rows = selected_upgrade_requirements(roster, selected)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operator"], "巫恋")
        self.assertEqual(rows[0]["target_elite"], 2)
        self.assertEqual(rows[0]["target_level"], 1)
        self.assertIn("lmd_order", rows[0]["products"])

    def test_no_upgrade_when_current_skill_already_max(self):
        roster = [OwnedOperator(name="巫恋", elite=2, level=1, recruited=True, morale=24)]
        selected = {
            "plan": {
                "segments": {
                    "segment_1": {
                        "hours": 12,
                        "rooms": {
                            "trading_post_1": {
                                "facility_id": "trading_post",
                                "product_id": "lmd_order",
                                "operators": [{"name": "巫恋"}],
                            }
                        },
                    }
                }
            }
        }
        self.assertEqual(selected_upgrade_requirements(roster, selected), [])


if __name__ == "__main__":
    unittest.main()
