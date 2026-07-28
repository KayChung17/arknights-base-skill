#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare evaluated candidates with transparent, advisory profiles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_PROFILES = {
    "balanced": {
        "efficiency": 0.25,
        "coverage": 0.15,
        "stability": 0.10,
        "rotation_diversity": 0.10,
        "warning_cleanliness": 0.10,
        "baseline_fidelity": 0.15,
        "economy_completeness": 0.15,
    },
    "efficiency": {
        "efficiency": 0.55,
        "coverage": 0.10,
        "stability": 0.05,
        "rotation_diversity": 0.05,
        "warning_cleanliness": 0.05,
        "baseline_fidelity": 0.10,
        "economy_completeness": 0.10,
    },
    "low_operation": {
        "efficiency": 0.15,
        "coverage": 0.10,
        "stability": 0.35,
        "rotation_diversity": 0.10,
        "warning_cleanliness": 0.10,
        "baseline_fidelity": 0.10,
        "economy_completeness": 0.10,
    },
    "rotation_safe": {
        "efficiency": 0.15,
        "coverage": 0.10,
        "stability": 0.10,
        "rotation_diversity": 0.30,
        "warning_cleanliness": 0.15,
        "baseline_fidelity": 0.10,
        "economy_completeness": 0.10,
    },
    "guide_fidelity": {
        "efficiency": 0.10,
        "coverage": 0.10,
        "stability": 0.10,
        "rotation_diversity": 0.05,
        "warning_cleanliness": 0.10,
        "baseline_fidelity": 0.35,
        "economy_completeness": 0.20,
    },
}


def _load_evaluation(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if "evaluation" in value and isinstance(value["evaluation"], dict):
        return value["evaluation"]
    return value


def _minmax(values: list[float], value: float, *, higher_is_better: bool = True) -> float:
    low = min(values)
    high = max(values)
    if high == low:
        normalized = 1.0
    else:
        normalized = (value - low) / (high - low)
    return normalized if higher_is_better else 1.0 - normalized


def compare(evaluations: list[dict[str, Any]], weights: dict[str, float]) -> dict[str, Any]:
    valid = [item for item in evaluations if item.get("valid")]
    efficiency_values = [float(item["metrics"].get("weighted_efficiency_points", 0)) for item in valid] or [0]
    reuse_values = [float(item["metrics"].get("cross_shift_reuse_operator_count", 0)) for item in valid] or [0]
    warning_values = [float(item["metrics"].get("validation_warning_count", 0)) for item in valid] or [0]

    rows = []
    for item in evaluations:
        metrics = item.get("metrics", {})
        if not item.get("valid"):
            components = {
                "efficiency": 0.0,
                "coverage": 0.0,
                "stability": 0.0,
                "rotation_diversity": 0.0,
                "warning_cleanliness": 0.0,
                "baseline_fidelity": 0.0,
                "economy_completeness": 0.0,
            }
            score = -1000.0 - float(metrics.get("validation_error_count", 0))
        else:
            components = {
                "efficiency": _minmax(
                    efficiency_values,
                    float(metrics.get("weighted_efficiency_points", 0)),
                ),
                "coverage": float(metrics.get("coverage_ratio", 0)),
                "stability": float(metrics.get("assignment_stability_ratio", 0)),
                "rotation_diversity": _minmax(
                    reuse_values,
                    float(metrics.get("cross_shift_reuse_operator_count", 0)),
                    higher_is_better=False,
                ),
                "warning_cleanliness": _minmax(
                    warning_values,
                    float(metrics.get("validation_warning_count", 0)),
                    higher_is_better=False,
                ),
                "baseline_fidelity": float(metrics.get("guide_baseline_structural_match_ratio", 0)),
                "economy_completeness": float(metrics.get("economy_projection_completeness", 0)),
            }
            score = sum(components[key] * float(weights.get(key, 0)) for key in components) * 100

        rows.append({
            "plan_id": item.get("plan_id"),
            "title": item.get("title"),
            "valid": bool(item.get("valid")),
            "advisory_score": round(score, 3),
            "components": {key: round(value, 6) for key, value in components.items()},
            "metrics": metrics,
        })

    rows.sort(key=lambda row: (row["valid"], row["advisory_score"], str(row["plan_id"])), reverse=True)
    for rank, row in enumerate(rows, 1):
        row["advisory_rank"] = rank

    return {
        "comparison_type": "advisory_script_ranking",
        "weights": weights,
        "ranking": rows,
        "decision_owner": "language_model_with_user_preferences",
        "selection_rule": (
            "模型应结合用户目标、外部证据和候选方案的定性取舍作出最终选择；"
            "可以选择非第一名方案，并说明与用户偏好的对应关系。"
        ),
        "limits": [
            "无效方案始终排在有效方案之后。",
            "分数只在本次候选集合内归一化，不适合跨批次比较。",
            "攻略匹配度与经济完整度已进入参考分，但仍需模型解释所有基线偏离。",
            "参考排序没有证明全局最优。",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="横向比较多个已评估候选排班")
    parser.add_argument("evaluations", nargs="+")
    parser.add_argument("--profile", choices=sorted(DEFAULT_PROFILES), default="balanced")
    parser.add_argument("--weights", help="自定义权重 JSON 文件或 JSON 字符串")
    parser.add_argument("--output")
    args = parser.parse_args()

    weights = DEFAULT_PROFILES[args.profile]
    if args.weights:
        candidate = Path(args.weights)
        custom = json.loads(candidate.read_text(encoding="utf-8") if candidate.exists() else args.weights)
        if not isinstance(custom, dict):
            raise ValueError("自定义权重必须是 JSON 对象")
        weights = {key: float(value) for key, value in custom.items()}
        total = sum(weights.values())
        if total <= 0:
            raise ValueError("自定义权重总和必须大于 0")
        weights = {key: value / total for key, value in weights.items()}

    result = compare([_load_evaluation(path) for path in args.evaluations], weights)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(2)
