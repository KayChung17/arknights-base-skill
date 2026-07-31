#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict input preflight and configuration resolution for arkbase projects."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from layout_profiles import facility_configuration_power_summary
from online_schedule import candidate_online_times
from data_loader import apply_roster_overrides, read_roster
from right_side_schedule import validate_right_side_schedule

TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

REPOSITORY_DEFAULTS: dict[str, Any] = {
    "/objective/goal": "赚钱+搓玉",
    "/objective/online_schedule": {"mode": "optimize", "count": 3, "candidate_step_minutes": 60, "max_candidates": 48},
    "/objective/minimum_net_lmd_per_day": 0.0,
    "/objective/minimum_originium_shard_balance": 0.0,
    "/objective/minimum_pure_gold_balance": 0.0,
    "/base_state/drone_capacity": 235.0,
    "/base_state/dormitory_levels": [5, 5, 5, 5],
    "/base_state/right_side_levels": {
        "reception_room": 3,
        "office": 3,
        "training_room": 3,
        "workshop": 3,
    },
    "/horizon/mode": "steady_state",
    "/profiles/mode": "representative",
    "/search/top_k": 30,
    "/search/operator_pool_size": 12,
    "/search/time_limit_seconds": 12.0,
    "/search/max_proxy_attempts": 4,
    "/search/top_solutions": 5,
    "/search/mip_rel_gap": 0.001,
}

CRITICAL_PATHS = (
    "/objective/goal",
    "/objective/online_schedule",
    "/objective/minimum_net_lmd_per_day",
    "/base_state/drone_capacity",
    "/base_state/dormitory_levels",
    "/base_state/right_side_levels",
    "/base_state/right_side_levels_confirmed",
    "/right_side_schedule",
    "/horizon/mode",
)


class PreflightError(RuntimeError):
    """Raised when a project cannot enter the solver."""

    def __init__(self, report: dict[str, Any]):
        super().__init__(f"项目预检状态为 {report.get('status')}")
        self.report = report


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("项目配置根节点必须是JSON对象")
    return value


def _parts(pointer: str) -> list[str]:
    return [part for part in pointer.split("/") if part]


def _get(value: dict[str, Any], pointer: str) -> Any:
    current: Any = value
    for part in _parts(pointer):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _has(value: dict[str, Any], pointer: str) -> bool:
    current: Any = value
    for part in _parts(pointer):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def _set(value: dict[str, Any], pointer: str, item: Any) -> None:
    parts = _parts(pointer)
    current = value
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = copy.deepcopy(item)


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            pointer = f"{prefix}/{key}"
            output.update(_flatten(item, pointer))
        return output
    return {prefix or "/": value}


def canonical_config(config: dict[str, Any]) -> dict[str, Any]:
    clean = copy.deepcopy(config)
    clean.pop("_resolution", None)
    return clean


