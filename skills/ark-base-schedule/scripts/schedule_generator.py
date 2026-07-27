#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schedule_generator.py — 明日方舟排班方案生成器

根据干员练度、排班目标、换班次数，生成自定义最优排班表。

Usage:
  python schedule_generator.py --roster 干员练度表.txt --shifts 3 --goal 纯赚钱
  python schedule_generator.py --roster 干员练度表.txt --shifts 3 --goal 全力搓玉 --output 搓玉方案.json
  python schedule_generator.py --roster 干员练度表.txt --shifts 2 --goal 赚钱+搓玉 --layout 243

参数:
  --roster  干员练度表.txt（tab 分隔，列依次为：名称、是否已招募、星级、等级、精英化等级）
  --shifts  每天换班次数：2（12h/12h）或 3（12h/6h/6h，默认）
  --goal    排班目标：
              纯赚钱     — 全部产出龙门币（赤金→贸易站）
              纯搓玉     — 全部产出合成玉（源石碎片→贸易站）
              全力搓玉   — 最大化搓玉效率（推荐 2 贸易）
              赚钱+经验书 — 赤金 + 作战记录混合
              赚钱+搓玉   — 赤金 + 源石碎片混合
  --layout  基建布局（可选）：243(默认) / 252 / 153 / 333 / 342
            不指定时自动推荐最优布局
  --output  输出文件路径（.json），可选，不指定只打印表格
