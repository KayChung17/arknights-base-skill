# 明日方舟基建排班优化器

面向中文用户的明日方舟基建布局、排班、无人机与培养决策工具。仓库将单房间组合枚举、全局混合整数规划、逐区间模拟和大模型解释组合为一条可审计、可复现的求解链路。

当前版本：**2.0.0** · [发布说明](RELEASE_NOTES-2.0.0.md) · [迁移指南](MIGRATION-2.0.0.md)

> 本项目是非官方社区工具。游戏名称、角色名称与相关素材权利归其权利人所有。内置数据是版本化快照，不保证覆盖游戏中的全部干员或未来更新。

## 能解决什么问题

- 根据自己的干员池和练度安排贸易站、制造站、发电站与控制中枢。
- 比较 252、342、333 等布局，也可以搜索自定义房间等级配置。
- 以合成玉、龙门币、赤金、经验、低操作量或自定义权重为目标。
- 把无人机恢复、持有上限、节点分配、资源成本和重复日库存闭环纳入模型。
- 检查同一时间重复进驻、工时、仓库封顶、赤金与碎片收支、龙门币下限和心情风险。
- 长期重复日默认要求赤金与源石碎片日净变化均不低于 0，并在同收益候选中优先更接近收支平衡的方案。
- 比较当前练度、基建技能全解锁上限和定向培养方案。
- 输出可直接导入排班工具的 `schedule.json`、中文报告、审计结果和可复现清单。
- 将攻略模板作为比较基线，不把模板限制成唯一搜索空间。

## 方法概览

```text
用户配置、干员表、基建状态
        ↓
房间等级与产品配置搜索
        ↓
单房间合法组合枚举
        ↓
全局 MILP 分配干员、区间和无人机
        ↓
逐区间复算全局联动、仓库、经济和心情
        ↓
硬约束审计、攻略对比、中文报告
```

MILP 使用 SciPy HiGHS。模型会区分“代理目标在候选库内达到求解界限”和“最终模拟产出”；常规结果只称为“当前搜索设置中的最高分候选”。

## 安装

需要 Python 3.10 及以上版本。

```bash
python -m pip install "scipy>=1.11" "openpyxl>=3.1"
```

克隆仓库后先运行环境检查：

```bash
python arkbase.py doctor
```

## 三分钟开始

复制示例配置和干员表：

```bash
cp examples/roster.example.tsv roster.tsv
cp examples/configs/合成玉优先_布局搜索.json project.json
```

修改 `project.json` 中的 `roster`、上线时间、龙门币下限和基建状态，然后运行：

```bash
python arkbase.py run project.json \
  --output-dir output/my-project
```

输出目录包含：

```text
schedule.json                   template.json 兼容的独立排班表
result.json                     完整结构化结果
report.md                       中文摘要与排班报告
audit.json                      硬约束和最优性措辞审计
coverage.json                   干员与技能结构化覆盖报告
pareto.json                     多目标候选前沿
preflight.json                  输入门禁结果
unmodeled-relevant-skills.json  本次范围内未结构化技能
config.resolved.json            本次使用的配置副本
run-manifest.json               同次运行产物哈希
verification.json               发布验证结果
summary.json                    文件位置与运行状态
```

配置字段见 [配置说明](docs/配置说明.md)。排班文件可参考 [2.0 脱敏样例](skills/ark-base-schedule/samples/sample_schedule_v2.json)，完整字段骨架见 [导出模板](skills/ark-base-schedule/assets/template.json)。

## 常用模式

### 1. 搜索最适合自己的布局

```json
{
  "schema_version": 1,
  "mode": "layout_search",
  "roster": "roster.xlsx",
  "objective": {
    "online_times": ["08:00", "14:00", "20:00"],
    "minimum_net_lmd_per_day": -10000,
    "minimum_orundum_shard_balance": 0,
    "minimum_pure_gold_balance": 0,
    "max_daily_work_hours": 18
  },
  "profiles": {
    "mode": "representative",
    "ids": ["252-output", "342-output", "333-max", "243-max"]
  }
}
```

`representative` 适合日常使用。`level_grid` 会枚举选定布局的房间等级多重集，并明确记录求解前是否截断 profile。

### 2. 搜索值得培养的基建干员

将 `mode` 改为 `upgrade_search`，并设置：

```json
"upgrades": {"marginal_limit": 5}
```

工具会比较当前练度、全部已拥有干员的基建技能上限和定向最低解锁。`marginal_limit` 会对前 N 项培养执行留一法复算，处理孑、冬时等非单调或不可逆技能时更可靠。

### 3. 固定布局只求排班

使用 `mode: fixed_schedule` 和 `layout: "342"`。也可以直接提供 `facility_configuration`，逐房间指定等级和产品。

## 命令入口

```bash
# 完整项目流程
python arkbase.py run project.json

# 严格输入预检
python arkbase.py preflight project.json

# 验证同次运行产物
python arkbase.py verify output/my-project

# 环境与数据诊断
python arkbase.py doctor

# 审计已有结果
python arkbase.py audit result.json --output audit.json

# 重新生成中文报告
python arkbase.py report result.json --output report.md

# 检查自己的干员池数据覆盖
python arkbase.py coverage --roster roster.xlsx --output coverage.json
```

已有 `result.json` 可以补导排班表：

```bash
python skills/ark-base-schedule/scripts/export_schedule_template.py \
  result.json --output schedule.json
```

底层脚本仍可独立调用，详见 `skills/ark-base-schedule/SKILL.md` 与 `references/script-contracts.md`。

## 数据与隐私

- 干员表只在本地读取，不会上传。
- `output/`、常见用户 roster 文件和本地缓存默认被 `.gitignore` 排除。
- 内置 `operator-skills.json` 是经过脱敏的版本化社区快照，不包含来源账号的拥有状态、练度或原始导出文件，也不代表游戏完整数据库。导入器回归使用独立的最小合成样例。
- 未结构化或来源不明的技能不会获得猜测数值。

## 结果可信度

结果会使用以下范围声明：

- `proxy_optimal_within_complete_candidate_library`：候选库未截断，代理 MILP 达到声明 gap。
- `best_found_within_truncated_candidate_library`：房间组合或外层 profile 发生截断。
- `actual_simulation_global_optimality_proven: false`：最终模拟包含代理模型尚未完全表示的机制。

只有候选库完整、代理目标与最终模拟目标一致、随机订单和所有全局机制均已进入模型时，才能声明实际全局最优。当前正常输出不会这样声明。

## 仓库结构

```text
skills/ark-base-schedule/      排班与优化 Skill
skills/update-ark-skills/      技能数据导入与维护 Skill
examples/                      示例 roster、配置和自定义 profile
docs/                          中文文档
scripts/validate-all.sh        完整校验
scripts/build-release.sh       发布包构建
```

## 测试

```bash
bash scripts/validate-all.sh
```

测试覆盖技能数据、组合枚举、MILP、无人机闭环、布局等级搜索、培养留一法基础逻辑、结果审计、中文报告和项目化运行入口。

## 参与贡献

参见 [贡献指南](CONTRIBUTING.md)、[实际问题复盘](docs/实际问题复盘.md)、[数据维护](docs/数据维护.md) 和 [发布检查清单](docs/发布检查清单.md)。提交新机制时必须附来源、适用版本、单位、作用范围和回归测试。

## 许可证

代码采用 MIT License。游戏数据与名称不因本仓库许可证而改变其原有权利归属，详见 [NOTICE](NOTICE.md)。
