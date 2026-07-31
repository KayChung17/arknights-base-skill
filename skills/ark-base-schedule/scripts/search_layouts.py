#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare power-feasible base layouts for an owned roster and economy target.

The outer search can use a small representative profile library, a user-supplied
profile file, or a generated room-level grid. Every configuration is passed to
the same room enumerator, global MILP, drone inventory model and simulator.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from build_combinations import build_library
from data_loader import load_mechanics
from layout_profiles import (
    DEFAULT_RIGHT_SIDE_LEVELS,
    REPRESENTATIVE_PROFILES,
    generate_grid_profiles,
    load_profile_file,
    normalize_profile,
    power_summary as _power_summary,
)
from normalize_input import build_decision_packet
from reproducibility import build_manifest
from solve_schedule import solve_hybrid

# Backward-compatible public name used by existing callers and tests.
COMMON_PROFILES = REPRESENTATIVE_PROFILES

DEFAULT_ECONOMIC_VALUES = {
    "orundum_lmd": 160.0,
    "orundum_shard_shortfall_lmd": 1600.0,
    "pure_gold_shortfall_lmd": 500.0,
}


def economic_utility_lmd(
    orundum: float,
    net_lmd: float,
    shard_balance: float,
    gold_balance: float,
    values: dict[str, float] | None = None,
) -> float:
    """Convert output and inventory drawdown into one LMD-equivalent value."""
    rates = dict(DEFAULT_ECONOMIC_VALUES)
    rates.update(values or {})
    return (
        float(orundum) * float(rates["orundum_lmd"])
        + float(net_lmd)
        - max(0.0, -float(shard_balance)) * float(rates["orundum_shard_shortfall_lmd"])
        - max(0.0, -float(gold_balance)) * float(rates["pure_gold_shortfall_lmd"])
    )


def economic_result_sort_key(item: dict[str, Any]) -> tuple[float, float, float, float, float]:
    return (
        -float(item["economic_utility_lmd_per_day"]),
        -float(item["net_lmd_per_day"]),
        -float(item["orundum_per_day"]),
        -float(item["battle_record_exp_per_day"]),
        float(item["resource_balance_deviation"]),
    )


def power_summary(profile: dict[str, Any], right_side_levels: dict[str, int] | None = None) -> dict[str, float]:
    return _power_summary(profile, right_side_levels=right_side_levels)


def product_splits(
    profile: dict[str, Any],
    *,
    max_orundum_trading_posts: int | None = None,
    max_shard_factories: int | None = None,
    minimum_battle_record_factories: int = 0,
    allow_zero_orundum: bool = False,
) -> list[tuple[int, int, int]]:
    """Return feasible product choices for trading posts and factories.

    At least one LMD trading post and one pure-gold factory are retained. Only
    level-3 rooms can host orundum orders/source-shard recipes. Battle-record
    factories remain disabled by default for backward compatibility; setting a
    positive minimum enables their enumeration.
    """

    profile = normalize_profile(profile)
    tp3 = profile["trading_levels"].count(3)
    f3 = profile["factory_levels"].count(3)
    trading_count = len(profile["trading_levels"])
    factory_count = len(profile["factory_levels"])
    max_tp = min(tp3, trading_count - 1)
    max_f = min(f3, factory_count - 1)
    if max_orundum_trading_posts is not None:
        max_tp = min(max_tp, max(0, int(max_orundum_trading_posts)))
    if max_shard_factories is not None:
        max_f = min(max_f, max(0, int(max_shard_factories)))
    minimum_battle = max(0, int(minimum_battle_record_factories))
    splits = [
        (origin_rooms, shard_factories, battle_record_factories)
        for origin_rooms in range(1, max_tp + 1)
        for shard_factories in range(1, max_f + 1)
        for battle_record_factories in (
            range(minimum_battle, factory_count - shard_factories)
            if minimum_battle > 0
            else (0,)
        )
    ]
    if allow_zero_orundum:
        splits.extend(
            (0, 0, battle_record_factories)
            for battle_record_factories in (
                range(minimum_battle, factory_count)
                if minimum_battle > 0
                else (0,)
            )
        )
    return splits


