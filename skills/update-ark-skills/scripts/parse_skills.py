#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parse raw or delimited Arknights base-skill text into normalized JSON/pipe data.

Accepted inputs:
1. Pipe/TSV rows containing operator, elite, facility, skill name, description.
2. Loose text blocks where a header line contains operator/elite/facility and
   the following line contains a skill name and description.

The parser preserves unrecognized rows in the warnings output instead of
silently dropping them.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

FACILITY_ALIASES = {
    "贸易站": "trading_post",
    "制造站": "factory",
    "发电站": "power_plant",
    "控制中枢": "control_center",
    "宿舍": "dormitory",
    "会客室": "reception_room",
    "办公室": "office",
    "训练室": "training_room",
}

ELITE_RE = re.compile(r"\bE?([012])\b", re.IGNORECASE)


def normalize_elite(value: str) -> int:
    match = ELITE_RE.search(value.strip())
    return int(match.group(1)) if match else 0


def detect_facility(text: str) -> str:
    for label, facility_id in FACILITY_ALIASES.items():
        if label in text:
            return facility_id
    return ""


def parse_delimited(lines: list[str]) -> list[dict]:
    records = []
    for line_no, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        delimiter = "|" if "|" in line else "\t" if "\t" in line else None
        if not delimiter:
            continue
        parts = [part.strip() for part in line.split(delimiter)]
        if len(parts) < 5:
            continue
        if parts[0] in {"干员名", "干员名称", "operator", "name"}:
            continue
        name, elite, facility, skill_name = parts[:4]
        description = delimiter.join(parts[4:]).strip()
        facility_id = FACILITY_ALIASES.get(facility, facility)
        if not name or not skill_name or not description:
            continue
        records.append({
            "name": name,
            "elite": normalize_elite(elite),
            "facility": facility_id,
            "skill_name": skill_name,
            "description": description,
            "base_bonus_pct": 0,
            "tags": [],
            "products": [],
            "source_line": line_no,
        })
    return records


def parse_loose_blocks(lines: list[str]) -> tuple[list[dict], list[str]]:
    records = []
    warnings = []
    current_name = ""
    current_elite = 0
    current_facility = ""

    for line_no, raw in enumerate(lines, 1):
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue

        facility = detect_facility(line)
        elite_match = ELITE_RE.search(line)
        # Header patterns such as: 但书 E2 贸易站
        if facility and elite_match:
            before_facility = line.split(next(label for label in FACILITY_ALIASES if label in line), 1)[0]
            name = re.sub(r"\bE?[012]\b", "", before_facility, flags=re.IGNORECASE).strip(" -|：:")
            if name:
                current_name = name.split()[-1]
                current_elite = normalize_elite(elite_match.group(0))
                current_facility = facility
                continue

        # Skill rows: 技能名 | 描述, 技能名：描述, or two-space separated.
        if current_name and current_facility:
            if "|" in line:
                parts = [part.strip() for part in line.split("|", 1)]
            elif "：" in line:
                parts = [part.strip() for part in line.split("：", 1)]
            elif ":" in line:
                parts = [part.strip() for part in line.split(":", 1)]
            else:
                parts = re.split(r"\s{2,}", raw.strip(), maxsplit=1)
                parts = [part.strip() for part in parts]
            if len(parts) == 2 and all(parts):
                records.append({
                    "name": current_name,
                    "elite": current_elite,
                    "facility": current_facility,
                    "skill_name": parts[0],
                    "description": parts[1],
                    "base_bonus_pct": 0,
                    "tags": [],
                    "products": [],
                    "source_line": line_no,
                })
                continue

        warnings.append(f"第 {line_no} 行未解析: {line[:120]}")
    return records, warnings


def deduplicate(records: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for record in records:
        key = (
            record["name"],
            record["elite"],
            record["facility"],
            record["skill_name"],
            record["description"],
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result


def write_output(path: Path, records: list[dict], warnings: list[str], output_format: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "json":
        payload = {
            "schema_version": 1,
            "records": records,
            "warnings": warnings,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        lines = ["干员名|精等级|设施|技能名|技能描述"]
        for item in records:
            lines.append(
                f"{item['name']}|E{item['elite']}|{item['facility']}|"
                f"{item['skill_name']}|{item['description']}"
            )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="解析明日方舟基建技能文本")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--format", choices=["json", "pipe"], default="json")
    args = parser.parse_args()

    input_path = Path(args.input)
    lines = input_path.read_text(encoding="utf-8-sig").splitlines()
    records = parse_delimited(lines)
    warnings: list[str] = []
    if not records:
        records, warnings = parse_loose_blocks(lines)
    records = deduplicate(records)
    write_output(Path(args.output), records, warnings, args.format)
    print(f"解析完成：{len(records)} 条技能，{len(warnings)} 条未解析记录")
    return 0 if records else 1


if __name__ == "__main__":
    raise SystemExit(main())
