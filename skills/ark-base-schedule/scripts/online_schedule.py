"""Helpers for fixed or optimizable daily operation nodes."""

from __future__ import annotations

from itertools import combinations


def format_minutes(value: int) -> str:
    value %= 24 * 60
    return f"{value // 60:02d}:{value % 60:02d}"


def candidate_online_times(
    count: int,
    *,
    mode: str = "fixed",
    fixed_times: list[str] | None = None,
    step_minutes: int = 60,
    max_candidates: int = 48,
) -> list[list[str]]:
    """Return deterministic daily node candidates, preserving chronological order."""
    if count < 1 or count > 4:
        raise ValueError("上线次数必须在 1 到 4 次之间")
    if mode == "fixed":
        if not fixed_times or len(fixed_times) != count:
            raise ValueError("固定上线模式必须提供与次数一致的 online_times")
        return [list(fixed_times)]
    if mode != "optimize":
        raise ValueError("online_schedule.mode 必须是 fixed 或 optimize")
    step = int(step_minutes)
    if step <= 0 or (24 * 60) % step:
        raise ValueError("candidate_step_minutes 必须是 1440 的正因数")
    slots = list(range(0, 24 * 60, step))
    if count == 1:
        return [[format_minutes(0)]]

    # Search interval patterns, then rotate them through the day. This covers
    # unequal shifts while keeping candidate count bounded for MILP execution.
    intervals = []
    units = 24 * 60 // step
    for cuts in combinations(range(1, units), count - 1):
        points = (0,) + cuts + (units,)
        gaps = tuple(points[i + 1] - points[i] for i in range(count))
        intervals.append(gaps)
    intervals.sort(key=lambda gaps: (max(gaps) - min(gaps), gaps))
    output: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for gaps in intervals:
        for offset in slots:
            values = []
            cursor = offset
            for gap in gaps:
                values.append(format_minutes(cursor))
                cursor += gap * step
            key = tuple(values)
            if key in seen:
                continue
            seen.add(key)
            output.append(values)
            if len(output) >= max(1, int(max_candidates)):
                return output
    return output
