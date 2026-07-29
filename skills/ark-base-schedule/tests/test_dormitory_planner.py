from __future__ import annotations

import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from dormitory_planner import dormitory_base_recovery, plan_dormitories
from optimizer_common import Segment


class DormitoryPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.segments = [
            Segment("segment_1", "早班", "08:00", "14:00", 6.0),
            Segment("segment_2", "中班", "14:00", "20:00", 6.0),
            Segment("segment_3", "晚班", "20:00", "08:00", 12.0),
        ]
        self.work = {"测试干员": [False, True, True]}
        self.costs = {"测试干员": [0.0, 1.0, 1.0]}

    def test_full_ambience_recovery_by_level(self) -> None:
        self.assertEqual([dormitory_base_recovery(level) for level in range(1, 6)], [2, 2.5, 3, 3.5, 4])

    def test_level_one_dorm_cannot_sustain_eighteen_hours(self) -> None:
        value = plan_dormitories(
            self.segments,
            [{"room_id": "dormitory_1", "level": 1}],
            self.work,
            self.costs,
        )
        self.assertFalse(value["repeating_day_verified"])

    def test_level_five_dorm_sustains_eighteen_hours(self) -> None:
        value = plan_dormitories(
            self.segments,
            [{"room_id": "dormitory_1", "level": 5}],
            self.work,
            self.costs,
        )
        self.assertTrue(value["repeating_day_verified"])
        assignment = next(item for item in value["assignments"] if item["segment_id"] == "segment_1")
        self.assertEqual(assignment["operators"], ["测试干员"])
        self.assertGreaterEqual(value["operator_flows"]["测试干员"]["daily_margin"], 0)


if __name__ == "__main__":
    unittest.main()
