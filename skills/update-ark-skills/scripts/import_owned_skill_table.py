#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import full or roster-scoped Chinese base-skill tables into operator-skills.json.

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
        "送葬人", "圣约送葬人", "见行者", "空弦", "炎狱炎熔", "安德切尔",
    },
    "work_platform": {"Friston-3", "GALLUS²", "Lancet-2", "THRM-EX", "Castle-3"},
    "penguin_logistics": {"能天使", "德克萨斯", "可颂", "空", "莫斯提马"},
    "karlan_trade": {
        "银灰", "灵知", "初雪", "圣聆初雪", "崖心", "角峰", "讯使", "耶拉", "极光", "锏", "雪猎", "凛御银灰",
    },
    "monster_hunter": {"火龙S黑角", "麒麟R夜刀", "泰拉大陆调查团"},
    "sui": {"年", "夕", "令", "重岳", "黍", "望", "余"},
    "siracusa": {
        "安洁莉娜", "拉普兰德", "普罗旺斯", "红云", "布洛卡", "巫恋", "铃兰", "贾维", "奥斯塔",
        "斥罪", "子月", "伺夜", "阿罗玛", "忍冬", "裁度", "荒芜拉普兰德", "贝洛内", "复奏", "德克萨斯",
    },
    "lungmen_guard_department": {"陈", "星熊", "诗怀雅", "斩业星熊"},
    "alternate": {
        "百炼嘉维尔", "承曦格雷伊", "赤刃明霄陈", "纯烬艾雅法拉", "淬羽赫默", "涤火杰西卡",
        "归溟幽灵鲨", "寒芒克洛丝", "荒芜拉普兰德", "火龙S黑角", "假日威龙陈", "缄默德克萨斯",
        "酒神", "凯尔希·思衡托", "雷狼龙S空爆", "历阵锐枪芬", "琳琅诗怀雅", "凛御银灰",
        "怒潮凛冬", "麒麟R夜刀", "圣聆初雪", "圣约送葬人", "司霆惊蛰", "溯光星源",
        "维娜·维多利亚", "维什戴尔", "撷英调香师", "新约能天使", "焰狐龙梓兰", "焰影苇草",
        "炎狱炎熔", "耀骑士临光", "引星棘刺", "斩业星熊", "烛煌", "濯尘芙蓉", "浊心斯卡蒂",
    },
    "glasgow": {"推进之王", "摩根", "戴菲恩", "达格达", "因陀罗"},
    "lee_detective_agency": {"老鲤", "阿", "吽", "槐琥"},
}

