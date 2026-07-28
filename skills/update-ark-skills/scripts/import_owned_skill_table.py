#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import the roster-scoped Chinese base-skill table into operator-skills.json.

Expected block format::

    【干员名】星级6 Lv60 E2
      精2 | 贸易站 | 技能名 | 技能描述
      无 | 宿舍 | 技能名 | 技能描述

The importer keeps the original description, extracts conservative numerical
bonuses for common facilities, and carries forward hand-authored special tags
from an existing structured data file. Complex rules remain explicit tags or
zero-valued rules rather than being silently guessed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

FACILITY_ALIASES = {
    "贸易站": "trading_post",
    "制造站": "factory",
    "发电站": "power_plant",
    "控制中枢": "control_center",
    "宿舍": "dormitory",
    "办公室": "office",
    "人力办公室": "office",
    "会客室": "reception_room",
    "训练室": "training_room",
    "加工站": "workshop",
}

HEADER_RE = re.compile(r"^【(.+?)】星级(\d+)\s+Lv(\d+)\s+E([012])$")
SKILL_RE = re.compile(r"^\s*(精[12]|\d+级|无)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*(.+)$")
PERCENT_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)%")
CAPACITY_RE = re.compile(r"仓库容量(?:上限)?\+([0-9]+(?:\.[0-9]+)?)")
MORALE_DELTA_RE = re.compile(r"心情每小时消耗([+-])([0-9]+(?:\.[0-9]+)?)")

# Group membership needed by structured room-local/global rules. The list is
# intentionally explicit and can be expanded without changing the importer.
EXTRA_GROUPS = {
    "laterano": {
        "新约能天使", "蕾缪安", "安比尔", "能天使", "菲亚梅塔", "莫斯提马",
        "送葬人", "圣约送葬人", "见行者", "空弦", "炎狱炎熔",
    },
    "work_platform": {"Friston-3", "GALLUS²", "Lancet-2", "THRM-EX", "Castle-3"},
    "penguin_logistics": {"能天使", "德克萨斯", "可颂", "空", "莫斯提马"},
    "karlan_trade": {"银灰", "讯使", "崖心", "初雪", "圣聆初雪", "凛御银灰"},
}

KNOWN_TAGS: dict[tuple[str, str], list[str]] = {
    ("三角初华", "偶像光环"): ["ave_dorm_heat_1"],
    ("八幡海铃", "可靠伙伴"): ["ave_heat_10"],
    ("祐天寺若麦", "勤学苦练"): ["ave_heat_10"],
    ("若叶睦", "演技的怪物"): ["ave_heat_20", "ave_trade_per_8_heat_1"],
    ("丰川祥子", "丰富工作经验"): ["ave_gold_base_1_per_20_heat_1"],
    ("新约能天使", "同城加急单"): ["laterano_per_member_15"],
    ("蕾缪安", "相伴"): ["lemuen_with_exusiai_25"],
    ("孑", "摊贩经济"): ["jaye_order_gap_4"],
    ("孑", "市井之道"): ["jaye_order_count_4"],
    ("雪雉", "天道酬勤·α"): ["snowant_amplifier_cap_25"],
    ("雪雉", "天道酬勤·β"): ["snowant_amplifier_cap_35"],
    ("可露希尔", "特别订单"): ["special_order"],
    ("但书", "违约索赔·β"): ["multiplier_1_556"],
    ("但书", "合同法"): ["proviso_breach_order"],
    ("阿兰娜", "机械精通·α"): ["work_platform_per_member_5"],
    ("阿兰娜", "机械精通·β"): ["work_platform_per_member_10"],
    ("阿兰娜", "“搭把手！”"): ["with_wanqing_gold_15"],
    ("凯尔希·思衡托", "“泰拉的方舟”"): ["office_per_elite_facility_4_cap_5"],
    ("望", "权变"): ["wang_layout_balance"],
    ("凯尔希", "最高权限"): ["all_factory_bonus_2"],
    ("明椒", "朝气蓬勃"): ["all_trading_bonus_7"],
    ("阿斯卡纶", "情报主脑"): ["all_trading_bonus_7"],
    ("克洛丝", "慢性子"): ["hourly_growth_15_to_25"],
    ("芬", "急性子"): ["hourly_growth_20_to_25"],
    ("泡泡", "大就是好！"): ["bubble_capacity_conversion"],
}


