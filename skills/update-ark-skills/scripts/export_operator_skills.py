#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Merge parsed records into the canonical operator-skills.json asset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_records(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    return payload.get("records", [])


def main() -> int:
    parser = argparse.ArgumentParser(description="更新标准化干员技能数据文件")
    parser.add_argument("--parsed", required=True, help="parse_skills.py 生成的 JSON")
    parser.add_argument("--existing", required=True, help="现有 operator-skills.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--data-version", required=True)
    args = parser.parse_args()

    existing_path = Path(args.existing)
    existing = json.loads(existing_path.read_text(encoding="utf-8"))
    operators = {item["name"]: item for item in existing.get("operators", [])}

    for record in load_records(Path(args.parsed)):
        name = record["name"]
        operator = operators.setdefault(
            name,
            {"id": f"custom_{len(operators)+1:04d}", "name": name, "groups": [], "skills": []},
        )
        key = (
            int(record.get("elite", 0)),
            record.get("facility", ""),
            record.get("skill_name", ""),
        )
        existing_match = next(
            (
                skill
                for skill in operator.get("skills", [])
                if (
                    int(skill.get("elite", 0)),
                    skill.get("facility", ""),
                    skill.get("skill_name", ""),
                ) == key
            ),
            {},
        )
        parsed_tags = list(record.get("tags", []))
        parsed_products = list(record.get("products", []))
        parsed_bonus = record.get("base_bonus_pct")
        parsed_mechanism = record.get("mechanism")
        parsed_status = record.get("model_status")
        explicit_zero_statuses = {"verified_zero", "conservative_zero", "description_only", "unsupported"}
        resolved_bonus = (
            float(parsed_bonus)
            if parsed_bonus not in (None, "")
            and (
                float(parsed_bonus) != 0
                or not existing_match
                or parsed_status in explicit_zero_statuses
            )
            else float(existing_match.get("base_bonus_pct", 0))
        )
        resolved_tags = parsed_tags or list(existing_match.get("tags", []))
        resolved_mechanism = parsed_mechanism or existing_match.get("mechanism")
        resolved_effects = record.get("effects") or existing_match.get("effects")
        resolved_special_rules = record.get("special_rules") or existing_match.get("special_rules")
        resolved_status = parsed_status or existing_match.get("model_status")
        if not resolved_status:
            resolved_status = (
                "structured"
                if abs(resolved_bonus) > 1e-12 or resolved_tags or resolved_mechanism or resolved_effects or resolved_special_rules
                else ("description_only" if record.get("description") or existing_match.get("description") else "verified_zero")
            )
        replacement = {
            "facility": record.get("facility", ""),
            "elite": int(record.get("elite", 0)),
            "required_level": int(record.get("required_level", existing_match.get("required_level", 1)) or 1),
            "skill_name": record.get("skill_name", ""),
            "variant_group": record.get("variant_group") or existing_match.get("variant_group") or f"{record.get('facility', '')}:skill:{record.get('skill_name', '')}",
            "description": record.get("description", "") or existing_match.get("description", ""),
            "base_bonus_pct": resolved_bonus,
            "model_status": resolved_status,
            "tags": resolved_tags,
            "products": parsed_products or list(existing_match.get("products", [])),
        }
        if resolved_mechanism:
            replacement["mechanism"] = resolved_mechanism
        if resolved_effects:
            replacement["effects"] = resolved_effects
        if resolved_special_rules:
            replacement["special_rules"] = resolved_special_rules
        if record.get("source_line") is not None:
            replacement["source_line"] = record["source_line"]
        filtered = [
            skill for skill in operator.get("skills", [])
            if (
                int(skill.get("elite", 0)),
                skill.get("facility", ""),
                skill.get("skill_name", ""),
            ) != key
        ]
        operator["skills"] = filtered + [replacement]

    output_payload = {
        "schema_version": 1,
        "data_version": args.data_version,
        "source": "Merged from parsed records",
        "operators": sorted(operators.values(), key=lambda item: item["name"]),
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"输出完成：{len(output_payload['operators'])} 名干员")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