KNOWN_TAGS: dict[tuple[str, str], list[str]] = {
    ("巫恋", "低语"): ["shamare_whisper_per_other_worker_45", "room_morale_cost_plus_0.25"],
    ("龙舌兰", "投资·α"): ["tequila_investment_order"],
    ("龙舌兰", "投资·β"): ["tequila_investment_order"],
    ("Friston-3", "“愉快的对谈”"): ["power_with_kaltsit_control_5"],
    ("GALLUS²", "鸡励机制"): ["power_with_other_work_platform_5"],
    ("Miss.Christine", "盛餐的回报"): ["with_jiushen_battle_record_30"],
    ("掠风", "自动化·α"): ["automation_reset_others_per_power_plant_5"],
    ("摩根", "帮派指南针"): ["morgan_glasgow_compass"],
    ("火哨", "代为说项"): ["trade_per_other_worker_15"],
    ("红云", "回收利用"): ["redcloud_capacity_conversion_2"],
    ("维伊", "手艺人"): ["training_room_level_10_cap_30"],
    ("风絮", "“孺子可教！”"): ["trade_per_sui_occupied_facility_4_cap_20"],
    ("三角初华", "偶像光环"): ["ave_dorm_heat_1"],
    ("八幡海铃", "可靠伙伴"): ["ave_heat_10"],
    ("八幡海铃", "家族认可"): ["siracusa_center"],
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
    ("但书", "违约索赔·β"): ["proviso_breach_order"],
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
    ("德克萨斯", "恩怨"): ["texas_with_lappland_65"],
    ("承曦格雷伊", "晨曦"): ["automation_virtual_power_plant_1"],
    ("泰拉大陆调查团", "可靠的随从们"): ["factory_per_catnip_1"],
    ("泰拉大陆调查团", "可爱的艾露猫"): ["trade_per_catnip_3"],
    ("火龙S黑角", "团队合作"): ["catnip_per_control_monster_hunter_2"],
    ("麒麟R夜刀", "耐力回复"): ["catnip_fixed_8"],
    ("暴雨", "护卫"): ["control_room_all_morale_recovery_0.05"],
    ("清道夫", "S.W.E.E.P."): ["control_room_all_morale_recovery_0.05"],
    ("红", "S.W.E.E.P."): ["control_room_all_morale_recovery_0.05"],
    ("玛恩纳", "独善其身"): ["control_room_all_morale_recovery_0.05"],
    ("玛恩纳", "公事公办"): ["mlynar_business_is_business"],
    ("电弧", "点滴关照"): ["control_room_all_morale_recovery_0.05"],
    ("魔王", "魔王传承"): ["demon_king_amiya_pair_morale_recovery_0.05"],
    ("维什戴尔", "巴别塔之帜"): ["babel_other_facility_morale_recovery"],
    ("重岳", "孤光共照"): ["chongyue_other_facility_morale_recovery"],
    ("炎狱炎熔", "异格者"): ["control_alternate_per_member_morale_recovery_0.05"],
    ("濯尘芙蓉", "异格者"): ["control_alternate_per_member_morale_recovery_0.05"],
    ("寒芒克洛丝", "异格者"): ["control_alternate_per_member_morale_recovery_0.05"],
    ("陈", "德才兼备"): ["control_lgd_per_member_morale_recovery_0.05"],
    ("吽", "坚毅随和"): ["control_lee_per_member_morale_recovery"],
    ("戴菲恩", "运筹好手"): ["glasgow_center"],
    ("红隼", "捍卫之道"): ["control_room_all_morale_recovery_0.05"],
    ("魔王", "“未完的故事”"): ["demon_king_amiya_pair_morale_recovery_0.10"],
    ("折光", "鉴定师的眼光"): ["morale_cost_minus_0.25", "time_dependent_probability", "tailoring_alpha_empirical"],
    ("折光", "鉴定师的手段"): ["morale_cost_minus_0.25", "time_dependent_probability", "tailoring_beta_empirical"],
    ("贝洛内", "家族经营·α"): ["vigil_anywhere_trade_bonus_5"],
    ("贝洛内", "家族经营·β"): ["vigil_anywhere_trade_bonus_10"],
    ("贝洛内", "未偿还的债务"): ["vigil_same_room_order_capacity_2", "vigil_same_room_morale_reduction_0.1"],
    ("黑键", "乐感"): ["perception_per_dorm_occupant_1", "perception_to_silent_resonance_1"],
    ("黑键", "徘徊旋律"): ["trade_per_silent_resonance_4_1"],
    ("黑键", "怅惘和声"): ["trade_per_silent_resonance_2_1"],
    ("深律", "心声图绘"): ["silent_resonance_per_extra_recruitment_slot_15"],
    ("伺夜", "新城贸易"): ["trade_reception_room_level_5_cap_40"],
}

KNOWN_BASE_BONUS_OVERRIDES: dict[tuple[str, str], float] = {
    ("巫恋", "低语"): 0.0,
    ("龙舌兰", "投资·α"): 0.0,
    ("龙舌兰", "投资·β"): 0.0,
    ("深律", "心声图绘"): 0.0,
}

