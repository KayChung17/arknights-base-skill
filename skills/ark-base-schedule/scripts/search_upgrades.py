#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare current roster, all-owned max-base-skill ceiling, and targeted upgrades.

The outer layout optimizer is run under the same economy constraints for all
scenarios. The maxed-owned scenario is not a recommendation to level everyone;
it identifies a performance ceiling. Operators whose higher unlocked skill is
actually used by that ceiling plan are extracted, then a minimum-unlock roster
is generated and solved again.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from data_loader import operator_index, read_roster, select_available_skills
from layout_profiles import DEFAULT_RIGHT_SIDE_LEVELS
from reproducibility import build_manifest
from search_layouts import COMMON_PROFILES, search_layouts


def write_roster_tsv(path: str | Path, roster, overrides: dict[str, tuple[int, int]] | None = None) -> None:
    overrides = overrides or {}
    lines = ["干员名称\t是否已招募\t等级\t精英化等级\t当前心情"]
    for op in roster:
        elite, level = overrides.get(op.name, (op.elite, op.level))
        morale = 24 if op.morale is None else op.morale
        lines.append(f"{op.name}\tTRUE\t{int(level)}\t{int(elite)}\t{morale}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def maximum_base_unlock(record: dict[str, Any]) -> tuple[int, int]:
    rarity = int(record.get("rarity", 6) or 6)
    if rarity <= 2:
        return 0, 30
    if rarity == 3:
        return 1, 55
    return 2, 90


def skill_signature(skills: list[dict[str, Any]]) -> tuple:
    return tuple(sorted(
        (
            str(item.get("variant_group") or item.get("skill_name") or ""),
            str(item.get("skill_name") or ""),
            int(item.get("elite", 0)),
            int(item.get("required_level", 1) or 1),
        )
        for item in skills
    ))


PROMOTION_RISK_TAGS = {
    "jaye_order_count_4": "精英1会改变孑的订单数联动，收益不具单调性且不可逆。",
    "dongshi_reset": "精英1会清零同站其他干员的直接生产力，只适合特定容量/设施联动组。",
    "override_room_direct_bonus": "技能会覆盖同站其他直接效率，需要按完整组合验证。",
}


def selected_upgrade_requirements(current_roster, selected: dict[str, Any]) -> list[dict[str, Any]]:
    current = {op.name: op for op in current_roster}
    index = operator_index()
    usage: dict[str, dict[str, Any]] = {}
    plan = selected.get("plan") or {}
    for segment in (plan.get("segments") or {}).values():
        hours = float(segment.get("hours", 0.0))
        for room_id, room in (segment.get("rooms") or {}).items():
            facility = str(room.get("facility_id") or "")
            product = str(room.get("product_id") or "")
            for operator in room.get("operators") or []:
                name = str(operator.get("name") or "")
                if not name or name not in current or name not in index:
                    continue
                record = index[name]
                cur = current[name]
                current_skills = select_available_skills(record, facility, cur.elite, product, cur.level)
                # The selected plan stores the actual operator state used to build
                # the room candidate. Compare against that state rather than the
                # theoretical maximum; otherwise an E0 operator used by the plan
                # can be falsely reported as requiring promotion.
                if "elite" in operator or "level" in operator:
                    target_elite = int(operator.get("elite", cur.elite) or 0)
                    target_level = int(operator.get("level", cur.level) or 1)
                else:
                    # Backward-compatible fallback for plans that omit the state.
                    target_elite, target_level = maximum_base_unlock(record)
                target_skills = select_available_skills(record, facility, target_elite, product, target_level)
                if skill_signature(current_skills) == skill_signature(target_skills):
                    continue
                newly_used = [
                    item for item in target_skills
                    if (str(item.get("variant_group") or item.get("skill_name") or ""), str(item.get("skill_name") or ""))
                    not in {
                        (str(x.get("variant_group") or x.get("skill_name") or ""), str(x.get("skill_name") or ""))
                        for x in current_skills
                    }
                    or int(item.get("elite", 0)) > cur.elite
                    or int(item.get("required_level", 1) or 1) > cur.level
                ]
                if not newly_used:
                    newly_used = target_skills
                required_elite = max(int(item.get("elite", 0)) for item in newly_used)
                required_level = max(
                    int(item.get("required_level", 1) or 1)
                    for item in newly_used
                    if int(item.get("elite", 0)) == required_elite
                )
                # Preserve the plan state when it is higher than the bare skill
                # unlock threshold, while recommending only the minimum level
                # needed at that elite stage.
                target_elite = max(required_elite, target_elite)
                target_level = required_level if target_elite == required_elite else target_level
                row = usage.setdefault(name, {
                    "operator": name,
                    "current_elite": cur.elite,
                    "current_level": cur.level,
                    "target_elite": target_elite,
                    "target_level": target_level,
                    "hours_used_in_ceiling_plan": 0.0,
                    "rooms": set(),
                    "products": set(),
                    "unlocked_skills": {},
                    "base_bonus_delta_pct": 0.0,
                    "promotion_risk_notes": set(),
                })
                row["target_elite"] = max(row["target_elite"], target_elite)
                if target_elite == row["target_elite"]:
                    row["target_level"] = max(row["target_level"], target_level)
                row["hours_used_in_ceiling_plan"] += hours
                row["rooms"].add(room_id)
                row["products"].add(product)
                current_bonus = sum(float(x.get("base_bonus_pct", 0.0)) for x in current_skills)
                max_bonus = sum(float(x.get("base_bonus_pct", 0.0)) for x in target_skills)
                row["base_bonus_delta_pct"] = max(row["base_bonus_delta_pct"], max_bonus - current_bonus)
                for item in newly_used:
                    for tag in item.get("tags") or []:
                        if tag in PROMOTION_RISK_TAGS:
                            row["promotion_risk_notes"].add(PROMOTION_RISK_TAGS[tag])
                    row["unlocked_skills"][str(item.get("skill_name") or "")] = {
                        "facility": facility,
                        "product": product,
                        "elite": int(item.get("elite", 0)),
                        "required_level": int(item.get("required_level", 1) or 1),
                        "description": item.get("description", ""),
                        "tags": item.get("tags") or [],
                    }

    product_weight = {
        "orundum_order": 6.0,
        "orundum_shard": 6.0,
        "lmd_order": 4.0,
        "pure_gold": 3.0,
        "drone_recovery": 2.5,
        "base_management": 2.0,
    }
    result = []
    for row in usage.values():
        row["rooms"] = sorted(row["rooms"])
        row["products"] = sorted(row["products"])
        row["unlocked_skills"] = list(row["unlocked_skills"].values())
        row["promotion_risk_notes"] = sorted(row["promotion_risk_notes"])
        row["recommendation_status"] = (
            "requires_state_comparison_before_irreversible_upgrade"
            if row["promotion_risk_notes"]
            else "direct_upgrade_candidate"
        )
        row["priority_score"] = round(
            row["hours_used_in_ceiling_plan"] * max(product_weight.get(x, 1.0) for x in row["products"])
            + max(0.0, row["base_bonus_delta_pct"]),
            3,
        )
        result.append(row)
    result.sort(key=lambda x: (-x["priority_score"], x["target_elite"], x["operator"]))
    return result


def compact_scenario(search: dict[str, Any]) -> dict[str, Any] | None:
    selected = search.get("selected")
    if not selected:
        return None
    return {
        "profile_id": selected.get("profile_id"),
        "layout": selected.get("layout"),
        "product_split": selected.get("product_split"),
        "orundum_per_day": selected.get("orundum_per_day"),
        "net_lmd_per_day": selected.get("net_lmd_per_day"),
        "orundum_shard_balance": selected.get("orundum_shard_balance"),
        "pure_gold_balance": selected.get("pure_gold_balance"),
        "drones_recovered": selected.get("drones_recovered"),
        "optimality_claim": selected.get("optimality_claim"),
        "accepted_time_limit_incumbents": ((selected.get("solver_result") or {}).get("solver") or {}).get("accepted_time_limit_incumbents", 0),
    }


def run_upgrade_search(
    roster_path: str | Path,
    *,
    online_times: list[str],
    lmd_floor: float,
    profiles: list[str] | None,
    top_k: int,
    operator_pool_size: int,
    time_limit: float,
    max_proxy_attempts: int,
    work_dir: str | Path,
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
    marginal_limit: int = 0,
    right_side_schedule: list[dict[str, list[str]]] | None = None,
) -> dict[str, Any]:
    """Compare current, ceiling and targeted states under identical constraints.

    ``marginal_limit`` optionally performs leave-one-upgrade-out solves on the
    highest-priority recommendations. This is more reliable than treating skill
    percentage deltas as independent, especially for irreversible or synergy
    upgrades, but it can be computationally expensive.
    """

    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    current_roster = read_roster(roster_path)
    maxed_path = work / "owned_roster_max_base_skills.tsv"
    index = operator_index()
    write_roster_tsv(
        maxed_path,
        current_roster,
        {op.name: maximum_base_unlock(index[op.name]) for op in current_roster if op.name in index},
    )

    kwargs = dict(
        online_times=online_times,
        lmd_floor=lmd_floor,
        top_k=top_k,
        operator_pool_size=operator_pool_size,
        time_limit=time_limit,
        max_proxy_attempts=max_proxy_attempts,
        profiles=profiles,
        lmd_proxy_floor_slack=0.0,
        profile_mode=profile_mode,
        profiles_file=profiles_file,
        grid_layouts=grid_layouts,
        max_profiles=max_profiles,
        dorm_levels=dorm_levels,
        right_side_levels=right_side_levels,
        drone_capacity=drone_capacity,
        initial_drone_stock=initial_drone_stock,
        minimum_shard_balance=minimum_shard_balance,
        minimum_gold_balance=minimum_gold_balance,
        right_side_schedule=right_side_schedule,
    )
    current_search = search_layouts(roster_path, **kwargs)
    maxed_search = search_layouts(maxed_path, **kwargs)

    upgrades: list[dict[str, Any]] = []
    targeted_search = None
    targeted_path = work / "owned_roster_targeted_base_upgrades.tsv"
    overrides: dict[str, tuple[int, int]] = {}
    if maxed_search.get("selected"):
        upgrades = selected_upgrade_requirements(current_roster, maxed_search["selected"])
        overrides = {
            item["operator"]: (int(item["target_elite"]), int(item["target_level"]))
            for item in upgrades
        }
        write_roster_tsv(targeted_path, current_roster, overrides)
        targeted_search = search_layouts(targeted_path, **kwargs)

    marginal_rows: list[dict[str, Any]] = []
    targeted_summary = compact_scenario(targeted_search or {})
    if targeted_summary and marginal_limit > 0:
        for item in upgrades[: max(0, int(marginal_limit))]:
            operator = item["operator"]
            without = dict(overrides)
            without.pop(operator, None)
            path = work / f"without_{operator}_upgrade.tsv"
            write_roster_tsv(path, current_roster, without)
            try:
                search = search_layouts(path, **kwargs)
                scenario = compact_scenario(search)
                if scenario:
                    marginal_rows.append({
                        "operator": operator,
                        "target_elite": item["target_elite"],
                        "target_level": item["target_level"],
                        "orundum_loss_without_upgrade": round(
                            float(targeted_summary["orundum_per_day"]) - float(scenario["orundum_per_day"]), 6
                        ),
                        "net_lmd_change_without_upgrade": round(
                            float(scenario["net_lmd_per_day"]) - float(targeted_summary["net_lmd_per_day"]), 6
                        ),
                        "without_upgrade_scenario": scenario,
                        "risk_notes": item.get("promotion_risk_notes") or [],
                    })
            except Exception as exc:
                marginal_rows.append({
                    "operator": operator,
                    "status": "comparison_failed",
                    "reason": str(exc),
                    "risk_notes": item.get("promotion_risk_notes") or [],
                })
        marginal_rows.sort(key=lambda row: -float(row.get("orundum_loss_without_upgrade", -1e30)))

    result = {
        "schema_version": 2,
        "search_type": "current_vs_owned_max_base_skill_ceiling_vs_targeted_unlocks",
        "constraints": {
            "minimum_net_lmd_per_day": lmd_floor,
            "minimum_orundum_shard_balance": minimum_shard_balance,
            "minimum_pure_gold_balance": minimum_gold_balance,
            "battle_record_factories": 0,
            "online_times": online_times,
        },
        "scenarios": {
            "current": compact_scenario(current_search),
            "all_owned_max_base_skills": compact_scenario(maxed_search),
            "targeted_minimum_unlocks": targeted_summary,
        },
        "upgrade_recommendations": upgrades,
        "marginal_upgrade_evaluation": marginal_rows,
        "generated_rosters": {
            "maxed_owned": str(maxed_path),
            "targeted": str(targeted_path) if upgrades else None,
        },
        "search_details": {
            "current": current_search,
            "all_owned_max_base_skills": maxed_search,
            "targeted_minimum_unlocks": targeted_search,
        },
        "limitations": [
            "升级成本未进入目标函数；全满场景只用于估计基建性能上限。",
            "定向推荐来自上限方案实际使用的更高技能，协同与替代关系应结合边际对照解释。",
            "培养建议只评价基建性能，不代表战斗培养优先级。",
            "候选组合库和profile搜索可能截断，结果不构成完整数学全局最优证明。",
        ],
    }
    result["reproducibility"] = build_manifest(
        run_type="upgrade_search",
        extra={
            "upgrade_recommendation_count": len(upgrades),
            "marginal_comparison_count": len(marginal_rows),
        },
    )
    return result


def _parse_levels(value: str | None) -> list[int] | None:
    if not value:
        return None
    return [int(x.strip()) for x in value.split(",") if x.strip()]


def _parse_json_object(value: str | None) -> dict[str, int]:
    if not value:
        return dict(DEFAULT_RIGHT_SIDE_LEVELS)
    candidate = Path(value)
    parsed = json.loads(candidate.read_text(encoding="utf-8") if candidate.exists() else value)
    if not isinstance(parsed, dict):
        raise ValueError("right-side-levels 必须是JSON对象")
    result = dict(DEFAULT_RIGHT_SIDE_LEVELS)
    result.update({str(key): int(item) for key, item in parsed.items()})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="比较当前练度、基建技能上限和定向升级方案")
    parser.add_argument("--roster", required=True)
    parser.add_argument("--online-times", default="08:00,14:00,20:00")
    parser.add_argument("--lmd-floor", type=float, default=-10000.0)
    parser.add_argument("--minimum-shard-balance", type=float, default=0.0)
    parser.add_argument("--minimum-gold-balance", type=float, default=0.0)
    parser.add_argument("--profiles", default="252-output,342-output,333-max,243-max,432-output,423-max,522-output")
    parser.add_argument("--profile-mode", choices=["representative", "level_grid"], default="representative")
    parser.add_argument("--profiles-file")
    parser.add_argument("--grid-layouts")
    parser.add_argument("--max-profiles", type=int)
    parser.add_argument("--dorm-levels")
    parser.add_argument("--right-side-levels")
    parser.add_argument("--drone-capacity", type=float, default=235.0)
    parser.add_argument("--initial-drone-stock", type=float)
    parser.add_argument("--top-k", type=int, default=24)
    parser.add_argument("--operator-pool-size", type=int, default=12)
    parser.add_argument("--time-limit", type=float, default=10.0)
    parser.add_argument("--max-proxy-attempts", type=int, default=4)
    parser.add_argument("--marginal-limit", type=int, default=0, help="对前N项培养做留一法边际复算")
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    value = run_upgrade_search(
        args.roster,
        online_times=[x.strip() for x in args.online_times.split(",") if x.strip()],
        lmd_floor=args.lmd_floor,
        profiles=[x.strip() for x in args.profiles.split(",") if x.strip()] if args.profiles else None,
        profile_mode=args.profile_mode,
        profiles_file=args.profiles_file,
        grid_layouts=[x.strip() for x in args.grid_layouts.split(",") if x.strip()] if args.grid_layouts else None,
        max_profiles=args.max_profiles,
        dorm_levels=_parse_levels(args.dorm_levels),
        right_side_levels=_parse_json_object(args.right_side_levels),
        drone_capacity=args.drone_capacity,
        initial_drone_stock=args.initial_drone_stock,
        minimum_shard_balance=args.minimum_shard_balance,
        minimum_gold_balance=args.minimum_gold_balance,
        top_k=args.top_k,
        operator_pool_size=args.operator_pool_size,
        time_limit=args.time_limit,
        max_proxy_attempts=args.max_proxy_attempts,
        marginal_limit=args.marginal_limit,
        work_dir=args.work_dir,
    )
    Path(args.output).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": args.output,
        "current": value["scenarios"]["current"],
        "ceiling": value["scenarios"]["all_owned_max_base_skills"],
        "targeted": value["scenarios"]["targeted_minimum_unlocks"],
        "upgrade_count": len(value["upgrade_recommendations"]),
        "marginal_comparisons": len(value["marginal_upgrade_evaluation"]),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
