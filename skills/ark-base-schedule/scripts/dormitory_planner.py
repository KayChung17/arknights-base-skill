#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Allocate fixed dormitory beds and verify a repeating-day morale cycle."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp


def dormitory_base_recovery(level: int, ambience: float | None = None) -> float:
    """Return hourly base recovery at the supplied level and ambience."""
    level = int(level)
    if level not in {1, 2, 3, 4, 5}:
        raise ValueError("宿舍等级必须为1至5")
    cap = 1000.0 * level
    actual_ambience = cap if ambience is None else float(ambience)
    if actual_ambience < 0 or actual_ambience > cap + 1e-6:
        raise ValueError(f"{level}级宿舍氛围必须在0至{cap:.0f}之间")
    return 1.5 + 0.1 * level + 0.0004 * actual_ambience


def plan_dormitories(
    segments: list[Any],
    dormitories: list[dict[str, Any]],
    work_states: dict[str, list[bool]],
    morale_cost_rates: dict[str, list[float]],
    *,
    ambience: list[float] | None = None,
    max_morale: float = 24.0,
) -> dict[str, Any]:
    """Assign active off-duty operators to beds and verify the fixed daily cycle."""
    dorms = list(dormitories or [])
    levels = [int(item.get("level", 0)) for item in dorms]
    if ambience is not None and len(ambience) != len(dorms):
        raise ValueError("dormitory_ambience 必须与宿舍数量一致")
    recoveries = [
        dormitory_base_recovery(level, None if ambience is None else ambience[index])
        for index, level in enumerate(levels)
    ]
    active = sorted(
        name for name, states in work_states.items()
        if any(states)
    )
    consumption = {
        name: sum(
            float(rate) * float(segment.hours)
            for state, rate, segment in zip(work_states[name], morale_cost_rates[name], segments)
            if state
        )
        for name in active
    }
    if not active:
        return {
            "enabled": True,
            "model": "fixed_operation_node_bed_allocation_base_recovery_only",
            "automation_rules_used": False,
            "feasible": True,
            "repeating_day_verified": True,
            "assignments": [],
            "operator_flows": {},
        }
    variables: list[tuple[str, int, int]] = []
    for name in active:
        for segment_index, state in enumerate(work_states[name]):
            if state:
                continue
            for dorm_index in range(len(dorms)):
                variables.append((name, segment_index, dorm_index))

    if active and (not dorms or not variables):
        return {
            "enabled": True,
            "feasible": False,
            "repeating_day_verified": False,
            "reason": "没有可用于工作干员恢复的宿舍床位",
            "assignments": [],
            "operator_flows": {},
        }

    index = {record: position for position, record in enumerate(variables)}
    rows: list[np.ndarray] = []
    lower: list[float] = []
    upper: list[float] = []

    for name in active:
        row = np.zeros(len(variables))
        for segment_index, segment in enumerate(segments):
            for dorm_index, recovery in enumerate(recoveries):
                position = index.get((name, segment_index, dorm_index))
                if position is not None:
                    row[position] = recovery * float(segment.hours)
        rows.append(row)
        lower.append(consumption[name])
        upper.append(np.inf)

    for segment_index, _segment in enumerate(segments):
        for dorm_index, _dorm in enumerate(dorms):
            row = np.zeros(len(variables))
            for name in active:
                position = index.get((name, segment_index, dorm_index))
                if position is not None:
                    row[position] = 1.0
            rows.append(row)
            lower.append(-np.inf)
            upper.append(5.0)
        for name in active:
            row = np.zeros(len(variables))
            for dorm_index in range(len(dorms)):
                position = index.get((name, segment_index, dorm_index))
                if position is not None:
                    row[position] = 1.0
            rows.append(row)
            lower.append(-np.inf)
            upper.append(1.0)

    objective = np.array([
        float(segments[segment_index].hours) + dorm_index * 1e-5
        for _name, segment_index, dorm_index in variables
    ])
    constraint = LinearConstraint(np.vstack(rows), np.array(lower), np.array(upper))
    result = milp(
        objective,
        integrality=np.ones(len(variables)),
        bounds=Bounds(np.zeros(len(variables)), np.ones(len(variables))),
        constraints=constraint,
        options={"presolve": True},
    )
    if result.x is None:
        return {
            "enabled": True,
            "feasible": False,
            "repeating_day_verified": False,
            "reason": "宿舍床位与恢复能力不足以覆盖每日心情消耗",
            "assignments": [],
            "operator_flows": {
                name: {"daily_consumption": consumption[name]}
                for name in active
            },
        }

    selected = {
        record for record, value in zip(variables, result.x)
        if value >= 0.5
    }
    assignments: list[dict[str, Any]] = []
    recovery_by_operator = {name: 0.0 for name in active}
    assigned_rate: dict[tuple[str, int], float] = {}
    for segment_index, segment in enumerate(segments):
        for dorm_index, dorm in enumerate(dorms):
            operators = sorted(
                name for name in active
                if (name, segment_index, dorm_index) in selected
            )
            for name in operators:
                recovery_by_operator[name] += recoveries[dorm_index] * float(segment.hours)
                assigned_rate[(name, segment_index)] = recoveries[dorm_index]
            assignments.append({
                "segment_id": segment.segment_id,
                "dormitory_id": str(dorm.get("room_id") or f"dormitory_{dorm_index + 1}"),
                "level": levels[dorm_index],
                "ambience": 1000.0 * levels[dorm_index] if ambience is None else float(ambience[dorm_index]),
                "base_recovery_per_hour": recoveries[dorm_index],
                "operators": operators,
            })

    flows: dict[str, dict[str, Any]] = {}
    repeating = True
    for name in active:
        morale = float(max_morale)
        minimum = morale
        for _day in range(64):
            start = morale
            for segment_index, segment in enumerate(segments):
                if work_states[name][segment_index]:
                    morale -= float(morale_cost_rates[name][segment_index]) * float(segment.hours)
                else:
                    morale = min(
                        float(max_morale),
                        morale + assigned_rate.get((name, segment_index), 0.0) * float(segment.hours),
                    )
                minimum = min(minimum, morale)
            if abs(morale - start) <= 1e-7:
                break
        feasible = minimum >= -1e-6 and recovery_by_operator[name] + 1e-6 >= consumption[name]
        repeating = repeating and feasible
        flows[name] = {
            "daily_consumption": consumption[name],
            "daily_recovery_capacity": recovery_by_operator[name],
            "daily_margin": recovery_by_operator[name] - consumption[name],
            "cyclic_start": start,
            "cyclic_end": morale,
            "cyclic_minimum": minimum,
            "repeating_day_feasible": feasible,
        }

    return {
        "enabled": True,
        "model": "fixed_operation_node_bed_allocation_base_recovery_only",
        "source": "arknights-mower-schedule-manual-game-mechanics-only",
        "automation_rules_used": False,
        "dorm_manager_bonus_included": False,
        "capacity_per_dormitory": 5,
        "feasible": repeating,
        "repeating_day_verified": repeating,
        "assignments": assignments,
        "operator_flows": flows,
    }
