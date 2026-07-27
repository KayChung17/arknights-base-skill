#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
efficiency_calculator.py — 明日方舟基建效率计算器（五层叠加模型）

Usage:
  python efficiency_calculator.py 贸易站 "干员1,干员2,干员3"
  python efficiency_calculator.py 制造站 "干员1,干员2,干员3" <产品类型>
  python efficiency_calculator.py --check <排班方案.json>
  python efficiency_calculator.py --list-skills

五层叠加体系：
  第1层：干员纸面（技能直接加法 / 巫恋低语清空替代）
  第2层：设施数量加成（按贸易站/发电站数量）
  第3层：全局计数器（杜林/莱茵/精英干员数量→转化）
  第4层：乘算区（但书×1.556等）
  第5层：独立收益（龙舌兰大单+500等）
"""

import json
import sys
from typing import Any


# ============================================================
# Windows 编码兼容
# ============================================================

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def t(text: str) -> str:
    """将 emoji 替换为纯文本以兼容 GBK 终端"""
    return (
        text.replace("\U0001f4ca", "[效率]")
        .replace("\U0001f4cb", "[方案]")
        .replace("✅", "[OK]")
        .replace("❌", "[ERR]")
        .replace("⚠️", "[WARN]")
        .replace("\U0001f389", "[完成]")
    )


# ============================================================
# 干员技能数据库（内建）
# ============================================================

OPERATOR_SKILLS: dict[str, list[dict]] = {
    "但书": [
        {
            "facility": "贸易站",
            "elite": "E2",
            "skill_name": "违约体验·β",
            "desc": "每有4赤金订单中的赤金交付数-1（即2贸易），但每有4赤金订单变为特殊订单",
            "base_bonus": 0,
            "tags": ["multiplier_1.556"],
        }
    ],
    "龙舌兰": [
        {
            "facility": "贸易站",
            "elite": "E2",
            "skill_name": "天道酬勤·β",
            "desc": "订单交付赤金数+1，且订单获取效率+30%",
            "base_bonus": 30,
            "tags": ["independent_500"],
        }
    ],
    "巫恋": [
        {
            "facility": "贸易站",
            "elite": "E1",
            "skill_name": "低语",
            "desc": "订单获取效率+30%",
            "base_bonus": 30,
            "tags": [],
        },
        {
            "facility": "贸易站",
            "elite": "E2",
            "skill_name": "低语",
            "desc": "清空当前贸易站其他干员的订单获取效率加成，改为自己的+65%",
            "base_bonus": 65,
            "tags": ["override"],
        },
    ],
    "鸿雪": [
        {
            "facility": "贸易站",
            "elite": "E2",
            "skill_name": "际崖居民",
            "desc": "每有1名杜林族在非宿舍设施内，提供1条赤金生产线（最多4条）",
            "base_bonus": 0,
            "tags": ["hongxue_production_line"],
        },
        {
            "facility": "贸易站",
            "elite": "E2",
            "skill_name": "销路宣发",
            "desc": "每条赤金生产线提供+5%订单获取效率",
            "base_bonus": 5,
            "tags": ["hongxue_per_line"],
        },
    ],
    "图耶": [
        {
            "facility": "贸易站",
            "elite": "E2",
            "skill_name": "物流规划·β",
            "desc": "订单获取效率+5%；每有2条赤金生产线，额外+15%",
            "base_bonus": 5,
            "tags": ["tuye_per_2_lines"],
        }
    ],
    "绮良": [
        {
            "facility": "贸易站",
            "elite": "E2",
            "skill_name": "订单流可视化·β",
            "desc": "订单获取效率+5%；每有2条赤金生产线可额外获得2条虚拟生产线",
            "base_bonus": 5,
            "tags": ["qiliang_double"],
        }
    ],
    "可露希尔": [
        {
            "facility": "贸易站",
            "elite": "E2",
            "skill_name": "特别订单",
            "desc": "固定获取特别订单，不受效率加成影响",
            "base_bonus": 0,
            "tags": ["special_order"],
        }
    ],
    "月见夜": [{"facility": "贸易站", "elite": "E1", "skill_name": "订单谈判·α", "desc": "订单获取效率+30%", "base_bonus": 30, "tags": []}],
    "空爆": [{"facility": "贸易站", "elite": "E1", "skill_name": "订单谈判·β", "desc": "订单获取效率+35%", "base_bonus": 35, "tags": []}],
    "玫兰莎": [{"facility": "贸易站", "elite": "E1", "skill_name": "订单管理·α", "desc": "订单获取效率+20%", "base_bonus": 20, "tags": []}],
    "慕斯": [{"facility": "贸易站", "elite": "E1", "skill_name": "订单管理·β", "desc": "订单获取效率+25%", "base_bonus": 25, "tags": []}],
    "雪雉": [{"facility": "贸易站", "elite": "E2", "skill_name": "天道酬勤·β", "desc": "每有5%订单效率加成额外提供5%（放大器）", "base_bonus": 0, "tags": ["amplifier"]}],
    "拜松": [{"facility": "贸易站", "elite": "E1", "skill_name": "谈判策略·β", "desc": "订单获取效率+30%", "base_bonus": 30, "tags": []}],
    "推进之王": [{"facility": "贸易站", "elite": "E0", "skill_name": "领袖", "desc": "订单获取效率+15%", "base_bonus": 15, "tags": []}],
    "摩根": [{"facility": "贸易站", "elite": "E0", "skill_name": "格拉斯哥帮·贸易", "desc": "同站每有1名格拉斯哥帮干员，订单获取效率+20%", "base_bonus": 0, "tags": ["glasgow_per_member"]}],
    "戴菲恩": [{"facility": "控制中枢", "elite": "E0", "skill_name": "格拉斯哥帮·中枢", "desc": "每有1名格拉斯哥帮干员在外设施，订单获取效率+10%", "base_bonus": 0, "tags": ["glasgow_center"]}],
    "贝洛内": [{"facility": "贸易站", "elite": "E0", "skill_name": "叙拉古·贸易", "desc": "贸易站每有1名叙拉古干员获取效率+15%", "base_bonus": 15, "tags": []}],
    "伺夜": [{"facility": "贸易站", "elite": "E0", "skill_name": "叙拉古·贸易", "desc": "订单获取效率+30%", "base_bonus": 30, "tags": []}],
    "八幡海铃": [{"facility": "控制中枢", "elite": "E0", "skill_name": "叙拉古·中枢", "desc": "每有1名叙拉古干员在贸易站，全贸易站订单效率+5%", "base_bonus": 0, "tags": ["sirakusa_center"]}],
    "清流": [{"facility": "制造站", "elite": "E1", "skill_name": "水文循环", "desc": "每个贸易站为贵金属类配方提供+20%生产力", "base_bonus": 0, "tags": ["qingliu_per_trading_post"]}],
    "温蒂": [{"facility": "制造站", "elite": "E1", "skill_name": "自动化·β", "desc": "每个发电站为制造站提供+15%生产力", "base_bonus": 0, "tags": ["wendy_per_power_plant"]}],
    "森蚺": [{"facility": "制造站", "elite": "E1", "skill_name": "自动化·γ", "desc": "每个发电站为制造站提供+20%生产力", "base_bonus": 0, "tags": ["senran_per_power_plant"]}],
    "冬时": [{"facility": "制造站", "elite": "E1", "skill_name": "流程优化", "desc": "当前站内其他干员生产力归零，每有1名当前站干员提供+10%", "base_bonus": 0, "tags": ["dongshi_reset"]}],
    "娜斯提": [{"facility": "制造站", "elite": "E2", "skill_name": "莱茵科技·赤金", "desc": "每有1名莱茵生命干员在基建内，赤金生产力+3%（最多5名）", "base_bonus": 0, "tags": ["nasti_per_rhine"]}],
    "多萝西": [{"facility": "制造站", "elite": "E1", "skill_name": "莱茵科技·联动", "desc": "制造站内每有1种莱茵生命干员，+10%生产力（可叠加）", "base_bonus": 0, "tags": ["dorothy_rhine"]}],
    "缪尔赛思": [{"facility": "发电站", "elite": "E0", "skill_name": "莱茵科技·能源", "desc": "每有1名莱茵生命干员在基建内，无人机恢复速度+3%", "base_bonus": 0, "tags": ["miersi_per_rhine"]}],
    "斯卡蒂": [{"facility": "制造站", "elite": "E0", "skill_name": "晨夜间巡", "desc": "制造站生产力+25%", "base_bonus": 25, "tags": []}],
    "幽灵鲨": [{"facility": "制造站", "elite": "E0", "skill_name": "剪不断理还乱", "desc": "制造站生产力+25%", "base_bonus": 25, "tags": []}],
    "歌蕾蒂娅": [{"facility": "控制中枢", "elite": "E0", "skill_name": "集群狩猎·β", "desc": "深海猎人干员在基建内获得特殊加成", "base_bonus": 0, "tags": ["gladia_abyssal"]}],
    "乌尔比安": [{"facility": "制造站", "elite": "E0", "skill_name": "深海猎人·制造", "desc": "深海猎人联动，制造站生产力+20%", "base_bonus": 20, "tags": []}],
    "安哲拉": [{"facility": "制造站", "elite": "E0", "skill_name": "深海猎人·制造", "desc": "深海猎人联动，制造站生产力+20%", "base_bonus": 20, "tags": []}],
    "砾": [{"facility": "制造站", "elite": "E1", "skill_name": "金属工艺·α", "desc": "制造站生产力+35%", "base_bonus": 35, "tags": []}],
    "斑点": [{"facility": "制造站", "elite": "E1", "skill_name": "金属工艺·α", "desc": "制造站生产力+30%", "base_bonus": 30, "tags": []}],
    "夜烟": [{"facility": "制造站", "elite": "E1", "skill_name": "金属工艺·α", "desc": "制造站生产力+30%", "base_bonus": 30, "tags": []}],
    "温米": [{"facility": "制造站", "elite": "E1", "skill_name": "金属工艺·α", "desc": "制造站生产力+30%", "base_bonus": 30, "tags": []}],
    "苍苔": [{"facility": "制造站", "elite": "E0", "skill_name": "金属工艺·苍苔", "desc": "每有1名金属工艺标签干员在同站，赤金生产力+5%", "base_bonus": 0, "tags": ["cangtai_per_metalcraft"]}],
    "引星棘刺": [{"facility": "制造站", "elite": "E0", "skill_name": "金属工艺·引星", "desc": "每有1间贸易站，赤金生产力+3%", "base_bonus": 0, "tags": ["yinji_per_trading_post"]}],
    "野鬃": [{"facility": "制造站", "elite": "E0", "skill_name": "红松骑士·制造", "desc": "制造站生产力+25%", "base_bonus": 25, "tags": []}],
    "灰毫": [{"facility": "制造站", "elite": "E0", "skill_name": "红松骑士·制造", "desc": "制造站生产力+25%", "base_bonus": 25, "tags": []}],
    "正义骑士号": [{"facility": "发电站", "elite": "E0", "skill_name": "红松骑士·能源", "desc": "红松骑士团联动", "base_bonus": 0, "tags": ["redpine_power"]}],
    "芬": [{"facility": "制造站", "elite": "E1", "skill_name": "历阵锐枪·α", "desc": "同站每有1名A1小队成员，+10%生产力", "base_bonus": 0, "tags": ["fen_per_a1"]}],
    "泡普卡": [{"facility": "制造站", "elite": "E1", "skill_name": "beta", "desc": "制造站生产力+30%", "base_bonus": 30, "tags": []}],
    "桃金娘": [{"facility": "制造站", "elite": "E1", "skill_name": "杜林·制造", "desc": "制造站生产力+15%", "base_bonus": 15, "tags": ["duline"]}],
    "杜林": [{"facility": "制造站", "elite": "E1", "skill_name": "杜林·制造", "desc": "制造站生产力+15%", "base_bonus": 15, "tags": ["duline"]}],
    "褐果": [{"facility": "制造站", "elite": "E1", "skill_name": "杜林·制造", "desc": "制造站生产力+15%", "base_bonus": 15, "tags": ["duline"]}],
}

GLOBAL_COUNTER_TAGS = {
    "duline": ["桃金娘", "杜林", "褐果"],
    "rhine_lab": ["伊芙利特", "赫默", "白面鸮", "梅尔", "麦哲伦", "多萝西", "娜斯提", "缪尔赛思", "淬羽赫默", "星源"],
    "abyssal_hunter": ["歌蕾蒂娅", "斯卡蒂", "幽灵鲨", "安哲拉", "乌尔比安", "劳伦缇娜"],
    "glasgow": ["推进之王", "摩根", "戴菲恩"],
    "sirakusa": ["贝洛内", "伺夜", "八幡海铃"],
}


# ============================================================
# 五层计算器
# ============================================================


class EfficiencyCalculator:
    def __init__(self, facility: str, operators: list[str], product: str = "", trading_post_count: int = 3, power_plant_count: int = 2, global_operators: list[str] | None = None):
        self.facility = facility
        self.operators = operators
        self.product = product
        self.trading_post_count = trading_post_count
        self.power_plant_count = power_plant_count
        self.all_ops_in_base = global_operators or operators[:]

    def get_skills(self, name: str) -> list[dict]:
        """返回干员在当前设施下的所有技能，同名技能取最高精英等级版本"""
        if name not in OPERATOR_SKILLS:
            return []
        facility_skills = [s for s in OPERATOR_SKILLS[name] if s["facility"] == self.facility]
        if not facility_skills:
            return []
        # 按技能名分组，每组取最高精英等级
        elite_order = {"E2": 2, "E1": 1, "E0": 0}
        groups: dict[str, list[dict]] = {}
        for s in facility_skills:
            groups.setdefault(s["skill_name"], []).append(s)
        result = []
        for skill_name, variants in groups.items():
            variants.sort(key=lambda x: elite_order.get(x["elite"], 0), reverse=True)
            result.append(variants[0])
        return result

    def count_global_tag(self, tag: str) -> int:
        members = GLOBAL_COUNTER_TAGS.get(tag, [])
        return sum(1 for op in self.all_ops_in_base if op in members)

    def compute(self) -> dict[str, Any]:
        if self.facility == "贸易站":
            return self._compute_trading_post()
        elif self.facility == "制造站":
            return self._compute_manufacturing()
        return {"error": f"不支持的设施: {self.facility}"}

    def _compute_trading_post(self) -> dict[str, Any]:
        layer1_base = 0
        layer2_facility = 0
        layer3_global = 0
        layer4_multiplier = 1.0
        layer5_independent = 0
        has_override = False
        override_value = 0
        operator_details = []

        for op in self.operators:
            skills = self.get_skills(op)
            for s in skills:
                if "override" in s.get("tags", []):
                    has_override = True
                    override_value = s["base_bonus"]

        duline_count = self.count_global_tag("duline")
        production_lines = duline_count
        has_qiliang = "绮良" in self.operators
        has_hongxue = "鸿雪" in self.operators
        has_tuye = "图耶" in self.operators
        if has_qiliang:
            virtual_lines = (production_lines // 2) * 2
            production_lines += virtual_lines

        for op in self.operators:
            skills = self.get_skills(op)
            op_layer1 = 0
            op_layer2 = 0
            op_layer3 = 0
            notes = []
            for s in skills:
                tags = s.get("tags", [])
                if "override" in tags:
                    if has_override:
                        op_layer1 = override_value
                        notes.append(f"低语清空替代: +{override_value}%")
                    continue
                if "multiplier_1.556" in tags:
                    layer4_multiplier = 1.556
                    notes.append("乘算区 ×1.556")
                    continue
                if "independent_500" in tags:
                    layer5_independent = 500
                    notes.append("大单独立收益 +500")
                    if s["base_bonus"] > 0:
                        op_layer1 += s["base_bonus"]
                    continue
                if "hongxue_per_line" in tags:
                    if has_hongxue and production_lines > 0:
                        bonus = production_lines * s["base_bonus"]
                        op_layer3 += bonus
                        notes.append(f"鸿雪销路宣发: {production_lines}条×{s['base_bonus']}%=+{bonus}%")
                    continue
                if "tuye_per_2_lines" in tags:
                    if has_tuye and production_lines >= 2:
                        bonus = (production_lines // 2) * 15
                        op_layer3 += bonus
                        notes.append(f"图耶物流规划: {production_lines}÷2×15%=+{bonus}%")
                    if s["base_bonus"] > 0:
                        op_layer1 += s["base_bonus"]
                    continue
                if "qiliang_double" in tags:
                    if s["base_bonus"] > 0:
                        op_layer1 += s["base_bonus"]
                    notes.append(f"绮良翻倍: {duline_count}条→{production_lines}条")
                    continue
                if "amplifier" in tags:
                    notes.append("雪雉放大器（按队友总效率加成）")
                    continue
                if s["base_bonus"] > 0:
                    op_layer1 += s["base_bonus"]
                    notes.append(f"纸面: +{s['base_bonus']}%")
                if "glasgow_per_member" in tags:
                    glasgow_count = self.count_global_tag("glasgow")
                    bonus = glasgow_count * 20
                    op_layer3 += bonus
                    notes.append(f"摩根: {glasgow_count}名格拉斯哥×20%=+{bonus}%")
                if "glasgow_center" in tags:
                    glasgow_count = self.count_global_tag("glasgow")
                    bonus = glasgow_count * 10
                    op_layer3 += bonus
                    notes.append(f"戴菲恩中枢: {glasgow_count}名格拉斯哥×10%=+{bonus}%")
                if "sirakusa_center" in tags:
                    sirakusa_count = self.count_global_tag("sirakusa")
                    bonus = sirakusa_count * 5
                    op_layer3 += bonus
                    notes.append(f"八幡海铃中枢: {sirakusa_count}名叙拉古×5%=+{bonus}%")

            if not has_override:
                layer1_base += op_layer1
            layer3_global += op_layer3
            operator_details.append({"name": op, "layer1": op_layer1, "layer2": op_layer2, "layer3": op_layer3, "notes": notes})

        paper_total = layer1_base + layer2_facility + layer3_global
        if layer4_multiplier != 1.0:
            effective = paper_total * layer4_multiplier
        else:
            effective = float(paper_total)
        equivalent_total = effective + layer5_independent
        return {
            "facility": "贸易站",
            "operator_detail": operator_details,
            "layer1_base": layer1_base,
            "layer2_facility": layer2_facility,
            "layer3_global": layer3_global,
            "paper_total": paper_total,
            "layer4_multiplier": layer4_multiplier,
            "layer5_independent": layer5_independent,
            "equivalent_total": equivalent_total,
        }

    def _compute_manufacturing(self) -> dict[str, Any]:
        layer1_base = 0
        layer2_facility = 0
        layer3_global = 0
        layer4_multiplier = 1.0
        layer5_independent = 0
        has_dongshi = False
        operator_details = []

        for op in self.operators:
            skills = self.get_skills(op)
            op_layer1 = 0
            op_layer2 = 0
            op_layer3 = 0
            notes = []
            for s in skills:
                tags = s.get("tags", [])
                if "dongshi_reset" in tags:
                    has_dongshi = True
                    num_in_station = len(self.operators)
                    bonus = num_in_station * 10
                    op_layer1 = bonus
                    notes.append(f"冬时归零: {num_in_station}人×10%=+{bonus}%")
                    continue
                if "qingliu_per_trading_post" in tags:
                    if "贵金属" in self.product or "赤金" in self.product:
                        bonus = self.trading_post_count * 20
                        op_layer2 += bonus
                        notes.append(f"清流: {self.trading_post_count}个贸易站×20%=+{bonus}%")
                    else:
                        notes.append("清流: 仅对赤金类配方生效（当前产品不匹配）")
                    continue
                if "wendy_per_power_plant" in tags:
                    bonus = self.power_plant_count * 15
                    op_layer2 += bonus
                    notes.append(f"温蒂: {self.power_plant_count}个发电站×15%=+{bonus}%")
                    continue
                if "senran_per_power_plant" in tags:
                    bonus = self.power_plant_count * 20
                    op_layer2 += bonus
                    notes.append(f"森蚺: {self.power_plant_count}个发电站×20%=+{bonus}%")
                    continue
                if "nasti_per_rhine" in tags:
                    rhine_count = self.count_global_tag("rhine_lab")
                    bonus = min(rhine_count, 5) * 3
                    op_layer3 += bonus
                    notes.append(f"娜斯提: {min(rhine_count, 5)}名莱茵×3%=+{bonus}%")
                    continue
                if "cangtai_per_metalcraft" in tags:
                    metalcraft_names = ["砾", "斑点", "夜烟", "温米", "苍苔"]
                    metalcraft_count = sum(1 for op_name in self.operators if op_name in metalcraft_names)
                    bonus = max(0, (metalcraft_count - 1) * 5)
                    op_layer3 += bonus
                    notes.append(f"苍苔: {metalcraft_count}名金属工艺×5%=+{bonus}%")
                    continue
                if "yinji_per_trading_post" in tags:
                    bonus = self.trading_post_count * 3
                    op_layer3 += bonus
                    notes.append(f"引星棘刺: {self.trading_post_count}个贸易站×3%=+{bonus}%")
                    continue
                if s["base_bonus"] > 0:
                    op_layer1 += s["base_bonus"]

            if not has_dongshi and op_layer1 > 0:
                notes.insert(0, f"纸面: +{op_layer1}%")
            layer1_base += op_layer1
            layer2_facility += op_layer2
            layer3_global += op_layer3
            operator_details.append({"name": op, "layer1": op_layer1, "layer2": op_layer2, "layer3": op_layer3, "notes": notes})

        paper_total = layer1_base + layer2_facility + layer3_global
        equivalent_total = paper_total * layer4_multiplier + layer5_independent

        metalcraft_ops = ["砾", "斑点", "夜烟", "温米", "苍苔"]
        is_metalcraft = any(op in metalcraft_ops for op in self.operators)
        return {
            "facility": "制造站",
            "product": self.product,
            "operator_detail": operator_details,
            "layer1_base": layer1_base,
            "layer2_facility": layer2_facility,
            "layer3_global": layer3_global,
            "paper_total": paper_total,
            "layer4_multiplier": layer4_multiplier,
            "layer5_independent": layer5_independent,
            "equivalent_total": equivalent_total,
            "dongshi_active": has_dongshi,
            "metalcraft_active": is_metalcraft,
        }


# ============================================================
# CLI 入口
# ============================================================


def format_result(result: dict[str, Any]) -> str:
    lines = []
    lines.append(f"\n{'='*50}")
    lines.append(f"{t('[效率]')} {result['facility']} 效率计算")
    lines.append(f"{'='*50}")
    if "product" in result and result["product"]:
        lines.append(f"产品类型: {result['product']}")
    lines.append("\n--- 干员详情 ---")
    for op in result.get("operator_detail", []):
        lines.append(f"  {op['name']}:")
        for note in op.get("notes", []):
            lines.append(f"    {note}")
        if not op.get("notes"):
            lines.append("    （无特殊加成）")
    lines.append("\n--- 五层叠加 ---")
    lines.append(f"第1层（干员纸面）: +{result['layer1_base']:.1f}%")
    lines.append(f"第2层（设施加成）: +{result['layer2_facility']:.1f}%")
    lines.append(f"第3层（全局计数）: +{result['layer3_global']:.1f}%")
    lines.append(f"第4层（乘算区）: ×{result['layer4_multiplier']:.3f}")
    lines.append(f"第5层（独立收益）: +{result['layer5_independent']:.1f}")
    lines.append(f"\n纸面合计: +{result['paper_total']:.1f}%")
    lines.append(f"等效效率: +{result['equivalent_total']:.1f}%")
    lines.append(f"{'='*50}\n")
    return "\n".join(lines)


def check_schedule(schedule_path: str) -> int:
    try:
        with open(schedule_path, "r", encoding="utf-8") as f:
            schedule = json.load(f)
    except FileNotFoundError:
        print(t(f"[ERR] 文件不存在: {schedule_path}"))
        return 1
    except json.JSONDecodeError as e:
        print(t(f"[ERR] JSON 格式错误: {e}"))
        return 1

    errors = []
    layout = schedule.get("layout", "")
    title = schedule.get("name") or schedule.get("title", "")

    # 兼容两种格式：shifts / plans
    shifts_data = schedule.get("shifts", {})
    if not shifts_data and "plans" in schedule:
        # 旧格式：plans
        shifts_data = {}
        for i, plan in enumerate(schedule["plans"]):
            label = plan.get("name", f"方案{i+1}")
            rooms_flat: dict[str, list[str]] = {}
            room_map = {"trading": "贸易站", "manufacture": "制造站", "power": "发电站", "control": "控制中枢", "meeting": "会客室", "hire": "办公室"}
            for room_key, room_list in plan.get("rooms", {}).items():
                rt = room_map.get(room_key, room_key)
                for j, room in enumerate(room_list):
                    ops = room.get("operators", [])
                    label_room = f"{rt}#{j+1}"
                    rooms_flat[label_room] = ops
            shifts_data[label] = {"rooms": rooms_flat, "product": "Pure Gold"}

    print(t(f"\n[方案] 检查排班方案: {schedule_path}"))
    if layout:
        print(f"   布局: {layout}")
    if title:
        print(f"   名称: {title}")
    print(f"   班次: {', '.join(shifts_data.keys())}")

    all_operators_in_schedule: dict[str, list[str]] = {}
    for shift_name, shift_data in shifts_data.items():
        rooms = shift_data.get("rooms", {})
        shift_ops = []
        for room_name, occupants in rooms.items():
            if isinstance(occupants, list):
                shift_ops.extend(occupants)
        all_operators_in_schedule[shift_name] = shift_ops

    for shift_name, ops in all_operators_in_schedule.items():
        seen = {}
        for op in ops:
            if op in seen:
                errors.append(t(f"[ERR] [{shift_name}] {op} 重复出现在同班次"))
            seen[op] = seen.get(op, 0) + 1

    shift_names = list(shifts_data.keys())
    for i in range(len(shift_names) - 1):
        current = shift_names[i]
        next_s = shift_names[i + 1]
        common = set(all_operators_in_schedule.get(current, [])) & set(all_operators_in_schedule.get(next_s, []))
        for op in sorted(common):
            errors.append(t(f"[ERR] [{current}→{next_s}] {op} 连续出现在相邻班次"))

    for shift_name, shift_data in shifts_data.items():
        rooms = shift_data.get("rooms", {})
        for room_name, occupants in rooms.items():
            if not isinstance(occupants, list) or len(occupants) == 0:
                continue
            if "贸易" in room_name or "trading" in room_name:
                calc = EfficiencyCalculator("贸易站", occupants)
                result = calc.compute()
                print(f"\n  [{shift_name}] {room_name}:")
                print(f"    纸面+{result['paper_total']:.1f}% → 等效+{result['equivalent_total']:.1f}%")
            elif "制造" in room_name or "manufacturing" in room_name:
                product_key = shift_data.get("product", "")
                product = {"Pure Gold": "贵金属", "LMD": "龙门币"}.get(product_key, product_key)
                calc = EfficiencyCalculator("制造站", occupants, product=product)
                result = calc.compute()
                print(f"\n  [{shift_name}] {room_name}:")
                print(f"    纸面+{result['paper_total']:.1f}% → 等效+{result['equivalent_total']:.1f}%")

    if errors:
        print(f"\n{'='*50}")
        print(t(f"[ERR] 发现 {len(errors)} 个问题:"))
        for e in errors:
            print(f"  {e}")
        return 1
    else:
        print(t(f"\n[OK] 校验完成，未发现问题"))
        return 0


def list_skills() -> None:
    print(t(f"\n[技能库] 干员技能数据库（共 {len(OPERATOR_SKILLS)} 名干员）"))
    print(f"{'='*60}")
    for name in sorted(OPERATOR_SKILLS.keys()):
        skills = OPERATOR_SKILLS[name]
        for s in skills:
            print(f"  {name} | {s['elite']} | {s['facility']} | {s['skill_name']} | {s['desc']}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] == "--check":
        if len(sys.argv) < 3:
            print("请指定排班方案 JSON 文件路径")
            sys.exit(1)
        sys.exit(check_schedule(sys.argv[2]))

    if sys.argv[1] == "--list-skills":
        list_skills()
        sys.exit(0)

    facility = sys.argv[1]
    if facility not in ["贸易站", "制造站"]:
        print(f"不支持设施: {facility}，仅支持 贸易站/制造站")
        sys.exit(1)

    if len(sys.argv) < 3:
        print(f"请指定干员列表，例如: python {sys.argv[0]} {facility} \"干员1,干员2,干员3\"")
        sys.exit(1)

    operators = [op.strip() for op in sys.argv[2].split(",")]
    product = sys.argv[3] if len(sys.argv) > 3 else ""

    calc = EfficiencyCalculator(facility, operators, product=product)
    result = calc.compute()

    print(format_result(result))
    if "error" in result:
        print(t(f"[ERR] {result['error']}"))
        sys.exit(1)


if __name__ == "__main__":
    main()
