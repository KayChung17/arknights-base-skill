#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit solver/search outputs for internal consistency and release-safe claims."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from optimizer_common import write_json

TOLERANCE = 1e-6


def _check(checks: list[dict[str, Any]], code: str, ok: bool, message: str, *, severity: str = "error") -> None:
    checks.append({"code": code, "ok": bool(ok), "severity": severity, "message": message})


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _audit_drone_plan(plan: dict[str, Any] | None, checks: list[dict[str, Any]]) -> None:
    if not plan:
        _check(checks, "drone_plan_present", False, "结果未包含无人机计划。", severity="warning")
        return
    _check(checks, "drone_plan_feasible", bool(plan.get("feasible", True)), "无人机计划必须标记为可行。")
    targets_by_segment: dict[str, set[str]] = {}
    for allocation in plan.get("allocations") or []:
        drones = float(allocation.get("drones", 0.0) or 0.0)
        if drones <= TOLERANCE:
            continue
        segment_id = str(allocation.get("segment_id") or "")
        room_id = str(allocation.get("room_id") or "")
        targets_by_segment.setdefault(segment_id, set()).add(room_id)
    invalid_targets = {
        segment_id: sorted(room_ids)
        for segment_id, room_ids in targets_by_segment.items()
        if len(room_ids) > 1
    }
    _check(
        checks,
        "single_drone_target_per_operation_node",
        not invalid_targets,
        f"每个上线操作节点只能选择一个无人机加速房间；多目标节点: {invalid_targets}",
    )
    recovered = float(plan.get("total_recovered", 0.0) or 0.0)
    used = float(plan.get("total_used", 0.0) or 0.0)
    wasted = float(plan.get("total_wasted", 0.0) or 0.0)
    repeating = bool(plan.get("repeating_day_balance", plan.get("repeating_day_verified", False)))
    if repeating:
        _check(
            checks,
            "drone_repeating_day_flow",
            abs(recovered - used - wasted) <= 1e-4,
            f"重复日无人机流应闭合：恢复{recovered:.3f}，使用{used:.3f}，浪费{wasted:.3f}。",
        )
    capacity = plan.get("capacity")
    timeline = plan.get("timeline") or plan.get("inventory_timeline") or []
    if capacity is not None and timeline:
        stocks: list[float] = []
        after_use: list[float] = []
        for item in timeline:
            start = float(item.get("start_inventory", item.get("stock_before", item.get("inventory", 0.0))) or 0.0)
            used_at_start = float(item.get("used_at_start", 0.0) or 0.0)
            end = float(item.get("end_inventory", item.get("stock_after", item.get("inventory", 0.0))) or 0.0)
            stocks.extend([start, end])
            after_use.append(start - used_at_start)
        _check(checks, "drone_capacity_upper_bound", max(stocks) <= float(capacity) + TOLERANCE, f"无人机库存不得超过容量{capacity}。")
        _check(checks, "drone_inventory_nonnegative", min(after_use + stocks) >= -TOLERANCE, "无人机库存不得为负。")
        if plan.get("empty_inventory_at_each_operation_node"):
            _check(
                checks,
                "drone_inventory_emptied_at_each_operation_node",
                max(after_use) < 1.0 + TOLERANCE,
                f"每次上线后可用无人机库存必须清空；各节点余量: {[round(value, 3) for value in after_use]}",
            )


