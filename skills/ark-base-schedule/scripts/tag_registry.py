#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Authoritative registry for structured skill-tag consumers."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class TagRegistration:
    pattern: str
    consumer: str
    role: str = "runtime"
    description: str = ""

    def matches(self, tag: str) -> bool:
        return re.fullmatch(self.pattern, tag) is not None


_EFFICIENCY_TAGS = {
    "trade_per_elite_operator_facility_2_cap_20",
    "all_factory_bonus_2", "all_trading_bonus_7", "amplifier_equal_additive",
    "automation_virtual_power_plant_1", "automation_virtual_power_plant_2_if_lancet",
    "ave_dorm_heat_1", "ave_gold_base_1_per_20_heat_1", "ave_heat_10", "ave_heat_20",
    "ave_trade_per_8_heat_1", "babel_other_facility_morale_recovery", "bubble_capacity_conversion",
    "cancel_ave_morale_cost_with_sakiko", "cangtai_per_other_metalcraft", "catnip_fixed_8",
    "catnip_per_control_monster_hunter_2", "chongyue_other_facility_morale_recovery",
    "control_alternate_per_member_morale_recovery_0.05", "control_hoederer_order_capacity_1",
    "control_hoederer_order_capacity_2", "control_lee_per_member_morale_recovery",
    "control_lgd_per_member_morale_recovery_0.05", "control_room_all_morale_recovery_0.05",
    "control_sui_self_morale_modifier_immunity", "demon_king_amiya_pair_morale_recovery_0.05",
    "demon_king_amiya_pair_morale_recovery_0.10", "dongshi_reset", "dorm_level_sum_gold_1",
    "dorothy_rhine_room", "engineering_robot_per_facility_level_1_cap_64",
    "factory_gold_per_trading_post_3", "factory_per_3_human_fireworks_1", "factory_per_a1_skill_10",
    "factory_per_catnip_1", "factory_per_engineering_robot_16_5", "factory_per_engineering_robot_8_5",
    "factory_per_metalcraft_skill_5", "factory_per_monster_cooking_1", "factory_per_rhine_skill_5",
    "factory_per_standardization_skill_5", "factory_per_thought_chain_1_1",
    "factory_per_thought_chain_2_1", "factory_per_witchcraft_1", "factory_per_witchcraft_2",
    "fireworks_to_witchcraft_5_1", "glasgow_center", "hongxue_line_source", "hongxue_per_line_5",
    "hourly_growth_15_to_25", "hourly_growth_20_to_25", "human_fireworks_per_dorm_occupant_1",
    "human_fireworks_per_extra_recruitment_slot_10", "human_fireworks_per_sui_5_cap_25",
    "jaye_order_count_4", "jaye_order_gap_4", "justice_wild_mane_factory_5", "karlan_full_trade_10",
    "knight_factory_productivity_7", "laterano_per_member_15", "lemuen_with_exusiai_25",
    "mlynar_business_is_business", "monster_cooking_per_dorm_level_1",
    "morale_above_12_perception_10", "morale_at_most_12_fireworks_15",
    "morale_threshold_fireworks_15_else_perception_10", "morgan_glasgow_compass",
    "muelsyse_drone_per_rhine", "nasti_per_rhine", "order_capacity_per_room_level_1",
    "perception_per_dorm_occupant_1", "perception_to_silent_resonance_1",
    "perception_to_thought_chain_1", "power_per_dorm_level_sum_0.5", "power_with_kaltsit_control_5",
    "power_with_other_work_platform_5", "qiliang_virtual_lines", "qingliu_per_trading_post",
    "red_pine_factory_record_10_gold_minus_10", "redcloud_capacity_conversion_2",
    "shamare_whisper_per_other_worker_45", "silent_resonance_per_dorm_occupant_1",
    "silent_resonance_per_extra_recruitment_slot_15", "siracusa_center", "snowant_amplifier_cap_25",
    "snowant_amplifier_cap_35", "standardization_alias_rhine_red_pine", "texas_with_lappland_65",
    "trade_per_catnip_3", "trade_per_dorm_level_sum_1", "trade_per_dorm_level_sum_2",
    "trade_per_human_fireworks_1", "trade_per_monster_cooking_1", "trade_per_other_worker_10",
    "trade_per_other_worker_15", "trade_per_other_worker_20", "trade_per_positive_order_capacity_1_4",
    "trade_per_positive_order_capacity_5_25_cap_100", "trade_per_silent_resonance_2_1",
    "trade_per_silent_resonance_4_1", "trade_per_sui_occupied_facility_4_cap_20",
    "trade_reception_room_level_5_cap_40", "training_room_level_10_cap_30", "tuye_per_two_lines",
    "vigil_anywhere_trade_bonus_10", "vigil_anywhere_trade_bonus_5",
    "vigil_same_room_morale_reduction_0.1", "vigil_same_room_order_capacity_2",
    "vigil_same_room_order_capacity_2", "wang_layout_balance", "with_jiushen_battle_record_30",
    "with_wanqing_gold_15", "work_platform_per_member_10", "work_platform_per_member_5",
    "yinji_per_trading_post",
}

_ORDER_TAGS = {
    "pepe_exclusive_order", "proviso_breach_order", "tailoring_alpha_empirical",
    "tailoring_beta_empirical", "tequila_investment_order", "u_official_two_gold_order",
}

TAG_REGISTRY = tuple(
    [TagRegistration(re.escape(tag), "efficiency_calculator.py") for tag in sorted(_EFFICIENCY_TAGS)]
    + [TagRegistration(re.escape(tag), "drone_model.py") for tag in sorted(_ORDER_TAGS)]
    + [
        TagRegistration(r"automation_reset_others_per_power_plant_(5|10|15)", "efficiency_calculator.py"),
        TagRegistration(r"gladiia_abyssal_activation_(5|10)", "efficiency_calculator.py"),
        TagRegistration(r"morale_cost_(minus|plus)_\d+(\.\d+)?", "efficiency_calculator.py"),
        TagRegistration(r"room_morale_(cost_plus|recovery)_\d+(\.\d+)?", "efficiency_calculator.py"),
        TagRegistration(r"order_capacity_\d+", "efficiency_calculator.py"),
        TagRegistration(r"order_capacity_minus_\d+", "efficiency_calculator.py"),
        TagRegistration(r"warehouse_capacity_\d+", "optimizer_common.py"),
        TagRegistration(r"warehouse_per_rhine_skill_5", "optimizer_common.py"),
        TagRegistration(r"special_order", "drone_model.py"),
        TagRegistration(r"time_dependent", "simulate_schedule.py", "solver_control"),
        TagRegistration(r"non_stacking_max", "effect_resolver.py", "stacking_control"),
        TagRegistration(r"time_dependent_probability", "drone_model.py", "probability_control"),
        TagRegistration(
            r"office_per_elite_facility_4_cap_5",
            "right_side_schedule.py",
            "right_side_metric",
            "办公室联络速度指标，不进入生产经济目标。",
        ),
    ]
)


def registration_for(tag: str) -> TagRegistration | None:
    matches = [item for item in TAG_REGISTRY if item.matches(tag)]
    if len(matches) > 1:
        raise ValueError(f"tag 注册重叠: {tag}: {[item.pattern for item in matches]}")
    return matches[0] if matches else None


def unregistered_tags(tags: set[str]) -> list[str]:
    return sorted(tag for tag in tags if registration_for(tag) is None)
