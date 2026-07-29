#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the global mixed-integer scheduling and drone-allocation model.

The model selects one precomputed room combination for every room and segment.
It enforces same-time operator exclusivity, daily work-hour limits, resource
balance, and optional crew continuity. Drone recovery and use form a closed
inventory flow across operation nodes:

* base recovery: one drone per six minutes;
* each occupied power plant: built-in +5% recovery;
* operator power-plant bonuses: additive to the global recovery rate;
* one drone: three minutes of base manufacturing/order progress;
* drone output is not multiplied by room efficiency.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import Bounds, LinearConstraint
from scipy.sparse import coo_matrix

from data_loader import load_mechanics
from drone_model import drone_metrics_per_drone, drone_rules, recovery_rate_per_hour
from optimizer_common import (
    context_roster,
    context_segments,
    metric_score,
    objective_profile,
    read_json,
    utc_now,
    write_json,
)


@dataclass
class ModelBundle:
    c: np.ndarray
    integrality: np.ndarray
    bounds: Bounds
    constraints: LinearConstraint
    variable_records: list[dict[str, Any]]
    metadata: dict[str, Any]


def _segment_metrics(combo: dict[str, Any], hours: float) -> dict[str, float]:
    metrics = {key: float(value) * hours for key, value in (combo.get("metrics_per_hour") or {}).items()}
    product = combo.get("product_id")
    units_key = {
        "pure_gold": "pure_gold",
        "orundum_shard": "orundum_shard",
        "battle_record": "battle_record_units",
    }.get(product)
    if units_key and units_key in metrics and combo.get("warehouse_capacity") is not None:
        mechanics = load_mechanics()
        item_size = float((mechanics.get("warehouse_item_size") or {}).get(product, 1))
        raw_units = metrics[units_key]
        max_units = float(combo["warehouse_capacity"]) / item_size
        effective_units = min(raw_units, max_units)
        metrics[units_key] = effective_units
        ratio = effective_units / raw_units if raw_units > 0 else 1.0
        if units_key == "battle_record_units" and raw_units > 0 and "battle_record_exp" in metrics:
            metrics["battle_record_exp"] *= ratio
        if product == "orundum_shard":
            for cost_key in ("lmd_cost", "orirock_cube_consumption"):
                if cost_key in metrics:
                    metrics[cost_key] *= ratio
    return metrics


def _solver_settings(context: dict[str, Any]) -> dict[str, Any]:
    preferences = (context.get("objective") or {}).get("preferences") or {}
    settings = dict(preferences.get("solver") or {})
    goal = str((context.get("objective") or {}).get("goal_id") or "")
    priority = str(preferences.get("priority") or "")
    settings.setdefault("max_daily_work_hours", 24.0)
    settings.setdefault("require_resource_balance", goal in {"gold_origin", "all_origin", "max_origin"})
    baseline_delta = 0.0
    baseline = context.get("baseline") or {}
    template = baseline.get("template") or {}
    costs = ((template.get("economy_baseline") or {}).get("costs") or {})
    if goal == "gold_origin":
        try:
            baseline_delta = float(costs.get("orundum_shard_inventory_delta", -4.0))
        except (TypeError, ValueError):
            baseline_delta = -4.0
    settings.setdefault("minimum_orundum_shard_balance", baseline_delta)
    settings.setdefault(
        "resource_balance_safety_factor",
        1.07 if goal in {"gold_origin", "all_origin", "max_origin"} else 1.0,
    )
    settings.setdefault("repeat_day_continuity", False)
    settings.setdefault("require_lmd_balance", priority == "orundum_lmd_balance")
    settings.setdefault("minimum_net_lmd_balance", -5000.0 if priority == "orundum_lmd_balance" else -np.inf)
    settings.setdefault("lmd_cost_safety_factor", 1.0)
    settings.setdefault("lmd_proxy_floor_slack", 3000.0 if priority == "orundum_lmd_balance" else 0.0)
    settings.setdefault("require_pure_gold_balance", priority == "orundum_lmd_balance")
    settings.setdefault("minimum_pure_gold_balance", 0.0)
    settings.setdefault("pure_gold_consumption_safety_factor", 1.0)

    # Drone defaults are a sustainable repeating-day policy. A non-cyclic run
    # can instead set drone_repeating_day_balance=false and initial_drone_stock.
    rules = drone_rules()
    settings.setdefault("allocate_drones", True)
    settings.setdefault("drone_repeating_day_balance", True)
    settings.setdefault("drone_capacity", float(rules.get("default_capacity", 235)))
    settings.setdefault("initial_drone_stock", float(rules.get("default_capacity", 235)))
    settings.setdefault("max_drone_use_per_node", float(rules.get("default_capacity", 235)))
    settings.setdefault(
        "drone_target_products",
        ["lmd_order", "orundum_order", "pure_gold", "orundum_shard", "battle_record"],
    )
    return settings


