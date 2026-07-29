#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Report roster coverage, unlocked skills and structured-mechanic confidence."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from data_loader import apply_roster_overrides, operator_index, read_roster, select_available_skills


QUANTITATIVE_EFFECT_RE = re.compile(
    r"(?:\+|-)?\d+(?:\.\d+)?%|每\s*\d+|最多\s*(?:\+|-)?\d+|上限|效率\s*(?:\+|-)?\d+|生产力\s*(?:\+|-)?\d+"
)
PRODUCTION_MAX_EFFECT_RE = re.compile(r"所有(?:贸易站订单效率|制造站生产力).+同种效果取最高")


def skill_structure_issues(skill: dict[str, Any]) -> list[str]:
    description = str(skill.get("description") or "")
    issues: list[str] = []
    if PRODUCTION_MAX_EFFECT_RE.search(description) and not skill.get("effects"):
        issues.append("production_max_effect_missing_effect_group")
    if "与部分技能有特殊叠加规则" in description and not skill.get("special_rules"):
        issues.append("special_stacking_rule_missing_interaction_data")
    return issues


def skill_model_status(skill: dict[str, Any]) -> str:
    if skill_structure_issues(skill):
        return "unsupported"
    explicit = str(skill.get("model_status") or "")
    if explicit:
        return explicit
    bonus = float(skill.get("base_bonus_pct", 0.0) or 0.0)
    if abs(bonus) > 1e-12 or skill.get("tags") or skill.get("mechanism") or skill.get("effects") or skill.get("special_rules"):
        return "structured"
    return "description_only" if skill.get("description") else "verified_zero"


def _relevant_facility_products(config: dict[str, Any]) -> dict[str, set[str]]:
    mode = str(config.get("mode") or "")
    if mode == "fixed_schedule":
        pairs: dict[str, set[str]] = defaultdict(set)
        rooms = ((config.get("facility_configuration") or {}).get("rooms") or {})
        for room in rooms.values():
            facility = str(room.get("facility_id") or "")
            product = str(room.get("product_id") or "")
            if facility:
                pairs[facility].add(product)
        return pairs
    objective = config.get("objective") or {}
    factory_products = {"orundum_shard", "pure_gold"}
    if int(objective.get("minimum_battle_record_factories", 0) or 0) > 0:
        factory_products.add("battle_record")
    return {
        "trading_post": {"orundum_order", "lmd_order"},
        "factory": factory_products,
        "power_plant": {"drone_recovery"},
        "control_center": {"base_management"},
    }


