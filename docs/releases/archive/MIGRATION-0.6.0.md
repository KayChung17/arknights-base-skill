# 0.6.0 迁移说明

## 从模型手工候选迁移到混合求解

0.5.0 的主流程由模型读取攻略后设计多个候选。0.6.0 增加统一的数学求解路径：

```text
normalize_input
→ build_combinations
→ build_model
→ solve_schedule
→ simulate_schedule
```

原有 `validate_plan.py`、`compare_to_baseline.py`、`evaluate_plan.py` 和 `compare_plans.py` 继续保留。

## 攻略语义变化

0.5.0 字段 `must_start_from_current_guide_baseline` 在 0.6.0 决策包中固定为 `false`。新增：

- `must_include_current_guide_baseline_as_candidate`
- `guide_template_is_search_boundary: false`
- `hybrid_solver_preferred: true`

## 新依赖

安装：

```bash
python -m pip install "scipy>=1.11" "openpyxl>=3.1"
```

## 新文件

- `scripts/build_combinations.py`
- `scripts/build_model.py`
- `scripts/solve_schedule.py`
- `scripts/simulate_schedule.py`
- `scripts/optimizer_common.py`
- `schemas/combination-library.schema.json`
- `schemas/hybrid-solver-result.schema.json`
- `references/optimization-model.md`

## 最优性字段

读取 `solver.optimality_claim` 和 `solver.actual_simulation_global_optimality_proven`，不要根据 `result.success` 直接显示“全局最优”。
