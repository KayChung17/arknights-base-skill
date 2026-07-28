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
        if shard_floor is not None:
            _check(
                checks,
                "shard_balance_floor",
                float(selected.get("orundum_shard_balance", -math.inf)) + TOLERANCE >= float(shard_floor),
                f"源石碎片净变化必须不低于{float(shard_floor):.3f}。",
            )
        gold_floor = constraints.get("minimum_pure_gold_balance", constraints.get("pure_gold_balance_min"))
        if gold_floor is not None:
            _check(
                checks,
                "pure_gold_balance_floor",
                float(selected.get("pure_gold_balance", -math.inf)) + TOLERANCE >= float(gold_floor),
                f"赤金净变化必须不低于{float(gold_floor):.3f}。",
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
