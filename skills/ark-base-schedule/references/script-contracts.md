# 脚本接口

## normalize_input.py

生成决策上下文、设施配置、操作节点、攻略基准和 roster 能力表。支持 TSV、CSV 和 XLSX。

```bash
python scripts/normalize_input.py \
  --roster roster.xlsx \
  --goal 赚钱+搓玉 \
  --online-count 3 \
  --online-times 08:00,14:00,20:00 \
  --preferences '{"priority":"balanced","solver":{"max_daily_work_hours":18}}' \
  --output decision-context.json
```

## build_combinations.py

枚举经过技能数据验证的单房间组合。

```bash
python scripts/build_combinations.py decision-context.json \
  --top-k 60 \
  --operator-pool-size 14 \
  --output combinations.json
```

输出符合 `schemas/combination-library.schema.json`。`truncated_rooms` 非空时，后续模型没有完整搜索空间。

## drone_model.py

独立计算无人机恢复、基础时间缩减和指定目标的每架无人机产出。

```bash
python scripts/drone_model.py --drones 120 --recovery-bonus 40 --hours 12
```

传入 `--room` 和可选的 `--combo` 后，会输出目标完成一个单位或订单所需的无人机数量，以及每架无人机对应的产出和消耗。

## build_model.py

构建并导出 MILP 元数据摘要。数值矩阵在求解时重新生成。

```bash
python scripts/build_model.py decision-context.json combinations.json \
  --output model-summary.json
```

## solve_schedule.py

调用 SciPy HiGHS MILP 后端，生成多个代理目标候选并执行复算。无人机分配、库存和溢出变量包含在同一模型中。

```bash
python scripts/solve_schedule.py decision-context.json \
  --combination-library combinations.json \
  --top-solutions 5 \
  --time-limit 60 \
  --mip-rel-gap 0.001 \
  --output solver-result.json \
  --plan-output solver-candidate.json
```

未传入 `--combination-library` 时会自动枚举。可使用 `--write-combination-library` 保存自动生成的组合库。

## simulate_schedule.py

独立复算包含 `assignments`、`selected_assignments` 或求解器选择结果的文件。

```bash
python scripts/simulate_schedule.py \
  decision-context.json combinations.json assignment.json \
  --output simulation.json
```

## validate_plan.py

绑定 roster 后执行设施等级、技能证据、重复进驻、时间线、恢复、产品和经济门禁。退出码 0 表示无硬错误。

## compare_to_baseline.py

将候选与结构化攻略比较。攻略只作为比较基准。

## evaluate_plan.py / compare_plans.py

保留用于模型手工候选与旧工作流的参考评价。

## schedule_generator.py

旧式房间级贪心生成器，仅用于诊断与回归测试。

## export_schedule_template.py

把 `result.json`、`solver-result.json` 或 schema 4 候选排班导出为 `assets/template.json` 兼容文件：

```bash
python scripts/export_schedule_template.py result.json \
  --output schedule.json
```

导出器动态写入班次数和设施数量，并校验顶层字段、房间键和 `scheduleType`。完整项目入口会自动调用该导出器。可供对照的脱敏产物是 `samples/sample_schedule_v2.json`。
