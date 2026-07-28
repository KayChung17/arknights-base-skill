#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate local operator and mechanics JSON files."""

from __future__ import annotations

import json
from pathlib import Path

from data_loader import ASSETS_DIR, load_mechanics, load_operator_data


def validate() -> list[str]:
    errors: list[str] = []
    operator_data = load_operator_data()
    mechanics = load_mechanics()

    if operator_data.get("schema_version") != 1:
        errors.append("operator-skills.json schema_version 必须为 1")
    if mechanics.get("schema_version") not in (2, 3, 4, 5, 6):
        errors.append("mechanics.json schema_version 必须为 2 至 6")


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
