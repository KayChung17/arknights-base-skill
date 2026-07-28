# 数据格式

## Roster

TSV 或 CSV，至少包含干员名称、是否已招募和精英化等级。Roster 是练度和所有权的权威来源。

## v4 候选方案

```json
{
  "schema_version": 4,
  "plan_id": "guide-adapted-342-a",
  "plan_status": "candidate",
  "layout": "342",
  "goal": "gold_origin",
  "decision": {
    "strategy": "攻略还原优先",
    "rationale": [],
    "tradeoffs": [],
    "external_evidence_ids": []
  },
  "baseline": {
    "reference_id": "guide_342_orundum_3_login",
    "deviations": [],
    "comparison": {}
  },
  "facility_configuration": {
    "rooms": {
      "trading_post_1": {
        "facility_id": "trading_post",
        "level": 3,
        "product_id": "lmd_order"
      }
    }
  },
  "operation_nodes": [
    {"time": "08:00", "label": "第1次上线"},
    {"time": "14:00", "label": "第2次上线"},
    {"time": "20:00", "label": "第3次上线"}
  ],
  "segments": {
    "segment_1": {
      "start": "08:00",
      "end": "14:00",
      "hours": 6,
      "rooms": {
        "trading_post_1": {
          "operators": ["巫恋", "龙舌兰", "但书"]
        }
      }
    }
  },
  "external_skill_evidence": [
    {
      "operator": "新干员",
      "facility_id": "factory",
      "product_ids": ["pure_gold"],
      "source_id": "source-1",
      "verified": true
    }
  ],
  "recovery_plan": {
    "events": [
      {
        "time": "20:00",
        "type": "fiammetta_full_restore",
        "targets": ["但书"],
        "verified": true
      }
    ],
    "repeating_day_verified": true
  },
  "economy_projection": {
    "source": "verified_guide",
    "daily": {
      "lmd_orders_lmd": 47000,
      "pure_gold_lmd_equivalent": 44000,
      "orundum": 535,
      "battle_record_exp": 14000
    },
    "costs": {
      "orirock_cube": 100,
      "lmd": 80000
    },
    "inventory_delta": {
      "orundum_shard": -4
    },
    "warehouse_overflow_checked": true,
    "drone_policy": "优先处理碎片线爆仓风险"
  },
  "assumptions": {
    "repeating_daily": true
  }
}
```

`plan_status: candidate` 允许警告和未完成的证据。升级为 `final` 后，技能数据、经济、恢复、基线比较和设施等级均进入硬门禁。

## 兼容字段

0.4.x 的 `shifts` 仍可读取。新方案使用 `segments`，因为不同房间可以跨相邻区间保持同一组干员，形成不同的连续工时。

## v0.6 组合库

`build_combinations.py` 输出：

```json
{
  "schema_version": 1,
  "library_type": "room_combination_library",
  "parameters": {
    "top_k_per_room": 60,
    "operator_pool_size": 14,
    "allow_partial": false
  },
  "rooms": {
    "factory_1": {
      "room": {
        "facility_id": "factory",
        "level": 3,
        "product_id": "pure_gold",
        "capacity": 3
      },
      "eligible_operator_count": 20,
      "enumerated_count": 364,
      "kept_count": 120,
      "truncated": true,
      "combinations": [
        {
          "combination_id": "combo_xxx",
          "operators": [
            {"name": "清流", "elite": 1, "skill_source": "local_versioned_data"}
          ],
          "metrics_per_hour": {"pure_gold": 1.5},
          "proxy_score_per_hour": 30,
          "warehouse_capacity": 54
        }
      ]
    }
  },
  "search_completeness": {
    "all_rooms_untruncated": false,
    "truncated_rooms": ["factory_1"]
  }
}
```

`top_k_per_room` 是高分集合大小。枚举器还会增加最多同等数量的多样性尾部，所以 `kept_count` 可以达到 `2 × top_k_per_room`。

## v0.6 求解结果

```json
{
  "schema_version": 1,
  "result_type": "hybrid_schedule_solution",
  "solver": {
    "architecture": "enumerated_room_combinations_plus_global_milp_plus_simulation_rerank",
    "backend": "scipy.optimize.milp_highs",
    "optimality_claim": "best_found_within_truncated_candidate_library",
    "actual_simulation_global_optimality_proven": false
  },
  "selected_solution": {
    "proxy_objective": 1000,
    "mip_gap": 0,
    "assignments": [],
    "drone_allocations": [],
    "drone_inventory": [],
    "drone_waste": [],
    "simulation": {
      "drone_plan": {
        "feasible": true,
        "total_recovered": 345,
        "total_used": 345,
        "total_wasted": 0,
        "timeline": [],
        "allocations": []
      }
    }
  },
  "candidate_plan": {},
  "alternatives": []
}
```

求解结果中的 `candidate_plan` 符合 v4 候选方案结构，并保留完整 solver 与 simulation 元数据。


无人机参数位于 `objective.preferences.solver`：`allocate_drones`、`drone_repeating_day_balance`、`drone_capacity`、`initial_drone_stock`、`max_drone_use_per_node` 和 `drone_target_products`。
