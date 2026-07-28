#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Report roster coverage, unlocked skills and structured-mechanic confidence."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from data_loader import operator_index, read_roster, select_available_skills


def build_coverage_report(roster_path: str | Path) -> dict[str, Any]:
    roster = read_roster(roster_path)
    index = operator_index()
    unknown: list[str] = []
    by_facility: Counter[str] = Counter()
    by_product: Counter[str] = Counter()
    unlocked_count = 0
    numeric_count = 0
    tagged_count = 0
    description_only: list[dict[str, Any]] = []
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
            if abs(bonus) > 1e-12:
                numeric_count += 1
            if tags:
                tagged_count += 1
            if abs(bonus) <= 1e-12 and not tags and skill.get("description"):
                description_only.append({
                    "operator": op.name,
                    "facility": facility,
                    "skill_name": skill.get("skill_name"),
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
            "by_facility": dict(sorted(by_facility.items())),
            "by_product": dict(sorted(by_product.items())),
        },
        "description_only_examples": description_only[:100],
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