def facility_configuration(
    profile: dict[str, Any],
    origin_rooms: int,
    shard_factories: int,
    battle_record_factories: int = 0,
) -> dict[str, Any]:
    profile = normalize_profile(profile)
    rooms: dict[str, dict[str, Any]] = {}
    tp_levels = sorted(profile["trading_levels"], reverse=True)
    f_levels = sorted(profile["factory_levels"], reverse=True)
    origin_remaining = int(origin_rooms)
    for index, level in enumerate(tp_levels, start=1):
        product = "orundum_order" if level == 3 and origin_remaining > 0 else "lmd_order"
        if product == "orundum_order":
            origin_remaining -= 1
        rooms[f"trading_post_{index}"] = {"facility_id": "trading_post", "level": level, "product_id": product}
    factory_products = ["pure_gold"] * len(f_levels)
    shard_remaining = int(shard_factories)
    for index, level in enumerate(f_levels):
        if level == 3 and shard_remaining > 0:
            factory_products[index] = "orundum_shard"
            shard_remaining -= 1
    battle_remaining = int(battle_record_factories)
    for index in range(len(f_levels) - 1, -1, -1):
        if factory_products[index] == "pure_gold" and battle_remaining > 0:
            factory_products[index] = "battle_record"
            battle_remaining -= 1
    if shard_remaining or battle_remaining:
        raise ValueError("制造站产品数量超过当前布局可承载范围")
    for index, (level, product) in enumerate(zip(f_levels, factory_products), start=1):
        rooms[f"factory_{index}"] = {"facility_id": "factory", "level": level, "product_id": product}
    for index, level in enumerate(profile["power_plant_levels"], start=1):
        rooms[f"power_plant_{index}"] = {"facility_id": "power_plant", "level": level, "product_id": "drone_recovery"}
    rooms["control_center"] = {"facility_id": "control_center", "level": 5, "product_id": "base_management"}
    return {
        "rooms": rooms,
        "dormitories": [
            {"room_id": f"dormitory_{i + 1}", "level": level}
            for i, level in enumerate(profile["dorm_levels"])
        ],
    }


