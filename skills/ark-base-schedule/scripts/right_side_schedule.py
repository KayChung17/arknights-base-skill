#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize fixed reception-room and office rotations."""

from __future__ import annotations

from typing import Any

from optimizer_common import context_segments


FACILITIES = {"meeting": "reception_room", "hire": "office"}
CAPACITIES = {"meeting": 2, "hire": 1}


def validate_right_side_schedule(
    schedule: Any,
    *,
    segment_count: int,
    known_names: set[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(schedule, list):
        return ["right_side_schedule 必须是按班次排列的数组"]
    if len(schedule) != segment_count:
        errors.append(f"right_side_schedule 必须包含 {segment_count} 个班次，当前为 {len(schedule)}")
    for index, entry in enumerate(schedule, 1):
        if not isinstance(entry, dict):
            errors.append(f"第 {index} 班右侧安排必须是对象")
            continue
        extras = sorted(set(entry) - set(FACILITIES))
        if extras:
            errors.append(f"第 {index} 班右侧安排包含未知设施: {extras}")
        names_in_shift: list[str] = []
        for key, capacity in CAPACITIES.items():
            names = entry.get(key)
            if not isinstance(names, list) or len(names) != capacity:
                errors.append(f"第 {index} 班 {key} 必须恰好安排 {capacity} 名干员")
                continue
            normalized = [str(name).strip() for name in names]
            if any(not name for name in normalized):
                errors.append(f"第 {index} 班 {key} 包含空干员名")
            names_in_shift.extend(normalized)
            if known_names is not None:
                unknown = sorted(set(normalized) - known_names)
                if unknown:
                    errors.append(f"第 {index} 班 {key} 含练度表外干员: {unknown}")
        duplicates = sorted({name for name in names_in_shift if names_in_shift.count(name) > 1})
        if duplicates:
            errors.append(f"第 {index} 班右侧设施重复进驻: {duplicates}")
    return errors


def assignments_for_context(context: dict[str, Any]) -> list[dict[str, Any]]:
    segments = context_segments(context)
    if "right_side_schedule" not in context:
        return [
            {
                "segment_id": segment.segment_id,
                "start": segment.start,
                "end": segment.end,
                "hours": segment.hours,
                "rooms": {key: [] for key in FACILITIES},
            }
            for segment in segments
        ]
    schedule = context.get("right_side_schedule") or []
    errors = validate_right_side_schedule(
        schedule,
        segment_count=len(segments),
        known_names={str(item.get("name")) for item in context.get("roster") or []},
    )
    if errors:
        raise ValueError("；".join(errors))
    result: list[dict[str, Any]] = []
    for segment, entry in zip(segments, schedule):
        rooms = {
            key: [str(name).strip() for name in entry[key]]
            for key in FACILITIES
        }
        result.append({
            "segment_id": segment.segment_id,
            "start": segment.start,
            "end": segment.end,
            "hours": segment.hours,
            "rooms": rooms,
        })
    return result


def fixed_work_by_segment(context: dict[str, Any]) -> dict[str, set[str]]:
    return {
        item["segment_id"]: {
            name for names in item["rooms"].values() for name in names
        }
        for item in assignments_for_context(context)
    }


def fixed_hours_by_operator(context: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for item in assignments_for_context(context):
        for names in item["rooms"].values():
            for name in names:
                result[name] = result.get(name, 0.0) + float(item["hours"])
    return result
