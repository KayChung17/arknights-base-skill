import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from online_schedule import candidate_online_times
from timeline_utils import rotation_analysis


def test_fixed_times_are_preserved():
    assert candidate_online_times(3, mode="fixed", fixed_times=["08:00", "14:00", "20:00"]) == [["08:00", "14:00", "20:00"]]


def test_optimized_times_have_requested_count_and_grid():
    candidates = candidate_online_times(3, mode="optimize", step_minutes=120, max_candidates=8)
    assert len(candidates) == 8
    assert all(len(item) == 3 for item in candidates)
    assert all(int(value[:2]) % 2 == 0 for item in candidates for value in item)


def test_rotation_analysis_reports_cross_shift_presence():
    plan = {
        "segments": {
            "s1": {"hours": 8, "rooms": {"factory_1": {"operators": [{"name": "甲"}, {"name": "乙"}]}}},
            "s2": {"hours": 8, "rooms": {"factory_1": {"operators": [{"name": "甲"}, {"name": "丙"}]}}},
            "s3": {"hours": 8, "rooms": {"factory_1": {"operators": [{"name": "乙"}, {"name": "丙"}]}}},
        }
    }
    result = rotation_analysis(plan)
    assert result["rooms"][0]["operator_presence"] == {"丙": "011", "乙": "101", "甲": "110"}
