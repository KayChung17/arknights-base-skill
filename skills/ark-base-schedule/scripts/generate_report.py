#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a concise Chinese Markdown report from solver/search JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _n(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _sustainability_sections(simulation: dict[str, Any]) -> list[str]:
    value = simulation.get("resource_sustainability") or {}
    if not value:
        return []
    labels = {
        "sustainable_repeating_day": "可持续重复日",
        "inventory_consuming_candidate": "库存消耗型候选",
    }
    drawdown = value.get("daily_drawdown") or {}
    runway = value.get("runway_days") or {}
    lines = [
        "", "### 资源可持续性", "",
        f"- 分类：{labels.get(value.get('classification'), value.get('classification', '—'))}",
        f"- 源石碎片每日消耗：{_n(drawdown.get('orundum_shard'))}；可维持天数：{_n(runway.get('orundum_shard')) if runway.get('orundum_shard') is not None else '未知或无需库存'}",
        f"- 赤金每日消耗：{_n(drawdown.get('pure_gold'))}；可维持天数：{_n(runway.get('pure_gold')) if runway.get('pure_gold') is not None else '未知或无需库存'}",
    ]
    overall = value.get("overall_runway_days")
    known_runways = [item for item in runway.values() if item is not None]
    if overall is None and known_runways and min(known_runways) <= 1e-9:
        overall = 0.0
    if value.get("repeatable_without_inventory") is True:
        overall_text = "无限"
    elif overall is None:
        overall_text = "库存未知"
    else:
        overall_text = _n(overall)
    lines.append(f"- 整体可维持天数：{overall_text}")
    if value.get("feasible_for_configured_horizon") is False:
        lines.append("- 当前库存无法支撑配置中的执行周期。")
    return lines