KNOWN_VARIANT_GROUPS: dict[str, list[set[str]]] = {
    "蕾缪安": [{"相伴", "订单分发·α"}],
    "圣聆初雪": [{"雪境归心", "圣女声望"}],
    "深靛": [{"灯塔供能模块", "光能充能·α"}],
    "褐果": [{"地质学·α", "标准化·α"}],
    "酒神": [{"戏中人", "镜中影"}],
    "凯尔希·思衡托": [{"理论革新", "技术阐明"}],
    "乌啾": [{"街头法则", "“号外！”"}],
    "可颂": [{"使命必达", "企鹅物流·α"}],
    "地灵": [{"准时下班", "天灾信使·α"}],
    "月禾": [{"洞悉人心", "天灾信使·α"}],
    "水灯心": [{"永不停歇·β", "永不停歇·α"}],
    "海蒂": [{"名流欢会", "订单分发·α"}],
    "行箸": [{"踏坊寻味·β", "踏坊寻味·α"}],
    "衡沙": [{"大巴扎管理学", "订单分发·α"}],
    "银灰": [{"喀兰之主", "喀兰贸易·α"}],
    "维娜·维多利亚": [{"外贸决议·β", "外贸决议·α"}],
    "雷蛇": [{"脉冲电弧·β", "脉冲电弧·α"}],
    "雪猎": [{"独当一面", "虔信"}],
}

def normalized_variant_name(skill_name: str) -> str:
    value = re.sub(r"[·・._-]?[αβγ]$", "", skill_name.strip(), flags=re.IGNORECASE)
    return value.strip()

def variant_group(name: str, facility: str, skill_name: str) -> str:
    for index, names in enumerate(KNOWN_VARIANT_GROUPS.get(name, []), 1):
        if skill_name in names:
            return f"{facility}:known:{index}"
    normalized = normalized_variant_name(skill_name)
    if normalized != skill_name.strip():
        return f"{facility}:name:{normalized}"
    return f"{facility}:skill:{skill_name}"


def stable_operator_id(name: str) -> str:
    return "op_" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]


def unlock_requirement(text: str) -> tuple[int, int]:
    text = text.strip()
    if text == "精1":
        return 1, 1
    if text == "精2":
        return 2, 1
    if text.endswith("级"):
        try:
            return 0, int(text[:-1])
        except ValueError:
            return 0, 1
    return 0, 1


def products_for(facility: str, description: str, name: str, skill_name: str) -> list[str]:
    if facility == "trading_post":
        if name in {"但书", "龙舌兰", "可露希尔", "U-Official"}:
            return ["lmd_order"]
        return ["lmd_order", "orundum_order"]
    if facility == "factory":
        if "贵金属" in description or "赤金" in description:
            return ["pure_gold"]
        if "作战记录" in description:
            return ["battle_record"]
        if "源石类" in description or "源石碎片" in description:
            return ["orundum_shard"]
        return ["pure_gold", "battle_record", "orundum_shard"]
    if facility == "power_plant":
        return ["drone_recovery"]
    if facility == "control_center":
        return ["base_management"]
    if facility == "office":
        return ["hr_network"]
    if facility == "dormitory":
        return ["morale_recovery"]
    return []


