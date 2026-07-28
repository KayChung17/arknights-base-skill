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
        replacement = {
            "facility": record.get("facility", ""),
            "elite": int(record.get("elite", 0)),
            "skill_name": record.get("skill_name", ""),
            "description": record.get("description", "") or existing_match.get("description", ""),
            "base_bonus_pct": (
                float(parsed_bonus)
                if parsed_bonus not in (None, "") and (float(parsed_bonus) != 0 or not existing_match)
                else float(existing_match.get("base_bonus_pct", 0))
            ),
            "tags": parsed_tags or list(existing_match.get("tags", [])),
            "products": parsed_products or list(existing_match.get("products", [])),
        }
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
