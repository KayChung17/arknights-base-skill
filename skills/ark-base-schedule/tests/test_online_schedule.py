import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from online_schedule import candidate_online_times


def test_fixed_times_are_preserved():
    assert candidate_online_times(3, mode="fixed", fixed_times=["08:00", "14:00", "20:00"]) == [["08:00", "14:00", "20:00"]]


def test_optimized_times_have_requested_count_and_grid():
    candidates = candidate_online_times(3, mode="optimize", step_minutes=120, max_candidates=8)
    assert len(candidates) == 8
    assert all(len(item) == 3 for item in candidates)
    assert all(int(value[:2]) % 2 == 0 for item in candidates for value in item)
