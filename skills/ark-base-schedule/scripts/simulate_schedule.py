#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Recalculate a solver assignment with global effects and drone flow.

Natural room production is evaluated with all simultaneously working operators.
Drone recovery is then calculated once for the whole base, and model-selected
integer drone allocations are converted into product/order output at three
minutes of base progress per drone. Drone output is collected immediately at
an operation node and therefore does not occupy the between-node warehouse.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from drone_model import (
    drone_metrics_per_drone,
    drone_rules,
    recovered_drones,
    simulate_lmd_order_queue,
)
from efficiency_calculator import EfficiencyCalculator, production_bonus_for_duration
from dormitory_planner import plan_dormitories
from data_loader import operator_index, select_available_skills
from optimizer_common import (
    context_rooms,
    context_roster,
    context_segments,
    factory_base_metrics,
    metric_score,
    objective_profile,
    read_json,
    trading_base_metrics,
    utc_now,
    warehouse_capacity,
    write_json,
)
from right_side_schedule import FACILITIES as RIGHT_SIDE_FACILITIES, assignments_for_context


def _effective_bonus(result: dict[str, Any], fallback: float = 0.0) -> float:
    for key in ("estimated_efficiency_bonus_pct", "effective_efficiency_bonus_pct", "paper_bonus_pct"):
        if result.get(key) is not None:
            return float(result[key])
    return fallback


def _metrics_from_result(
    facility: str,
    product: str,
    result: dict[str, Any],
    fallback_metrics: dict[str, float],
    fallback_bonus: float,
    hours: float = 8.0,
) -> dict[str, float]:
    if result.get("error"):
        return dict(fallback_metrics)
    bonus = production_bonus_for_duration(result, hours)
    if not result.get("time_dependent_bonus_profiles"):
        bonus = _effective_bonus(result, fallback_bonus) + float(
            result.get("staffing_base_bonus_pct", 0.0) or 0.0
        )
    multiplier = max(0.0, 1.0 + bonus / 100.0)
    if facility == "trading_post":
        base = trading_base_metrics(product)
    else:
        base = factory_base_metrics(product)
    metrics = {key: float(value) * multiplier for key, value in base.items()}
    if facility == "factory" and product == "orundum_shard":
        units = float(metrics.get("orundum_shard", 0.0))
        costs = (drone_rules().get("factory_costs_per_unit") or {}).get(product) or {}
        for key, value in costs.items():
            metrics[str(key)] = units * float(value)
    return metrics


def _product_units_key(product_id: str) -> str | None:
    return {
        "pure_gold": "pure_gold",
        "orundum_shard": "orundum_shard",
        "battle_record": "battle_record_units",
    }.get(product_id)


def _warehouse_item_size(product_id: str) -> float:
    from data_loader import load_mechanics

    mechanics = load_mechanics()
    return float((mechanics.get("warehouse_item_size") or {}).get(product_id, {
        "pure_gold": 2,
        "orundum_shard": 3,
        "battle_record": 5,
    }.get(product_id, 1)))


