---
name: ark-base-schedule
description: 根据用户干员池、练度、建筑等级、产品、上线节点、无人机、仓库、心情和经济目标，运行仓库完整求解链路，使用全局混合整数规划求解并逐区间复算明日方舟基建布局与排班；关键输入不足时必须先询问并停止执行，未取得并验证求解器、模拟器和审计器输出时不得给出最终排班或收益结论。
---

# 明日方舟基建布局与排班

本 Skill 使用“模型解释目标、脚本枚举与计算、求解器全局分配、模拟器复算、审计器检查”的混合流程。


## 强制执行门禁

以下规则优先于本 Skill 的其他流程：

1. **先检查输入完整性，再运行任何求解命令。** 若关键输入缺失、互相矛盾或无法从用户文件中确定，先用一条集中问题列出缺失项，等待用户补充。此时不得生成排班、估算每日收益、套用布局模板或启动求解器。
2. **禁止静默采用默认值。** 建筑等级、无人机容量、库存、上线时刻、经济下限、工时上限和产品需求均不得由模型自行补齐。用户明确授权采用仓库默认值时，必须先列出每个默认值及其影响，再执行。
3. **最终方案必须来自仓库完整入口。** 使用 `python arkbase.py run project.json --output-dir ...` 运行布局搜索或固定排班。攻略模板、常用组合、手工效率排序和人工算术只能用于解释或对照，不能代替求解器输出。
4. **求解后必须验证输出。** 只有同一次运行生成的 `result.json`、`audit.json`、`coverage.json`、`summary.json` 和 `config.resolved.json`，以及 `result.json` 中的逐区间模拟结果均完成检查后，才能提供最终排班和收益。
5. **执行失败时停止给结论。** 环境检查失败、输入标准化失败、求解无可行解、模拟不可行、审计失败、关键技能数据缺失或运行产物不完整时，只报告失败阶段、错误信息和需要补充或修复的内容，不得改用手工方案填补。
6. **所有数值必须可追溯。** 最终回答中的无人机、龙门币、赤金、源石碎片、合成玉、材料消耗和工时数字必须能够定位到本次运行的结构化输出字段。禁止把经验值或另一次运行的数字混入结果。
7. **仅在用户明确要求粗略估算时允许脱离求解器。** 此类输出必须标记为 `manual_estimate`，列出全部假设，并且不能称为排班结果、最优候选或已验证收益。

## 决策职责

大模型负责：

- 把用户自然语言转换为明确目标和硬约束。
- 核对建筑状态、上线时间、库存、无人机容量和可接受亏损。
- 在机制可能更新时检索当前来源，并将采用的规则转为结构化证据。
- 选择布局搜索、固定排班或培养搜索模式。
- 比较攻略基线、求解器候选和不同经济档位。
- 解释偏离攻略的原因、收益和风险。

脚本负责：

- 绑定用户实际拥有情况、精英化和等级。
- 排除没有本地版本化数据或已验证外部证据的生产技能。
- 枚举合法单房间组合。
- 处理房间容量、同一时间干员互斥、每日工时和资源下限。
- 建模无人机恢复、库存、节点使用、溢出和重复日闭环。
- 复算全局联动、仓库、经济与保守心情。
- 记录候选库截断、MILP 状态、gap 和时间限制 incumbent。
- 拒绝最终模拟违反硬约束的候选。
- 生成可复现清单、审计结果和中文报告。

攻略模板必须作为比较候选时使用，但不得成为搜索边界。

## 首选入口

必须使用项目配置运行完整流程；底层脚本仅用于调试和定位失败阶段：

```bash
python arkbase.py run project.json --output-dir output/my-project
```

输出必须包含：

- `result.json`
- `report.md`
- `audit.json`
- `coverage.json`
- `summary.json`
- `config.resolved.json`

首次使用先运行：

```bash
python arkbase.py doctor
```

## 必要输入与询问规则

在运行前必须取得以下关键输入：