KNOWN_EFFECTS: dict[tuple[str, str], list[dict[str, Any]]] = {
    ("Mon3tr", "最高权限"): [{
        "effect_key": "global_factory_productivity_pct",
        "stacking": "max",
        "value_pct": 2.0,
    }],
    ("布丁", "超频"): [{
        "effect_key": "global_factory_productivity_pct",
        "stacking": "max",
        "value_pct": 2.0,
        "condition": {
            "type": "global_group_at_facility_minimum",
            "group": "work_platform",
            "facility": "power_plant",
            "minimum_count": 2,
        },
    }],
    ("斩业星熊", "共事情谊"): [{
        "effect_key": "global_factory_productivity_pct",
        "stacking": "max",
        "value_pct": 3.0,
        "condition": {
            "type": "control_center_group_companion",
            "group": "lungmen_guard_department",
            "minimum_count": 2,
        },
    }],
}

LEGACY_SKILL_RENAMES: dict[tuple[str, str], str] = {
    ("缪尔赛思", "莱茵科技·能源"): "生态科主任",
}

OBSOLETE_TAGS_BY_SKILL: dict[tuple[str, str], set[str]] = {
    ("巫恋", "低语"): {"override_room_direct_bonus", "morale_cost_plus_0.25"},
    ("龙舌兰", "投资·β"): {"independent_order_lmd_500"},
    ("贝洛内", "未偿还的债务"): {"order_capacity_2", "morale_cost_minus_0.1"},
}