def _selected_layout_payload(value: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    selected = value.get("selected")
    constraints = (value.get("objective") or {}).get("constraints") or {}
    return selected, constraints


def _selected_solver_payload(value: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    solution = value.get("selected_solution")
    if not solution:
        return None, {}
    simulation = solution.get("simulation") or {}
    selected = {
        "orundum_per_day": (simulation.get("aggregate_metrics") or {}).get("orundum", 0.0),
        "net_lmd_per_day": simulation.get("net_lmd_balance", 0.0),
        "orundum_shard_balance": simulation.get("orundum_shard_balance", 0.0),
        "pure_gold_balance": simulation.get("pure_gold_balance", 0.0),
        "solver_result": value,
    }
    constraints = (((value.get("candidate_plan") or {}).get("simulation") or {}).get("constraints") or {})
    return selected, constraints


def audit_result(value: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    result_type = value.get("search_type") or value.get("result_type") or "unknown"
    if value.get("search_type") == "outer_layout_configuration_plus_inner_hybrid_schedule_solver":
        selected, constraints = _selected_layout_payload(value)
    elif value.get("result_type") == "hybrid_schedule_solution":
        selected, constraints = _selected_solver_payload(value)
    elif value.get("search_type") == "current_vs_owned_max_base_skill_ceiling_vs_targeted_unlocks":
        selected = (value.get("search_details") or {}).get("targeted_minimum_unlocks", {}).get("selected")
        constraints = value.get("constraints") or {}
    else:
        selected = value.get("selected")
        constraints = value.get("constraints") or {}

    _check(checks, "selected_candidate_present", selected is not None, "结果必须包含选中的候选方案。")
    reproducibility_present = isinstance(value.get("reproducibility"), dict) or isinstance(value.get("project_reproducibility"), dict)
    _check(checks, "reproducibility_manifest_present", reproducibility_present, "结果应包含可复现清单。", severity="warning")
    coverage = value.get("project_data_coverage")
    is_project_output = bool(value.get("project") or value.get("project_reproducibility"))
    if is_project_output:
        project = value.get("project") or {}
        _check(
            checks,
            "right_side_levels_explicitly_confirmed",
            not project or project.get("right_side_levels_confirmed") is True,
            "右侧功能设施不可降级，项目必须确认游戏内实际等级。",
        )
        _check(
            checks,
            "right_side_levels_immutable",
            not project or project.get("right_side_levels_immutable") is True,
            "布局搜索必须把右侧设施等级作为不可变基建状态。",
        )
        selected_power = project.get("selected_power") or {}
        _check(
            checks,
            "selected_layout_power_feasible",
            not project or (_finite(selected_power.get("spare_power")) and float(selected_power.get("spare_power")) >= -TOLERANCE),
            f"选中布局必须不缺电，当前电力余量为 {selected_power.get('spare_power')}。",
        )
        expected_right_power = project.get("fixed_right_power_consumption")
        _check(
            checks,
            "right_side_power_bound_to_selected_layout",
            not project or (_finite(expected_right_power)
            and _finite(selected_power.get("fixed_right_consumption"))
            and abs(float(expected_right_power) - float(selected_power.get("fixed_right_consumption"))) <= TOLERANCE),
            "选中布局的右侧耗电必须与用户确认的不可逆设施等级一致。",
        )
        _check(checks, "data_coverage_present", isinstance(coverage, dict), "项目结果应包含干员与技能结构化覆盖摘要。", severity="warning")
        if isinstance(coverage, dict):
            roster_coverage = coverage.get("roster") or {}
            ratio = float(roster_coverage.get("operator_coverage_ratio", 0.0) or 0.0)
            _check(
                checks,
                "operator_data_coverage_complete",
                ratio >= 1.0 - TOLERANCE,
                f"干员名称数据覆盖率为{ratio:.2%}；未知干员可能造成假性无解或漏解。",
                severity="warning",
            )
            relevant = coverage.get("relevant_unmodeled_skills") or {}
            blocking_count = int(relevant.get("blocking_count", 0) or 0)
            _check(
                checks,
                "relevant_unmodeled_skill_coverage",
                blocking_count == 0,
                f"本次设施与产品范围仍有{blocking_count}条高风险未结构化技能，可能造成候选被低估。",
                severity="warning",
            )

    if selected:
        metrics = {
            "orundum": selected.get("orundum_per_day"),
            "net_lmd": selected.get("net_lmd_per_day"),
            "shard_balance": selected.get("orundum_shard_balance"),
            "gold_balance": selected.get("pure_gold_balance"),
        }
        for name, metric in metrics.items():
            _check(checks, f"finite_{name}", _finite(metric), f"{name} 必须是有限数值。")

        lmd_floor = constraints.get("minimum_net_lmd_per_day")
        if lmd_floor is not None:
            _check(
                checks,
                "net_lmd_floor",
                float(selected.get("net_lmd_per_day", -math.inf)) + TOLERANCE >= float(lmd_floor),
                f"龙门币净变化必须不低于{float(lmd_floor):.3f}。",
            )
        shard_floor = constraints.get("minimum_orundum_shard_balance", constraints.get("orundum_shard_balance_min"))
        balance_policy = constraints.get("balance_policy") or {}
        shard_policy = balance_policy.get("originium_shard") or {"mode": "hard"}
        gold_policy = balance_policy.get("pure_gold") or {"mode": "hard"}
        if shard_floor is not None:
            shard_mode = str(shard_policy.get("mode", "hard"))
            shard_minimum = shard_policy.get("hard_minimum") if shard_mode == "soft" else shard_floor
            _check(
                checks,
                "shard_balance_floor" if shard_mode == "hard" else "shard_balance_soft_safety_floor",
                shard_minimum is None or float(selected.get("orundum_shard_balance", -math.inf)) + TOLERANCE >= float(shard_minimum),
                (
                    f"源石碎片净变化必须不低于{float(shard_floor):.3f}。"
                    if shard_mode == "hard"
                    else f"源石碎片目标为{float(shard_floor):.3f}，软约束实际值为{float(selected.get('orundum_shard_balance', 0.0)):.3f}，硬安全线为{shard_minimum}。"
                ),
            )
            if shard_mode == "soft":
                shard_actual = float(selected.get("orundum_shard_balance", -math.inf))
                _check(
                    checks,
                    "shard_balance_soft_target",
                    shard_actual + TOLERANCE >= float(shard_floor),
                    f"源石碎片软目标为{float(shard_floor):.3f}，实际值为{shard_actual:.3f}；该方案会消耗库存。",
                    severity="warning",
                )
        gold_floor = constraints.get("minimum_pure_gold_balance", constraints.get("pure_gold_balance_min"))
        if gold_floor is not None:
            gold_mode = str(gold_policy.get("mode", "hard"))
            gold_minimum = gold_policy.get("hard_minimum") if gold_mode == "soft" else gold_floor
            _check(
                checks,
                "pure_gold_balance_floor" if gold_mode == "hard" else "pure_gold_balance_soft_safety_floor",
                gold_minimum is None or float(selected.get("pure_gold_balance", -math.inf)) + TOLERANCE >= float(gold_minimum),
                (
                    f"赤金净变化必须不低于{float(gold_floor):.3f}。"
                    if gold_mode == "hard"
                    else f"赤金目标为{float(gold_floor):.3f}，软约束实际值为{float(selected.get('pure_gold_balance', 0.0)):.3f}，硬安全线为{gold_minimum}。"
                ),
            )
            if gold_mode == "soft":
                gold_actual = float(selected.get("pure_gold_balance", -math.inf))
                _check(
                    checks,
                    "pure_gold_balance_soft_target",
                    gold_actual + TOLERANCE >= float(gold_floor),
                    f"赤金软目标为{float(gold_floor):.3f}，实际值为{gold_actual:.3f}；该方案会消耗库存。",
                    severity="warning",
                )

        solver_result = selected.get("solver_result") or value
        solver = solver_result.get("solver") or {}
        claim = solver.get("optimality_claim") or selected.get("optimality_claim")
        library_complete = solver.get("candidate_library_complete")
        if claim == "proxy_optimal_within_complete_candidate_library":
            _check(checks, "optimality_claim_library_complete", library_complete is True, "完整候选库最优声明要求候选库未截断。")
            _check(checks, "optimality_claim_proxy_gap", solver.get("proxy_models_solved_to_gap") is True, "完整候选库最优声明要求代理模型达到声明gap。")
        _check(
            checks,
            "no_actual_global_optimality_overclaim",
            solver.get("actual_simulation_global_optimality_proven") is not True,
            "当前模拟目标与代理模型不完全一致时不得声明实际全局最优。",
        )
        simulation = ((solver_result.get("selected_solution") or {}).get("simulation") or {})
        _audit_drone_plan(simulation.get("drone_plan"), checks)
        selected_plan = selected.get("plan") or solver_result.get("candidate_plan") or {}
        right_side_assignments = ((selected_plan.get("right_side_plan") or {}).get("assignments") or [])
        segment_records = selected_plan.get("segments") or {}
        right_side_by_segment = {
            str(item.get("segment_id")): item.get("rooms") or {}
            for item in right_side_assignments
        }
        _check(
            checks,
            "right_side_plan_complete",
            not is_project_output or set(right_side_by_segment) == set(segment_records),
            "项目结果必须逐班次包含结构化会客室和办公室安排。",
        )
        right_side_shape_ok = True
        right_side_conflicts: dict[str, list[str]] = {}
        for segment_id, segment in segment_records.items():
            fixed = right_side_by_segment.get(segment_id) or {}
            meeting = list(fixed.get("meeting") or [])
            hire = list(fixed.get("hire") or [])
            right_side_shape_ok = right_side_shape_ok and len(meeting) == 2 and len(hire) == 1
            fixed_names = set(meeting + hire)
            production_names = {
                str(operator.get("name"))
                for room in (segment.get("rooms") or {}).values()
                for operator in room.get("operators") or []
            }
            overlap = sorted(fixed_names & production_names)
            if overlap:
                right_side_conflicts[segment_id] = overlap
        _check(
            checks,
            "right_side_room_capacities",
            not is_project_output or right_side_shape_ok,
            "每班会客室必须安排2人，办公室必须安排1人。",
        )
        _check(
            checks,
            "right_side_production_exclusivity",
            not right_side_conflicts,
            f"右侧人员与同班生产房间冲突: {right_side_conflicts}",
        )
        dormitory_plan = simulation.get("dormitory_plan") or {}
        _check(
            checks,
            "dormitory_repeating_day_morale",
            not is_project_output or dormitory_plan.get("repeating_day_verified") is True,
            "长期排班必须包含固定宿舍床位安排，并证明逐干员重复日心情闭环。",
        )
        _check(
            checks,
            "dormitory_automation_independent",
            not is_project_output or dormitory_plan.get("automation_rules_used") is False,
            "宿舍计划只能使用游戏机制，不得依赖外部自动化脚本或副表规则。",
        )
        _check(
            checks,
            "dormitory_joint_iteration_converged",
            not is_project_output or dormitory_plan.get("joint_iteration_converged") is True,
            "宿舍居民、心情阈值和生产联动必须迭代到稳定。",
        )
        right_side_names = {
            name
            for rooms in right_side_by_segment.values()
            for key in ("meeting", "hire")
            for name in rooms.get(key) or []
        }
        dorm_flows = dormitory_plan.get("operator_flows") or {}
        missing_right_side_flows = sorted(name for name in right_side_names if name not in dorm_flows)
        _check(
            checks,
            "right_side_morale_cycle_included",
            not is_project_output or not missing_right_side_flows,
            f"右侧工作人员未进入宿舍心情闭环: {missing_right_side_flows}",
        )
        secondary = ((solver_result.get("selected_solution") or {}).get("secondary_output_postprocess"))
        if secondary is not None:
            _check(checks, "secondary_output_dominance_checked", secondary.get("checked") is True, "必须执行免费副产出支配检查。")
            remaining = secondary.get("remaining_dominated_empty_slots") or []
            _check(checks, "no_dominated_empty_slot", not remaining, f"仍存在可免费提高产出的空位: {remaining}")
        opportunity = ((solver_result.get("selected_solution") or {}).get("opportunity_cost_postprocess"))
        if opportunity is not None:
            _check(
                checks,
                "opportunity_cost_reoptimization_checked",
                opportunity.get("checked") is True,
                "被覆盖的高价值技能必须经过释放与全日反事实重排。",
            )
            _check(
                checks,
                "opportunity_cost_neighborhood_reported",
                isinstance(opportunity.get("remaining_opportunity_risks"), list),
                "机会成本搜索必须记录剩余风险，供候选截断范围解释。",
            )

    errors = [item for item in checks if not item["ok"] and item["severity"] == "error"]
    warnings = [item for item in checks if not item["ok"] and item["severity"] == "warning"]
    return {
        "audit_schema_version": 1,
        "result_type": result_type,
        "status": "failed" if errors else ("passed_with_warnings" if warnings else "passed"),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="审计求解结果的硬约束、无人机闭环和最优性措辞")
    parser.add_argument("input")
    parser.add_argument("--output")
    parser.add_argument("--strict-warnings", action="store_true")
    args = parser.parse_args()
    value = json.loads(Path(args.input).read_text(encoding="utf-8"))
    audit = audit_result(value)
    if args.output:
        write_json(args.output, audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    if audit["error_count"]:
        return 2
    if args.strict_warnings and audit["warning_count"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