- 已拥有干员、精英化等级和当前等级，并确认用户文件中的近期变更已经写入。
- 求解模式：布局搜索、固定布局排班或培养搜索。
- 目标优先级及硬约束，例如合成玉优先、每日龙门币净变化下限、赤金与源石碎片日净变化下限。
- 贸易站、制造站、发电站、宿舍和右侧设施的实际等级；固定排班还需要逐房间产品配置。
- 每天可操作的具体时刻。上线次数是操作节点，不自动等分成班次。
- 当前无人机库存和持有上限。持有上限来自基建区域清理进度；发电站数量和等级不决定持有上限。
- 当前赤金、源石碎片、龙门币和固源岩库存，或用户明确接受的每日净变化下限。
- 是否需要经验书，以及允许存在的产品线。
- 单名干员每日最大工作时间。
- 求解长期重复日时所采用的心情口径；需要精确心情闭环时，还要提供宿舍氛围、菲亚梅塔状态和当前心情。

输入检查分为三种状态：

- `ready`：关键输入完整，可以创建配置并运行仓库求解器。
- `needs_input`：存在缺失项。只向用户询问缺失信息，不执行求解，不输出候选排班或收益。
- `conflict`：用户输入互相矛盾。指出冲突字段并等待用户选择，不自行覆盖。

询问时优先一次列出全部关键缺失项，避免逐项来回。用户只给出上线次数时，必须继续询问具体上线时刻。用户只说“保留龙门币收益”时，必须继续询问可接受的每日龙门币净变化下限或库存安全线。

对 `08:00,14:00,20:00`，区间是 `6h、6h、12h`。同一房间可以跨前两个区间保持同一队伍，14:00 可以只收取产物和订单；这属于求解器可选择的操作方式，不应预先固定为人工排班规则。

## 模式选择

### 布局搜索

用户询问“我的练度适合什么布局”时，布局必须作为决策变量。使用：

```json
{
  "mode": "layout_search",
  "profiles": {"mode": "representative"}
}
```

`representative` 比较常见代表配置。需要更完整的房间等级搜索时使用：

```json
{
  "profiles": {
    "mode": "level_grid",
    "layouts": ["252", "342", "333"],
    "max_profiles": 120
  }
}
```

`level_grid` 枚举选定布局的非递增房间等级多重集，先按电力过滤，再按结构优先级保留 profile。必须报告总可行 profile、实际求解数量和是否截断。

合成玉优先且要求长期经济可持续时，至少保留：

- 一座三级合成玉贸易站。
- 一座龙门币贸易站。
- 一条三级源石碎片线。
- 一条赤金线。

用户不需要经验书时，经验书产线必须为零。

### 固定排班

用户已经给定布局和建筑等级时，使用 `fixed_schedule`。不要用攻略建筑等级覆盖用户实际建筑。若建筑状态不完整，只能输出候选和缺失信息。

### 培养搜索

用户允许提高练度时，使用 `upgrade_search`：

1. 用相同经济约束求当前练度。
2. 求全部已拥有干员基建技能解锁上限。
3. 只保留上限候选实际使用的更高技能，生成定向最低解锁 roster。
4. 对孑、冬时和覆盖同站效率等非单调技能执行状态对照。
5. 可使用 `marginal_limit` 对前 N 项培养进行留一法复算。

培养建议只评价基建性能，不代表战斗培养优先级；未提供培养成本数据时不得声称投资回收最优。

## 标准底层流程

### 1. 校验数据

```bash
python scripts/validate_data.py
```

### 2. 标准化输入

```bash
python scripts/normalize_input.py \
  --roster roster.xlsx \
  --goal 赚钱+搓玉 \
  --layout 342 \
  --online-count 3 \
  --online-times 08:00,14:00,20:00 \
  --preferences preferences.json \
  --output decision-context.json
```

### 3. 枚举房间组合

```bash
python scripts/build_combinations.py decision-context.json \
  --top-k 60 \
  --operator-pool-size 14 \
  --output combinations.json
```

提高 `top-k` 和 `operator-pool-size` 可以降低关键干员被预筛选删除的风险。只要任一关键房间发生截断，就不能声明完整搜索空间内最优。

### 4. 求解

```bash
python scripts/solve_schedule.py decision-context.json \
  --combination-library combinations.json \
  --top-solutions 5 \
  --time-limit 60 \
  --mip-rel-gap 0.001 \
  --output solver-result.json \
  --plan-output solver-candidate.json
```

