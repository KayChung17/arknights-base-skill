#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate local operator and mechanics JSON files."""

from __future__ import annotations

import json
import re
from pathlib import Path

from data_loader import ASSETS_DIR, load_mechanics, load_operator_data
from coverage_report import skill_structure_issues
from tag_registry import registration_for, unregistered_tags


DIRECT_BONUS_PATTERNS = {
    "trading_post": [r"进驻贸易站时，订单获取效率\+([0-9.]+)%", r"订单获取效率\+([0-9.]+)%"],
    "factory": [
        r"进驻制造站时，(?:生产[^，；]*?时，)?(?:贵金属类配方的|作战记录类配方的|源石类配方的)?生产力\+([0-9.]+)%",
        r"(?:贵金属类配方的|作战记录类配方的|源石类配方的)生产力\+([0-9.]+)%",
        r"生产力\+([0-9.]+)%",
    ],
    "power_plant": [r"无人机充能速度\+([0-9.]+)%"],
    "office": [r"人脉资源的联络速度\+([0-9.]+)%", r"联络速度\+([0-9.]+)%"],
    "reception_room": [r"线索搜集速度提升([0-9.]+)%"],
}
CONDITIONAL_BONUS_WORDS = (
    "如果", "当与", "当自身", "每有", "每个", "每名", "每间", "每台",
    "每差", "每10", "每3", "根据", "此后", "最终", "首小时", "基建内",
)
DIRECT_BONUS_OVERRIDES = {
    ("巫恋", "低语"): 0.0,
    ("龙舌兰", "投资·α"): 0.0,
    ("龙舌兰", "投资·β"): 0.0,
    ("深律", "心声图绘"): 0.0,
    ("鸿雪", "际崖居民"): 0.0,
    ("耶拉", "耶拉冈德"): 0.0,
}


def parsed_direct_bonus(description: str, facility: str) -> float:
    """Extract only an unconditional percentage from the current description."""
    for pattern in DIRECT_BONUS_PATTERNS.get(facility, []):
        match = re.search(pattern, description)
        if not match:
            continue
        clause = description[:match.start()].rsplit("；", 1)[-1]
        if any(word in clause for word in CONDITIONAL_BONUS_WORDS):
            continue
        return float(match.group(1))
    if facility == "factory":
        match = re.search(r"生产力-([0-9.]+)%", description)
        if match:
            clause = description[:match.start()].rsplit("；", 1)[-1]
            if not any(word in clause for word in ("如果", "当", "每有", "每个", "每名", "每间", "每台", "根据")):
                return -float(match.group(1))
    return 0.0


def granted_effect_conflicts(operator_data: dict) -> list[str]:
    """Reject effect aliases that were flattened into standalone operator skills."""
    granted_aliases: dict[tuple[str, str], list[str]] = {}
    for operator in operator_data.get("operators", []):
        for skill in operator.get("skills", []):
            for rule in skill.get("special_rules") or []:
                aliases = [str(value) for value in rule.get("granted_effect_skill_names") or [] if value]
                facility = str(rule.get("count_facility") or "")
                source = f"{operator.get('name')}/{skill.get('skill_name')}"
                for alias in aliases:
                    granted_aliases.setdefault((facility, alias), []).append(source)

    errors: list[str] = []
    for operator in operator_data.get("operators", []):
        for skill in operator.get("skills", []):
            key = (str(skill.get("facility") or ""), str(skill.get("skill_name") or ""))
            sources = granted_aliases.get(key)
            if sources:
                errors.append(
                    f"{operator.get('name')}/{skill.get('skill_name')}: "
                    f"授予型效果不得作为独立技能计入；来源 {', '.join(sorted(sources))}"
                )
    return errors


def singleton_semantic_tag_conflicts(operator_data: dict) -> list[str]:
    """Detect stale aliases that duplicate one activation mechanism on an operator."""
    singleton_tags = {"glasgow_center", "siracusa_center"}
    errors: list[str] = []
    for operator in operator_data.get("operators", []):
        slots_by_tag: dict[str, set[str]] = {}
        names_by_tag: dict[str, set[str]] = {}
        for skill in operator.get("skills", []):
            for tag in singleton_tags.intersection(skill.get("tags") or []):
                slots_by_tag.setdefault(tag, set()).add(
                    str(skill.get("variant_group") or skill.get("skill_name") or "")
                )
                names_by_tag.setdefault(tag, set()).add(str(skill.get("skill_name") or ""))
        for tag, slots in slots_by_tag.items():
            if len(slots) > 1:
                errors.append(
                    f"{operator.get('name')}: 单例联动标签 {tag} 出现在多个独立技能槽："
                    f"{', '.join(sorted(names_by_tag[tag]))}"
                )
    return errors


