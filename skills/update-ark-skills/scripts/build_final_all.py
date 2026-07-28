#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Merge an operator roster with parsed base-skill records."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def read_roster(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    first = text.splitlines()[0] if text.splitlines() else ""
    delimiter = "\t" if "\t" in first else ","
    rows = csv.DictReader(text.splitlines(), delimiter=delimiter)
    result = []
    for row in rows:
        name = (row.get("干员名称") or row.get("干员名") or row.get("name") or "").strip()
        if not name:
            continue
        recruited = str(
            row.get("是否已招募") or row.get("recruited") or "TRUE"
        ).strip().lower() in {"true", "1", "yes", "是", "已招募"}
        elite_raw = row.get("精英化等级") or row.get("精英等级") or row.get("elite") or 0
        try:
            elite = int(float(str(elite_raw).replace("E", "").replace("e", "")))
        except ValueError:
            elite = 0
        result.append({"name": name, "recruited": recruited, "elite": max(0, min(2, elite)), "raw": row})
    return result


def read_skills(path: Path) -> list[dict]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("records", payload if isinstance(payload, list) else [])
    result = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.startswith("干员名|"):
            continue
        parts = line.split("|", 4)
        if len(parts) != 5:
            continue
        name, elite, facility, skill_name, description = parts
        result.append({
            "name": name.strip(),
            "elite": int(elite.strip().upper().replace("E", "") or 0),
            "facility": facility.strip(),
            "skill_name": skill_name.strip(),
            "description": description.strip(),
            "base_bonus_pct": 0,
            "tags": [],
            "products": [],
        })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="合并干员练度表和基建技能")
    parser.add_argument("--operators", required=True)
    parser.add_argument("--skills", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--format", choices=["json", "tsv"], default="tsv")
    args = parser.parse_args()

    roster = read_roster(Path(args.operators))
    skills = read_skills(Path(args.skills))
    by_name: dict[str, list[dict]] = {}
    for skill in skills:
        by_name.setdefault(skill["name"], []).append(skill)

    rows = []
    for operator in roster:
        unlocked = [
            item for item in by_name.get(operator["name"], [])
            if int(item.get("elite", 0)) <= operator["elite"]
        ]
        rows.append({
            "name": operator["name"],
            "recruited": operator["recruited"],
            "elite": operator["elite"],
            "skills": unlocked,
        })

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.format == "json":
        output.write_text(json.dumps({"schema_version":1,"operators":rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        lines = ["干员名\t已招募\t精英等级\t设施\t技能名\t技能描述"]
        for operator in rows:
            if not operator["skills"]:
                lines.append(f"{operator['name']}\t{operator['recruited']}\tE{operator['elite']}\t\t\t")
            for skill in operator["skills"]:
                lines.append(
                    f"{operator['name']}\t{operator['recruited']}\tE{operator['elite']}\t"
                    f"{skill.get('facility','')}\t{skill.get('skill_name','')}\t{skill.get('description','')}"
                )
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"生成完成：{len(rows)} 名干员")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
