#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from export_schedule_template import export_schedule, validate_exported_schedule


class ExportScheduleTemplateTests(unittest.TestCase):
    def test_all_drone_allocations_are_written_to_plan_instructions(self):
        result = {
            "selected": {
                "orundum_per_day": 100,
                "net_lmd_per_day": -1000,
                "plan": {
                    "plan_id": "test",
                    "segments": {
                        "segment_1": {
                            "start": "00:00",
                            "end": "08:00",
                            "rooms": {
                                "factory_1": {"operators": []},
                                "trading_post_1": {"operators": []},
                            },
                        },
                    },
                    "facility_configuration": {
                        "rooms": {
                            "factory_1": {"facility_id": "factory", "product_id": "pure_gold"},
                            "trading_post_1": {"facility_id": "trading_post", "product_id": "lmd_order"},
                        },
                        "dormitories": [],
                    },
                    "simulation": {
                        "drone_plan": {
                            "allocations": [
                                {"segment_id": "segment_1", "facility_id": "factory", "room_id": "factory_1", "drones": 22},
                                {"segment_id": "segment_1", "facility_id": "trading_post", "room_id": "trading_post_1", "drones": 94},
                            ],
                        },
                    },
                    "recovery_plan": {"events": []},
                },
            },
        }
        schedule = export_schedule(result)
        plan = schedule["plans"][0]
        self.assertTrue(plan["drones"]["enable"])
        self.assertEqual((plan["drones"]["room"], plan["drones"]["index"]), ("trading", 1))
        self.assertIn("制造站1 22架", plan["description_post"])
        self.assertIn("贸易站1 94架", plan["description_post"])
        self.assertEqual(validate_exported_schedule(schedule), [])


if __name__ == "__main__":
    unittest.main()
