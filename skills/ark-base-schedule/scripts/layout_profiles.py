#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Base-layout profile generation and power accounting.

Representative profiles are fast and useful for normal runs. Grid profiles
enumerate every non-increasing room-level multiset for the selected layouts,
then keep power-feasible configurations. The grid can still be truncated before
solver execution; that truncation is recorded explicitly.
"""

from __future__ import annotations

import json
from itertools import combinations_with_replacement
from pathlib import Path
from typing import Any, Iterable

from data_loader import load_mechanics

DEFAULT_RIGHT_SIDE_LEVELS = {
    "reception_room": 3,
    "office": 3,
    "training_room": 3,
    "workshop": 3,
}

RIGHT_SIDE_FACILITIES = tuple(DEFAULT_RIGHT_SIDE_LEVELS)

REPRESENTATIVE_PROFILES: dict[str, dict[str, Any]] = {
    "153-max": {"layout": "153", "trading_levels": [3], "factory_levels": [3, 3, 3, 3, 3], "power_plant_levels": [3, 3, 3], "dorm_levels": [5, 5, 5, 5]},
    "243-max": {"layout": "243", "trading_levels": [3, 3], "factory_levels": [3, 3, 3, 3], "power_plant_levels": [3, 3, 3], "dorm_levels": [5, 5, 5, 5]},
    "333-max": {"layout": "333", "trading_levels": [3, 3, 3], "factory_levels": [3, 3, 3], "power_plant_levels": [3, 3, 3], "dorm_levels": [5, 5, 5, 5]},
    "423-max": {"layout": "423", "trading_levels": [3, 3, 3, 3], "factory_levels": [3, 3], "power_plant_levels": [3, 3, 3], "dorm_levels": [5, 5, 5, 5]},
    "513-max": {"layout": "513", "trading_levels": [3, 3, 3, 3, 3], "factory_levels": [3], "power_plant_levels": [3, 3, 3], "dorm_levels": [5, 5, 5, 5]},
    "252-output": {"layout": "252", "trading_levels": [3, 3], "factory_levels": [3, 3, 2, 2, 1], "power_plant_levels": [3, 3], "dorm_levels": [1, 1, 1, 1]},
    "342-output": {"layout": "342", "trading_levels": [3, 3, 1], "factory_levels": [3, 3, 2, 2], "power_plant_levels": [3, 3], "dorm_levels": [1, 1, 1, 1]},
    "432-output": {"layout": "432", "trading_levels": [3, 3, 3, 1], "factory_levels": [3, 2, 2], "power_plant_levels": [3, 3], "dorm_levels": [1, 1, 1, 1]},
    "522-output": {"layout": "522", "trading_levels": [3, 3, 3, 1, 1], "factory_levels": [3, 2], "power_plant_levels": [3, 3], "dorm_levels": [1, 1, 1, 1]},
    "351-min": {"layout": "351", "trading_levels": [1, 1, 1], "factory_levels": [1, 1, 1, 1, 1], "power_plant_levels": [3], "dorm_levels": [1, 1, 1, 1]},
    "441-min": {"layout": "441", "trading_levels": [1, 1, 1, 1], "factory_levels": [1, 1, 1, 1], "power_plant_levels": [3], "dorm_levels": [1, 1, 1, 1]},
    "531-min": {"layout": "531", "trading_levels": [1, 1, 1, 1, 1], "factory_levels": [1, 1, 1], "power_plant_levels": [3], "dorm_levels": [1, 1, 1, 1]},
}


def normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    mechanics = load_mechanics()
    layout_id = str(profile["layout"])
    if layout_id not in mechanics["layouts"]:
        raise ValueError(f"未知布局: {layout_id}")
    layout = mechanics["layouts"][layout_id]
    result = {
        "layout": layout_id,
        "trading_levels": sorted([int(x) for x in profile.get("trading_levels", [])], reverse=True),
        "factory_levels": sorted([int(x) for x in profile.get("factory_levels", [])], reverse=True),
        "power_plant_levels": sorted([int(x) for x in profile.get("power_plant_levels", [3] * int(layout["power_plant"]))], reverse=True),
        "dorm_levels": [int(x) for x in profile.get("dorm_levels", [1, 1, 1, 1])],
    }
    expected = {
        "trading_levels": int(layout["trading_post"]),
        "factory_levels": int(layout["factory"]),
        "power_plant_levels": int(layout["power_plant"]),
    }
    for key, count in expected.items():
        if len(result[key]) != count:
            raise ValueError(f"{layout_id} 的 {key} 应有 {count} 项，实际 {len(result[key])} 项")
    for key in ("trading_levels", "factory_levels", "power_plant_levels"):
        if any(level not in {1, 2, 3} for level in result[key]):
            raise ValueError(f"{key} 只支持1至3级")
    if len(result["dorm_levels"]) != 4 or any(level not in {1, 2, 3, 4, 5} for level in result["dorm_levels"]):
        raise ValueError("dorm_levels 必须包含4个1至5级整数")
    return result


def load_profile_file(path: str | Path) -> dict[str, dict[str, Any]]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    profiles = value.get("profiles") if isinstance(value, dict) and "profiles" in value else value
    if not isinstance(profiles, dict):
        raise ValueError("profiles文件必须是 profile_id 到配置对象的映射")
    return {str(key): normalize_profile(item) for key, item in profiles.items()}


def power_summary(
    profile: dict[str, Any],
    *,
    right_side_levels: dict[str, int] | None = None,
) -> dict[str, float]:
    mechanics = load_mechanics()
    model = mechanics["power_model"]
    profile = normalize_profile(profile)
    right = dict(DEFAULT_RIGHT_SIDE_LEVELS)
    right.update(right_side_levels or {})
    supply = sum(float(model["power_plant_supply_by_level"][str(level)]) for level in profile["power_plant_levels"])
    production = sum(float(model["production_room_consumption_by_level"][str(level)]) for level in profile["trading_levels"] + profile["factory_levels"])
    fixed = sum(
        float(model["fixed_facility_consumption_by_level"][name][str(int(right[name]))])
        for name in DEFAULT_RIGHT_SIDE_LEVELS
    )
    dorm = sum(float(model["dormitory_consumption_by_level"][str(level)]) for level in profile["dorm_levels"])
    total = production + fixed + dorm
    return {
        "supply": supply,
        "production_consumption": production,
        "fixed_right_consumption": fixed,
        "dormitory_consumption": dorm,
        "total_consumption": total,
        "spare_power": supply - total,
    }


def fixed_right_power_consumption(right_side_levels: dict[str, int]) -> float:
    """Return irreversible right-side power draw for explicitly confirmed levels."""
    mechanics = load_mechanics()
    model = mechanics["power_model"]["fixed_facility_consumption_by_level"]
    if set(right_side_levels) != set(RIGHT_SIDE_FACILITIES):
        raise ValueError("右侧设施必须完整提供会客室、办公室、训练室和加工站等级")
    return sum(
        float(model[name][str(int(right_side_levels[name]))])
        for name in RIGHT_SIDE_FACILITIES
    )


def facility_configuration_power_summary(
    facility_configuration: dict[str, Any],
    *,
    right_side_levels: dict[str, int],
    expected_layout: str | None = None,
) -> dict[str, float]:
    """Calculate fixed-schedule power from its actual room configuration."""
    rooms = (facility_configuration or {}).get("rooms") or {}
    levels: dict[str, list[int]] = {"trading_post": [], "factory": [], "power_plant": []}
    for room in rooms.values():
        facility = str((room or {}).get("facility_id") or "")
        if facility in levels:
            levels[facility].append(int((room or {}).get("level", 0)))
    layout = f"{len(levels['trading_post'])}{len(levels['factory'])}{len(levels['power_plant'])}"
    if expected_layout and layout != str(expected_layout):
        raise ValueError(f"逐房间配置是 {layout}，与 layout={expected_layout} 不一致")
    dormitories = (facility_configuration or {}).get("dormitories") or []
    profile = {
        "layout": layout,
        "trading_levels": levels["trading_post"],
        "factory_levels": levels["factory"],
        "power_plant_levels": levels["power_plant"],
        "dorm_levels": [int((room or {}).get("level", 0)) for room in dormitories],
    }
    return power_summary(profile, right_side_levels=right_side_levels)


def _level_multisets(count: int, values: Iterable[int] = (1, 2, 3)) -> list[list[int]]:
    return [sorted(items, reverse=True) for items in combinations_with_replacement(values, count)]


def _profile_priority(profile: dict[str, Any], right_side_levels: dict[str, int]) -> tuple:
    power = power_summary(profile, right_side_levels=right_side_levels)
    # For orundum+LMD use, level-3 trade/factory access and extra factories are
    # more valuable structural features than unused power.
    return (
        profile["factory_levels"].count(3),
        profile["trading_levels"].count(3),
        len(profile["factory_levels"]),
        sum(profile["factory_levels"]),
        sum(profile["trading_levels"]),
        -abs(power["spare_power"]),
        profile["layout"],
    )


def generate_grid_profiles(
    *,
    layouts: list[str] | None = None,
    dorm_levels: list[int] | None = None,
    right_side_levels: dict[str, int] | None = None,
    power_plant_levels: dict[str, list[int]] | None = None,
    max_profiles: int | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    mechanics = load_mechanics()
    right = dict(DEFAULT_RIGHT_SIDE_LEVELS)
    right.update(right_side_levels or {})
    selected_layouts = layouts or sorted(mechanics["layouts"])
    dorm = list(dorm_levels or [1, 1, 1, 1])
    generated: list[tuple[str, dict[str, Any]]] = []
    infeasible = 0
    for layout_id in selected_layouts:
        layout = mechanics["layouts"].get(layout_id)
        if not layout:
            raise ValueError(f"未知布局: {layout_id}")
        p_levels = (power_plant_levels or {}).get(layout_id, [3] * int(layout["power_plant"]))
        for trading in _level_multisets(int(layout["trading_post"])):
            for factories in _level_multisets(int(layout["factory"])):
                profile = normalize_profile({
                    "layout": layout_id,
                    "trading_levels": trading,
                    "factory_levels": factories,
                    "power_plant_levels": p_levels,
                    "dorm_levels": dorm,
                })
                if power_summary(profile, right_side_levels=right)["spare_power"] < -1e-9:
                    infeasible += 1
                    continue
                profile_id = (
                    f"{layout_id}-t{''.join(map(str, profile['trading_levels']))}"
                    f"-f{''.join(map(str, profile['factory_levels']))}"
                    f"-p{''.join(map(str, profile['power_plant_levels']))}"
                    f"-d{''.join(map(str, profile['dorm_levels']))}"
                )
                generated.append((profile_id, profile))
    generated.sort(key=lambda item: _profile_priority(item[1], right), reverse=True)
    total_feasible = len(generated)
    if max_profiles is not None:
        generated = generated[: max(1, int(max_profiles))]
    return dict(generated), {
        "mode": "level_grid",
        "layouts_considered": selected_layouts,
        "power_feasible_profiles": total_feasible,
        "power_infeasible_profiles": infeasible,
        "profiles_kept": len(generated),
        "profiles_truncated": len(generated) < total_feasible,
        "right_side_levels": right,
        "dorm_levels": dorm,
    }