def first_direct_percent(description: str, facility: str) -> float:
    """Extract a conservative unconditional base percentage.

    Conditional clauses after semicolons/commas are not folded into the base
    value. Complex conditions stay in descriptions/tags for later simulation.
    """
    patterns: list[str]
    if facility == "trading_post":
        patterns = [r"进驻贸易站时，订单获取效率\+([0-9.]+)%", r"订单获取效率\+([0-9.]+)%"]
    elif facility == "factory":
        patterns = [
            r"进驻制造站时，(?:生产[^，；]*?时，)?(?:贵金属类配方的|作战记录类配方的|源石类配方的)?生产力\+([0-9.]+)%",
            r"(?:贵金属类配方的|作战记录类配方的|源石类配方的)生产力\+([0-9.]+)%",
            r"生产力\+([0-9.]+)%",
        ]
    elif facility == "power_plant":
        patterns = [r"无人机充能速度\+([0-9.]+)%"]
    elif facility == "office":
        patterns = [r"人脉资源的联络速度\+([0-9.]+)%", r"联络速度\+([0-9.]+)%"]
    elif facility == "reception_room":
        patterns = [r"线索搜集速度提升([0-9.]+)%"]
    else:
        return 0.0
    for pattern in patterns:
        match = re.search(pattern, description)
        if match:
            value = float(match.group(1))
            prefix = description[: match.start()]
            # If the percentage is introduced by a condition in the current
            # semicolon-delimited clause, keep it out of the direct layer.
            clause = prefix.rsplit("；", 1)[-1]
            if any(word in clause for word in ("如果", "当与", "当自身", "每有", "每个", "每名", "每间", "每台", "每差", "每10", "每3", "根据", "此后", "最终", "首小时", "基建内")):
                continue
            return value
    if facility == "factory":
        negative = re.search(r"生产力-([0-9.]+)%", description)
        if negative:
            clause = description[:negative.start()].rsplit("；", 1)[-1]
            if not any(word in clause for word in ("如果", "当", "每有", "每个", "每名", "每间", "每台", "根据")):
                return -float(negative.group(1))
    return 0.0


def infer_tags(name: str, skill_name: str, description: str) -> list[str]:
    tags = list(KNOWN_TAGS.get((name, skill_name), []))
    capacity = CAPACITY_RE.search(description)
    if capacity:
        tags.append(f"warehouse_capacity_{capacity.group(1)}")
    morale = MORALE_DELTA_RE.search(description)
    if morale:
        sign = "plus" if morale.group(1) == "+" else "minus"
        tags.append(f"morale_cost_{sign}_{morale.group(2)}")
    if "订单上限+" in description:
        match = re.search(r"订单上限\+([0-9]+)", description)
        if match:
            tags.append(f"order_capacity_{match.group(1)}")
    if "最终达到" in description and "%" in description:
        tags.append("time_dependent")
    if "工作时长影响概率" in description:
        tags.append("time_dependent_probability")
    if "同种效果取最高" in description:
        tags.append("non_stacking_max")
    return sorted(set(tags))