def _project_sections(value: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    coverage = value.get("project_data_coverage") or {}
    roster = coverage.get("roster") or {}
    skills = coverage.get("unlocked_skill_coverage") or {}
    relevant = coverage.get("relevant_unmodeled_skills") or {}
    if coverage:
        lines += [
            "", "## 数据覆盖", "",
            f"- 已拥有干员：{roster.get('owned_count', '—')}",
            f"- 名称已收录：{roster.get('known_operator_count', '—')}",
            f"- 未收录：{roster.get('unknown_operator_count', '—')}",
            f"- 当前解锁技能：{skills.get('unlocked_skill_count', '—')}",
            f"- 直接数值 / 复杂标签 / 仅描述：{skills.get('direct_numeric_skill_count', '—')} / {skills.get('tagged_complex_skill_count', '—')} / {skills.get('description_only_skill_count', '—')}",
        ]
        if relevant:
            lines += [
                f"- 本次范围未结构化技能：{relevant.get('unmodeled_count', '—')}",
                f"- 其中高风险动态/数值技能：{relevant.get('blocking_count', '—')}",
                f"- 门禁策略：{relevant.get('policy', 'warn')}",
            ]
    profile = value.get("profile_search") or {}
    selected = value.get("selected") or {}
    solver_result = selected.get("solver_result") or {}
    library = solver_result.get("combination_library_summary") or {}
    complete = library.get("search_completeness") or {}
    if profile or library:
        lines += ["", "## 搜索范围", ""]
        if profile:
            lines += [
                f"- 外层模式：{profile.get('mode', '—')}",
                f"- 实际求解 profile：{profile.get('profiles_kept', profile.get('profiles_solved', '—'))}",
                f"- 外层截断：{profile.get('profiles_truncated', False)}",
            ]
        if library:
            lines += [
                f"- 房间组合库完整：{complete.get('all_rooms_untruncated', '—')}",
                f"- 发生截断的房间：{'、'.join(complete.get('truncated_rooms') or []) or '无'}",
            ]
    online = value.get("online_time_search") or {}
    if online:
        lines += [
            "", "## 上线时间搜索", "",
            f"- 选中时刻：{'、'.join(online.get('selected_online_times') or [])}",
            f"- 全部已评估：{online.get('evaluated_count', '—')}；分钟级细化：{online.get('refinement_evaluated_count', 0)}",
            f"- 最终时间精度：{online.get('time_resolution_minutes', '—')} 分钟",
        ]
    manifest = value.get("project_reproducibility") or value.get("reproducibility") or {}
    if manifest:
        lines += ["", "## 可复现信息", ""]
        runtime = manifest.get("runtime") or {}
        if runtime:
            lines.append(f"- Python：{runtime.get('python', '—')}；SciPy：{runtime.get('scipy', '—')}")
    return lines


def _plan_sections(plan: dict[str, Any] | None, simulation: dict[str, Any] | None = None) -> list[str]:
    if not plan:
        return []
    lines = ["", "## 操作时间表", "", "| 时段 | 房间 | 产品 | 干员 |", "|---|---|---|---|"]
    for segment in (plan.get("segments") or {}).values():
        span = f"{segment.get('start', '—')}–{segment.get('end', '—')}"
        for room_id, room in (segment.get("rooms") or {}).items():
            operators = "、".join(item.get("name", "") for item in room.get("operators") or []) or "空置"
            lines.append(f"| {span} | {room_id} | {room.get('product_id', '—')} | {operators} |")
    drone = (simulation or {}).get("drone_plan") or {}
    allocations = drone.get("allocations") or []
    if allocations:
        lines += ["", "### 无人机分配", "", "| 节点 | 房间 | 产品 | 无人机 |", "|---|---|---|---:|"]
        for item in allocations:
            lines.append(f"| {item.get('operation_time', '—')} | {item.get('room_id', '—')} | {item.get('product_id', '—')} | {_n(item.get('drones'), 0)} |")
    inventory = (simulation or {}).get("inventory_timeline") or {}
    if inventory:
        lines += ["", "### 日内库存", ""]
        minimum = inventory.get("minimum_balance") or {}
        lines.append(
            f"- 最低余额：赤金 {_n(minimum.get('pure_gold'))}；源石碎片 {_n(minimum.get('originium_shard'))}；龙门币 {_n(minimum.get('lmd'))}"
        )
        lines.append(f"- 爆单区间：{len(inventory.get('order_overflow_segments') or [])}")
    return lines


def _layout_report(value: dict[str, Any]) -> str:
    selected = value.get("selected")
    lines = ["# 基建布局优化报告", ""]
    objective = value.get("objective") or {}
    constraints = objective.get("constraints") or {}
    balance_policy = constraints.get("balance_policy") or {}
    shard_policy = balance_policy.get("originium_shard") or {"mode": "hard"}
    gold_policy = balance_policy.get("pure_gold") or {"mode": "hard"}
    lines += [
        "## 目标与约束",
        "",
        f"- 主目标：{objective.get('primary', '—')}",
        f"- 龙门币每日下限：{_n(constraints.get('minimum_net_lmd_per_day'))}",
        f"- 源石碎片净变化目标：{_n(constraints.get('minimum_orundum_shard_balance'))}（{shard_policy.get('mode', 'hard')}）",
        f"- 赤金净变化目标：{_n(constraints.get('minimum_pure_gold_balance'))}（{gold_policy.get('mode', 'hard')}）",
        "- 干员工时：由上线区间、心情消耗、宿舍恢复和重复日闭环决定",
        "",
    ]
    if not selected:
        lines += ["## 结果", "", "未找到满足硬约束的候选。", ""]
        return "\n".join(lines)
    lines += [
        "## 推荐候选",
        "",
        "| 项目 | 数值 |",
        "|---|---:|",
        f"| 布局 | {selected.get('layout', '—')} |",
        f"| 配置ID | {selected.get('profile_id', '—')} |",
        f"| 合成玉/天 | {_n(selected.get('orundum_per_day'))} |",
        f"| 等价龙门币收益/天 | {_n(selected.get('economic_utility_lmd_per_day'))} |",
        f"| 龙门币净变化/天 | {_n(selected.get('net_lmd_per_day'))} |",
        f"| 源石碎片净变化/天 | {_n(selected.get('orundum_shard_balance'))} |",
        f"| 赤金净变化/天 | {_n(selected.get('pure_gold_balance'))} |",
        f"| 无人机恢复/使用/浪费 | {_n(selected.get('drones_recovered'))} / {_n(selected.get('drones_used'))} / {_n(selected.get('drones_wasted'))} |",
        "",
        "### 房间等级",
        "",
        f"- 贸易站：{'/'.join(map(str, selected.get('trading_levels') or []))}",
        f"- 制造站：{'/'.join(map(str, selected.get('factory_levels') or []))}",
        f"- 发电站：{'/'.join(map(str, selected.get('power_plant_levels') or []))}",
        f"- 宿舍：{'/'.join(map(str, selected.get('dormitory_levels') or []))}",
        "",
        "### 产品分配",
        "",
    ]
    for key, item in (selected.get("product_split") or {}).items():
        lines.append(f"- {key}: {item}")
    results = value.get("results") or []
    if results:
        lines += ["", "## 前五名候选", "", "| 排名 | 配置 | 布局 | 等价龙门币/天 | 合成玉/天 | 龙门币净变化/天 |", "|---:|---|---:|---:|---:|---:|"]
        for index, item in enumerate(results[:5], start=1):
            lines.append(f"| {index} | {item.get('profile_id')} | {item.get('layout')} | {_n(item.get('economic_utility_lmd_per_day'))} | {_n(item.get('orundum_per_day'))} | {_n(item.get('net_lmd_per_day'))} |")
    solver_result = selected.get("solver_result") or {}
    plan = selected.get("plan") or solver_result.get("candidate_plan")
    simulation = ((solver_result.get("selected_solution") or {}).get("simulation") or {})
    balance_evaluation = simulation.get("resource_balance_evaluation") or {}
    if balance_evaluation:
        lines += ["", "### 资源软目标", ""]
        for resource, item in balance_evaluation.items():
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- {resource}：模式 {item.get('mode')}；目标 {_n(item.get('target'))}；"
                f"实际 {_n(item.get('actual'))}；短缺 {_n(item.get('shortfall'))}；硬安全线 {item.get('hard_minimum')}"
            )
    lines += _sustainability_sections(simulation)
    opportunity = ((solver_result.get("selected_solution") or {}).get("opportunity_cost_postprocess") or {})
    if opportunity:
        lines += ["", "### 高价值技能机会成本", ""]
        lines += [
            f"- 初始受抑制位置：{len(opportunity.get('initial_opportunity_risks') or [])}",
            f"- 已释放干员：{'、'.join(opportunity.get('released_operators') or []) or '无'}",
            f"- 全日重排变化：{len(opportunity.get('changes') or [])}",
            f"- 剩余风险：{len(opportunity.get('remaining_opportunity_risks') or [])}",
        ]
    lines += _plan_sections(plan, simulation)
    lines += ["", "## 最优性范围", "", f"- 声明：{selected.get('optimality_claim', '—')}"]
    for item in value.get("limitations") or []:
        lines.append(f"- {item}")
    lines += _project_sections(value)
    return "\n".join(lines) + "\n"