def build_context(
    roster_path: str | Path,
    profile_id: str,
    profile: dict[str, Any],
    origin_rooms: int,
    shard_factories: int,
    battle_record_factories: int,
    online_times: list[str],
    lmd_floor: float,
    lmd_proxy_floor_slack: float = 0.0,
    proxy_shard_consumption_factor: float = 1.0,
    proxy_gold_consumption_factor: float = 1.0,
    proxy_lmd_cost_factor: float = 1.0,
    opportunity_postprocess_max_iterations: int = 4,
    shard_balance_policy: dict[str, Any] | None = None,
    gold_balance_policy: dict[str, Any] | None = None,
    inventory: dict[str, Any] | None = None,
    horizon: dict[str, Any] | None = None,
    economic_values: dict[str, float] | None = None,
    *,
    drone_capacity: float = 235.0,
    initial_drone_stock: float | None = None,
    right_side_levels: dict[str, int] | None = None,
    minimum_shard_balance: float = 0.0,
    minimum_gold_balance: float = 0.0,
    operator_overrides: dict[str, dict[str, Any]] | None = None,
    right_side_schedule: list[dict[str, list[str]]] | None = None,
) -> dict[str, Any]:
    profile = normalize_profile(profile)
    right = dict(DEFAULT_RIGHT_SIDE_LEVELS)
    right.update(right_side_levels or {})
    horizon_value = dict(horizon or {"mode": "steady_state"})
    cyclic_phase_free = horizon_value.get("mode") == "steady_state" and initial_drone_stock is None
    initial_stock = None if cyclic_phase_free else float(initial_drone_stock if initial_drone_stock is not None else drone_capacity)
    shard_policy = dict(shard_balance_policy or {})
    gold_policy = dict(gold_balance_policy or {})
    preferences = {
        "priority": "lmd_equivalent",
        "solver": {
            "require_resource_balance": True,
            "minimum_orundum_shard_balance": float(minimum_shard_balance),
            "orundum_shard_balance_mode": str(shard_policy.get("mode", "hard")),
            "orundum_shard_shortfall_penalty": float(shard_policy.get("shortfall_penalty", 0.0)),
            "hard_minimum_orundum_shard_balance": shard_policy.get("hard_minimum"),
            "resource_balance_safety_factor": float(proxy_shard_consumption_factor),
            "require_lmd_balance": True,
            "minimum_net_lmd_balance": lmd_floor,
            "lmd_cost_safety_factor": float(proxy_lmd_cost_factor),
            "lmd_proxy_floor_slack": float(lmd_proxy_floor_slack),
            "require_pure_gold_balance": True,
            "pure_gold_consumption_safety_factor": float(proxy_gold_consumption_factor),
            "minimum_pure_gold_balance": float(minimum_gold_balance),
            "pure_gold_balance_mode": str(gold_policy.get("mode", "hard")),
            "pure_gold_shortfall_penalty": float(gold_policy.get("shortfall_penalty", 0.0)),
            "hard_minimum_pure_gold_balance": gold_policy.get("hard_minimum"),
            "allocate_drones": True,
            "drone_repeating_day_balance": True,
            "drone_capacity": float(drone_capacity),
            "initial_drone_stock": initial_stock,
            "initial_state_policy": "cyclic_phase_free" if cyclic_phase_free else "account_snapshot",
            "max_drone_use_per_node": float(drone_capacity),
            "drone_target_products": ["lmd_order", "orundum_order", "pure_gold", "orundum_shard"],
            "forbid_drone_waste": True,
            "empty_drone_inventory_at_each_node": True,
            "repeat_day_continuity": False,
            "require_dormitory_cycle": True,
            "opportunity_postprocess_max_iterations": max(0, int(opportunity_postprocess_max_iterations)),
        },
    }
    context = build_decision_packet(
        roster_path,
        "lmd_equivalent",
        profile["layout"],
        len(online_times),
        preferences,
        online_times,
        operator_overrides,
    )
    context["baseline"] = None
    context["facility_configuration"] = facility_configuration(
        profile,
        origin_rooms,
        shard_factories,
        battle_record_factories,
    )
    context["objective"]["layout_profile"] = profile_id
    context["objective"]["product_split"] = {
        "orundum_trading_posts": origin_rooms,
        "lmd_trading_posts": len(profile["trading_levels"]) - origin_rooms,
        "orundum_shard_factories": shard_factories,
        "pure_gold_factories": len(profile["factory_levels"]) - shard_factories - battle_record_factories,
        "battle_record_factories": battle_record_factories,
    }
    context["base_state"] = {
        "drone_capacity": float(drone_capacity),
        "initial_drone_stock": initial_stock,
        "drone_capacity_source": "user_input_or_cleared_base_progress",
        "power": power_summary(profile, right),
        "fixed_right_side_levels": right,
        "dormitory_levels": profile["dorm_levels"],
        "power_plant_levels": profile["power_plant_levels"],
        "inventory": dict(inventory or {}),
    }
    context["horizon"] = horizon_value
    context["right_side_schedule"] = list(right_side_schedule or [])
    return context