def parse_table(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    operators: list[dict[str, Any]] = []
    warnings: list[str] = []
    current: dict[str, Any] | None = None
    for line_no, raw in enumerate(lines, 1):
        line = raw.strip()
        header = HEADER_RE.match(line)
        if header:
            current = {
                "id": stable_operator_id(header.group(1)),
                "name": header.group(1),
                "rarity": int(header.group(2)),
                "source_level": int(header.group(3)),
                "source_elite": int(header.group(4)),
                "groups": [],
                "skills": [],
            }
            operators.append(current)
            continue
        match = SKILL_RE.match(raw)
        if match and current:
            elite, required_level = unlock_requirement(match.group(1))
            facility = FACILITY_ALIASES.get(match.group(2).strip(), "")
            if not facility:
                warnings.append(f"第 {line_no} 行未知设施: {match.group(2).strip()}")
                continue
            skill_name = match.group(3).strip()
            description = match.group(4).strip()
            current["skills"].append({
                "facility": facility,
                "elite": elite,
                "required_level": required_level,
                "skill_name": skill_name,
                "variant_group": variant_group(current["name"], facility, skill_name),
                "description": description,
                "base_bonus_pct": first_direct_percent(description, facility),
                "tags": infer_tags(current["name"], skill_name, description),
                "products": products_for(facility, description, current["name"], skill_name),
                "source_line": line_no,
            })
            continue
        if line.startswith("(无基建技能数据)") or line.startswith("（无基建技能数据）"):
            continue
        if line and current and not line.startswith("="):
            if not line.startswith("干员基建技能全表") and not line.startswith("你拥有"):
                warnings.append(f"第 {line_no} 行未解析: {line[:120]}")

    for operator in operators:
        for group, members in EXTRA_GROUPS.items():
            if operator["name"] in members:
                operator["groups"].append(group)
    return operators, warnings


def merge_existing(parsed: list[dict[str, Any]], existing_path: Path | None) -> list[dict[str, Any]]:
    by_name = {item["name"]: item for item in parsed}
    if not existing_path or not existing_path.exists():
        return sorted(by_name.values(), key=lambda item: item["name"])
    existing = json.loads(existing_path.read_text(encoding="utf-8"))
    for old in existing.get("operators", []):
        target = by_name.get(old["name"])
        if target is None:
            by_name[old["name"]] = old
            continue
        target["groups"] = sorted(set(target.get("groups", [])) | set(old.get("groups", [])))
        for old_skill in old.get("skills", []):
            tags = list(old_skill.get("tags", []))
            if not tags:
                continue
            same_facility = [
                skill for skill in target["skills"]
                if skill.get("facility") == old_skill.get("facility")
                and int(skill.get("elite", 0)) <= int(old_skill.get("elite", 0))
            ]
            if same_facility:
                chosen = max(same_facility, key=lambda skill: (int(skill.get("elite", 0)), int(skill.get("required_level", 1))))
                chosen["tags"] = sorted(set(chosen.get("tags", [])) | set(tags))
                if float(chosen.get("base_bonus_pct", 0) or 0) == 0 and float(old_skill.get("base_bonus_pct", 0) or 0):
                    chosen["base_bonus_pct"] = float(old_skill["base_bonus_pct"])
                if old_skill.get("products"):
                    chosen["products"] = list(old_skill["products"])
            else:
                copied = dict(old_skill)
                copied.setdefault("required_level", 1)
                copied.setdefault("variant_group", variant_group(old["name"], str(copied.get("facility", "")), str(copied.get("skill_name", ""))))
                copied["source_line"] = None
                target["skills"].append(copied)
    return sorted(by_name.values(), key=lambda item: item["name"])


def main() -> int:
    parser = argparse.ArgumentParser(description="导入干员基建技能全表")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--existing")
    parser.add_argument("--data-version", required=True)
    parser.add_argument("--warnings-output")
    args = parser.parse_args()

    source_operators, warnings = parse_table(Path(args.input))
    source_operator_count = len(source_operators)
    source_skill_count = sum(len(item.get("skills", [])) for item in source_operators)
    operators = merge_existing(source_operators, Path(args.existing) if args.existing else None)
    payload = {
        "schema_version": 1,
        "data_version": args.data_version,
        "source": "Roster-scoped owned operator base-skill table; imported with conservative structured extraction",
        "operators": operators,
        "import_summary": {
            "source_operator_count": source_operator_count,
            "source_skill_count": source_skill_count,
            "canonical_operator_count": len(operators),
            "canonical_skill_count": sum(len(item.get("skills", [])) for item in operators),
            "warning_count": len(warnings),
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.warnings_output:
        Path(args.warnings_output).write_text("\n".join(warnings) + ("\n" if warnings else ""), encoding="utf-8")
    print(
        f"导入完成：{len(operators)} 名干员，"
        f"{sum(len(item.get('skills', [])) for item in operators)} 条技能，{len(warnings)} 条警告"
    )
    return 0 if operators else 1


if __name__ == "__main__":
    raise SystemExit(main())
