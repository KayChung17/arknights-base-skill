#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from export_schedule_template import export_schedule, validate_exported_schedule, validate_schedule_matches_result


class ExportScheduleTemplateTests(unittest.TestCase):
    def _result_with_right_side(self):
        return {
            "base_state": {"initial_drone_stock": 235},
            "selected": {
                "orundum_per_day": 100,
                "net_lmd_per_day": -1000,
                "plan": {
                    "plan_id": "right-side",
                    "segments": {
                        "segment_1": {
                            "start": "00:00", "end": "00:00",
                            "rooms": {"trading_post_1": {"operators": []}},
                        },
                    },
                    "facility_configuration": {
                        "rooms": {"trading_post_1": {"facility_id": "trading_post", "product_id": "lmd_order"}},
                        "dormitories": [{"room_id": "dormitory_1", "level": 1}],
                    },
                    "simulation": {"drone_plan": {"allocations": []}},
                    "recovery_plan": {"events": [{
                        "segment_id": "segment_1", "dormitory_id": "dormitory_1", "operators": ["休息者"],
                    }]},
                    "right_side_plan": {"assignments": [{
                        "segment_id": "segment_1",
                        "rooms": {"meeting": ["甲", "乙"], "hire": ["丙"]},
                    }]},
                },
            },
        }

    def test_right_side_and_dormitory_are_exported_from_structured_plan(self):
        result = self._result_with_right_side()
        schedule = export_schedule(result)
        rooms = schedule["plans"][0]["rooms"]
        self.assertEqual(rooms["meeting"][0]["operators"], ["甲", "乙"])
        self.assertEqual(rooms["hire"][0]["operators"], ["丙"])
        self.assertEqual(rooms["dormitory"][0]["operators"], ["休息者"])
        self.assertEqual(validate_schedule_matches_result(result, schedule), [])

    def test_post_export_room_edit_is_rejected(self):
        result = self._result_with_right_side()
        schedule = export_schedule(result)
        schedule["plans"][0]["rooms"]["trading"][0]["operators"] = ["甲", "乙", "丙"]
        errors = validate_schedule_matches_result(result, schedule)
        self.assertTrue(errors)
        self.assertIn("rooms.trading", errors[0])

    def test_multiple_drone_targets_at_one_node_are_rejected(self):
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
                    "right_side_plan": {
                        "assignments": [{
                            "segment_id": "segment_1",
                            "rooms": {"meeting": ["甲", "乙"], "hire": ["丙"]},
                        }],
                    },
                },
            },
        }
        with self.assertRaisesRegex(ValueError, "多个无人机目标"):
            export_schedule(result)


if __name__ == "__main__":
    unittest.main()