def compact_result(
    profile_id: str,
    profile: dict[str, Any],
    split: tuple[int, int, int],
    result: dict[str, Any],
    *,
    right_side_levels: dict[str, int] | None = None,
    economic_values: dict[str, float] | None = None,
) -> dict[str, Any]:
    selected = result["selected_solution"]
    simulation = selected["simulation"]
    aggregate = simulation["aggregate_metrics"]
    shift_scores: dict[str, float] = {}
    product_hours: dict[str, float] = {}
    room_products: dict[str, set[str]] = {}
    for room_result in simulation.get("room_results") or []:
        segment_id = str(room_result.get("segment_id") or "")
        hours = float(room_result.get("hours", 0.0) or 0.0)
        rate = float(room_result.get("local_proxy_score_per_hour", 0.0) or 0.0)
        shift_scores[segment_id] = shift_scores.get(segment_id, 0.0) + rate * hours
        product_id = str(room_result.get("product_id") or "")
        if product_id:
            product_hours[product_id] = product_hours.get(product_id, 0.0) + hours
            room_products.setdefault(str(room_result.get("room_id") or ""), set()).add(product_id)
    score_values = list(shift_scores.values())
    mean_score = sum(score_values) / len(score_values) if score_values else 0.0
    variance = sum((value - mean_score) ** 2 for value in score_values) / len(score_values) if score_values else 0.0
    shard_balance = float(simulation.get("orundum_shard_balance", 0.0))
    gold_balance = float(simulation.get("pure_gold_balance", 0.0))
    orundum = float(aggregate.get("orundum", 0.0))
    net_lmd = float(simulation.get("net_lmd_balance", 0.0))
    return {
        "profile_id": profile_id,
        "layout": profile["layout"],
        "trading_levels": profile["trading_levels"],
        "factory_levels": profile["factory_levels"],
        "power_plant_levels": profile["power_plant_levels"],
        "dormitory_levels": profile["dorm_levels"],
        "power": power_summary(profile, right_side_levels),
        "product_split": {
            "orundum_trading_posts": split[0],
            "lmd_trading_posts": len(profile["trading_levels"]) - split[0],
            "orundum_shard_factories": split[1],
            "pure_gold_factories": len(profile["factory_levels"]) - split[1] - split[2],
            "battle_record_factories": split[2],
            "role": "initial_room_products_before_per_segment_selection",
        },
        "product_hours": product_hours,
        "product_switching_used": any(len(products) > 1 for products in room_products.values()),
        "products_by_room": {room_id: sorted(products) for room_id, products in room_products.items()},
        "orundum_per_day": orundum,
        "gross_lmd_per_day": float(aggregate.get("lmd", 0.0)),
        "shard_lmd_cost_per_day": float(aggregate.get("lmd_cost", 0.0)),
        "net_lmd_per_day": net_lmd,
        "orundum_shard_balance": shard_balance,
        "pure_gold_balance": gold_balance,
        "resource_balance_deviation": abs(shard_balance) + abs(gold_balance),
        "economic_utility_lmd_per_day": economic_utility_lmd(
            orundum, net_lmd, shard_balance, gold_balance, economic_values
        ),
        "orundum_shard_produced": float(aggregate.get("orundum_shard", 0.0)),
        "orundum_shard_consumed": float(aggregate.get("orundum_shard_consumption", 0.0)),
        "pure_gold_produced": float(aggregate.get("pure_gold", 0.0)),
        "pure_gold_consumed": float(aggregate.get("pure_gold_consumption", 0.0)),
        "battle_record_exp_per_day": float(aggregate.get("battle_record_exp", 0.0)),
        "drones_recovered": float((simulation.get("drone_plan") or {}).get("total_recovered", 0.0)),
        "drones_used": float((simulation.get("drone_plan") or {}).get("total_used", 0.0)),
        "drones_wasted": float((simulation.get("drone_plan") or {}).get("total_wasted", 0.0)),
        "actual_objective_score": float(simulation.get("actual_objective_score", 0.0)),
        "shift_profile": {
            "scores": shift_scores,
            "minimum_score": min(score_values) if score_values else 0.0,
            "variance": variance,
            "source": "simulation.room_results.local_proxy_score_per_hour",
        },
        "optimality_claim": result.get("solver", {}).get("optimality_claim"),
        "plan": result.get("candidate_plan"),
        "solver_result": result,
    }