时间限制到达但存在可行 incumbent 时，可以保留该候选；必须标记为当前搜索最佳，不能称为求解完成的最优解。

### 5. 独立复算

```bash
python scripts/simulate_schedule.py \
  decision-context.json combinations.json solver-result.json \
  --output simulation.json
```

### 6. 审计与报告

```bash
python scripts/audit_result.py solver-result.json --output audit.json
python scripts/generate_report.py solver-result.json --output report.md
```


### 7. 运行产物验证

最终回答前逐项检查：

- `summary.json` 表明项目运行完成，且列出的文件路径均存在。
- `config.resolved.json` 与本次用户确认的输入一致，不含未经授权的默认值。
- `coverage.json` 区分未知干员、未解锁技能、仅有描述的技能和已结构化技能；关键房间候选涉及未结构化技能时不得发布已验证方案。
- `result.json` 记录求解器后端、状态、gap、时间限制、profile 数量、候选库截断和选中候选。
- 选中候选的逐区间模拟为可行，且无人机、仓库、经济、工时和重复日状态闭合。
- `audit.json` 状态不是 `failed`，并逐项检查警告是否影响用户目标。
- `report.md` 中的核心数字与 `result.json`、模拟结果一致。

最终回答必须附带本次运行证据摘要：运行模式、配置文件、求解器状态与 gap、profile 和候选库是否截断、模拟可行性、审计状态、数据覆盖警告。缺少这些证据时，方案状态保持 `candidate` 或 `execution_blocked`。

## 无人机规则

- 持有上限由用户基建清理进度输入，全部清理时通常使用 235。
- 基础恢复为每 6 分钟 1 架。
- 每座已进驻发电站提供基础恢复加成，再叠加干员技能。
- 1 架无人机减少 3 分钟基础制造或订单时间。
- 无人机加速不再乘房间生产力或订单效率。
- 分配只能发生在用户可操作节点。
- 重复日模式要求次日同一时间回到相同库存。
- 加速源石碎片必须计入龙门币和固源岩成本。
- 加速订单必须计入赤金或碎片消耗。

详细公式见 `references/drone-model.md`。

## 结果表述

使用求解结果字段：

- `proxy_optimal_within_complete_candidate_library`
- `best_found_within_truncated_candidate_library`
- `actual_simulation_global_optimality_proven`

只有候选库完整、代理模型达到声明 gap、代理目标与最终模拟目标一致、全部相关机制和随机状态均已建模时，才能写“全局最优”。正常输出使用“当前搜索设置中的最高分候选”。未运行仓库求解器时不得使用“候选”“最优”“推荐排班”“每日净收益”等会被理解为求解结论的表述。

## 最终方案门禁

最终方案必须包含：

- 明确布局、建筑等级和产品。
- 完整 24 小时时间表。
- 所有生产和控制中枢干员的已验证技能。
- 无同一时间重复进驻。
- 每日工时与重复日心情说明。
- 仓库封顶检查。
- 无人机库存和分配闭环。
- 龙门币、赤金、碎片、合成玉及材料日净变化。
- 攻略基线比较。
- 候选库截断、求解 gap、数据覆盖和未建模机制。
- `audit.json` 不得为 `failed`。
- 提供同一次运行的执行证据摘要，包括配置、求解状态、gap、截断状态、模拟可行性和数据覆盖。
- 最终回答中的每个核心数值都能追溯到结构化输出。

缺少用户输入时输出 `needs_input` 并先询问；环境或运行链路受阻时输出 `execution_blocked`；求解或模拟未通过时输出对应失败状态。只有已经运行求解器但仍存在非致命范围限制时才使用 `candidate`，并列出限制。

## 网络证据

采用外部机制时记录：

- 来源 URL 或来源 ID。
- 获取日期。
- 适用游戏版本。
- 官方描述、社区实测、攻略或推断。
- 结构化规则和验证状态。

未结构化的网页文字不能直接获得猜测数值。

## 参考资料

- `references/optimization-model.md`
- `references/drone-model.md`
- `references/layout-optimization.md`
- `references/model-scope.md`
- `references/script-contracts.md`
- `references/reproducibility-and-audit.md`
- `references/schedule-rules.md`
- `schemas/project-config.schema.json`