def config_sha256(config: dict[str, Any]) -> str:
    payload = json.dumps(canonical_config(config), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _migrate_shard_field(config: dict[str, Any], deprecations: list[dict[str, Any]], conflicts: list[dict[str, Any]]) -> None:
    objective = config.setdefault("objective", {})
    if not isinstance(objective, dict):
        conflicts.append({"path": "/objective", "code": "invalid_type", "message": "objective 必须是对象。"})
        return
    old = objective.get("minimum_orundum_shard_balance")
    new = objective.get("minimum_originium_shard_balance")
    if old is not None and new is not None and float(old) != float(new):
        conflicts.append({
            "path": "/objective",
            "code": "shard_field_conflict",
            "message": "minimum_orundum_shard_balance 与 minimum_originium_shard_balance 数值冲突。",
        })
        return
    if new is None and old is not None:
        objective["minimum_originium_shard_balance"] = old
        deprecations.append({
            "path": "/objective/minimum_orundum_shard_balance",
            "replacement": "/objective/minimum_originium_shard_balance",
            "message": "Orundum 表示合成玉；源石碎片字段已迁移为 Originium Shard。",
        })


def preflight_project(config_path: str | Path, *, strict: bool = True) -> dict[str, Any]:
    path = Path(config_path).resolve()
    raw = _read_json(path)
    config = copy.deepcopy(raw)
    missing: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    deprecations: list[dict[str, Any]] = []
    assumptions: list[dict[str, Any]] = []
    source_map = {pointer: "user_provided" for pointer in _flatten(config)}

    _migrate_shard_field(config, deprecations, conflicts)
    objective = config.setdefault("objective", {})
    if isinstance(objective, dict) and "online_schedule" not in objective and "online_times" in objective:
        values = list(objective.get("online_times") or [])
        objective["online_schedule"] = {
            "mode": "fixed", "count": len(values),
            "candidate_step_minutes": 60, "max_candidates": 1,
        }
        deprecations.append({
            "path": "/objective/online_times",
            "replacement": "/objective/online_schedule",
            "message": "显式 online_times 已迁移为 online_schedule.mode=fixed。",
        })
    if deprecations:
        source_map["/objective/minimum_originium_shard_balance"] = "deprecated_alias"

    policy = config.get("input_policy") or {}
    if not isinstance(policy, dict):
        conflicts.append({"path": "/input_policy", "code": "invalid_type", "message": "input_policy 必须是对象。"})
        policy = {}
    allow_all_defaults = bool(policy.get("allow_repository_defaults", False)) and not strict
    authorized = set(str(item) for item in policy.get("authorized_defaults", []) or [])

    semantic_defaults = set(CRITICAL_PATHS) | {"/profiles/mode"}
    for pointer, default in REPOSITORY_DEFAULTS.items():
        if _has(config, pointer):
            continue
        if pointer not in semantic_defaults:
            _set(config, pointer, default)
            source_map[pointer] = "repository_default"
            assumptions.append({"path": pointer, "value": default, "source": source_map[pointer]})
        elif not strict and (allow_all_defaults or pointer in authorized):
            _set(config, pointer, default)
            source_map[pointer] = "explicit_user_authorized_default"
            assumptions.append({"path": pointer, "value": default, "source": source_map[pointer]})

    required_top = ("schema_version", "mode", "roster", "objective", "base_state", "horizon")
    for key in required_top:
        if key not in config:
            missing.append({"path": f"/{key}", "code": "required", "message": f"缺少字段 {key}。"})

    for pointer in CRITICAL_PATHS:
        if not _has(config, pointer):
            missing.append({"path": pointer, "code": "required", "message": f"缺少关键输入 {pointer}。"})
    for pointer in ("/objective/max_daily_work_hours", "/preferences/solver/operator_max_daily_hours"):
        if _has(config, pointer):
            conflicts.append({
                "path": pointer,
                "code": "obsolete_work_hour_limit",
                "message": "固定工时上限已移除；可工作时长由上线区间、心情消耗、宿舍恢复和重复日闭环决定。",
            })
    if not _has(config, "/objective/online_schedule") and not _has(config, "/objective/online_times"):
        # Keep the legacy diagnostic path so callers can migrate incrementally.
        missing.append({"path": "/objective/online_times", "code": "legacy_alias_required", "message": "请提供 online_schedule，或提供旧字段 online_times。"})

    mode = str(config.get("mode", ""))
    if mode not in {"layout_search", "upgrade_search", "fixed_schedule"}:
        conflicts.append({"path": "/mode", "code": "invalid_mode", "message": "mode 必须是 layout_search、upgrade_search 或 fixed_schedule。"})
    if mode in {"layout_search", "upgrade_search"} and not isinstance(config.get("profiles"), dict):
        missing.append({"path": "/profiles", "code": "required_for_mode", "message": f"{mode} 必须提供 profiles。"})
    elif mode in {"layout_search", "upgrade_search"} and not _has(config, "/profiles/mode"):
        missing.append({"path": "/profiles/mode", "code": "required_for_mode", "message": f"{mode} 必须明确 profiles.mode。"})
    if mode == "fixed_schedule" and not isinstance(config.get("facility_configuration"), dict):
        missing.append({
            "path": "/facility_configuration",
            "code": "required_for_fixed_schedule",
            "message": "fixed_schedule 必须逐房间提供 facility_configuration，单独提供布局简称不足以复算。",
        })

    roster_value = config.get("roster")
    roster_path: Path | None = None
    if isinstance(roster_value, str) and roster_value:
        roster_path = Path(roster_value)
        roster_path = roster_path if roster_path.is_absolute() else (path.parent / roster_path).resolve()
        if not roster_path.exists():
            conflicts.append({"path": "/roster", "code": "file_not_found", "message": f"干员表不存在: {roster_path}"})
    elif "roster" in config:
        conflicts.append({"path": "/roster", "code": "invalid_roster", "message": "roster 必须是非空路径字符串。"})

    schedule = _get(config, "/objective/online_schedule")
    if schedule is None:
        schedule = {}
    elif not isinstance(schedule, dict):
        conflicts.append({"path": "/objective/online_schedule", "code": "invalid_schedule", "message": "online_schedule 必须是对象。"})
        schedule = {}
    schedule_mode = str(schedule.get("mode", ""))
    count = schedule.get("count")
    if schedule_mode and schedule_mode not in {"fixed", "optimize"}:
        conflicts.append({"path": "/objective/online_schedule/mode", "code": "invalid_schedule_mode", "message": "mode 必须是 fixed 或 optimize。"})
    if schedule_mode and (not isinstance(count, int) or not 1 <= count <= 4):
        conflicts.append({"path": "/objective/online_schedule/count", "code": "invalid_online_count", "message": "count 必须是 1 到 4 的整数。"})
    online_times = _get(config, "/objective/online_times")
    if schedule_mode == "fixed" and online_times is None:
        conflicts.append({"path": "/objective/online_times", "code": "required_for_fixed_schedule", "message": "fixed 模式必须提供 online_times。"})
    if online_times is not None:
        if not isinstance(online_times, list) or not online_times:
            conflicts.append({"path": "/objective/online_times", "code": "invalid_times", "message": "online_times 必须是非空数组。"})
        else:
            invalid = [item for item in online_times if not isinstance(item, str) or not TIME_RE.match(item)]
            if invalid:
                conflicts.append({"path": "/objective/online_times", "code": "invalid_time_format", "message": f"无效上线时刻: {invalid}"})
            if len(set(online_times)) != len(online_times):
                conflicts.append({"path": "/objective/online_times", "code": "duplicate_times", "message": "上线时刻不得重复。"})
            if isinstance(count, int) and len(online_times) != count:
                conflicts.append({"path": "/objective/online_times", "code": "count_mismatch", "message": "online_times 数量必须与 online_schedule.count 一致。"})
    if schedule_mode == "optimize":
        try:
            candidates = candidate_online_times(
                int(count), mode="optimize",
                step_minutes=int(schedule.get("candidate_step_minutes", 60)),
                max_candidates=int(schedule.get("max_candidates", 48)),
            )
            if not _has(config, "/objective/online_times") and candidates:
                _set(config, "/objective/online_times", candidates[0])
                source_map["/objective/online_times"] = "derived_candidate_baseline"
                assumptions.append({"path": "/objective/online_times", "value": candidates[0], "source": "derived_candidate_baseline"})
        except (TypeError, ValueError) as exc:
            conflicts.append({"path": "/objective/online_schedule", "code": "invalid_candidate_grid", "message": str(exc)})

    right_side_schedule = config.get("right_side_schedule")
    if right_side_schedule is not None and isinstance(count, int):
        known_names: set[str] | None = None
        if roster_path is not None and roster_path.exists():
            known_names = {
                item.name
                for item in apply_roster_overrides(read_roster(roster_path), config.get("operator_overrides"))
            }
        for message in validate_right_side_schedule(
            right_side_schedule,
            segment_count=count,
            known_names=known_names,
        ):
            conflicts.append({
                "path": "/right_side_schedule",
                "code": "invalid_right_side_schedule",
                "message": message,
            })

    capacity = _get(config, "/base_state/drone_capacity")
    stock = _get(config, "/base_state/initial_drone_stock")
    if capacity is not None and stock is not None:
        try:
            if float(capacity) < 0 or float(stock) < 0:
                raise ValueError
            if float(stock) > float(capacity):
                conflicts.append({"path": "/base_state/initial_drone_stock", "code": "stock_exceeds_capacity", "message": "初始无人机库存不能超过持有上限。"})
        except (TypeError, ValueError):
            conflicts.append({"path": "/base_state", "code": "invalid_drone_values", "message": "无人机库存和上限必须是非负数值。"})

    dorms = _get(config, "/base_state/dormitory_levels")
    if dorms is not None and (not isinstance(dorms, list) or len(dorms) != 4):
        conflicts.append({"path": "/base_state/dormitory_levels", "code": "invalid_dormitories", "message": "dormitory_levels 必须包含四座宿舍等级。"})
    right = _get(config, "/base_state/right_side_levels")
    if isinstance(right, dict):
        for key in ("reception_room", "office", "training_room", "workshop"):
            if key not in right:
                missing.append({"path": f"/base_state/right_side_levels/{key}", "code": "required", "message": f"缺少右侧设施等级 {key}。"})
            elif not isinstance(right[key], int) or right[key] not in {1, 2, 3}:
                conflicts.append({"path": f"/base_state/right_side_levels/{key}", "code": "invalid_level", "message": f"{key} 必须是 1至3 级整数。"})
        extras = sorted(set(right) - {"reception_room", "office", "training_room", "workshop"})
        if extras:
            conflicts.append({"path": "/base_state/right_side_levels", "code": "unknown_facilities", "message": f"右侧设施等级包含未知字段: {extras}"})
    confirmed = _get(config, "/base_state/right_side_levels_confirmed")
    if confirmed is not None and confirmed is not True:
        conflicts.append({
            "path": "/base_state/right_side_levels_confirmed",
            "code": "explicit_confirmation_required",
            "message": "右侧功能设施不可降级，必须根据游戏内实际等级确认为 true。",
        })

    if mode == "fixed_schedule" and isinstance(config.get("facility_configuration"), dict) and isinstance(right, dict):
        try:
            fixed_power = facility_configuration_power_summary(
                config["facility_configuration"],
                right_side_levels=right,
                expected_layout=config.get("layout"),
            )
            if fixed_power["spare_power"] < -1e-9:
                conflicts.append({
                    "path": "/facility_configuration",
                    "code": "power_infeasible",
                    "message": (
                        f"固定排班缺电 {-fixed_power['spare_power']:.0f}："
                        f"供电 {fixed_power['supply']:.0f}，总耗电 {fixed_power['total_consumption']:.0f}，"
                        f"其中不可逆右侧设施 {fixed_power['fixed_right_consumption']:.0f}。"
                    ),
                    "power": fixed_power,
                })
        except (KeyError, TypeError, ValueError) as exc:
            conflicts.append({"path": "/facility_configuration", "code": "invalid_power_configuration", "message": str(exc)})

    horizon = config.get("horizon") or {}
    horizon_mode = horizon.get("mode") if isinstance(horizon, dict) else None
    if horizon_mode not in {None, "steady_state", "finite_days"}:
        conflicts.append({"path": "/horizon/mode", "code": "invalid_horizon", "message": "horizon.mode 必须是 steady_state 或 finite_days。"})
    if horizon_mode == "finite_days":
        if not _has(config, "/base_state/initial_drone_stock"):
            missing.append({
                "path": "/base_state/initial_drone_stock",
                "code": "required_for_finite_days",
                "message": "finite_days 必须提供首个操作节点的无人机库存。",
            })
        days = horizon.get("days")
        if not isinstance(days, int) or days < 1:
            missing.append({"path": "/horizon/days", "code": "required_for_finite_days", "message": "finite_days 必须提供正整数 days。"})
        resources = _get(config, "/base_state/initial_resources")
        if not isinstance(resources, dict):
            missing.append({"path": "/base_state/initial_resources", "code": "required_for_finite_days", "message": "finite_days 必须提供初始资源库存。"})
        else:
            for key in ("lmd", "pure_gold", "originium_shard", "orirock_cube"):
                if key not in resources:
                    missing.append({"path": f"/base_state/initial_resources/{key}", "code": "required_for_finite_days", "message": f"缺少初始资源 {key}。"})
        if roster_path is not None and roster_path.exists():
            roster = apply_roster_overrides(read_roster(roster_path), config.get("operator_overrides"))
            missing_morale = sorted(item.name for item in roster if item.morale is None)
            if missing_morale:
                missing.append({
                    "path": "/roster/current_morale",
                    "code": "required_for_finite_days",
                    "message": f"finite_days 缺少当前心情: {missing_morale}",
                })
        warnings.append({
            "path": "/horizon/mode",
            "code": "finite_days_solver_not_implemented",
            "message": "当前求解器只验证重复日稳态；finite_days 配置会在 run 阶段进入 execution_blocked，避免被当作稳态处理。",
        })

    if isinstance(horizon, dict) and horizon_mode in {"steady_state", "finite_days"}:
        initial_state_policy = "cyclic_phase_free" if horizon_mode == "steady_state" else "account_snapshot_required"
        horizon["initial_state_policy"] = initial_state_policy
        config["horizon"] = horizon
        source_map["/horizon/initial_state_policy"] = "derived_from_horizon_mode"
        assumptions.append({
            "path": "/horizon/initial_state_policy",
            "value": initial_state_policy,
            "source": "derived_from_horizon_mode",
        })

    if int(config.get("schema_version", 1) or 1) != 1:
        conflicts.append({"path": "/schema_version", "code": "unsupported_schema", "message": "当前只支持 schema_version=1。"})

    status = "conflict" if conflicts else ("needs_input" if missing else "ready")
    return {
        "preflight_schema_version": 1,
        "status": status,
        "strict_input": bool(strict),
        "configuration_file": str(path),
        "missing": missing,
        "conflicts": conflicts,
        "warnings": warnings,
        "deprecations": deprecations,
        "assumptions": assumptions,
        "source_map": source_map,
        "config_sha256": config_sha256(config),
        "resolved_config": config,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="检查项目输入是否足以进入仓库求解器")
    parser.add_argument("config")
    parser.add_argument("--output")
    parser.add_argument("--allow-defaults", action="store_true", help="允许使用配置中明确授权的仓库默认值")
    args = parser.parse_args()
    report = preflight_project(args.config, strict=not args.allow_defaults)
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ready" else 4


if __name__ == "__main__":
    raise SystemExit(main())