def resolve_profiles(
    *,
    profile_mode: str,
    profiles: list[str] | None,
    profiles_file: str | Path | None,
    grid_layouts: list[str] | None,
    dorm_levels: list[int] | None,
    right_side_levels: dict[str, int],
    max_profiles: int | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if profiles_file:
        loaded = load_profile_file(profiles_file)
        if profiles:
            loaded = {key: loaded[key] for key in profiles}
        return loaded, {"mode": "custom_file", "profiles_kept": len(loaded), "profiles_truncated": False}
    if profile_mode == "level_grid":
        return generate_grid_profiles(
            layouts=grid_layouts,
            dorm_levels=dorm_levels,
            right_side_levels=right_side_levels,
            max_profiles=max_profiles,
        )
    selected = profiles or list(COMMON_PROFILES)
    return ({key: copy.deepcopy(COMMON_PROFILES[key]) for key in selected}, {
        "mode": "representative",
        "profiles_kept": len(selected),
        "profiles_truncated": False,
    })


def search_layouts(
    roster_path: str | Path,
    *,
    online_times: list[str],
    lmd_floor: float,
    top_k: int = 30,
    operator_pool_size: int = 12,
    time_limit: float = 12.0,
    max_proxy_attempts: int = 4,
    mip_rel_gap: float = 0.01,
    profiles: list[str] | None = None,
    lmd_proxy_floor_slack: float = 0.0,
    proxy_shard_consumption_factor: float = 1.0,
    proxy_gold_consumption_factor: float = 1.0,
    proxy_lmd_cost_factor: float = 1.0,
    opportunity_postprocess_max_iterations: int = 4,
    shard_balance_policy: dict[str, Any] | None = None,
    gold_balance_policy: dict[str, Any] | None = None,
    inventory: dict[str, Any] | None = None,
    horizon: dict[str, Any] | None = None,
    economic_values: dict[str, float] | None = None,
    profile_mode: str = "representative",
    profiles_file: str | Path | None = None,
    grid_layouts: list[str] | None = None,
    max_profiles: int | None = None,
    dorm_levels: list[int] | None = None,
    right_side_levels: dict[str, int] | None = None,
    drone_capacity: float = 235.0,
    initial_drone_stock: float | None = None,
    minimum_shard_balance: float = 0.0,
    minimum_gold_balance: float = 0.0,
    max_orundum_trading_posts: int | None = None,
    max_shard_factories: int | None = None,
    minimum_battle_record_factories: int = 0,
    operator_overrides: dict[str, dict[str, Any]] | None = None,
    right_side_schedule: list[dict[str, list[str]]] | None = None,
    allow_product_switching: bool = False,
    adaptive_candidate_expansion_rounds: int = 2,
    adaptive_candidate_expansion_factor: float = 2.0,
    adaptive_candidate_max_top_k: int | None = None,
    adaptive_candidate_max_operator_pool_size: int | None = None,
    adaptive_rejection_expansion_rounds: int = 1,
    adaptive_rejection_max_top_k: int | None = None,
    adaptive_rejection_max_operator_pool_size: int | None = None,
    random_order_trials: int = 0,
    random_order_seed: int = 20260730,
) -> dict[str, Any]:
    right = dict(DEFAULT_RIGHT_SIDE_LEVELS)
    right.update(right_side_levels or {})
    profile_map, profile_search = resolve_profiles(
        profile_mode=profile_mode,
        profiles=profiles,
        profiles_file=profiles_file,
        grid_layouts=grid_layouts,
        dorm_levels=dorm_levels,
        right_side_levels=right,
        max_profiles=max_profiles,
    )
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    attempted_configurations = 0
    for profile_id, raw_profile in profile_map.items():
        profile = normalize_profile(raw_profile)
        power = power_summary(profile, right)
        if power["spare_power"] < -1e-6:
            failures.append({"profile_id": profile_id, "layout": profile["layout"], "reason": "power_infeasible", "power": power})
            continue
        splits = product_splits(
            profile,
            max_orundum_trading_posts=max_orundum_trading_posts,
            max_shard_factories=max_shard_factories,
            minimum_battle_record_factories=minimum_battle_record_factories,
            allow_zero_orundum=True,
        )
        if not splits:
            failures.append({
                "profile_id": profile_id,
                "layout": profile["layout"],
                "reason": "cannot_keep_orundum_lmd_shard_and_gold_products_together",
                "power": power,
            })
            continue
        if allow_product_switching:
            factory_count = len(profile["factory_levels"])
            if minimum_battle_record_factories > factory_count:
                failures.append({
                    "profile_id": profile_id,
                    "layout": profile["layout"],
                    "reason": "minimum_battle_record_factories_exceeds_factory_count",
                })
                continue
            splits = [(0, 0, minimum_battle_record_factories)]
        for split in splits:
            attempted_configurations += 1
            context = build_context(
                roster_path,
                profile_id,
                profile,
                split[0],
                split[1],
                split[2],
                online_times,
                lmd_floor,
                lmd_proxy_floor_slack,
                proxy_shard_consumption_factor,
                proxy_gold_consumption_factor,
                proxy_lmd_cost_factor,
                opportunity_postprocess_max_iterations,
                shard_balance_policy,
                gold_balance_policy,
                inventory,
                horizon,
                economic_values,
                drone_capacity=drone_capacity,
                initial_drone_stock=initial_drone_stock,
                right_side_levels=right,
                minimum_shard_balance=minimum_shard_balance,
                minimum_gold_balance=minimum_gold_balance,
                operator_overrides=operator_overrides,
                right_side_schedule=right_side_schedule,
            )
            solver_settings = context["objective"]["preferences"]["solver"]
            solver_settings["adaptive_candidate_expansion_rounds"] = max(0, int(adaptive_candidate_expansion_rounds))
            solver_settings["adaptive_candidate_expansion_factor"] = max(1.1, float(adaptive_candidate_expansion_factor))
            if adaptive_candidate_max_top_k is not None:
                solver_settings["adaptive_candidate_max_top_k"] = int(adaptive_candidate_max_top_k)
            if adaptive_candidate_max_operator_pool_size is not None:
                solver_settings["adaptive_candidate_max_operator_pool_size"] = int(adaptive_candidate_max_operator_pool_size)
            solver_settings["adaptive_rejection_expansion_rounds"] = max(0, int(adaptive_rejection_expansion_rounds))
            if adaptive_rejection_max_top_k is not None:
                solver_settings["adaptive_rejection_max_top_k"] = int(adaptive_rejection_max_top_k)
            if adaptive_rejection_max_operator_pool_size is not None:
                solver_settings["adaptive_rejection_max_operator_pool_size"] = int(
                    adaptive_rejection_max_operator_pool_size
                )
            solver_settings["random_order_trials"] = max(0, int(random_order_trials))
            solver_settings["random_order_seed"] = int(random_order_seed)
            if allow_product_switching:
                for room in (context.get("facility_configuration") or {}).get("rooms", {}).values():
                    if room.get("facility_id") == "factory":
                        room["product_options"] = ["pure_gold", "orundum_shard", "battle_record"]
                    elif room.get("facility_id") == "trading_post":
                        room["product_options"] = ["lmd_order", "orundum_order"]
                solver_settings["max_orundum_trading_posts"] = (
                    len(profile["trading_levels"])
                    if max_orundum_trading_posts is None else int(max_orundum_trading_posts)
                )
                solver_settings["max_shard_factories"] = (
                    len(profile["factory_levels"])
                    if max_shard_factories is None else int(max_shard_factories)
                )
                solver_settings["minimum_battle_record_factories"] = int(minimum_battle_record_factories)
            try:
                library = build_library(context, top_k=top_k, operator_pool_size=operator_pool_size, allow_partial=True)
                result = solve_hybrid(
                    context,
                    library=library,
                    top_k=top_k,
                    operator_pool_size=operator_pool_size,
                    top_solutions=1,
                    time_limit=time_limit,
                    mip_rel_gap=mip_rel_gap,
                    max_proxy_attempts=max_proxy_attempts,
                )
                rows.append(compact_result(
                    profile_id,
                    profile,
                    split,
                    result,
                    right_side_levels=right,
                    economic_values=economic_values,
                ))
            except Exception as exc:
                failure = {
                    "profile_id": profile_id,
                    "layout": profile["layout"],
                    "split": {
                        "orundum_trading_posts": split[0],
                        "orundum_shard_factories": split[1],
                        "battle_record_factories": split[2],
                    },
                    "reason": str(exc),
                }
                diagnostics = getattr(exc, "diagnostics", None)
                if isinstance(diagnostics, dict):
                    failure["diagnostics"] = diagnostics
                failures.append(failure)
    rows.sort(key=economic_result_sort_key)
    resolved_economic_values = dict(DEFAULT_ECONOMIC_VALUES)
    resolved_economic_values.update(economic_values or {})
    result = {
        "schema_version": 2,
        "search_type": "outer_layout_configuration_plus_inner_hybrid_schedule_solver",
        "objective": {
            "primary": "maximize_lmd_equivalent_utility",
            "selection_policy": "lmd_equivalent_then_net_lmd_then_orundum",
            "economic_values": resolved_economic_values,
            "default_resource_balance_policy": "soft_shortfall_valued_as_inventory_drawdown",
            "constraints": {
                "minimum_net_lmd_per_day": lmd_floor,
                "minimum_orundum_shard_balance": minimum_shard_balance,
                "minimum_pure_gold_balance": minimum_gold_balance,
                "minimum_battle_record_factories": minimum_battle_record_factories,
                "balance_policy": {
                    "originium_shard": dict(shard_balance_policy or {"mode": "hard"}),
                    "pure_gold": dict(gold_balance_policy or {"mode": "hard"}),
                },
            },
        },
        "base_state": {
            "drone_capacity": float(drone_capacity),
            "initial_drone_stock": None if initial_drone_stock is None else float(initial_drone_stock),
            "initial_state_policy": (
                "cyclic_phase_free"
                if (horizon or {"mode": "steady_state"}).get("mode") == "steady_state" and initial_drone_stock is None
                else "account_snapshot"
            ),
            "right_side_levels": right,
            "inventory": dict(inventory or {}),
            "drone_capacity_depends_on_power_plant_count_or_level": False,
        },
        "online_times": online_times,
        "profile_search": profile_search,
        "search_settings": {
            "top_k_per_room": top_k,
            "operator_pool_size": operator_pool_size,
            "time_limit_per_proxy_model_seconds": time_limit,
            "max_proxy_attempts_per_configuration": max_proxy_attempts,
            "mip_rel_gap": mip_rel_gap,
            "lmd_proxy_floor_slack": lmd_proxy_floor_slack,
            "proxy_shard_consumption_factor": proxy_shard_consumption_factor,
            "proxy_gold_consumption_factor": proxy_gold_consumption_factor,
            "proxy_lmd_cost_factor": proxy_lmd_cost_factor,
            "opportunity_postprocess_max_iterations": opportunity_postprocess_max_iterations,
            "attempted_configurations": attempted_configurations,
            "allow_product_switching": bool(allow_product_switching),
            "random_order_trials": int(random_order_trials),
            "random_order_seed": int(random_order_seed),
        },
        "results": rows,
        "failures": failures,
        "selected": rows[0] if rows else None,
        "limitations": [
            (
                "制造站和贸易站产品可在上线节点切换，切换发生在收取后。"
                if allow_product_switching
                else "产品分配在一天内固定；可通过 allow_product_switching 开启节点切换。"
            ),
            "房间组合库可能截断，最终结论是当前搜索设置中的最高分候选。",
            (
                "level_grid枚举了选定布局的房间等级多重集，但求解前profile数量发生截断。"
                if profile_search.get("profiles_truncated")
                else "profile搜索范围由profile_mode与用户配置决定。"
            ),
        ],
    }
    result["reproducibility"] = build_manifest(
        run_type="layout_search",
        extra={
            "feasible_results": len(rows),
            "failures": len(failures),
            "selected_profile": rows[0]["profile_id"] if rows else None,
        },
    )
    return result


def _json_object(value: str | None, default: dict[str, int]) -> dict[str, int]:
    if not value:
        return dict(default)
    candidate = Path(value)
    parsed = json.loads(candidate.read_text(encoding="utf-8") if candidate.exists() else value)
    if not isinstance(parsed, dict):
        raise ValueError("参数必须是JSON对象")
    return {str(key): int(item) for key, item in parsed.items()}


def _int_list(value: str | None, default: list[int] | None = None) -> list[int] | None:
    if not value:
        return default
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="比较不同基建布局的合成玉与龙门币收益")
    parser.add_argument("--roster", required=True)
    parser.add_argument("--online-times", default="08:00,14:00,20:00")
    parser.add_argument("--lmd-floor", type=float, default=0.0)
    parser.add_argument("--minimum-shard-balance", type=float, default=0.0)
    parser.add_argument("--minimum-gold-balance", type=float, default=0.0)
    parser.add_argument("--top-k", type=int, default=30)
    parser.add_argument("--operator-pool-size", type=int, default=12)
    parser.add_argument("--time-limit", type=float, default=12.0)
    parser.add_argument("--max-proxy-attempts", type=int, default=4)
    parser.add_argument("--mip-rel-gap", type=float, default=0.01)
    parser.add_argument("--profile-mode", choices=["representative", "level_grid"], default="representative")
    parser.add_argument("--profiles", help="逗号分隔的profile id")
    parser.add_argument("--profiles-file", help="自定义profile JSON文件")
    parser.add_argument("--grid-layouts", help="level_grid模式下要枚举的布局，例如252,342,333")
    parser.add_argument("--max-profiles", type=int, help="level_grid求解前最多保留的profile数量")
    parser.add_argument("--dorm-levels", help="四间宿舍等级，例如1,1,1,1")
    parser.add_argument("--right-side-levels", help="JSON对象或文件，例如{\"office\":3,...}")
    parser.add_argument("--drone-capacity", type=float, default=235.0)
    parser.add_argument("--initial-drone-stock", type=float)
    parser.add_argument("--max-orundum-trading-posts", type=int)
    parser.add_argument("--max-shard-factories", type=int)
    parser.add_argument("--minimum-battle-record-factories", type=int, default=0)
    parser.add_argument("--lmd-proxy-floor-slack", type=float, default=0.0, help="MILP代理龙门币下限相对实际下限的放宽量")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    value = search_layouts(
        args.roster,
        online_times=[item.strip() for item in args.online_times.split(",") if item.strip()],
        lmd_floor=args.lmd_floor,
        minimum_shard_balance=args.minimum_shard_balance,
        minimum_gold_balance=args.minimum_gold_balance,
        top_k=args.top_k,
        operator_pool_size=args.operator_pool_size,
        time_limit=args.time_limit,
        max_proxy_attempts=args.max_proxy_attempts,
        mip_rel_gap=args.mip_rel_gap,
        profiles=[item.strip() for item in args.profiles.split(",") if item.strip()] if args.profiles else None,
        lmd_proxy_floor_slack=args.lmd_proxy_floor_slack,
        profile_mode=args.profile_mode,
        profiles_file=args.profiles_file,
        grid_layouts=[item.strip() for item in args.grid_layouts.split(",") if item.strip()] if args.grid_layouts else None,
        max_profiles=args.max_profiles,
        dorm_levels=_int_list(args.dorm_levels),
        right_side_levels=_json_object(args.right_side_levels, DEFAULT_RIGHT_SIDE_LEVELS),
        drone_capacity=args.drone_capacity,
        initial_drone_stock=args.initial_drone_stock,
        max_orundum_trading_posts=args.max_orundum_trading_posts,
        max_shard_factories=args.max_shard_factories,
        minimum_battle_record_factories=args.minimum_battle_record_factories,
    )
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(path),
        "feasible_results": len(value["results"]),
        "failures": len(value["failures"]),
        "selected": value["selected"] and {
            "profile_id": value["selected"]["profile_id"],
            "layout": value["selected"]["layout"],
            "orundum_per_day": value["selected"]["orundum_per_day"],
            "net_lmd_per_day": value["selected"]["net_lmd_per_day"],
        },
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