def _combo_power_bonus_pct(combo: dict[str, Any]) -> float:
    result = combo.get("efficiency_result") or {}
    for key in ("estimated_efficiency_bonus_pct", "paper_bonus_pct"):
        if result.get(key) is not None:
            return float(result[key])
    metrics = combo.get("metrics_per_hour") or {}
    # Legacy fallback: old libraries encoded 1.20 as +20% over a unit base.
    value = metrics.get("drone_recovery")
    if value is not None:
        return max(0.0, (float(value) - 1.0) * 100.0)
    return 0.0


def build_milp(
    context: dict[str, Any],
    library: dict[str, Any],
    *,
    no_good_solutions: list[list[int]] | None = None,
) -> ModelBundle:
    segments = context_segments(context)
    roster = context_roster(context)
    weights = objective_profile(context)
    settings = _solver_settings(context)
    rooms = library.get("rooms") or {}

    variable_records: list[dict[str, Any]] = []
    x_lookup: dict[tuple[str, str, str], int] = {}

    # Assignment variables.
    for segment in segments:
        for room_id, room_result in rooms.items():
            for combo in room_result.get("combinations") or []:
                metrics = _segment_metrics(combo, segment.hours)
                # Once drone allocation is explicit, power-plant recovery is a
                # resource-flow coefficient rather than a separately rewarded
                # output. This prevents double counting.
                if settings.get("allocate_drones") and combo.get("facility_id") == "power_plant":
                    metrics.pop("drone_recovery", None)
                index = len(variable_records)
                x_lookup[(segment.segment_id, room_id, combo["combination_id"])] = index
                variable_records.append(
                    {
                        "kind": "assignment",
                        "segment_id": segment.segment_id,
                        "room_id": room_id,
                        "combination_id": combo["combination_id"],
                        "operators": [item["name"] for item in combo.get("operators") or []],
                        "hours": segment.hours,
                        "objective_coefficient": metric_score(metrics, weights)
                        + float((combo.get("fixed_metrics") or {}).get("fixed_lmd_per_trigger", 0))
                        * float(weights.get("fixed_lmd", 0)),
                    }
                )

    # Reward exact crew continuity across adjacent segments in the same room.
    adjacent_pairs = list(zip(segments, segments[1:]))
    if settings.get("repeat_day_continuity") and len(segments) > 1:
        adjacent_pairs.append((segments[-1], segments[0]))
    continuity_weight = float(weights.get("continuity", 0.0))
    continuity_records: list[tuple[int, int, int]] = []
    if continuity_weight:
        for left, right in adjacent_pairs:
            for room_id, room_result in rooms.items():
                for combo in room_result.get("combinations") or []:
                    left_index = x_lookup[(left.segment_id, room_id, combo["combination_id"])]
                    right_index = x_lookup[(right.segment_id, room_id, combo["combination_id"])]
                    index = len(variable_records)
                    operators = [item["name"] for item in combo.get("operators") or []]
                    variable_records.append(
                        {
                            "kind": "continuity",
                            "left_segment_id": left.segment_id,
                            "right_segment_id": right.segment_id,
                            "room_id": room_id,
                            "combination_id": combo["combination_id"],
                            "operators": operators,
                            "objective_coefficient": continuity_weight * len(operators),
                        }
                    )
                    continuity_records.append((index, left_index, right_index))

    # Drone-allocation profiles are linked to the selected room combination.
    # Combinations with identical per-drone yield share one integer variable,
    # which keeps the MILP small while retaining special-order distinctions.
    drone_allocation_records: list[tuple[int, list[int]]] = []  # (drone var, eligible assignment vars)
    drone_allocation_by_segment: dict[str, list[int]] = {segment.segment_id: [] for segment in segments}
    target_products = {str(value) for value in settings.get("drone_target_products") or []}
    max_drone_use = float(settings.get("max_drone_use_per_node", drone_rules()["default_capacity"]))
    if settings.get("allocate_drones"):
        for segment in segments:
            for room_id, room_result in rooms.items():
                room = room_result.get("room") or {}
                if room.get("facility_id") not in {"factory", "trading_post"}:
                    continue
                if str(room.get("product_id") or "") not in target_products:
                    continue
                profiles: dict[tuple[tuple[str, float], ...], dict[str, Any]] = {}
                for combo in room_result.get("combinations") or []:
                    metrics = drone_metrics_per_drone(room, combo)
                    if not metrics:
                        continue
                    signature = tuple(sorted((str(key), round(float(value), 12)) for key, value in metrics.items()))
                    profile = profiles.setdefault(signature, {"metrics": metrics, "combos": []})
                    profile["combos"].append(combo)
                for profile_number, profile in enumerate(profiles.values(), start=1):
                    combos = profile["combos"]
                    eligible_ids = [combo["combination_id"] for combo in combos]
                    x_indices = [x_lookup[(segment.segment_id, room_id, combo_id)] for combo_id in eligible_ids]
                    d_index = len(variable_records)
                    variable_records.append(
                        {
                            "kind": "drone_allocation",
                            "segment_id": segment.segment_id,
                            "room_id": room_id,
                            "profile_id": f"{room_id}:profile_{profile_number}",
                            "combination_id": None,
                            "eligible_combination_ids": eligible_ids,
                            "metrics_per_drone": profile["metrics"],
                            "objective_coefficient": metric_score(profile["metrics"], weights),
                            "max_value": max_drone_use,
                        }
                    )
                    drone_allocation_records.append((d_index, x_indices))
                    drone_allocation_by_segment[segment.segment_id].append(d_index)

    # Drone stock and overflow variables. In repeating-day mode there is one
    # inventory state at every operation node and the final segment wraps to the
    # first node. In one-shot mode an extra terminal inventory state is used.
    cyclic_drones = bool(settings.get("allocate_drones") and settings.get("drone_repeating_day_balance"))
    inventory_count = len(segments) if cyclic_drones else len(segments) + (1 if settings.get("allocate_drones") else 0)
    drone_inventory_indices: list[int] = []
    drone_waste_indices: list[int] = []
    if settings.get("allocate_drones"):
        for node_index in range(inventory_count):
            index = len(variable_records)
            variable_records.append(
                {
                    "kind": "drone_inventory",
                    "node_index": node_index,
                    "segment_id": segments[node_index].segment_id if node_index < len(segments) else "terminal",
                    "objective_coefficient": 0.0,
                    "max_value": float(settings["drone_capacity"]),
                }
            )
            drone_inventory_indices.append(index)
        for segment in segments:
            index = len(variable_records)
            variable_records.append(
                {
                    "kind": "drone_waste",
                    "segment_id": segment.segment_id,
                    "objective_coefficient": 0.0,
                    "max_value": 0.0 if settings.get("forbid_drone_waste") else float(settings["drone_capacity"]) + recovery_rate_per_hour(100.0) * segment.hours,
                }
            )
            drone_waste_indices.append(index)

    n = len(variable_records)
    c = np.zeros(n, dtype=float)
    for index, record in enumerate(variable_records):
        # scipy.milp minimizes, so maximize by negating the declared score.
        c[index] = -float(record.get("objective_coefficient", 0.0))

    row_indices: list[int] = []
    col_indices: list[int] = []
    values: list[float] = []
    lower: list[float] = []
    upper: list[float] = []
    constraint_records: list[dict[str, Any]] = []

    def add_constraint(coefficients: dict[int, float], lb: float, ub: float, record: dict[str, Any]) -> None:
        row = len(lower)
        for col, value in coefficients.items():
            if value:
                row_indices.append(row)
                col_indices.append(col)
                values.append(float(value))
        lower.append(float(lb))
        upper.append(float(ub))
        constraint_records.append(record)

    # Exactly one combination per room and segment.
    for segment in segments:
        for room_id, room_result in rooms.items():
            coeff = {
                x_lookup[(segment.segment_id, room_id, combo["combination_id"])]: 1.0
                for combo in room_result.get("combinations") or []
            }
            add_constraint(
                coeff,
                1.0,
                1.0,
                {"type": "one_combination", "segment_id": segment.segment_id, "room_id": room_id},
            )

    roster_names = [item["name"] for item in roster]

    # Same-time operator exclusivity.
    for segment in segments:
        for operator in roster_names:
            coeff: dict[int, float] = {}
            for room_id, room_result in rooms.items():
                for combo in room_result.get("combinations") or []:
                    if operator in {item["name"] for item in combo.get("operators") or []}:
                        coeff[x_lookup[(segment.segment_id, room_id, combo["combination_id"])]] = 1.0
            if coeff:
                add_constraint(
                    coeff,
                    -np.inf,
                    1.0,
                    {"type": "operator_exclusivity", "segment_id": segment.segment_id, "operator": operator},
                )

    # Daily work-hour limit.
    default_max_hours = float(settings.get("max_daily_work_hours", 12.0))
    overrides = settings.get("operator_max_daily_hours") or {}
    for operator in roster_names:
        coeff: dict[int, float] = {}
        for segment in segments:
            for room_id, room_result in rooms.items():
                for combo in room_result.get("combinations") or []:
                    if operator in {item["name"] for item in combo.get("operators") or []}:
                        coeff[x_lookup[(segment.segment_id, room_id, combo["combination_id"])]] = segment.hours
        if coeff:
            max_hours = float(overrides.get(operator, default_max_hours))
            add_constraint(
                coeff,
                -np.inf,
                max_hours,
                {"type": "daily_work_hours", "operator": operator, "max_hours": max_hours},
            )

    # Link drone allocation to the selected combination.
    for drone_index, assignment_indices in drone_allocation_records:
        coefficients = {drone_index: 1.0}
        for assignment_index in assignment_indices:
            coefficients[assignment_index] = coefficients.get(assignment_index, 0.0) - max_drone_use
        add_constraint(
            coefficients,
            -np.inf,
            0.0,
            {"type": "drone_allocation_link", "drone_variable": drone_index, "eligible_assignment_count": len(assignment_indices)},
        )

    # Closed drone inventory flow.
    if settings.get("allocate_drones"):
        rules = drone_rules()
        base_rate = recovery_rate_per_hour(0.0)
        occupied_bonus = float(rules.get("occupied_power_plant_base_bonus_pct", 5.0))
        capacity = float(settings["drone_capacity"])
        if not cyclic_drones:
            add_constraint(
                {drone_inventory_indices[0]: 1.0},
                float(settings.get("initial_drone_stock", capacity)),
                float(settings.get("initial_drone_stock", capacity)),
                {"type": "initial_drone_inventory"},
            )
        for segment_index, segment in enumerate(segments):
            current_inventory = drone_inventory_indices[segment_index]
            next_inventory = (
                drone_inventory_indices[(segment_index + 1) % len(segments)]
                if cyclic_drones
                else drone_inventory_indices[segment_index + 1]
            )
            use_indices = drone_allocation_by_segment.get(segment.segment_id) or []
            if use_indices:
                add_constraint(
                    {**{index: 1.0 for index in use_indices}, current_inventory: -1.0},
                    -np.inf,
                    0.0,
                    {"type": "drone_use_available_at_node", "segment_id": segment.segment_id},
                )

            # I_next = I_current - use + base_recovery + bonus_recovery - waste
            coeff: dict[int, float] = {}
            coeff[next_inventory] = coeff.get(next_inventory, 0.0) + 1.0
            coeff[current_inventory] = coeff.get(current_inventory, 0.0) - 1.0
            coeff[drone_waste_indices[segment_index]] = (
                coeff.get(drone_waste_indices[segment_index], 0.0) + 1.0
            )
            for index in use_indices:
                coeff[index] = coeff.get(index, 0.0) + 1.0
            for room_id, room_result in rooms.items():
                room = room_result.get("room") or {}
                if room.get("facility_id") != "power_plant":
                    continue
                for combo in room_result.get("combinations") or []:
                    x_index = x_lookup[(segment.segment_id, room_id, combo["combination_id"])]
                    occupied = int(combo.get("staffed_slots", len(combo.get("operators") or []))) > 0
                    bonus_pct = (occupied_bonus if occupied else 0.0) + _combo_power_bonus_pct(combo)
                    bonus_recovery = base_rate * segment.hours * bonus_pct / 100.0
                    coeff[x_index] = coeff.get(x_index, 0.0) - bonus_recovery
            base_recovery = base_rate * segment.hours
            add_constraint(
                coeff,
                base_recovery,
                base_recovery,
                {"type": "drone_inventory_flow", "segment_id": segment.segment_id},
            )

    # Optional aggregate source-shard resource balance, including drone output
    # and drone-accelerated source orders.
    if settings.get("require_resource_balance"):
        coeff: dict[int, float] = {}
        for segment in segments:
            for room_id, room_result in rooms.items():
                for combo in room_result.get("combinations") or []:
                    metrics = _segment_metrics(combo, segment.hours)
                    balance = float(metrics.get("orundum_shard", 0.0)) - float(
                        settings.get("resource_balance_safety_factor", 1.0)
                    ) * float(metrics.get("orundum_shard_consumption", 0.0))
                    index = x_lookup[(segment.segment_id, room_id, combo["combination_id"])]
                    coeff[index] = balance
        for record_index, record in enumerate(variable_records):
            if record.get("kind") != "drone_allocation":
                continue
            metrics = record.get("metrics_per_drone") or {}
            balance = float(metrics.get("orundum_shard", 0.0)) - float(
                settings.get("resource_balance_safety_factor", 1.0)
            ) * float(metrics.get("orundum_shard_consumption", 0.0))
            coeff[record_index] = balance
        add_constraint(
            coeff,
            float(settings.get("minimum_orundum_shard_balance", 0.0)),
            np.inf,
            {"type": "orundum_shard_balance_including_drones"},
        )

    def add_metric_balance_constraint(
        positive_key: str,
        negative_key: str,
        minimum: float,
        constraint_type: str,
        negative_factor: float = 1.0,
    ) -> None:
        coeff: dict[int, float] = {}
        for segment in segments:
            for room_id, room_result in rooms.items():
                for combo in room_result.get("combinations") or []:
                    metrics = _segment_metrics(combo, segment.hours)
                    balance = float(metrics.get(positive_key, 0.0)) - float(negative_factor) * float(metrics.get(negative_key, 0.0))
                    index = x_lookup[(segment.segment_id, room_id, combo["combination_id"])]
                    coeff[index] = balance
        for record_index, record in enumerate(variable_records):
            if record.get("kind") != "drone_allocation":
                continue
            metrics = record.get("metrics_per_drone") or {}
            coeff[record_index] = float(metrics.get(positive_key, 0.0)) - float(negative_factor) * float(
                metrics.get(negative_key, 0.0)
            )
        add_constraint(coeff, float(minimum), np.inf, {"type": constraint_type})

    if settings.get("require_lmd_balance"):
        add_metric_balance_constraint(
            "lmd",
            "lmd_cost",
            float(settings.get("minimum_net_lmd_balance", 0.0)) - float(settings.get("lmd_proxy_floor_slack", 0.0)),
            "net_lmd_balance_including_drones",
            float(settings.get("lmd_cost_safety_factor", 1.0)),
        )

    if settings.get("require_pure_gold_balance"):
        add_metric_balance_constraint(
            "pure_gold",
            "pure_gold_consumption",
            float(settings.get("minimum_pure_gold_balance", 0.0)),
            "pure_gold_balance_including_drones",
            float(settings.get("pure_gold_consumption_safety_factor", 1.0)),
        )

    # Continuity linearization: y == x_left AND x_right.
    for y_index, left_index, right_index in continuity_records:
        add_constraint(
            {y_index: 1.0, left_index: -1.0},
            -np.inf,
            0.0,
            {"type": "continuity_upper_left", "variable": y_index},
        )
        add_constraint(
            {y_index: 1.0, right_index: -1.0},
            -np.inf,
            0.0,
            {"type": "continuity_upper_right", "variable": y_index},
        )
        add_constraint(
            {y_index: 1.0, left_index: -1.0, right_index: -1.0},
            -1.0,
            np.inf,
            {"type": "continuity_lower", "variable": y_index},
        )

    # Exclude previously returned complete assignment vectors when requesting
    # multiple schedules. Drone allocations may differ when the crew changes.
    for selected in no_good_solutions or []:
        if selected:
            add_constraint(
                {index: 1.0 for index in selected},
                -np.inf,
                float(len(selected) - 1),
                {"type": "no_good_cut", "selected_count": len(selected)},
            )

    matrix = coo_matrix((values, (row_indices, col_indices)), shape=(len(lower), n)).tocsc()
    constraints = LinearConstraint(matrix, np.asarray(lower), np.asarray(upper))

    lb = np.zeros(n, dtype=float)
    ub = np.ones(n, dtype=float)
    integrality = np.ones(n, dtype=int)
    for index, record in enumerate(variable_records):
        kind = record.get("kind")
        if kind in {"assignment", "continuity"}:
            ub[index] = 1.0
            integrality[index] = 1
        elif kind == "drone_allocation":
            ub[index] = float(record.get("max_value", max_drone_use))
            integrality[index] = 1
        elif kind in {"drone_inventory", "drone_waste"}:
            ub[index] = float(record.get("max_value", settings.get("drone_capacity", 235)))
            integrality[index] = 0
        else:
            integrality[index] = 0
    bounds = Bounds(lb, ub)

    metadata = {
        "schema_version": 2,
        "model_type": "mixed_binary_integer_continuous_milp",
        "backend": "scipy.optimize.milp_highs",
        "built_at": utc_now(),
        "variable_count": n,
        "assignment_variable_count": sum(1 for item in variable_records if item["kind"] == "assignment"),
        "continuity_variable_count": sum(1 for item in variable_records if item["kind"] == "continuity"),
        "drone_allocation_variable_count": sum(1 for item in variable_records if item["kind"] == "drone_allocation"),
        "drone_inventory_variable_count": sum(1 for item in variable_records if item["kind"] == "drone_inventory"),
        "constraint_count": len(lower),
        "constraint_summary": {
            key: sum(1 for item in constraint_records if item["type"] == key)
            for key in sorted({item["type"] for item in constraint_records})
        },
        "objective_weights": weights,
        "solver_settings": settings,
        "drone_model": {
            "base_recovery_drones_per_hour": recovery_rate_per_hour(0.0),
            "acceleration_minutes_per_drone": drone_rules()["acceleration_minutes_per_drone"],
            "occupied_power_plant_base_bonus_pct": drone_rules()["occupied_power_plant_base_bonus_pct"],
            "repeating_day_balance": cyclic_drones,
        },
        "search_completeness": library.get("search_completeness") or {},
    }
    return ModelBundle(c, integrality, bounds, constraints, variable_records, metadata)


def model_summary(context: dict[str, Any], library: dict[str, Any]) -> dict[str, Any]:
    bundle = build_milp(context, library)
    return {
        **bundle.metadata,
        "variables": bundle.variable_records,
        "note": "JSON保存模型元数据；数值矩阵在solve_schedule.py运行时重建。",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="构建全局MILP模型元数据")
    parser.add_argument("context")
    parser.add_argument("combinations")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    value = model_summary(read_json(args.context), read_json(args.combinations))
    write_json(args.output, value)
    print(json.dumps({
        "output": str(Path(args.output)),
        "variables": value["variable_count"],
        "constraints": value["constraint_count"],
        "backend": value["backend"],
        "drone_allocation_variables": value["drone_allocation_variable_count"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
