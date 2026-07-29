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


def simulate_assignment(
    context: dict[str, Any],
    library: dict[str, Any],
    assignment: list[dict[str, Any]],
    drone_allocations: list[dict[str, Any]] | None = None,
    drone_inventory: list[dict[str, Any]] | None = None,
    drone_waste: list[dict[str, Any]] | None = None,
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
    queue_drone_metrics: dict[tuple[str, str], dict[str, float]] = {}
    queue_drone_details: dict[tuple[str, str], dict[str, Any]] = {}

    trading_post_count = sum(1 for value in rooms.values() if value["facility_id"] == "trading_post")
    power_plant_count = sum(1 for value in rooms.values() if value["facility_id"] == "power_plant")
    settings = ((context.get("objective") or {}).get("preferences") or {}).get("solver") or {}
    drone_capacity = float(settings.get("drone_capacity", drone_rules()["default_capacity"]))

    for segment in segments:
        selected = by_segment.get(segment.segment_id, [])
        all_operators: list[dict[str, Any]] = []
        for item in selected:
            combo = lookup[(item["room_id"], item["combination_id"])]
            facility_id = rooms[item["room_id"]]["facility_id"]
            all_operators.extend(
                dict(op, assigned_facility=facility_id, assigned_room_id=item["room_id"])
                for op in (combo.get("operators") or [])
            )
        working_names = {item["name"] for item in all_operators}
        dormitory_capacity = len((context.get("base_state") or {}).get("dormitory_levels") or [1, 1, 1, 1]) * 5
        dormitory_occupant_count = min(dormitory_capacity, max(0, len(roster) - len(working_names)))
        segment_morale_costs = {name: 0.0 for name in roster}
        for name in roster:
            operator_work[name].append(name in working_names)
            if name in working_names:
                operator_hours[name] += segment.hours

        for item in selected:
            room_id = item["room_id"]
            room = rooms[room_id]
            combo = lookup[(room_id, item["combination_id"])]
            selected_combo_by_segment_room[(segment.segment_id, room_id)] = combo
            operators = combo.get("operators") or []
            if room["facility_id"] in {"trading_post", "factory", "power_plant", "control_center"}:
                calculator = EfficiencyCalculator(
                    room["facility_id"],
                    operators,
                    room["product_id"],
                    trading_post_count=trading_post_count,
                    power_plant_count=power_plant_count,
                    drone_capacity=drone_capacity,
                    facility_level=int(room.get("level", 1)),
                    training_room_level=int(
                        (((context.get("base_state") or {}).get("right_side_levels") or {}).get("training_room", 3))
                    ),
                    dormitory_levels=list((context.get("base_state") or {}).get("dormitory_levels") or [1, 1, 1, 1]),
                    dormitory_occupant_count=dormitory_occupant_count,
                    global_operators=all_operators,
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
            elif room["facility_id"] == "trading_post" and room["product_id"] == "lmd_order":
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
                    room["product_id"],
                    calculated,
                    combo.get("metrics_per_hour") or {},
                    fallback_bonus,
                    segment.hours,
                )

            raw_metrics = {key: value * segment.hours for key, value in metrics_per_hour.items()}
            effective_metrics = dict(raw_metrics)
            overflow = None
            units_key = _product_units_key(room["product_id"])
            if units_key and units_key in raw_metrics:
                capacity = combo.get("warehouse_capacity")
                if capacity is None:
                    capacity = warehouse_capacity(room, operators)
                size = _warehouse_item_size(room["product_id"])
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
                    if room["product_id"] == "orundum_shard":
                        for cost_key in ("lmd_cost", "orirock_cube_consumption"):
                            if cost_key in effective_metrics:
                                effective_metrics[cost_key] *= ratio
                    warnings.append(
                        f"{segment.segment_id}/{room_id} 仓库封顶，损失约 {raw_units - effective_units:.2f} 单位 {room['product_id']}"
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
                    "product_id": room["product_id"],
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
            for allocation in allocations:
                room_id = str(allocation.get("room_id") or "")
                combo_id = str(allocation.get("combination_id") or "")
                combo = lookup.get((room_id, combo_id)) or selected_combo_by_segment_room.get((segment.segment_id, room_id))
                room = rooms[room_id]
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
                    metrics_per_drone = drone_metrics_per_drone(room, combo)
                    metrics = {key: value * count for key, value in metrics_per_drone.items()}
                for key, value in metrics.items():
                    drone_aggregate[key] += float(value)
                drone_allocation_results.append({
                    "segment_id": segment.segment_id,
                    "operation_time": segment.start,
                    "room_id": room_id,
                    "facility_id": room["facility_id"],
                    "product_id": room["product_id"],
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
    dormitory_plan = plan_dormitories(
        segments,
        dormitories,
        operator_work,
        operator_morale_costs,
        ambience=(context.get("base_state") or {}).get("dormitory_ambience"),
        max_morale=max_morale,
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
    if shard_balance < -1e-6:
        warnings.append(f"源石碎片经济流为负：{shard_balance:.2f}")
    if net_lmd_balance < -1e-6:
        warnings.append(f"龙门币经济流为负：{net_lmd_balance:.2f}")
    if pure_gold_balance < -1e-6:
        warnings.append(f"赤金经济流为负：{pure_gold_balance:.2f}")

    return {
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
        "continuity_matches": continuity_matches,
        "room_results": room_results,
        "drone_plan": {
            "enabled": allocate_drones,
            "capacity": drone_capacity,
            "repeating_day_balance": cyclic,
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
        "warnings": warnings,
        "assumptions": {
            "collection_at_every_operation_node": True,
            "drone_acceleration_collected_immediately_at_node": True,
            "drone_output_ignores_room_efficiency_bonus": True,
            "lmd_drone_output_uses_expected_order_distribution_when_current_order_unknown": True,
            "rest_recovery_per_hour": rest_recovery,
            "initial_morale_default": max_morale,
            "dormitory_base_recovery_formula": "1.5 + 0.1 * level + 0.0004 * ambience",
            "dormitory_manager_bonus_included": False,
            "random_lmd_order_sequence_not_simulated": True,
        },
    }


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