def _solver_report(value: dict[str, Any]) -> str:
    selected = value.get("selected_solution") or {}
    sim = selected.get("simulation") or {}
    aggregate = sim.get("aggregate_metrics") or {}
    balance_evaluation = sim.get("resource_balance_evaluation") or {}
    solver = value.get("solver") or {}
    plan = value.get("candidate_plan") or {}
    lines = [
        "# 基建排班求解报告", "",
        f"- 方案ID：{plan.get('plan_id', '—')}",
        f"- 布局：{plan.get('layout', '—')}",
        f"- 目标：{plan.get('goal', '—')}",
        f"- 最优性声明：{solver.get('optimality_claim', '—')}",
        "",
        "## 每日经济", "",
        "| 指标 | 数值 |", "|---|---:|",
        f"| 合成玉 | {_n(aggregate.get('orundum'))} |",
        f"| 龙门币收入 | {_n(aggregate.get('lmd'))} |",
        f"| 龙门币成本 | {_n(aggregate.get('lmd_cost'))} |",
        f"| 龙门币净变化 | {_n(sim.get('net_lmd_balance'))} |",
        f"| 源石碎片净变化 | {_n(sim.get('orundum_shard_balance'))} |",
        f"| 赤金净变化 | {_n(sim.get('pure_gold_balance'))} |",
        "",
        "## 操作时间表", "",
    ]
    if balance_evaluation:
        lines += ["## 资源目标结算", ""]
        for resource, item in balance_evaluation.items():
            if isinstance(item, dict):
                lines.append(
                    f"- {resource}：{item.get('mode')}；目标 {_n(item.get('target'))}；实际 {_n(item.get('actual'))}；短缺 {_n(item.get('shortfall'))}"
                )
        lines.append("")
    lines += _sustainability_sections(sim)
    if sim.get("resource_sustainability"):
        lines.append("")
    for segment_id, segment in (plan.get("segments") or {}).items():
        lines += [f"### {segment.get('start')}–{segment.get('end')}（{_n(segment.get('hours'))}小时）", ""]
        for room_id, room in (segment.get("rooms") or {}).items():
            ops = "、".join(item.get("name", "") for item in room.get("operators") or []) or "空置"
            lines.append(f"- {room_id} / {room.get('product_id')}：{ops}")
        lines.append("")
    lines += ["## 模型边界", ""]
    for item in solver.get("limitations") or []:
        lines.append(f"- {item}")
    lines += _project_sections(value)
    return "\n".join(lines) + "\n"