def validate() -> list[str]:
    errors: list[str] = []
    operator_data = load_operator_data()
    mechanics = load_mechanics()

    version_path = ASSETS_DIR / "data-version.json"
    if not version_path.exists():
        errors.append("缺少 data-version.json")
    else:
        version_data = json.loads(version_path.read_text(encoding="utf-8"))
        if version_data.get("data_version") != operator_data.get("data_version"):
            errors.append("data-version.json 与 operator-skills.json 的 data_version 不一致")
        operators = operator_data.get("operators", [])
        skill_count = sum(len(operator.get("skills", [])) for operator in operators)
        if int(version_data.get("canonical_operator_count", -1)) != len(operators):
            errors.append("data-version.json 的 canonical_operator_count 与数据不一致")
        if int(version_data.get("canonical_skill_count", -1)) != skill_count:
            errors.append("data-version.json 的 canonical_skill_count 与数据不一致")

    if operator_data.get("schema_version") != 1:
        errors.append("operator-skills.json schema_version 必须为 1")
    if mechanics.get("schema_version") not in (2, 3, 4, 5, 6):
        errors.append("mechanics.json schema_version 必须为 2 至 6")

    power_model = mechanics.get("power_model") or {}
    if power_model.get("right_side_facilities_irreversible") is not True:
        errors.append("mechanics.json 必须标记右侧功能设施不可降级")
    full_right = {"reception_room": 3, "office": 3, "training_room": 3, "workshop": 3}
    fixed_model = power_model.get("fixed_facility_consumption_by_level") or {}
    calculated_full_right = sum(
        float((fixed_model.get(name) or {}).get(str(level), 0.0) or 0.0)
        for name, level in full_right.items()
    )
    if abs(calculated_full_right - float(power_model.get("full_right_side_consumption", -1))) > 1e-9:
        errors.append("mechanics.json 右满耗电声明与分设施数据不一致")
    if abs(calculated_full_right - 190.0) > 1e-9:
        errors.append(f"mechanics.json 右满耗电应为 190，当前为 {calculated_full_right}")


    if mechanics.get("schema_version") >= 3:
        output_metrics = mechanics.get("base_output_metrics_per_hour") or {}
        item_sizes = mechanics.get("warehouse_item_size") or {}
        factory_capacity = (mechanics.get("warehouse_capacity") or {}).get("factory") or {}
        for product_id in ("pure_gold", "orundum_shard", "battle_record"):
            if product_id not in output_metrics:
                errors.append(f"mechanics.json 缺少 {product_id} 的基础产出指标")
            if product_id not in item_sizes:
                errors.append(f"mechanics.json 缺少 {product_id} 的仓库占用")
        for level in ("1", "2", "3"):
            if level not in factory_capacity:
                errors.append(f"mechanics.json 缺少 {level} 级制造站仓库容量")

    names = set()
    ids = set()
    facilities = set(mechanics.get("facilities", {}))
    products = mechanics.get("products", {})
    valid_model_statuses = {"structured", "verified_zero", "conservative_zero", "description_only", "unsupported"}
    supported_mechanisms = {"step_bonus"}
    supported_stacking = {"add", "max", "replace", "multiply", "exclusive"}
    all_asset_tags = {
        str(tag)
        for operator in operator_data.get("operators", [])
        for skill in operator.get("skills", [])
        for tag in skill.get("tags", [])
    }
    for tag in unregistered_tags(all_asset_tags):
        errors.append(f"未注册的技能 tag: {tag}")

    for operator in operator_data.get("operators", []):
        name = operator.get("name")
        operator_id = operator.get("id")
        if not name:
            errors.append("存在缺少 name 的干员")
        if name in names:
            errors.append(f"干员名称重复: {name}")
        names.add(name)
        if not operator_id:
            errors.append(f"{name}: 缺少 id")
        if operator_id in ids:
            errors.append(f"干员 id 重复: {operator_id}")
        ids.add(operator_id)
        for skill in operator.get("skills", []):
            facility = skill.get("facility")
            if facility not in facilities:
                errors.append(f"{name}/{skill.get('skill_name')}: 未知设施 {facility}")
            elite = skill.get("elite")
            if elite not in (0, 1, 2):
                errors.append(f"{name}/{skill.get('skill_name')}: elite 必须为 0/1/2")
            for product in skill.get("products", []):
                if product not in products:
                    errors.append(f"{name}/{skill.get('skill_name')}: 未知产品 {product}")
                elif products[product]["facility"] != facility:
                    errors.append(
                        f"{name}/{skill.get('skill_name')}: 产品 {product} 与设施 {facility} 不匹配"
                    )
            status = skill.get("model_status")
            mechanism = skill.get("mechanism")
            bonus = float(skill.get("base_bonus_pct", 0.0) or 0.0)
            expected_bonus = DIRECT_BONUS_OVERRIDES.get(
                (str(name or ""), str(skill.get("skill_name") or "")),
                parsed_direct_bonus(str(skill.get("description") or ""), str(facility or "")),
            )
            if abs(bonus - expected_bonus) > 1e-9:
                errors.append(
                    f"{name}/{skill.get('skill_name')}: base_bonus_pct={bonus:g} "
                    f"缺少无条件文字证据，解析值为 {expected_bonus:g}"
                )
            tags = list(skill.get("tags") or [])
            for tag in tags:
                registration = registration_for(str(tag))
                if registration is None:
                    continue
                if not registration.consumer.endswith(".py"):
                    errors.append(f"{name}/{skill.get('skill_name')}: tag {tag} 缺少运行消费者")
            effects = list(skill.get("effects") or [])
            special_rules = list(skill.get("special_rules") or [])
            if status is not None and status not in valid_model_statuses:
                errors.append(f"{name}/{skill.get('skill_name')}: 未知 model_status {status}")
            if mechanism is not None:
                mechanism_type = mechanism.get("type") if isinstance(mechanism, dict) else None
                if mechanism_type not in supported_mechanisms:
                    errors.append(f"{name}/{skill.get('skill_name')}: 未支持 mechanism.type {mechanism_type}")
                if status != "structured":
                    errors.append(f"{name}/{skill.get('skill_name')}: mechanism 要求 model_status=structured")
                if mechanism_type == "step_bonus":
                    if mechanism.get("input") != "drone_capacity":
                        errors.append(f"{name}/{skill.get('skill_name')}: step_bonus 使用未知 input")
                    if float(mechanism.get("step", 0.0) or 0.0) <= 0:
                        errors.append(f"{name}/{skill.get('skill_name')}: step_bonus.step 必须大于0")
                    if float(mechanism.get("cap_pct", -1.0)) < 0:
                        errors.append(f"{name}/{skill.get('skill_name')}: step_bonus.cap_pct 必须非负")
            if status == "structured" and abs(bonus) <= 1e-12 and not tags and mechanism is None and not effects and not special_rules:
                errors.append(f"{name}/{skill.get('skill_name')}: structured 技能缺少数值、标签或机制")
            if status == "verified_zero" and (abs(bonus) > 1e-12 or tags or mechanism is not None or effects or special_rules):
                errors.append(f"{name}/{skill.get('skill_name')}: verified_zero 技能不得携带收益机制")
            for issue in skill_structure_issues(skill):
                errors.append(f"{name}/{skill.get('skill_name')}: {issue}")
            for effect in effects:
                if not str(effect.get("effect_key") or ""):
                    errors.append(f"{name}/{skill.get('skill_name')}: effect_key 不能为空")
                if effect.get("stacking") not in supported_stacking:
                    errors.append(f"{name}/{skill.get('skill_name')}: 未支持 stacking {effect.get('stacking')}")
                if effect.get("value_pct") is None and not effect.get("mechanism"):
                    errors.append(f"{name}/{skill.get('skill_name')}: effect 缺少 value_pct 或 mechanism")
            for rule in special_rules:
                if rule.get("type") not in {"group_factory_bonus", "amplifier_exclusion"}:
                    errors.append(f"{name}/{skill.get('skill_name')}: 未支持 special rule {rule.get('type')}")
                if rule.get("type") == "group_factory_bonus":
                    if not str(rule.get("target_group") or ""):
                        errors.append(f"{name}/{skill.get('skill_name')}: group_factory_bonus 缺少 target_group")
                    if not str(rule.get("count_facility") or ""):
                        errors.append(f"{name}/{skill.get('skill_name')}: group_factory_bonus 缺少 count_facility")
                    if float(rule.get("bonus_pct_per_member", 0.0) or 0.0) <= 0:
                        errors.append(f"{name}/{skill.get('skill_name')}: group_factory_bonus 缺少正数 bonus_pct_per_member")
                    if float(rule.get("cap_pct_per_room", 0.0) or 0.0) <= 0:
                        errors.append(f"{name}/{skill.get('skill_name')}: group_factory_bonus 缺少正数 cap_pct_per_room")
                    if not rule.get("granted_effect_skill_names"):
                        errors.append(
                            f"{name}/{skill.get('skill_name')}: group_factory_bonus "
                            "必须声明 granted_effect_skill_names"
                        )

        variant_sets: dict[tuple[str, str], set[str]] = {}
        for skill in operator.get("skills", []):
            skill_name = str(skill.get("skill_name") or "")
            normalized = re.sub(r"[·・._-]?[αβγ]$", "", skill_name, flags=re.IGNORECASE)
            if normalized == skill_name:
                continue
            key = (str(skill.get("facility") or ""), normalized)
            variant_sets.setdefault(key, set()).add(str(skill.get("variant_group") or skill_name))
        for (facility, normalized), groups in variant_sets.items():
            if len(groups) > 1:
                errors.append(f"{name}/{facility}/{normalized}: αβγ升级技能必须使用同一 variant_group")

    errors.extend(granted_effect_conflicts(operator_data))
    errors.extend(singleton_semantic_tag_conflicts(operator_data))

    bundle_path = ASSETS_DIR / "synergy-bundles.json"
    if not bundle_path.exists():
        errors.append("缺少 synergy-bundles.json")
    else:
        bundle_data = json.loads(bundle_path.read_text(encoding="utf-8"))
        if bundle_data.get("schema_version") != 1:
            errors.append("synergy-bundles.json schema_version 必须为 1")
        bundle_ids: set[str] = set()
        known_groups = {
            str(group)
            for operator in operator_data.get("operators", [])
            for group in operator.get("groups", [])
        }
        for bundle in bundle_data.get("bundles", []):
            bundle_id = str(bundle.get("id") or "")
            if not bundle_id:
                errors.append("synergy-bundles.json 存在缺少 id 的组合包")
            elif bundle_id in bundle_ids:
                errors.append(f"联动组合包 id 重复: {bundle_id}")
            bundle_ids.add(bundle_id)
            if bundle.get("confidence") not in {"verified_formula", "discovery_only"}:
                errors.append(f"联动组合包 {bundle_id}: confidence 无效")
            placements = bundle.get("placements") or {}
            if not placements:
                errors.append(f"联动组合包 {bundle_id}: 缺少 placements")
            for facility, spec in placements.items():
                if facility not in facilities:
                    errors.append(f"联动组合包 {bundle_id}: 未知设施 {facility}")
                if not isinstance(spec, dict):
                    errors.append(f"联动组合包 {bundle_id}/{facility}: 配置必须为对象")
                    continue
                for key in ("all_of", "one_of"):
                    for name in spec.get(key, []) or []:
                        if name not in names:
                            errors.append(f"联动组合包 {bundle_id}: 未知干员 {name}")
                group = str(spec.get("group") or "")
                if group and group not in known_groups:
                    errors.append(f"联动组合包 {bundle_id}: 未知分组 {group}")

    for layout_id, layout in mechanics.get("layouts", {}).items():
        total = sum(int(layout.get(key, 0)) for key in ("trading_post", "factory", "power_plant"))
        if total != 9:
            errors.append(f"布局 {layout_id} 左侧设施总数应为 9，实际 {total}")
        if layout.get("control_center") != 1:
            errors.append(f"布局 {layout_id} 控制中枢数量应为 1")

    for goal_id, goal in mechanics.get("goals", {}).items():
        recommended_layout = goal.get("recommended_layout")
        if recommended_layout != "layout_search_required" and recommended_layout not in mechanics.get("layouts", {}):
            errors.append(f"目标 {goal_id} 推荐了未知布局")
        for product in goal.get("factory_products", []):
            if products.get(product, {}).get("facility") != "factory":
                errors.append(f"目标 {goal_id} 的制造产品无效: {product}")
        trading_products = goal.get("trading_products") or [goal.get("trading_product")]
        for trading_product in trading_products:
            if products.get(trading_product, {}).get("facility") != "trading_post":
                errors.append(f"目标 {goal_id} 的贸易产品无效: {trading_product}")

    templates_path = ASSETS_DIR / "strategy-templates.json"
    if not templates_path.exists():
        errors.append("缺少 strategy-templates.json")
    else:
        templates = json.loads(templates_path.read_text(encoding="utf-8")).get("templates", {})
        for goal_id, goal in mechanics.get("goals", {}).items():
            template_id = goal.get("recommended_template")
            if template_id and template_id not in templates:
                errors.append(f"目标 {goal_id} 推荐了未知攻略模板 {template_id}")
        for template_id, template in templates.items():
            if template.get("layout") not in mechanics.get("layouts", {}):
                errors.append(f"攻略模板 {template_id} 使用未知布局")
            for room_id, room in template.get("facility_configuration", {}).get("rooms", {}).items():
                facility = room.get("facility_id")
                product = room.get("product_id")
                if facility not in facilities:
                    errors.append(f"攻略模板 {template_id}/{room_id}: 未知设施 {facility}")
                if product not in products:
                    errors.append(f"攻略模板 {template_id}/{room_id}: 未知产品 {product}")
                elif products[product]["facility"] != facility:
                    errors.append(f"攻略模板 {template_id}/{room_id}: 产品与设施不匹配")

    return errors


def main() -> int:
    errors = validate()
    if errors:
        print(f"数据校验失败：{len(errors)} 个问题")
        for item in errors:
            print(f"  - {item}")
        return 1
    data = load_operator_data()
    print(f"数据校验通过：{len(data.get('operators', []))} 名干员")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