"""

import json
import os
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
    return (
        text.replace("\U0001f4ca", "[效率]")
        .replace("\U0001f4cb", "[方案]")
        .replace("✅", "[OK]")
        .replace("❌", "[ERR]")
        .replace("⚠️", "[WARN]")
        .replace("\U0001f389", "[完成]")
    )


# ============================================================
# 布局定义
# ============================================================

LAYOUTS = {
    "243": {"name": "243", "trading": 3, "manufacturing": 4, "power": 2, "desc": "标准均衡布局，适用于多数场景"},
    "252": {"name": "252", "trading": 2, "manufacturing": 5, "power": 2, "desc": "制造站最大化，适合生产型策略"},
    "153": {"name": "153", "trading": 1, "manufacturing": 5, "power": 3, "desc": "贸易站最少，无人机加速最强"},
    "333": {"name": "333", "trading": 3, "manufacturing": 3, "power": 3, "desc": "电力充沛，适合无人机依赖体系"},
    "342": {"name": "342", "trading": 3, "manufacturing": 4, "power": 2, "desc": "同 243，控制中枢另一配置"},
}

GOALS = {
    "纯赚钱":      "all_gold",
    "纯搓玉":      "all_origin",
    "全力搓玉":    "max_origin",
    "赚钱+经验书": "gold_record",
    "赚钱+搓玉":   "gold_origin",
}

PRODUCT_MAP = {
    "all_gold":    {"制造站": "Pure Gold",  "贸易站": "LMD"},
    "all_origin":  {"制造站": "Origin Stone", "贸易站": "Synthetic Jade"},
    "max_origin":  {"制造站": "Origin Stone", "贸易站": "Synthetic Jade"},
    "gold_record": {"制造站": "Battle Record", "贸易站": "LMD"},
    "gold_origin": {"制造站": "Pure Gold",    "贸易站": "LMD"},
}

PRODUCT_CN = {"Pure Gold": "赤金", "Origin Stone": "源石碎片", "Battle Record": "作战记录", "LMD": "龙门币", "Synthetic Jade": "合成玉"}

# ============================================================
# 链路模板（七条核心链路 + 补充组合）
# ============================================================

# 每个条目：(链路名, 设施, 干员列表, 全局计数器条件, 权重)
CIRCUIT_LINES: list[dict] = [
    # ---- 贸易站 ----
    {"name": "赤金生产线满配", "facility": "贸易站", "key_ops": ["鸿雪E2", "图耶E2", "绮良E2", "桃金娘", "杜林", "褐果"], "min_ops": 3, "weight": 100, "requires_global": {"duline": 4}},
    {"name": "但书×1.556乘算", "facility": "贸易站", "key_ops": ["但书E2"], "min_ops": 1, "weight": 90},
    {"name": "龙舌兰独立收益", "facility": "贸易站", "key_ops": ["龙舌兰E2"], "min_ops": 1, "weight": 85},
    {"name": "巫恋低语替代", "facility": "贸易站", "key_ops": ["巫恋E2"], "min_ops": 1, "weight": 80},
    {"name": "叙拉古链路", "facility": "贸易站", "key_ops": ["伺夜", "贝洛内", "八幡海铃"], "min_ops": 1, "weight": 60},
    {"name": "格拉斯哥帮链路", "facility": "贸易站", "key_ops": ["推进之王", "摩根", "戴菲恩"], "min_ops": 1, "weight": 55},
    {"name": "月见夜+空爆", "facility": "贸易站", "key_ops": ["月见夜E1", "空爆E1"], "min_ops": 1, "weight": 40, "fill": True},
    # ---- 制造站（赤金） ----
    {"name": "清流+温蒂+冬时", "facility": "制造站", "product": "Pure Gold", "key_ops": ["清流E1", "温蒂E1", "冬时E1"], "min_ops": 3, "weight": 100},
    {"name": "莱茵科技（娜斯提+多萝西）", "facility": "制造站", "product": "Pure Gold", "key_ops": ["娜斯提E2", "多萝西E1"], "min_ops": 2, "weight": 85, "requires_global": {"rhine_lab": 3}},
    {"name": "金属工艺（砾+引星棘刺+苍苔）", "facility": "制造站", "product": "Pure Gold", "key_ops": ["引星棘刺", "苍苔", "砾E1"], "min_ops": 2, "weight": 80},
    {"name": "深海猎人（斯卡蒂+幽灵鲨）", "facility": "制造站", "product": "Pure Gold", "key_ops": ["斯卡蒂", "幽灵鲨"], "min_ops": 2, "weight": 70},
    {"name": "红松骑士团（野鬃+灰毫）", "facility": "制造站", "product": "Pure Gold", "key_ops": ["野鬃", "灰毫"], "min_ops": 2, "weight": 65},
    {"name": "A1小队（芬+泡普卡）", "facility": "制造站", "product": "Pure Gold", "key_ops": ["芬E1", "泡普卡E1"], "min_ops": 1, "weight": 50, "fill": True},
    {"name": "森蚺自动化", "facility": "制造站", "product": "Pure Gold", "key_ops": ["森蚺E1"], "min_ops": 1, "weight": 45},
    {"name": "单金属工艺基础", "facility": "制造站", "product": "Pure Gold", "key_ops": ["砾E1", "斑点E1", "夜烟E1", "温米E1"], "min_ops": 1, "weight": 35, "fill": True},
    # ---- 制造站（源石） ----
    {"name": "源石制造基础", "facility": "制造站", "product": "Origin Stone", "key_ops": [], "min_ops": 0, "weight": 30, "fill": True},
    # ---- 发电站 ----
    {"name": "杜林族发电", "facility": "发电站", "key_ops": ["桃金娘", "杜林", "褐果"], "min_ops": 1, "weight": 80},
    {"name": "莱茵能源（缪尔赛思）", "facility": "发电站", "key_ops": ["缪尔赛思"], "min_ops": 1, "weight": 75, "requires_global": {"rhine_lab": 3}},
    {"name": "基础发电", "facility": "发电站", "key_ops": [], "min_ops": 0, "weight": 10, "fill": True},
    # ---- 控制中枢 ----
    {"name": "森蚺中枢（+Lancet-2）", "facility": "控制中枢", "key_ops": ["森蚺"], "min_ops": 1, "weight": 90},
    {"name": "歌蕾蒂娅中枢（深海猎人）", "facility": "控制中枢", "key_ops": ["歌蕾蒂娅"], "min_ops": 1, "weight": 85, "requires_global": {"abyssal_hunter": 2}},
    {"name": "八幡海铃中枢（叙拉古）", "facility": "控制中枢", "key_ops": ["八幡海铃"], "min_ops": 1, "weight": 70, "requires_global": {"sirakusa": 2}},
    {"name": "戴菲恩中枢（格拉斯哥）", "facility": "控制中枢", "key_ops": ["戴菲恩"], "min_ops": 1, "weight": 65, "requires_global": {"glasgow": 2}},
    {"name": "基础中枢", "facility": "控制中枢", "key_ops": [], "min_ops": 0, "weight": 10, "fill": True},
]

# 全局计数器标签定义
GLOBAL_TAGS: dict[str, list[str]] = {
    "duline": ["桃金娘", "杜林", "褐果"],
    "rhine_lab": ["伊芙利特", "赫默", "白面鸮", "梅尔", "麦哲伦", "多萝西", "娜斯提", "缪尔赛思", "淬羽赫默", "星源"],
    "abyssal_hunter": ["歌蕾蒂娅", "斯卡蒂", "幽灵鲨", "安哲拉", "乌尔比安", "劳伦缇娜"],
    "glasgow": ["推进之王", "摩根", "戴菲恩"],
    "sirakusa": ["贝洛内", "伺夜", "八幡海铃"],
}

# 干员精等级需求解析辅助
SKILL_ELITE: dict[str, dict[str, str]] = {
    "鸿雪E2":    {"name": "鸿雪", "elite": "2"},
    "图耶E2":    {"name": "图耶", "elite": "2"},
    "绮良E2":    {"name": "绮良", "elite": "2"},
    "但书E2":    {"name": "但书", "elite": "2"},
    "龙舌兰E2":  {"name": "龙舌兰", "elite": "2"},
    "巫恋E2":    {"name": "巫恋", "elite": "2"},
    "清流E1":    {"name": "清流", "elite": "1"},
    "温蒂E1":    {"name": "温蒂", "elite": "1"},
    "冬时E1":    {"name": "冬时", "elite": "1"},
    "娜斯提E2":  {"name": "娜斯提", "elite": "2"},
    "多萝西E1":  {"name": "多萝西", "elite": "1"},
    "砾E1":      {"name": "砾", "elite": "1"},
    "斑点E1":    {"name": "斑点", "elite": "1"},
    "夜烟E1":    {"name": "夜烟", "elite": "1"},
    "温米E1":    {"name": "温米", "elite": "1"},
    "芬E1":      {"name": "芬", "elite": "1"},
    "泡普卡E1":  {"name": "泡普卡", "elite": "1"},
    "月见夜E1":  {"name": "月见夜", "elite": "1"},
    "空爆E1":    {"name": "空爆", "elite": "1"},
    "森蚺E1":    {"name": "森蚺", "elite": "1"},
    "斯卡蒂E0":  {"name": "斯卡蒂", "elite": "0"},
    "幽灵鲨E0":  {"name": "幽灵鲨", "elite": "0"},
    "歌蕾蒂娅E0":{"name": "歌蕾蒂娅", "elite": "0"},
    "缪尔赛思":  {"name": "缪尔赛思", "elite": "0"},
    "八幡海铃":  {"name": "八幡海铃", "elite": "0"},
    "戴菲恩":    {"name": "戴菲恩", "elite": "0"},
    "推进之王E0":{"name": "推进之王", "elite": "0"},
    "摩根":      {"name": "摩根", "elite": "0"},
    "野鬃":      {"name": "野鬃", "elite": "0"},
    "灰毫":      {"name": "灰毫", "elite": "0"},
    "芬":        {"name": "芬", "elite": "0"},
    "泡普卡":    {"name": "泡普卡", "elite": "0"},
    "桃金娘":    {"name": "桃金娘", "elite": "0"},
    "杜林":      {"name": "杜林", "elite": "0"},
    "褐果":      {"name": "褐果", "elite": "0"},
    "贝洛内":    {"name": "贝洛内", "elite": "0"},
    "伺夜":      {"name": "伺夜", "elite": "0"},
    "苍苔":      {"name": "苍苔", "elite": "0"},
    "引星棘刺":  {"name": "引星棘刺", "elite": "0"},
    "安哲拉":    {"name": "安哲拉", "elite": "0"},
    "乌尔比安":  {"name": "乌尔比安", "elite": "0"},
}


# ============================================================
# 数据加载
# ============================================================


def load_roster(path: str) -> dict[str, dict[str, Any]]:
    """加载干员练度表，返回 {name: {star, level, elite, owned, elite_num}}"""
    roster: dict[str, dict[str, Any]] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f.readlines()[1:]:
            parts = line.strip().split("\t")
            if len(parts) < 5:
                continue
            name = parts[0].strip()
            owned = parts[1].strip().upper() == "TRUE"
            roster[name] = {
                "star": parts[2].strip(),
                "level": parts[3].strip(),
                "elite": parts[4].strip() if parts[4].strip() else "0",
                "owned": owned,
                "elite_num": int(parts[4].strip()) if parts[4].strip().isdigit() else 0,
            }
    return roster


def check_operator_available(name: str, required_elite: str, roster: dict[str, dict[str, Any]]) -> bool:
    """检查干员是否可用（已招募且精英等级达标）"""
    if name not in roster:
        return False
    info = roster[name]
    if not info["owned"]:
        return False
    return int(info["elite"]) >= int(required_elite)


def find_available_ops(roster: dict[str, dict[str, Any]], facility: str) -> list[str]:
    """找出在某设施有可用技能的干员"""
    from efficiency_calculator import OPERATOR_SKILLS
    available = []
    for name, info in roster.items():
        if not info["owned"]:
            continue
        if name not in OPERATOR_SKILLS:
            continue
        for sk in OPERATOR_SKILLS[name]:
            if sk["facility"] == facility:
                # 检查精英等级
                elite_order = {"E2": 2, "E1": 1, "E0": 0}
                req = elite_order.get(sk["elite"], 0)
                if info["elite_num"] >= req:
                    available.append(name)
                    break
    return available


# ============================================================
# 布局推荐
# ============================================================


def recommend_layout(goal_key: str, roster: dict[str, dict[str, Any]]) -> str:
    """根据排班目标和干员池推荐最优布局"""
    # 简单策略：看目标
    goal_to_layout = {
        "all_gold":    "243",   # 纯赚钱：3贸易卖赤金
        "all_origin":  "252",   # 纯搓玉：5制造搓源石
        "max_origin":  "252",   # 全力搓玉：5制造最大化
        "gold_record": "243",   # 赚钱+经验书 标准均衡
        "gold_origin": "243",   # 赚钱+搓玉 标准均勄
    }
    return goal_to_layout.get(goal_key, "243")


def count_global_tag(tag: str, roster: dict[str, dict[str, Any]]) -> int:
    """统计全基建某标签干员数量"""
    members = GLOBAL_TAGS.get(tag, [])
    return sum(1 for m in members if m in roster and roster[m]["owned"])


# ============================================================
# 排班生成
# ============================================================


def generate_schedule(
    roster: dict[str, dict[str, Any]],
    goal_key: str,
    shifts_per_day: int,
    layout_name: str = "243",
) -> dict[str, Any]:
    """根据输入生成排班方案。

    三班策略（shift=3）:
      A班(12h): 核心贸易链路 + 高倍率制造
      B班(6h):  核心贸易链路 + 第二梯队制造（贸易复用A班）
      C班(6h):  独立链路（叙拉古/格拉斯哥帮/深海猎人）

    两班策略（shift=2）:
      A班(12h): 核心贸易 + 高倍率制造
      B班(12h): 独立链路（复用部分核心）
    """

    layout = LAYOUTS[layout_name]
    product_map = PRODUCT_MAP[goal_key]
    trading_count = layout["trading"]
    mfg_count = layout["manufacturing"]
    power_count = layout["power"]
    mfg_product = product_map.get("制造站", "Pure Gold")

    # 分析可用链路，按 tier 分类
    trade_lines = _find_available_lines(roster, goal_key, "贸易站")
    mfg_lines_gold = _find_available_lines(roster, goal_key, "制造站", product="Pure Gold")
    mfg_lines_origin = _find_available_lines(roster, goal_key, "制造站", product="Origin Stone")
    power_lines = _find_available_lines(roster, goal_key, "发电站")
    ctrl_lines = _find_available_lines(roster, goal_key, "控制中枢")

    # 选择目标产品对应的制造线
    if mfg_product == "Origin Stone":
        mfg_lines = mfg_lines_origin if mfg_lines_origin else mfg_lines_gold
    else:
        mfg_lines = mfg_lines_gold if mfg_lines_gold else mfg_lines_origin

    if shifts_per_day == 2:
        return _build_2shift(roster, layout_name, layout, trade_lines, mfg_lines, power_lines, ctrl_lines, mfg_product, trading_count, mfg_count, power_count, goal_key)
    else:
        return _build_3shift(roster, layout_name, layout, trade_lines, mfg_lines, power_lines, ctrl_lines, mfg_product, trading_count, mfg_count, power_count, goal_key)


def _build_3shift(
    roster, layout_name, layout, trade_lines, mfg_lines, power_lines, ctrl_lines,
    mfg_product, trading_count, mfg_count, power_count, goal_key,
) -> dict[str, Any]:

    used_a: set[str] = set()
    used_b: set[str] = set()
    used_c: set[str] = set()

    # ---- A班：核心链路 ----
    rooms_a: dict[str, list[str]] = {}
    # 贸易站：选权重最高的链路
    for j in range(trading_count):
        combo = _pick_combo_weighted(trade_lines, used_a, roster, trading_count)
        if not combo:
            combo = _find_fill_ops(roster, "贸易站", used_a)[:3]
        rooms_a[f"贸易站#{j+1}"] = combo
        used_a.update(combo)

    # 制造站
    for j in range(mfg_count):
        combo = _pick_combo_weighted(mfg_lines, used_a, roster, trading_count)
        if not combo:
            combo = _find_fill_ops(roster, "制造站", used_a)[:3]
        rooms_a[f"制造站#{j+1}"] = combo
        used_a.update(combo)

    # 发电站
    for j in range(power_count):
        combo = _pick_combo_weighted(power_lines, used_a, roster, trading_count)
        if not combo:
            combo = _find_fill_ops(roster, "发电站", used_a)[:3]
        rooms_a[f"发电站#{j+1}"] = combo
        used_a.update(combo)

    # 控制中枢
    combo = _pick_combo_weighted(ctrl_lines, used_a, roster, trading_count)
    if not combo:
        combo = _find_fill_ops(roster, "控制中枢", used_a)[:5]
    rooms_a["控制中枢"] = combo
    used_a.update(combo)

    # ---- B班（6h）：贸易复用A班 + 第二梯队制造 ----
    rooms_b: dict[str, list[str]] = {}
    for j in range(trading_count):
        rooms_b[f"贸易站#{j+1}"] = rooms_a[f"贸易站#{j+1}"][:]  # 复用
        used_b.update(rooms_a[f"贸易站#{j+1}"])

    for j in range(mfg_count):
        combo = _pick_combo_weighted(mfg_lines, used_b, roster, trading_count)
        if not combo:
            combo = _find_fill_ops(roster, "制造站", used_b)[:3]
        rooms_b[f"制造站#{j+1}"] = combo
        used_b.update(combo)

    # 发电站（复用A班可能冲突，选不同的）
    for j in range(power_count):
        combo = _pick_combo_weighted(power_lines, used_b, roster, trading_count)
        if not combo:
            combo = _find_fill_ops(roster, "发电站", used_b)[:3]
        rooms_b[f"发电站#{j+1}"] = combo
        used_b.update(combo)

    combo = _pick_combo_weighted(ctrl_lines, used_b, roster, trading_count)
    if not combo:
        combo = _find_fill_ops(roster, "控制中枢", used_b)[:5]
    rooms_b["控制中枢"] = combo
    used_b.update(combo)

    # ---- C班（6h）：独立链路 ----
    # 从 A/B 冲突中解放出来，用尚未使用的干员
    all_used_ab = used_a | used_b
    independent_pool = set(op for op in roster if roster[op]["owned"]) - all_used_ab

    rooms_c: dict[str, list[str]] = {}
    for j in range(trading_count):
        combo = _pick_combo_weighted(trade_lines, used_c, roster, trading_count, pool=independent_pool)
        if not combo:
            combo = _find_fill_ops(roster, "贸易站", used_c | all_used_ab)[:3]
        rooms_c[f"贸易站#{j+1}"] = combo
        used_c.update(combo)

    for j in range(mfg_count):
        combo = _pick_combo_weighted(mfg_lines, used_c, roster, trading_count, pool=independent_pool)
        if not combo:
            combo = _find_fill_ops(roster, "制造站", used_c | all_used_ab)[:3]
        rooms_c[f"制造站#{j+1}"] = combo
        used_c.update(combo)

    for j in range(power_count):
        combo = _pick_combo_weighted(power_lines, used_c, roster, trading_count, pool=independent_pool)
        if not combo:
            combo = _find_fill_ops(roster, "发电站", used_c | all_used_ab)[:3]
        rooms_c[f"发电站#{j+1}"] = combo
        used_c.update(combo)

    combo = _pick_combo_weighted(ctrl_lines, used_c, roster, trading_count, pool=independent_pool)
    if not combo:
        combo = _find_fill_ops(roster, "控制中枢", used_c | all_used_ab)[:5]
    rooms_c["控制中枢"] = combo
    used_c.update(combo)

    shifts = {
        "A班 (08:00-20:00)": {"rooms": rooms_a, "product": mfg_product},
        "B班 (20:00-02:00)": {"rooms": rooms_b, "product": mfg_product},
        "C班 (02:00-08:00)": {"rooms": rooms_c, "product": mfg_product},
    }

    return {
        "name": f"{layout_name} {goal_key} 三班倒排班方案",
        "layout": layout_name,
        "goal": goal_key,
        "shifts_per_day": 3,
        "shifts": shifts,
    }


def _build_2shift(roster, layout_name, layout, trade_lines, mfg_lines, power_lines, ctrl_lines, mfg_product, trading_count, mfg_count, power_count, goal_key):
    used_a: set[str] = set()
    used_b: set[str] = set()

    rooms_a: dict[str, list[str]] = {}
    for j in range(trading_count):
        combo = _pick_combo_weighted(trade_lines, used_a, roster, trading_count)
        rooms_a[f"贸易站#{j+1}"] = combo or _find_fill_ops(roster, "贸易站", used_a)[:3]
        used_a.update(rooms_a[f"贸易站#{j+1}"])

    for j in range(mfg_count):
        combo = _pick_combo_weighted(mfg_lines, used_a, roster, trading_count)
        rooms_a[f"制造站#{j+1}"] = combo or _find_fill_ops(roster, "制造站", used_a)[:3]
        used_a.update(rooms_a[f"制造站#{j+1}"])

    for j in range(power_count):
        combo = _pick_combo_weighted(power_lines, used_a, roster, trading_count)
        rooms_a[f"发电站#{j+1}"] = combo or _find_fill_ops(roster, "发电站", used_a)[:3]
        used_a.update(rooms_a[f"发电站#{j+1}"])

    combo = _pick_combo_weighted(ctrl_lines, used_a, roster, trading_count)
    rooms_a["控制中枢"] = combo or _find_fill_ops(roster, "控制中枢", used_a)[:5]
    used_a.update(rooms_a["控制中枢"])

    # B班：复用核心贸易
    rooms_b: dict[str, list[str]] = {}
    for j in range(trading_count):
        rooms_b[f"贸易站#{j+1}"] = rooms_a[f"贸易站#{j+1}"][:]

    all_used_a = used_a
    for j in range(mfg_count):
        combo = _pick_combo_weighted(mfg_lines, used_b, roster, trading_count, pool=set(roster.keys()) - all_used_a)
        rooms_b[f"制造站#{j+1}"] = combo or _find_fill_ops(roster, "制造站", used_b | all_used_a)[:3]
        used_b.update(rooms_b[f"制造站#{j+1}"])

    for j in range(power_count):
        combo = _pick_combo_weighted(power_lines, used_b, roster, trading_count, pool=set(roster.keys()) - all_used_a)
        rooms_b[f"发电站#{j+1}"] = combo or _find_fill_ops(roster, "发电站", used_b | all_used_a)[:3]
        used_b.update(rooms_b[f"发电站#{j+1}"])

    combo = _pick_combo_weighted(ctrl_lines, used_b, roster, trading_count, pool=set(roster.keys()) - all_used_a)
    rooms_b["控制中枢"] = combo or _find_fill_ops(roster, "控制中枢", used_b | all_used_a)[:5]
    used_b.update(rooms_b["控制中枢"])

    shifts = {
        "A班 (06:00-18:00)": {"rooms": rooms_a, "product": mfg_product},
        "B班 (18:00-06:00)": {"rooms": rooms_b, "product": mfg_product},
    }

    return {
        "name": f"{layout_name} {goal_key} 两班倒排班方案",
        "layout": layout_name,
        "goal": goal_key,
        "shifts_per_day": 2,
        "shifts": shifts,
    }


def _find_available_lines(roster: dict[str, dict[str, Any]], goal_key: str, facility: str = "", product: str = "") -> list[dict]:
    """根据干员池判断哪些链路可用，可筛选设施和产品"""
    available = []
    for line in CIRCUIT_LINES:
        # 筛选设施
        if facility and line["facility"] != facility:
            continue

        # 筛选产品
        if product and line.get("product") and line["product"] != product:
            continue
        if not product and line.get("product"):
            # 如果有产品限定，检查是否匹配目标
            if goal_key == "all_gold" and line.get("product") != "Pure Gold":
                continue
            if goal_key in ("all_origin", "max_origin") and line.get("product") != "Origin Stone":
                continue

        # 检查关键干员是否可用
        if line["key_ops"]:
            available_count = 0
            for key_op in line["key_ops"]:
                if key_op in SKILL_ELITE:
                    info = SKILL_ELITE[key_op]
                    if check_operator_available(info["name"], info["elite"], roster):
                        available_count += 1
                else:
                    if key_op in roster and roster[key_op]["owned"]:
                        available_count += 1

            if available_count < line["min_ops"]:
                continue

            # 检查全局计数器条件
            requires = line.get("requires_global", {})
            satisfied = True
            for tag, minimum in requires.items():
                if count_global_tag(tag, roster) < minimum:
                    satisfied = False
                    break
            if not satisfied:
                continue

        # 找到可用替代干员
        fill_ops = _find_fill_ops(roster, facility, set())
        if line.get("fill", False) and not fill_ops:
            continue

        available.append(line)

    # 按权重排序
    available.sort(key=lambda x: x["weight"], reverse=True)
    return available


def _find_fill_ops(roster: dict[str, dict[str, Any]], facility: str, exclude: set[str]) -> list[str]:
    """找出可用作填充的干员"""
    from efficiency_calculator import OPERATOR_SKILLS
    candidates = []
    for name, info in roster.items():
        if not info["owned"] or name in exclude:
            continue
        if name not in OPERATOR_SKILLS:
            continue
        for sk in OPERATOR_SKILLS[name]:
            if sk["facility"] == facility:
                elite_order = {"E2": 2, "E1": 1, "E0": 0}
                req = elite_order.get(sk["elite"], 0)
                if info["elite_num"] >= req:
                    candidates.append(name)
                    break
    return candidates


def _pick_combo_weighted(
    available_lines: list[dict],
    used_ops: set[str],
    roster: dict[str, dict[str, Any]],
    trading_count: int = 3,
    pool: set[str] | None = None,
) -> list[str]:
    """从可用链路中按权重选最优组合，避免冲突"""
    pool = pool or set(roster.keys())

    for line in available_lines:
        if set(o for o in line["key_ops"] if o in SKILL_ELITE).intersection(used_ops):
            continue  # 关键干员已被占用

        combo = []
        for key_op in line["key_ops"]:
            if key_op in SKILL_ELITE:
                info = SKILL_ELITE[key_op]
                if check_operator_available(info["name"], info["elite"], roster):
                    if info["name"] not in used_ops and info["name"] in pool:
                        combo.append(info["name"])
            else:
                if key_op not in used_ops and key_op in pool and key_op in roster and roster[key_op]["owned"]:
                    combo.append(key_op)

        if len(combo) >= max(1, line["min_ops"]):
            return combo

    # 备选：找填充干员
    fill_ops = _find_fill_ops(roster, available_lines[0]["facility"] if available_lines else "", used_ops)
    return fill_ops[:3]


# ============================================================
# 输出格式化
# ============================================================


def format_table(schedule: dict[str, Any], goal_key: str) -> str:
    """格式化为文字表格"""
    lines = []
    lines.append("=" * 80)
    lines.append(f"  {schedule.get('name', '排班方案')}")
    lines.append(f"  布局: {schedule.get('layout', '')}  目标: {goal_key}")
    lines.append("=" * 80)
    lines.append("")

    product_cn = {"Pure Gold": "赤金", "Origin Stone": "源石", "Battle Record": "作战记录", "LMD": "龙门币", "Synthetic Jade": "合成玉"}
    shifts = schedule.get("shifts", {})

    # 收集所有房间类型
    all_room_types: list[str] = []
    for shift_data in shifts.values():
        for rn in shift_data.get("rooms", {}):
            if rn not in all_room_types:
                all_room_types.append(rn)

    for shift_name, shift_data in shifts.items():
        lines.append(f"── {shift_name} ──")
        rooms = shift_data.get("rooms", {})
        product_type = shift_data.get("product", "")
        if product_type in product_cn:
            lines.append(f"   目标产品: {product_cn[product_type]}")

        for rn, ops in rooms.items():
            if ops:
                lines.append(f"  {rn}: {' + '.join(ops)}")
            else:
                lines.append(f"  {rn}: （空）")
        lines.append("")

    # 统计每个干员出现的班次
    lines.append("─" * 60)
    lines.append("干员出勤统计:")
    op_shifts: dict[str, list[str]] = {}
    for sn, sd in shifts.items():
        rooms = sd.get("rooms", {})
        for ops in rooms.values():
            for op in ops:
                if op not in op_shifts:
                    op_shifts[op] = []
                op_shifts[op].append(sn)

    for op, sn_list in sorted(op_shifts.items()):
        lines.append(f"  {op}: {', '.join(sn_list)}")
    lines.append("")

    lines.append("=" * 80)
    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================


def main():
    import argparse

    parser = argparse.ArgumentParser(description="明日方舟基建排班方案生成器")
    parser.add_argument("--roster", required=True, help="干员练度表.txt 路径")
    parser.add_argument("--shifts", type=int, default=3, choices=[2, 3], help="每天换班次数（2 或 3），默认 3")
    parser.add_argument("--goal", required=True, choices=list(GOALS.keys()), help="排班目标")
    parser.add_argument("--layout", default="", choices=list(LAYOUTS.keys()) + [""], help="基建布局（不指定则自动推荐）")
    parser.add_argument("--output", default="", help="输出 JSON 文件路径（可选）")

    args = parser.parse_args()

    # 加载干员池
    try:
        roster = load_roster(args.roster)
    except FileNotFoundError:
        print(t(f"[ERR] 干员练度表不存在: {args.roster}"))
        sys.exit(1)

    owned_count = sum(1 for v in roster.values() if v["owned"])
    print(t(f"[OK] 加载干员练度表，共 {len(roster)} 名干员（{owned_count} 名已招募）"))

    # 解析目标
    goal_key = GOALS[args.goal]

    # 布局决策
    layout_name = args.layout if args.layout else recommend_layout(goal_key, roster)
    layout_info = LAYOUTS.get(layout_name, LAYOUTS["243"])
    print(f"布局: {layout_name}（{layout_info['desc']}）")
    print(f"目标: {args.goal}")
    print(f"换班: {args.shifts} 班/天")
    print("")

    # 生成方案
    schedule = generate_schedule(roster, goal_key, args.shifts, layout_name)
    schedule["name"] = f"{layout_name} {args.goal} {'三班倒' if args.shifts == 3 else '两班倒'}排班方案"

    # 输出文字表格
    print(format_table(schedule, args.goal))

    # 输出 JSON
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(schedule, f, ensure_ascii=False, indent=2)
        print(t(f"[OK] JSON 方案已保存: {args.output}"))
        print(f"可通过以下命令验证: python scripts/efficiency_calculator.py --check \"{args.output}\"")


if __name__ == "__main__":
    main()