def build_relevant_unmodeled_report(roster_path: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    roster = apply_roster_overrides(read_roster(roster_path), config.get("operator_overrides"))
    index = operator_index()
    relevant = _relevant_facility_products(config)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int]] = set()
    for op in roster:
        record = index.get(op.name)
        if not record:
            continue
        for facility, products in relevant.items():
            for product in products or {""}:
                for skill in select_available_skills(record, facility, op.elite, product, op.level):
                    status = skill_model_status(skill)
                    if status not in {"description_only", "unsupported"}:
                        continue
                    key = (
                        op.name,
                        str(skill.get("variant_group") or skill.get("skill_name") or ""),
                        facility,
                        int(skill.get("elite", 0) or 0),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    description = str(skill.get("description") or "")
                    risk_level = "blocking" if QUANTITATIVE_EFFECT_RE.search(description) else "warning"
                    rows.append({
                        "operator": op.name,
                        "operator_elite": op.elite,
                        "operator_level": op.level,
                        "facility": facility,
                        "products": sorted(set(skill.get("products") or products)),
                        "skill_name": skill.get("skill_name"),
                        "unlock_elite": int(skill.get("elite", 0) or 0),
                        "model_status": status,
                        "risk_level": risk_level,
                        "description": description,
                        "structure_issues": skill_structure_issues(skill),
                    })
    rows.sort(key=lambda item: (item["risk_level"] != "blocking", item["facility"], item["operator"], item["skill_name"]))
    return {
        "report_schema_version": 1,
        "policy": str((config.get("verification") or {}).get("relevant_unmodeled_skill_policy", "warn")),
        "relevant_facility_products": {key: sorted(value) for key, value in sorted(relevant.items())},
        "unmodeled_count": len(rows),
        "blocking_count": sum(item["risk_level"] == "blocking" for item in rows),
        "warning_count": sum(item["risk_level"] == "warning" for item in rows),
        "skills": rows,
    }


def build_coverage_report(
    roster_path: str | Path,
    operator_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    roster = apply_roster_overrides(read_roster(roster_path), operator_overrides)
    index = operator_index()
    unknown: list[str] = []
    by_facility: Counter[str] = Counter()
    by_product: Counter[str] = Counter()
    unlocked_count = 0
    numeric_count = 0
    tagged_count = 0
    description_only: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    operator_rows: list[dict[str, Any]] = []

    for op in roster:
        record = index.get(op.name)
        if not record:
            unknown.append(op.name)
            operator_rows.append({"operator": op.name, "known": False, "unlocked_skills": 0})
            continue
        available: list[dict[str, Any]] = []
        facilities = sorted({str(skill.get("facility") or "") for skill in record.get("skills") or [] if skill.get("facility")})
        products = sorted({product for skill in record.get("skills") or [] for product in skill.get("products") or []})
        for facility in facilities:
            product_candidates = products or [""]
            seen: set[tuple[str, str]] = set()
            for product in product_candidates:
                for skill in select_available_skills(record, facility, op.elite, product, op.level):
                    key = (str(skill.get("variant_group") or skill.get("skill_name") or ""), product)
                    if key in seen:
                        continue
                    seen.add(key)
                    available.append(skill)
        unlocked_count += len(available)
        for skill in available:
            facility = str(skill.get("facility") or "unknown")
            by_facility[facility] += 1
            for product in skill.get("products") or []:
                by_product[str(product)] += 1
            bonus = float(skill.get("base_bonus_pct", 0.0) or 0.0)
            tags = list(skill.get("tags") or [])
            status = skill_model_status(skill)
            status_counts[status] += 1
            if abs(bonus) > 1e-12:
                numeric_count += 1
            if tags or skill.get("mechanism"):
                tagged_count += 1
            if status in {"description_only", "unsupported"} and skill.get("description"):
                description_only.append({
                    "operator": op.name,
                    "facility": facility,
                    "skill_name": skill.get("skill_name"),
                    "model_status": status,
                    "description": skill.get("description"),
                })
        operator_rows.append({
            "operator": op.name,
            "known": True,
            "elite": op.elite,
            "level": op.level,
            "unlocked_skills": len(available),
            "facilities": sorted({str(skill.get("facility") or "") for skill in available}),
        })

    known = len(roster) - len(unknown)
    return {
        "coverage_schema_version": 1,
        "roster": {
            "owned_count": len(roster),
            "known_operator_count": known,
            "unknown_operator_count": len(unknown),
            "operator_coverage_ratio": round(known / len(roster), 6) if roster else 0.0,
            "unknown_operators": sorted(unknown),
        },
        "unlocked_skill_coverage": {
            "unlocked_skill_count": unlocked_count,
            "direct_numeric_skill_count": numeric_count,
            "tagged_complex_skill_count": tagged_count,
            "description_only_skill_count": len(description_only),
            "model_status_counts": dict(sorted(status_counts.items())),
            "by_facility": dict(sorted(by_facility.items())),
            "by_product": dict(sorted(by_product.items())),
        },
        "description_only_examples": description_only[:100],
        "description_only_skills": description_only,
        "operators": operator_rows,
        "interpretation": [
            "干员已收录不等于其全部技能都已数值化。",
            "base_bonus_pct为0且无机制标签的描述只作为证据保留，不应获得猜测收益。",
            "关键房间候选不足时，应先补数据或提供已验证外部证据，再扩大求解。",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="统计干员池数据覆盖和技能结构化程度")
    parser.add_argument("--roster", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    value = build_coverage_report(args.roster)
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(text)
    return 0 if value["roster"]["unknown_operator_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