KNOWN_VARIANT_GROUPS: dict[str, list[set[str]]] = {
    "蕾缪安": [{"相伴", "订单分发·α"}],
    "圣聆初雪": [{"雪境归心", "圣女声望"}],
    "深靛": [{"灯塔供能模块", "光能充能·α"}],
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
    "折光": [{"鉴定师的眼光", "鉴定师的手段"}],
    "黑键": [{"徘徊旋律", "怅惘和声"}],
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
            clause = description[:match.start()].rsplit("；", 1)[-1]
            if not any(word in clause for word in ("如果", "当与", "当自身", "每有", "每个", "每名", "根据")):
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
    if not any(HEADER_RE.match(line.strip()) for line in lines):
        return parse_full_delimited_table(lines)
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
            base_bonus = first_direct_percent(description, facility)
            base_bonus = KNOWN_BASE_BONUS_OVERRIDES.get(
                (current["name"], skill_name), base_bonus,
            )
            tags = infer_tags(current["name"], skill_name, description)
            current["skills"].append({
                "facility": facility,
                "elite": elite,
                "required_level": required_level,
                "skill_name": skill_name,
                "variant_group": variant_group(current["name"], facility, skill_name),
                "description": description,
                "base_bonus_pct": base_bonus,
                "model_status": "structured" if abs(base_bonus) > 1e-12 or tags else "description_only",
                "tags": tags,
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


def parse_full_delimited_table(lines: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse one complete skill per line: operator|unlock|facility|name|description."""
    by_name: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for line_no, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|", 4)]
        if len(parts) != 5:
            warnings.append(f"第 {line_no} 行字段数不是5: {line[:120]}")
            continue
        name, unlock, facility_label, skill_name, description = parts
        if name in {"干员名", "干员名称", "operator", "name"}:
            continue
        facility = FACILITY_ALIASES.get(facility_label, "")
        if not all((name, skill_name, description)) or not facility:
            warnings.append(f"第 {line_no} 行字段无效或设施未知: {line[:120]}")
            continue
        operator = by_name.setdefault(name, {
            "id": stable_operator_id(name),
            "name": name,
            "groups": [],
            "skills": [],
        })
        elite, required_level = unlock_requirement(unlock)
        base_bonus = KNOWN_BASE_BONUS_OVERRIDES.get(
            (name, skill_name), first_direct_percent(description, facility),
        )
        tags = infer_tags(name, skill_name, description)
        operator["skills"].append({
            "facility": facility,
            "elite": elite,
            "required_level": required_level,
            "skill_name": skill_name,
            "variant_group": variant_group(name, facility, skill_name),
            "description": description,
            "base_bonus_pct": base_bonus,
            "model_status": "structured" if abs(base_bonus) > 1e-12 or tags else "description_only",
            "tags": tags,
            "products": products_for(facility, description, name, skill_name),
            "source_line": line_no,
            **({"effects": KNOWN_EFFECTS[(name, skill_name)]} if (name, skill_name) in KNOWN_EFFECTS else {}),
        })
    for operator in by_name.values():
        for group, members in EXTRA_GROUPS.items():
            if operator["name"] in members:
                operator["groups"].append(group)
    return sorted(by_name.values(), key=lambda item: item["name"]), warnings


def merge_existing(
    parsed: list[dict[str, Any]],
    existing_path: Path | None,
    *,
    retain_existing_only: bool = True,
) -> list[dict[str, Any]]:
    by_name = {item["name"]: item for item in parsed}
    if not existing_path or not existing_path.exists():
        return sorted(by_name.values(), key=lambda item: item["name"])
    existing = json.loads(existing_path.read_text(encoding="utf-8"))
    for old in existing.get("operators", []):
        target = by_name.get(old["name"])
        if target is None:
            if retain_existing_only:
                by_name[old["name"]] = old
            continue
        target["groups"] = sorted(set(target.get("groups", [])) | set(old.get("groups", [])))
        for old_skill in old.get("skills", []):
            tags = list(old_skill.get("tags", []))
            mechanism = old_skill.get("mechanism")
            effects = old_skill.get("effects")
            special_rules = old_skill.get("special_rules")
            model_status = old_skill.get("model_status")
            if not tags and not mechanism and not effects and not special_rules and model_status is None:
                continue
            matched_skill_name = LEGACY_SKILL_RENAMES.get(
                (old["name"], str(old_skill.get("skill_name") or "")),
                old_skill.get("skill_name"),
            )
            renamed_legacy_skill = matched_skill_name != old_skill.get("skill_name")
            exact = next((
                skill for skill in target["skills"]
                if skill.get("facility") == old_skill.get("facility")
                and skill.get("skill_name") == matched_skill_name
                and (
                    renamed_legacy_skill
                    or int(skill.get("elite", 0)) == int(old_skill.get("elite", 0))
                )
            ), None)
            if exact is not None:
                obsolete = OBSOLETE_TAGS_BY_SKILL.get((target["name"], str(exact.get("skill_name") or "")), set())
                exact["tags"] = sorted((set(exact.get("tags", [])) | set(tags)) - obsolete)
                if (
                    (target["name"], str(exact.get("skill_name") or "")) not in KNOWN_BASE_BONUS_OVERRIDES
                    and float(exact.get("base_bonus_pct", 0) or 0) == 0
                    and float(old_skill.get("base_bonus_pct", 0) or 0)
                ):
                    exact["base_bonus_pct"] = float(old_skill["base_bonus_pct"])
                if old_skill.get("products"):
                    exact["products"] = list(old_skill["products"])
                if mechanism:
                    exact["mechanism"] = mechanism
                if effects:
                    exact["effects"] = effects
                if special_rules:
                    exact["special_rules"] = special_rules
                if model_status:
                    exact["model_status"] = model_status
                continue
            if mechanism or effects or special_rules or model_status in {"structured", "verified_zero", "unsupported"}:
                copied = dict(old_skill)
                copied.setdefault("required_level", 1)
                copied.setdefault("variant_group", variant_group(old["name"], str(copied.get("facility", "")), str(copied.get("skill_name", ""))))
                copied["source_line"] = None
                target["skills"].append(copied)
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
    source_lines = Path(args.input).read_text(encoding="utf-8-sig").splitlines()
    full_delimited_source = not any(HEADER_RE.match(line.strip()) for line in source_lines)
    source_operator_count = len(source_operators)
    source_skill_count = sum(len(item.get("skills", [])) for item in source_operators)
    operators = merge_existing(
        source_operators,
        Path(args.existing) if args.existing else None,
        retain_existing_only=not full_delimited_source,
    )
    payload = {
        "schema_version": 1,
        "data_version": args.data_version,
        "source": (
            "Full operator base-skill table with structured-rule overlay"
            if full_delimited_source
            else "Roster-scoped operator base-skill table with structured-rule overlay"
        ),
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
