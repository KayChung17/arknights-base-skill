#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Drone recovery, acceleration, and per-target yield mechanics.

Verified mechanics used by the model:

* Base recovery is one drone per six minutes: 10 drones/hour.
* Every occupied power plant contributes a built-in +5% recovery bonus.
* Power-plant operator bonuses add to the same global recovery-rate pool.
* One drone removes three minutes of *base* manufacturing/order time.
  Room productivity/order-efficiency bonuses do not multiply drone output.

The helper exposes both optimization coefficients and an explainable calculator.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable

from data_loader import load_mechanics
from optimizer_common import factory_base_metrics, trading_base_metrics, read_json, write_json


def drone_rules() -> dict[str, Any]:
    mechanics = load_mechanics()
    rules = dict(mechanics.get("drone_model") or {})
    rules.setdefault("base_recovery_minutes_per_drone", 6.0)
    rules.setdefault("acceleration_minutes_per_drone", 3.0)
    rules.setdefault("occupied_power_plant_base_bonus_pct", 5.0)
    # Drone carrying capacity is determined by cleared base areas, not by the
    # number or level of power plants. A fully cleared base has capacity 235.
    rules.setdefault("capacity_source", "cleared_base_areas")
    rules.setdefault("capacity_depends_on_power_plants", False)
    rules.setdefault("fully_cleared_capacity", 235)
    rules.setdefault("default_capacity", rules["fully_cleared_capacity"])
    rules.setdefault("factory_base_minutes", {
        "pure_gold": 72.0,
        "orundum_shard": 60.0,
        "battle_record": 180.0,
    })
    rules.setdefault("factory_costs_per_unit", {
        "orundum_shard": {"lmd_cost": 1600.0, "orirock_cube_consumption": 2.0},
    })
    rules.setdefault("lmd_order_distributions", {
        "1": [{"probability": 1.0, "minutes": 144.0, "pure_gold": 2.0, "lmd": 1000.0}],
        "2": [
            {"probability": 0.6, "minutes": 144.0, "pure_gold": 2.0, "lmd": 1000.0},
            {"probability": 0.4, "minutes": 210.0, "pure_gold": 3.0, "lmd": 1500.0},
        ],
        "3": [
            {"probability": 0.3, "minutes": 144.0, "pure_gold": 2.0, "lmd": 1000.0},
            {"probability": 0.5, "minutes": 210.0, "pure_gold": 3.0, "lmd": 1500.0},
            {"probability": 0.2, "minutes": 276.0, "pure_gold": 4.0, "lmd": 2000.0},
        ],
    })
    rules.setdefault("orundum_order", {
        "minutes": 120.0,
        "orundum_shard_consumption": 2.0,
        "orundum": 20.0,
    })
    rules.setdefault("closure_special_order", {
        "minutes": 144.0,
        "pure_gold": 2.0,
        "lmd": 1200.0,
    })
    rules.setdefault("pepe_exclusive_order", {
        "minutes": 270.0,
        "pure_gold": 0.0,
        "lmd": 1000.0,
    })
    return rules


def equivalent_base_hours_per_drone() -> float:
    return float(drone_rules()["acceleration_minutes_per_drone"]) / 60.0


def recovery_rate_per_hour(total_bonus_pct: float = 0.0) -> float:
    rules = drone_rules()
    base = 60.0 / float(rules["base_recovery_minutes_per_drone"])
    return base * (1.0 + float(total_bonus_pct) / 100.0)


def recovery_minutes_for_drones(drone_count: float, total_bonus_pct: float = 0.0) -> float:
    rate = recovery_rate_per_hour(total_bonus_pct)
    return 60.0 * float(drone_count) / rate if rate > 0 else math.inf


def recovered_drones(hours: float, total_bonus_pct: float = 0.0) -> float:
    return float(hours) * recovery_rate_per_hour(total_bonus_pct)


def drones_for_base_minutes(base_minutes: float) -> int:
    minutes_per_drone = float(drone_rules()["acceleration_minutes_per_drone"])
    return int(math.ceil(max(0.0, float(base_minutes)) / minutes_per_drone - 1e-12))


def accelerated_base_minutes(drone_count: float) -> float:
    return float(drone_count) * float(drone_rules()["acceleration_minutes_per_drone"])


def _operator_names(combo: dict[str, Any] | None) -> set[str]:
    return {str(item.get("name") or "") for item in ((combo or {}).get("operators") or [])}