def _upgrade_report(value: dict[str, Any]) -> str:
    lines = ["# 基建培养收益报告", "", "## 场景对比", "", "| 场景 | 布局 | 合成玉/天 | 龙门币净变化/天 |", "|---|---:|---:|---:|"]
    labels = {
        "current": "当前练度",
        "all_owned_max_base_skills": "全部已拥有干员基建技能上限",
        "targeted_minimum_unlocks": "定向最低解锁",
    }
    for key, label in labels.items():
        row = (value.get("scenarios") or {}).get(key) or {}
        lines.append(f"| {label} | {row.get('layout', '—')} | {_n(row.get('orundum_per_day'))} | {_n(row.get('net_lmd_per_day'))} |")
    lines += ["", "## 培养建议", ""]
    for item in value.get("upgrade_recommendations") or []:
        risk = "；".join(item.get("promotion_risk_notes") or [])
        suffix = f"。风险：{risk}" if risk else ""
        lines.append(
            f"- **{item.get('operator')}**：E{item.get('current_elite')}→E{item.get('target_elite')}，"
            f"目标等级{item.get('target_level')}，用于{'、'.join(item.get('products') or [])}{suffix}"
        )
    lines += ["", "## 边界", ""]
    for item in value.get("limitations") or []:
        lines.append(f"- {item}")
    lines += _project_sections(value)
    return "\n".join(lines) + "\n"


def generate_report(value: dict[str, Any]) -> str:
    if value.get("search_type") == "outer_layout_configuration_plus_inner_hybrid_schedule_solver":
        return _layout_report(value)
    if value.get("result_type") == "hybrid_schedule_solution":
        return _solver_report(value)
    if value.get("search_type") == "current_vs_owned_max_base_skill_ceiling_vs_targeted_unlocks":
        return _upgrade_report(value)
    return "# 结果报告\n\n当前JSON类型尚无专用渲染器。\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="把求解JSON转换为中文Markdown报告")
    parser.add_argument("input")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    value = json.loads(Path(args.input).read_text(encoding="utf-8"))
    text = generate_report(value)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(json.dumps({"output": str(target), "characters": len(text)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