def _combo_lookup(library: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for room_id, room_result in (library.get("rooms") or {}).items():
        for combo in room_result.get("combinations") or []:
            result[(room_id, combo["combination_id"])] = combo
    return result


def _normalise_drone_allocations(items: list[dict[str, Any]] | None) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items or []:
        count = float(item.get("drones", item.get("value", 0.0)) or 0.0)
        if count <= 1e-9:
            continue
        copy = dict(item)
        copy["drones"] = count
        result[str(item.get("segment_id") or "")].append(copy)
    return result


def resource_sustainability(
    shard_balance: float,
    pure_gold_balance: float,
    *,
    inventory: dict[str, Any] | None = None,
    horizon: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify whether a daily resource flow can repeat from configured stock."""
    stock = inventory or {}
    balances = {
        "orundum_shard": float(shard_balance),
        "pure_gold": float(pure_gold_balance),
    }
    stock_keys = {
        "orundum_shard": ("originium_shard", "orundum_shard"),
        "pure_gold": ("pure_gold",),
    }
    daily_drawdown = {key: max(0.0, -value) for key, value in balances.items()}
    runway_days: dict[str, float | None] = {}
    for resource, drawdown in daily_drawdown.items():
        if drawdown <= 1e-9:
            runway_days[resource] = None
            continue
        configured = next((stock.get(key) for key in stock_keys[resource] if key in stock), None)
        runway_days[resource] = None if configured is None else max(0.0, float(configured)) / drawdown

    consuming = [key for key, value in daily_drawdown.items() if value > 1e-9]
    known_runways = [runway_days[key] for key in consuming if runway_days[key] is not None]
    if known_runways and min(known_runways) <= 1e-9:
        overall_runway = 0.0
    elif len(known_runways) == len(consuming) and consuming:
        overall_runway = min(known_runways)
    else:
        overall_runway = None
    repeatable = not consuming
    horizon_value = horizon or {}
    horizon_mode = str(horizon_value.get("mode", "steady_state"))
    required_days = float(horizon_value.get("days", 0.0) or 0.0) if horizon_mode == "finite_days" else None
    if repeatable:
        feasible_for_horizon: bool | None = True
    elif horizon_mode == "steady_state":
        feasible_for_horizon = False
    elif overall_runway is None:
        feasible_for_horizon = None
    else:
        feasible_for_horizon = overall_runway + 1e-9 >= float(required_days or 0.0)
    return {
        "repeatable_without_inventory": repeatable,
        "classification": "sustainable_repeating_day" if repeatable else "inventory_consuming_candidate",
        "daily_drawdown": daily_drawdown,
        "configured_inventory": {
            resource: next((stock.get(key) for key in stock_keys[resource] if key in stock), None)
            for resource in balances
        },
        "runway_days": runway_days,
        "overall_runway_days": overall_runway,
        "horizon_mode": horizon_mode,
        "required_days": required_days,
        "feasible_for_configured_horizon": feasible_for_horizon,
    }


def simulate_assignment(
    context: dict[str, Any],
    library: dict[str, Any],
    assignment: list[dict[str, Any]],
    drone_allocations: list[dict[str, Any]] | None = None,
    drone_inventory: list[dict[str, Any]] | None = None,
    drone_waste: list[dict[str, Any]] | None = None,
    _dormitory_assignments: list[dict[str, Any]] | None = None,
    _dormitory_iteration: int = 0,
    _random_seed: int | None = None,
) -> dict[str, Any]:
    segments = context_segments(context)
    rooms = context_rooms(context)
    roster = {item["name"]: item for item in context_roster(context)}
    lookup = _combo_lookup(library)
    by_segment: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in assignment:
        by_segment[item["segment_id"]].append(item)
    allocations_by_segment = _normalise_drone_allocations(drone_allocations)

    inventory_by_node = {
        int(item.get("node_index", index)): float(item.get("inventory", item.get("value", 0.0)) or 0.0)
        for index, item in enumerate(drone_inventory or [])
    }
    waste_by_segment = {
        str(item.get("segment_id") or ""): float(item.get("waste", item.get("value", 0.0)) or 0.0)
        for item in (drone_waste or [])
    }

    room_results: list[dict[str, Any]] = []
    natural_aggregate: dict[str, float] = defaultdict(float)
    drone_aggregate: dict[str, float] = defaultdict(float)
    aggregate_fixed: dict[str, float] = defaultdict(float)
    warnings: list[str] = []
    operator_work: dict[str, list[bool]] = {name: [] for name in roster}
    operator_morale_costs: dict[str, list[float]] = {name: [] for name in roster}
    operator_hours: dict[str, float] = defaultdict(float)
    selected_combo_by_segment_room: dict[tuple[str, str], dict[str, Any]] = {}
    power_bonus_by_segment: dict[str, float] = defaultdict(float)
    trade_queue_states: dict[str, dict[str, Any]] = {}
    trade_queue_products: dict[str, str] = {}
    queue_drone_metrics: dict[tuple[str, str], dict[str, float]] = {}
    queue_drone_details: dict[tuple[str, str], dict[str, Any]] = {}
    current_morale = {
        name: float(item.get("morale") if item.get("morale") is not None else 24.0)
        for name, item in roster.items()
    }

    trading_post_count = sum(1 for value in rooms.values() if value["facility_id"] == "trading_post")
    power_plant_count = sum(1 for value in rooms.values() if value["facility_id"] == "power_plant")
    base_state = context.get("base_state") or {}
    dormitory_levels = list(base_state.get("dormitory_levels") or [1, 1, 1, 1])
    facility_level_sum = (
        sum(int(value.get("level", 0) or 0) for value in rooms.values())
        + sum(int(value or 0) for value in dormitory_levels)
        + sum(int(value or 0) for value in (base_state.get("right_side_levels") or {}).values())
    )
    settings = ((context.get("objective") or {}).get("preferences") or {}).get("solver") or {}
    drone_capacity = float(settings.get("drone_capacity", drone_rules()["default_capacity"]))
    order_rng = random.Random(_random_seed) if _random_seed is not None else None
    right_side_assignments = {
        item["segment_id"]: item
        for item in assignments_for_context(context)
    }
    dormitory_assignments_by_segment: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in _dormitory_assignments or []:
        dormitory_assignments_by_segment[str(item.get("segment_id") or "")].append(item)

    for segment in segments:
        selected = by_segment.get(segment.segment_id, [])
        all_operators: list[dict[str, Any]] = []
        for item in selected:
            combo = lookup[(item["room_id"], item["combination_id"])]
            facility_id = rooms[item["room_id"]]["facility_id"]
            all_operators.extend(
                dict(
                    op,
                    morale=current_morale.get(str(op.get("name") or ""), op.get("morale", 24.0)),
                    assigned_facility=facility_id,
                    assigned_room_id=item["room_id"],
                )
                for op in (combo.get("operators") or [])
            )
        fixed_right_side = right_side_assignments[segment.segment_id]
        for output_key, facility_id in RIGHT_SIDE_FACILITIES.items():
            for name in fixed_right_side["rooms"][output_key]:
                base = dict(roster[name])
                base.update(
                    morale=current_morale.get(name, base.get("morale", 24.0)),
                    assigned_facility=facility_id,
                    assigned_room_id=output_key,
                )
                all_operators.append(base)
        working_names = {item["name"] for item in all_operators}
        segment_dorm_recovery: dict[str, float] = {}
        for dormitory in dormitory_assignments_by_segment.get(segment.segment_id, []):
            for name in dormitory.get("operators") or []:
                if name in {item["name"] for item in all_operators} or name not in roster:
                    continue
                base = dict(roster[name])
                base.update(
                    morale=current_morale.get(name, base.get("morale", 24.0)),
                    assigned_facility="dormitory",
                    assigned_room_id=str(dormitory.get("dormitory_id") or "dormitory"),
                )
                all_operators.append(base)
                segment_dorm_recovery[name] = float(dormitory.get("base_recovery_per_hour", 0.0) or 0.0)
        dormitory_capacity = len((context.get("base_state") or {}).get("dormitory_levels") or [1, 1, 1, 1]) * 5
        dormitory_occupant_count = min(dormitory_capacity, max(0, len(roster) - len(working_names)))
        segment_morale_costs = {name: 0.0 for name in roster}
        for name in {name for names in fixed_right_side["rooms"].values() for name in names}:
            segment_morale_costs[name] = 1.0
        for name in roster:
            operator_work[name].append(name in working_names)
            if name in working_names:
                operator_hours[name] += segment.hours

        for item in selected:
            room_id = item["room_id"]
            room = rooms[room_id]
            combo = lookup[(room_id, item["combination_id"])]
            active_product = str(combo.get("product_id") or room["product_id"])
            active_room = dict(room, product_id=active_product)
            if room["facility_id"] == "trading_post":
                previous_product = trade_queue_products.get(room_id)
                if previous_product is not None and previous_product != active_product:
                    trade_queue_states.pop(room_id, None)
                trade_queue_products[room_id] = active_product
            selected_combo_by_segment_room[(segment.segment_id, room_id)] = combo
            operators = combo.get("operators") or []
            if room["facility_id"] in {"trading_post", "factory", "power_plant", "control_center"}:
                calculator = EfficiencyCalculator(
                    room["facility_id"],
                    operators,
                    active_product,
                    trading_post_count=trading_post_count,
                    power_plant_count=power_plant_count,
                    drone_capacity=drone_capacity,
                    facility_level=int(room.get("level", 1)),
                    training_room_level=int(
                        (((context.get("base_state") or {}).get("right_side_levels") or {}).get("training_room", 3))
                    ),
                    office_level=int(
                        (((context.get("base_state") or {}).get("right_side_levels") or {}).get("office", 3))
                    ),
                    reception_room_level=int(
                        (((context.get("base_state") or {}).get("right_side_levels") or {}).get("reception_room", 3))
                    ),
                    dormitory_levels=dormitory_levels,
                    dormitory_occupant_count=dormitory_occupant_count,
                    global_operators=all_operators,
                    facility_level_sum=facility_level_sum,
                )
                calculated = calculator.compute()
                for name, rate in calculator.morale_cost_rates().items():
                    segment_morale_costs[name] = rate
            else:
                calculated = {"error": f"calculator_unsupported:{room['facility_id']}", "warnings": []}
            fallback_bonus = float((combo.get("efficiency_result") or {}).get("estimated_efficiency_bonus_pct", 0) or 0)
            trade_queue_result = None

            if not operators and room["facility_id"] in {"factory", "trading_post", "power_plant", "control_center"}:
                # Production, order acquisition, and staffed power-plant bonuses
                # require at least one working operator. The room still exists
                # for electricity/layout purposes, but contributes no output.
                metrics_per_hour = {}
            elif room["facility_id"] == "power_plant":
                # Recovery is one global process. Each occupied plant adds +5%
                # plus its operator skill; the base 10 drones/hour is counted
                # once after all plants have been inspected.
                skill_bonus = _effective_bonus(calculated, fallback_bonus)
                power_bonus_by_segment[segment.segment_id] += (
                    float(drone_rules()["occupied_power_plant_base_bonus_pct"]) + skill_bonus
                )
                metrics_per_hour = {}
            elif room["facility_id"] == "trading_post" and active_product == "lmd_order":
                bonus = _effective_bonus(calculated, fallback_bonus) + float(
                    calculated.get("staffing_base_bonus_pct", 0.0) or 0.0
                )
                node_drones = sum(
                    float(allocation.get("drones", 0.0) or 0.0)
                    for allocation in allocations_by_segment.get(segment.segment_id, [])
                    if str(allocation.get("room_id") or "") == room_id
                )
                crew_signature = "|".join(
                    f"{index}:{op.get('name', '')}@E{op.get('elite', 0)}"
                    for index, op in enumerate(operators)
                )
                queue_result = simulate_lmd_order_queue(
                    int(room.get("level", 1)),
                    combo,
                    elapsed_hours=segment.hours,
                    base_efficiency_bonus_pct=(
                        bonus - float(calculated.get("jaye_e0_proxy_bonus_pct", 0.0) or 0.0)
                    ),
                    order_capacity=int(calculated.get("order_capacity", 10) or 10),
                    state=trade_queue_states.get(room_id),
                    collect_at_start=True,
                    drone_count=node_drones,
                    crew_signature=crew_signature,
                    rng=order_rng,
                )
                trade_queue_states[room_id] = queue_result["state"]
                trade_queue_result = queue_result
                queue_drone_metrics[(segment.segment_id, room_id)] = queue_result["drone_metrics"]
                queue_drone_details[(segment.segment_id, room_id)] = queue_result
                metrics_per_hour = {
                    key: value / segment.hours if segment.hours > 0 else 0.0
                    for key, value in queue_result["natural_metrics"].items()
                }
            else:
                metrics_per_hour = _metrics_from_result(
                    room["facility_id"],
                    active_product,
                    calculated,
                    combo.get("metrics_per_hour") or {},
                    fallback_bonus,
                    segment.hours,
                )

            raw_metrics = {key: value * segment.hours for key, value in metrics_per_hour.items()}
            effective_metrics = dict(raw_metrics)
            overflow = None
            units_key = _product_units_key(active_product)
            if units_key and units_key in raw_metrics:
                capacity = combo.get("warehouse_capacity")
                if capacity is None:
                    capacity = warehouse_capacity(active_room, operators)
                size = _warehouse_item_size(active_product)
                raw_units = float(raw_metrics[units_key])
                max_units = float(capacity) / size if capacity is not None else raw_units
                effective_units = min(raw_units, max_units)
                if effective_units + 1e-9 < raw_units:
                    overflow = {
                        "raw_units": raw_units,
                        "effective_units": effective_units,
                        "warehouse_capacity": capacity,
                        "item_size": size,
                        "lost_units": raw_units - effective_units,
                    }
                    effective_metrics[units_key] = effective_units
                    ratio = effective_units / raw_units if raw_units > 0 else 1.0
                    if units_key == "battle_record_units" and raw_units > 0 and "battle_record_exp" in effective_metrics:
                        effective_metrics["battle_record_exp"] *= ratio
                    if active_product == "orundum_shard":
                        for cost_key in ("lmd_cost", "orirock_cube_consumption"):
                            if cost_key in effective_metrics:
                                effective_metrics[cost_key] *= ratio
                    warnings.append(
                        f"{segment.segment_id}/{room_id} 仓库封顶，损失约 {raw_units - effective_units:.2f} 单位 {active_product}"
                    )
            for key, value in effective_metrics.items():
                natural_aggregate[key] += float(value)
            fixed = combo.get("fixed_metrics") or {}
            for key, value in fixed.items():
                aggregate_fixed[key] += float(value)
            room_results.append(
                {
                    "segment_id": segment.segment_id,
                    "hours": segment.hours,
                    "room_id": room_id,
                    "facility_id": room["facility_id"],
                    "product_id": active_product,
                    "combination_id": combo["combination_id"],
                    "operators": operators,
                    "local_proxy_score_per_hour": combo.get("proxy_score_per_hour", 0),
                    "global_recalculation": calculated,
                    "metrics_per_hour": metrics_per_hour,
                    "raw_metrics": raw_metrics,
                    "effective_metrics": effective_metrics,
                    "warehouse_overflow": overflow,
                    "trade_queue": trade_queue_result,
                }
            )
        for name in roster:
            operator_morale_costs[name].append(segment_morale_costs[name])
            if name in working_names:
                current_morale[name] = max(
                    0.0,
                    current_morale[name] - segment_morale_costs[name] * segment.hours,
                )
            elif name in segment_dorm_recovery:
                current_morale[name] = min(
                    24.0,
                    current_morale[name] + segment_dorm_recovery[name] * segment.hours,
                )

    # Drone recovery and operation-node allocation.
    allocate_drones = bool(settings.get("allocate_drones", True))
    cyclic = bool(settings.get("drone_repeating_day_balance", True))
    drone_timeline: list[dict[str, Any]] = []
    drone_allocation_results: list[dict[str, Any]] = []
    total_recovered = 0.0
    total_used = 0.0
    total_wasted = 0.0
    drone_feasible = True

    if allocate_drones:
        if inventory_by_node:
            current_inventory = inventory_by_node.get(0, 0.0)
        else:
            current_inventory = float(settings.get("initial_drone_stock", drone_capacity))
        first_inventory = current_inventory
        for segment_index, segment in enumerate(segments):
            if inventory_by_node:
                current_inventory = inventory_by_node.get(segment_index, current_inventory)
            allocations = allocations_by_segment.get(segment.segment_id) or []
            used = sum(float(item["drones"]) for item in allocations)
            if used > current_inventory + 1e-5:
                drone_feasible = False
                warnings.append(
                    f"{segment.segment_id} 无人机使用 {used:.2f} 超过节点库存 {current_inventory:.2f}"
                )
            remaining_after_use = current_inventory - used
            if settings.get("empty_drone_inventory_at_each_node") and remaining_after_use >= 1.0 - 1e-5:
                drone_feasible = False
                warnings.append(
                    f"{segment.segment_id} 上线后仍剩余 {remaining_after_use:.2f} 架无人机，未清空可用库存"
                )
            for allocation in allocations:
                room_id = str(allocation.get("room_id") or "")
                combo_id = str(allocation.get("combination_id") or "")
                combo = lookup.get((room_id, combo_id)) or selected_combo_by_segment_room.get((segment.segment_id, room_id))
                room = rooms[room_id]
                active_product = str((combo or {}).get("product_id") or room["product_id"])
                active_room = dict(room, product_id=active_product)
                count = float(allocation["drones"])
                queue_key = (segment.segment_id, room_id)
                if queue_key in queue_drone_metrics:
                    total_allocated = sum(
                        float(item["drones"])
                        for item in allocations
                        if str(item.get("room_id") or "") == room_id
                    )
                    ratio = count / total_allocated if total_allocated > 0 else 0.0
                    metrics = {
                        key: value * ratio
                        for key, value in queue_drone_metrics[queue_key].items()
                    }
                    metrics_per_drone = {
                        key: value / count if count > 0 else 0.0
                        for key, value in metrics.items()
                    }
                else:
                    metrics_per_drone = drone_metrics_per_drone(active_room, combo)
                    metrics = {key: value * count for key, value in metrics_per_drone.items()}
                for key, value in metrics.items():
                    drone_aggregate[key] += float(value)
                drone_allocation_results.append({
                    "segment_id": segment.segment_id,
                    "operation_time": segment.start,
                    "room_id": room_id,
                    "facility_id": room["facility_id"],
                    "product_id": active_product,
                    "combination_id": combo.get("combination_id") if combo else combo_id,
                    "drones": count,
                    "base_minutes_removed": count * float(drone_rules()["acceleration_minutes_per_drone"]),
                    "metrics_per_drone": metrics_per_drone,
                    "metrics": metrics,
                    "collection_assumption": "节点内加速后立即收取，可重复操作，不占用跨节点仓库容量",
                    "trade_queue": queue_drone_details.get(queue_key),
                })
            bonus_pct = power_bonus_by_segment.get(segment.segment_id, 0.0)
            recovered = recovered_drones(segment.hours, bonus_pct)
            raw_end = current_inventory - used + recovered
            model_waste = waste_by_segment.get(segment.segment_id)
            waste = max(0.0, raw_end - drone_capacity) if model_waste is None else model_waste
            end_inventory = min(drone_capacity, raw_end - (model_waste or 0.0)) if model_waste is not None else min(drone_capacity, raw_end)
            if end_inventory < -1e-5:
                drone_feasible = False
                warnings.append(f"{segment.segment_id} 无人机库存复算为负：{end_inventory:.2f}")
            next_expected = (
                inventory_by_node.get((segment_index + 1) % len(segments))
                if cyclic and inventory_by_node
                else inventory_by_node.get(segment_index + 1) if inventory_by_node else None
            )
            if next_expected is not None and abs(end_inventory - next_expected) > 1e-3:
                drone_feasible = False
                warnings.append(
                    f"{segment.segment_id} 无人机库存与MILP状态不一致：复算 {end_inventory:.3f}，模型 {next_expected:.3f}"
                )
            drone_timeline.append({
                "segment_id": segment.segment_id,
                "start": segment.start,
                "end": segment.end,
                "hours": segment.hours,
                "start_inventory": current_inventory,
                "used_at_start": used,
                "power_plant_total_bonus_pct": bonus_pct,
                "recovered_during_segment": recovered,
                "wasted_over_capacity": waste,
                "end_inventory": end_inventory,
            })
            total_used += used
            total_recovered += recovered
            total_wasted += waste
            current_inventory = end_inventory
        if cyclic and abs(current_inventory - first_inventory) > 1e-3:
            drone_feasible = False
            warnings.append(
                f"无人机重复日库存未闭环：首节点 {first_inventory:.3f}，次日首节点 {current_inventory:.3f}"
            )

    # Repeating-day morale trace.
    rest_recovery = float(settings.get("rest_recovery_per_hour", 0.0))
    max_morale = float(settings.get("max_morale", 24.0))
    morale_results: dict[str, Any] = {}
    for name, roster_item in roster.items():
        states = operator_work[name]
        initial = float(roster_item.get("morale") if roster_item.get("morale") is not None else max_morale)
        morale = initial
        minimum = morale
        for state, cost_rate, segment in zip(states, operator_morale_costs[name], segments):
            if state:
                morale -= cost_rate * segment.hours
            else:
                morale = min(max_morale, morale + rest_recovery * segment.hours)
            minimum = min(minimum, morale)
        runs: list[float] = []
        current = 0.0
        for state, segment in zip(states, segments):
            if state:
                current += segment.hours
            elif current:
                runs.append(current)
                current = 0.0
        if current:
            runs.append(current)
        if len(runs) >= 2 and states and states[0] and states[-1]:
            runs[0] += runs[-1]
            runs.pop()
        max_continuous = max(runs or [0.0])
        if minimum < 0:
            warnings.append(f"{name} 的保守心情模拟低于0，请补充宿舍或恢复事件")
        morale_results[name] = {
            "initial": initial,
            "end": morale,
            "minimum": minimum,
            "daily_work_hours": operator_hours[name],
            "max_continuous_work_hours": max_continuous,
            "working_segments": [segment.segment_id for state, segment in zip(states, segments) if state],
        }

    settings = ((context.get("objective") or {}).get("preferences") or {}).get("solver") or {}
    require_dormitory_cycle = bool(settings.get("require_dormitory_cycle", False))
    dormitories = (context.get("facility_configuration") or {}).get("dormitories") or []
    skill_index = operator_index()
    dormitory_support_weights: dict[str, float] = {}
    support_tags = {
        "monster_cooking_per_dorm_level_1",
        "silent_resonance_per_dorm_occupant_1",
        "ave_dorm_heat_1",
    }
    for name, item in roster.items():
        skills = select_available_skills(
            skill_index.get(name, {}), "dormitory", int(item.get("elite", 0)), "", int(item.get("level", 90) or 90)
        )
        if any(support_tags.intersection(skill.get("tags", [])) for skill in skills):
            dormitory_support_weights[name] = 2.0
    dormitory_plan = plan_dormitories(
        segments,
        dormitories,
        operator_work,
        operator_morale_costs,
        ambience=(context.get("base_state") or {}).get("dormitory_ambience"),
        max_morale=max_morale,
        support_weights=dormitory_support_weights,
    ) if require_dormitory_cycle else {
        "enabled": False,
        "feasible": True,
        "repeating_day_verified": False,
        "assignments": [],
        "operator_flows": {},
    }
    if require_dormitory_cycle and not dormitory_plan.get("feasible"):
        warnings.append("宿舍床位或恢复能力不足，重复日心情无法闭环")
    for name, flow in (dormitory_plan.get("operator_flows") or {}).items():
        if name in morale_results:
            morale_results[name].update(flow)

    continuity_matches = 0
    for left, right in zip(segments, segments[1:]):
        left_map = {item["room_id"]: item["combination_id"] for item in by_segment.get(left.segment_id, [])}
        right_map = {item["room_id"]: item["combination_id"] for item in by_segment.get(right.segment_id, [])}
        continuity_matches += sum(1 for room_id, combo_id in left_map.items() if right_map.get(room_id) == combo_id)

    aggregate: dict[str, float] = defaultdict(float)
    for source in (natural_aggregate, drone_aggregate):
        for key, value in source.items():
            aggregate[key] += float(value)
    if allocate_drones:
        aggregate["drone_recovery"] = total_recovered
        aggregate["drone_used"] = total_used
        aggregate["drone_waste"] = total_wasted

    score_metrics = dict(aggregate)
    # Recovered drones are an intermediate resource once allocation is explicit;
    # reward their resulting output, not the same resource twice.
    if allocate_drones:
        score_metrics.pop("drone_recovery", None)
    score_metrics["fixed_lmd"] = aggregate_fixed.get("fixed_lmd_per_trigger", 0.0)
    score_metrics["continuity"] = float(continuity_matches)
    actual_score = metric_score(score_metrics, objective_profile(context))

    shard_balance = aggregate.get("orundum_shard", 0.0) - aggregate.get("orundum_shard_consumption", 0.0)
    net_lmd_balance = aggregate.get("lmd", 0.0) - aggregate.get("lmd_cost", 0.0)
    pure_gold_balance = aggregate.get("pure_gold", 0.0) - aggregate.get("pure_gold_consumption", 0.0)
    balance_evaluation = {
        "orundum_shard": {
            "mode": str(settings.get("orundum_shard_balance_mode", "hard")),
            "target": float(settings.get("minimum_orundum_shard_balance", 0.0)),
            "hard_minimum": settings.get("hard_minimum_orundum_shard_balance"),
            "actual": shard_balance,
            "shortfall": max(0.0, float(settings.get("minimum_orundum_shard_balance", 0.0)) - shard_balance),
        },
        "pure_gold": {
            "mode": str(settings.get("pure_gold_balance_mode", "hard")),
            "target": float(settings.get("minimum_pure_gold_balance", 0.0)),
            "hard_minimum": settings.get("hard_minimum_pure_gold_balance"),
            "actual": pure_gold_balance,
            "shortfall": max(0.0, float(settings.get("minimum_pure_gold_balance", 0.0)) - pure_gold_balance),
        },
    }
    soft_shortfall_penalty = 0.0
    if balance_evaluation["orundum_shard"]["mode"] == "soft":
        soft_shortfall_penalty += balance_evaluation["orundum_shard"]["shortfall"] * float(
            settings.get("orundum_shard_shortfall_penalty", 0.0)
        )
    if balance_evaluation["pure_gold"]["mode"] == "soft":
        soft_shortfall_penalty += balance_evaluation["pure_gold"]["shortfall"] * float(
            settings.get("pure_gold_shortfall_penalty", 0.0)
        )
    actual_score -= soft_shortfall_penalty
    balance_evaluation["soft_shortfall_objective_penalty"] = soft_shortfall_penalty
    sustainability = resource_sustainability(
        shard_balance,
        pure_gold_balance,
        inventory=((context.get("base_state") or {}).get("inventory") or {}),
        horizon=context.get("horizon") or {},
    )
    if shard_balance < -1e-6:
        warnings.append(f"源石碎片经济流为负：{shard_balance:.2f}")
    if net_lmd_balance < -1e-6:
        warnings.append(f"龙门币经济流为负：{net_lmd_balance:.2f}")
    if pure_gold_balance < -1e-6:
        warnings.append(f"赤金经济流为负：{pure_gold_balance:.2f}")

    inventory_source = dict((context.get("base_state") or {}).get("inventory") or {})
    inventory_keys = ("pure_gold", "originium_shard", "lmd", "orirock_cube")
    inventory_state: dict[str, float | None] = {}
    for key in inventory_keys:
        value = inventory_source.get(key)
        inventory_state[key] = None if value is None else float(value)
    minimum_inventory = dict(inventory_state)
    cumulative_variance = {"pure_gold": 0.0, "lmd": 0.0}
    inventory_events: list[dict[str, Any]] = []

    def apply_inventory_event(segment_id: str, timing: str, metrics: dict[str, float]) -> None:
        deltas = {
            "pure_gold": float(metrics.get("pure_gold", 0.0)) - float(metrics.get("pure_gold_consumption", 0.0)),
            "originium_shard": float(metrics.get("orundum_shard", 0.0)) - float(metrics.get("orundum_shard_consumption", 0.0)),
            "lmd": float(metrics.get("lmd", 0.0)) - float(metrics.get("lmd_cost", 0.0)),
            "orirock_cube": -float(metrics.get("orirock_cube_consumption", 0.0)),
        }
        cumulative_variance["pure_gold"] += float(metrics.get("pure_gold_consumption_variance", 0.0) or 0.0)
        cumulative_variance["lmd"] += float(metrics.get("lmd_variance", 0.0) or 0.0)
        for key, delta in deltas.items():
            if inventory_state[key] is not None:
                inventory_state[key] = float(inventory_state[key]) + delta
                prior_minimum = minimum_inventory[key]
                minimum_inventory[key] = min(float(prior_minimum), float(inventory_state[key])) if prior_minimum is not None else inventory_state[key]
        stockout_probability: dict[str, float | None] = {}
        for key in ("pure_gold", "lmd"):
            variance = cumulative_variance[key]
            mean = inventory_state[key]
            if mean is None:
                stockout_probability[key] = None
            elif variance <= 1e-12:
                stockout_probability[key] = 1.0 if mean < 0 else 0.0
            else:
                stockout_probability[key] = 0.5 * math.erfc(float(mean) / math.sqrt(2.0 * variance))
        inventory_events.append({
            "segment_id": segment_id,
            "timing": timing,
            "delta": deltas,
            "balance": dict(inventory_state),
            "cumulative_variance": dict(cumulative_variance),
            "normal_approximation_stockout_probability": stockout_probability,
        })

    for segment in segments:
        node_metrics: dict[str, float] = defaultdict(float)
        interval_metrics: dict[str, float] = defaultdict(float)
        for item in drone_allocation_results:
            if item.get("segment_id") == segment.segment_id:
                for key, value in (item.get("metrics") or {}).items():
                    node_metrics[key] += float(value)
        for item in room_results:
            if item.get("segment_id") == segment.segment_id:
                for key, value in (item.get("effective_metrics") or {}).items():
                    interval_metrics[key] += float(value)
        apply_inventory_event(segment.segment_id, "operation_node_after_drone_acceleration", node_metrics)
        apply_inventory_event(segment.segment_id, "interval_end_collection", interval_metrics)

    inventory_timeline = {
        "model": "operation_node_events_with_interval_end_collection",
        "initial": {key: inventory_source.get(key) for key in inventory_keys},
        "events": inventory_events,
        "minimum_balance": minimum_inventory,
        "known_inventory_stockout": {
            key: (value is not None and float(value) < -1e-9)
            for key, value in minimum_inventory.items()
        },
        "order_overflow_segments": [
            {"segment_id": item["segment_id"], "room_id": item["room_id"]}
            for item in room_results
            if (item.get("trade_queue") or {}).get("queue_full_at_end")
        ],
    }

    output = {
        "schema_version": 2,
        "simulation_type": "segment_global_recalculation_with_trade_queue_and_drone_inventory",
        "simulated_at": utc_now(),
        "actual_objective_score": actual_score,
        "objective_weights": objective_profile(context),
        "aggregate_metrics": dict(aggregate),
        "natural_metrics": dict(natural_aggregate),
        "drone_metrics": dict(drone_aggregate),
        "aggregate_fixed_metrics": dict(aggregate_fixed),
        "orundum_shard_balance": shard_balance,
        "net_lmd_balance": net_lmd_balance,
        "pure_gold_balance": pure_gold_balance,
        "resource_balance_evaluation": balance_evaluation,
        "resource_sustainability": sustainability,
        "inventory_timeline": inventory_timeline,
        "continuity_matches": continuity_matches,
        "room_results": room_results,
        "drone_plan": {
            "enabled": allocate_drones,
            "capacity": drone_capacity,
            "repeating_day_balance": cyclic,
            "empty_inventory_at_each_operation_node": bool(settings.get("empty_drone_inventory_at_each_node", False)),
            "base_recovery_minutes_per_drone": drone_rules()["base_recovery_minutes_per_drone"],
            "acceleration_minutes_per_drone": drone_rules()["acceleration_minutes_per_drone"],
            "total_recovered": total_recovered,
            "total_used": total_used,
            "total_wasted": total_wasted,
            "feasible": drone_feasible,
            "timeline": drone_timeline,
            "allocations": drone_allocation_results,
        },
        "morale": morale_results,
        "dormitory_plan": dormitory_plan,
        "right_side_plan": {
            "source": "project.right_side_schedule",
            "assignments": list(right_side_assignments.values()),
        },
        "warnings": warnings,
        "assumptions": {
            "collection_at_every_operation_node": True,
            "drone_acceleration_collected_immediately_at_node": True,
            "drone_output_ignores_room_efficiency_bonus": True,
            "lmd_drone_output_uses_expected_order_distribution_when_current_order_unknown": True,
            "rest_recovery_per_hour": rest_recovery,
            "initial_morale_default": max_morale,
            "dormitory_base_recovery_formula": "1.5 + 0.1 * level + 0.0004 * ambience",
            "dormitory_support_skill_occupants_included": bool(dormitory_support_weights),
            "random_lmd_order_sequence_not_simulated": True,
        },
    }
    def dormitory_signature(items: list[dict[str, Any]] | None) -> tuple:
        return tuple(sorted(
            (
                str(item.get("segment_id") or ""),
                str(item.get("dormitory_id") or ""),
                tuple(sorted(str(name) for name in item.get("operators") or [])),
            )
            for item in items or []
        ))

    dormitory_changed = (
        dormitory_signature(_dormitory_assignments)
        != dormitory_signature(dormitory_plan.get("assignments"))
    )
    if require_dormitory_cycle and dormitory_plan.get("feasible") and dormitory_changed:
        if _dormitory_iteration < 3:
            refined = simulate_assignment(
                context, library, assignment, drone_allocations, drone_inventory, drone_waste,
                _dormitory_assignments=dormitory_plan.get("assignments") or [],
                _dormitory_iteration=_dormitory_iteration + 1,
                _random_seed=_random_seed,
            )
            return refined
    dormitory_plan["joint_iteration_count"] = _dormitory_iteration + 1
    dormitory_plan["joint_iteration_converged"] = not dormitory_changed
    monte_carlo_trials = max(0, int(settings.get("random_order_trials", 0) or 0))
    if _random_seed is None and monte_carlo_trials:
        base_seed = int(settings.get("random_order_seed", 20260730) or 20260730)
        samples: list[dict[str, Any]] = []
        for trial_index in range(monte_carlo_trials):
            sampled = simulate_assignment(
                context, library, assignment, drone_allocations, drone_inventory, drone_waste,
                _dormitory_assignments=dormitory_plan.get("assignments") or [],
                _dormitory_iteration=3,
                _random_seed=base_seed + trial_index,
            )
            aggregate_sample = sampled.get("aggregate_metrics") or {}
            samples.append({
                "trial": trial_index,
                "seed": base_seed + trial_index,
                "lmd": float(aggregate_sample.get("lmd", 0.0) or 0.0),
                "net_lmd": float(sampled.get("net_lmd_balance", 0.0) or 0.0),
                "pure_gold_consumption": float(aggregate_sample.get("pure_gold_consumption", 0.0) or 0.0),
                "pure_gold_balance": float(sampled.get("pure_gold_balance", 0.0) or 0.0),
                "orundum": float(aggregate_sample.get("orundum", 0.0) or 0.0),
                "overflow_count": len((sampled.get("inventory_timeline") or {}).get("order_overflow_segments") or []),
                "known_inventory_stockout": dict((sampled.get("inventory_timeline") or {}).get("known_inventory_stockout") or {}),
                "order_sequences": [
                    {
                        "segment_id": room_result.get("segment_id"),
                        "room_id": room_result.get("room_id"),
                        "orders": (room_result.get("trade_queue") or {}).get("completed_order_sequence") or [],
                    }
                    for room_result in sampled.get("room_results") or []
                    if room_result.get("trade_queue")
                ],
            })

        def distribution(values: list[float]) -> dict[str, float]:
            ordered = sorted(values)
            def percentile(fraction: float) -> float:
                if not ordered:
                    return 0.0
                position = fraction * (len(ordered) - 1)
                lower = int(math.floor(position))
                upper = int(math.ceil(position))
                if lower == upper:
                    return ordered[lower]
                return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)
            mean = sum(ordered) / len(ordered) if ordered else 0.0
            variance = sum((value - mean) ** 2 for value in ordered) / len(ordered) if ordered else 0.0
            return {"mean": mean, "stddev": math.sqrt(variance), "p05": percentile(0.05), "p50": percentile(0.5), "p95": percentile(0.95)}

        output["random_order_monte_carlo"] = {
            "model": "complete_sampled_order_sequences_with_fixed_schedule",
            "trial_count": monte_carlo_trials,
            "base_seed": base_seed,
            "lmd": distribution([item["lmd"] for item in samples]),
            "net_lmd": distribution([item["net_lmd"] for item in samples]),
            "pure_gold_consumption": distribution([item["pure_gold_consumption"] for item in samples]),
            "pure_gold_balance": distribution([item["pure_gold_balance"] for item in samples]),
            "overflow_trial_rate": sum(item["overflow_count"] > 0 for item in samples) / monte_carlo_trials,
            "known_inventory_stockout_rate": {
                key: sum(bool(item["known_inventory_stockout"].get(key)) for item in samples) / monte_carlo_trials
                for key in inventory_keys
            },
            "sample_sequences": samples[: min(10, len(samples))],
        }
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="复算求解器排班")
    parser.add_argument("context")
    parser.add_argument("combinations")
    parser.add_argument("assignment", help="包含assignments和可选无人机变量的JSON")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    assignment_document = read_json(args.assignment)
    selected = assignment_document.get("selected_solution") or assignment_document
    assignments = (
        selected.get("assignments")
        or assignment_document.get("selected_assignments")
        or []
    )
    value = simulate_assignment(
        read_json(args.context),
        read_json(args.combinations),
        assignments,
        selected.get("drone_allocations") or assignment_document.get("drone_allocations") or [],
        selected.get("drone_inventory") or assignment_document.get("drone_inventory") or [],
        selected.get("drone_waste") or assignment_document.get("drone_waste") or [],
    )
    write_json(args.output, value)
    print(json.dumps({
        "output": str(Path(args.output)),
        "actual_objective_score": value["actual_objective_score"],
        "drone_used": value["drone_plan"]["total_used"],
        "warnings": len(value["warnings"]),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