def _operator_elite(combo: dict[str, Any] | None, name: str) -> int:
    for item in ((combo or {}).get("operators") or []):
        if str(item.get("name") or "") == name:
            try:
                return int(item.get("elite", 0) or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def _weighted_order(distribution: Iterable[dict[str, Any]]) -> dict[str, float]:
    total_probability = 0.0
    totals = {"minutes": 0.0, "pure_gold": 0.0, "lmd": 0.0}
    for item in distribution:
        probability = float(item.get("probability", 0.0))
        total_probability += probability
        for key in totals:
            totals[key] += probability * float(item.get(key, 0.0))
    if total_probability <= 0:
        raise ValueError("订单概率分布为空")
    return {key: value / total_probability for key, value in totals.items()}


def expected_lmd_order(room_level: int, combo: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return expected order attributes used for drone acceleration.

    Drones advance the current order. If the user supplies an exact current
    order state, the simulator can override these expected values. Otherwise,
    the room-level order distribution and structured special-order operators
    provide the expectation.
    """

    rules = drone_rules()
    names = _operator_names(combo)

    # Special-order priority follows the game rule table: Pepe > Closure >
    # Eureka/U-Official > Proviso > Tequila. Only mechanics with verified
    # numeric attributes are transformed here.
    if "佩佩" in names:
        order = dict(rules["pepe_exclusive_order"])
        order.update({
            "model": "pepe_exclusive_order",
            "probabilistic": False,
            "efficiency_affected": False,
        })
        return order
    if "可露希尔" in names:
        order = dict(rules["closure_special_order"])
        order.update({
            "model": "closure_special_order",
            "probabilistic": False,
            "efficiency_affected": True,
        })
        return order

    distribution = [dict(item) for item in rules["lmd_order_distributions"].get(str(room_level), [])]
    if not distribution:
        distribution = [dict(item) for item in rules["lmd_order_distributions"]["1"]]

    # U-Official forces two-gold orders and explicitly prevents breach status.
    if "U-Official" in names:
        distribution = [{"probability": 1.0, "minutes": 144.0, "pure_gold": 2.0, "lmd": 1000.0}]
        order = _weighted_order(distribution)
        order.update({
            "model": "u_official_two_gold_order",
            "probabilistic": False,
            "efficiency_affected": True,
        })
        return order

    # Proviso changes delivery quantity/reward without changing original order
    # duration. E2 adds two gold/1000 LMD to orders below four gold; the lower
    # version adds one gold/500 LMD. Tequila then adds LMD only to original
    # non-breach four-gold orders.
    transformed = []
    proviso_active = "但书" in names
    tequila_active = "龙舌兰" in names
    proviso_extra = 2.0 if _operator_elite(combo, "但书") >= 2 else 1.0
    tequila_extra_lmd = 500.0 if _operator_elite(combo, "龙舌兰") >= 2 else 250.0
    for item in distribution:
        changed = dict(item)
        original_gold = float(changed.get("pure_gold", 0.0))
        breach = proviso_active and original_gold < 4.0
        if breach:
            changed["pure_gold"] = original_gold + proviso_extra
            changed["lmd"] = float(changed.get("lmd", 0.0)) + 500.0 * proviso_extra
        if tequila_active and original_gold > 3.0 and not breach:
            changed["lmd"] = float(changed.get("lmd", 0.0)) + tequila_extra_lmd
        transformed.append(changed)

    order = _weighted_order(transformed)
    models = []
    if proviso_active:
        models.append("proviso_breach")
    if tequila_active:
        models.append("tequila_investment")
    order.update({
        "model": "+".join(models) + "_order" if models else f"level_{room_level}_base_distribution",
        "probabilistic": len(transformed) > 1,
        "efficiency_affected": True,
    })
    return order


def natural_lmd_metrics_per_hour(
    room_level: int,
    combo: dict[str, Any] | None,
    efficiency_bonus_pct: float,
) -> dict[str, float]:
    """Expected natural LMD order output per hour.

    Most orders are accelerated by the room's order-efficiency multiplier.
    Pepe's exclusive order explicitly ignores order efficiency. Drone output is
    handled separately and always removes base time directly.
    """

    order = expected_lmd_order(room_level, combo)
    multiplier = (
        1.0
        if not bool(order.get("efficiency_affected", True))
        else max(0.0, 1.0 + float(efficiency_bonus_pct) / 100.0)
    )
    orders_per_hour = 60.0 * multiplier / float(order["minutes"])
    return {
        "lmd_trade_work": orders_per_hour,
        "lmd": float(order["lmd"]) * orders_per_hour,
        "pure_gold_consumption": float(order["pure_gold"]) * orders_per_hour,
    }


def drone_metrics_per_drone(room: dict[str, Any], combo: dict[str, Any] | None = None) -> dict[str, float]:
    facility = str(room.get("facility_id") or "")
    product = str(room.get("product_id") or "")
    equivalent_hours = equivalent_base_hours_per_drone()

    if facility == "factory":
        metrics = {key: float(value) * equivalent_hours for key, value in factory_base_metrics(product).items()}
        costs = (drone_rules().get("factory_costs_per_unit") or {}).get(product) or {}
        units_key = {
            "pure_gold": "pure_gold",
            "orundum_shard": "orundum_shard",
            "battle_record": "battle_record_units",
        }.get(product)
        units = float(metrics.get(units_key, 0.0)) if units_key else 0.0
        for key, value in costs.items():
            metrics[str(key)] = float(value) * units
        return metrics

    if facility == "trading_post" and product == "orundum_order":
        return {key: float(value) * equivalent_hours for key, value in trading_base_metrics(product).items()}

    if facility == "trading_post" and product == "lmd_order":
        order = expected_lmd_order(int(room.get("level", 1) or 1), combo)
        drones_per_order = float(order["minutes"]) / float(drone_rules()["acceleration_minutes_per_drone"])
        return {
            "lmd_trade_work": equivalent_hours,
            "lmd": float(order["lmd"]) / drones_per_order,
            "pure_gold_consumption": float(order["pure_gold"]) / drones_per_order,
        }

    return {}


def describe_target(room: dict[str, Any], combo: dict[str, Any] | None = None) -> dict[str, Any]:
    metrics = drone_metrics_per_drone(room, combo)
    product = str(room.get("product_id") or "")
    result: dict[str, Any] = {
        "facility_id": room.get("facility_id"),
        "product_id": product,
        "room_level": room.get("level"),
        "acceleration_minutes_per_drone": drone_rules()["acceleration_minutes_per_drone"],
        "metrics_per_drone": metrics,
    }
    if room.get("facility_id") == "factory":
        minutes = float((drone_rules().get("factory_base_minutes") or {}).get(product, 0.0))
        result.update({
            "base_minutes_per_unit": minutes,
            "drones_per_unit": drones_for_base_minutes(minutes),
        })
    elif room.get("facility_id") == "trading_post" and product == "orundum_order":
        order = drone_rules()["orundum_order"]
        result.update({
            "order_model": "orundum_order",
            "base_minutes_per_order": order["minutes"],
            "drones_per_order": drones_for_base_minutes(float(order["minutes"])),
            "order_reward": {"orundum": order["orundum"]},
            "order_cost": {"orundum_shard": order["orundum_shard_consumption"]},
        })
    elif room.get("facility_id") == "trading_post" and product == "lmd_order":
        order = expected_lmd_order(int(room.get("level", 1) or 1), combo)
        result.update({
            "order_model": order["model"],
            "probabilistic": order["probabilistic"],
            "expected_base_minutes_per_order": order["minutes"],
            "expected_drones_per_order": order["minutes"] / float(drone_rules()["acceleration_minutes_per_drone"]),
            "expected_order_reward": {"lmd": order["lmd"]},
            "expected_order_cost": {"pure_gold": order["pure_gold"]},
        })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="无人机恢复与加速计算器")
    parser.add_argument("--drones", type=float, default=0.0, help="无人机数量")
    parser.add_argument("--recovery-bonus", type=float, default=0.0, help="发电站总充能加成百分比")
    parser.add_argument("--hours", type=float, default=24.0, help="恢复时长")
    parser.add_argument("--room", help="包含facility_id/product_id/level的JSON文件")
    parser.add_argument("--combo", help="可选组合JSON，用于特殊订单")
    parser.add_argument("--output")
    args = parser.parse_args()

    value: dict[str, Any] = {
        "rules": drone_rules(),
        "recovery": {
            "total_bonus_pct": args.recovery_bonus,
            "rate_per_hour": recovery_rate_per_hour(args.recovery_bonus),
            "recovered_in_hours": recovered_drones(args.hours, args.recovery_bonus),
            "minutes_to_recover_requested_drones": recovery_minutes_for_drones(args.drones, args.recovery_bonus),
        },
        "acceleration": {
            "drone_count": args.drones,
            "base_minutes_removed": accelerated_base_minutes(args.drones),
        },
    }
    if args.room:
        room_document = read_json(args.room)
        room = room_document.get("room") or room_document
        combo = None
        if args.combo:
            combo_document = read_json(args.combo)
            combo = combo_document.get("combination") or combo_document
        value["target"] = describe_target(room, combo)
        value["target"]["metrics_for_requested_drones"] = {
            key: metric * args.drones for key, metric in value["target"]["metrics_per_drone"].items()
        }
    if args.output:
        write_json(args.output, value)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
