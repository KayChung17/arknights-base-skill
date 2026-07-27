# 明日方舟排班设计指南

设计最优基建排班方案，基于等效效率计算与七条核心链路。

## 什么时候用

- 需要设计/优化基建排班
- 计算贸易站/制造站等效效率
- 分配三班（A/B/C）干员
- 验证排班方案的正确性

## 文件说明

### 指南

| 文件 | 说明 |
|------|------|
| [schedule-guide.md](schedule-guide.md) | 主指南：核心认知 + 七条链路 + 设计流程 |
| [schedule-checklist.md](schedule-checklist.md) | 排班完整性校验表 |

### 工具

| 文件 | 说明 |
|------|------|
| [scripts/efficiency_calculator.py](scripts/efficiency_calculator.py) | 效率计算器（五层叠加模型），含 44 名干员内置数据库 |
| [scripts/schedule_generator.py](scripts/schedule_generator.py) | 排班方案生成器，根据干员池自动生成最优排班 |

### 样本

| 文件 | 说明 |
|------|------|
| [samples/sample_干员练度表.txt](samples/sample_干员练度表.txt) | 44 名干员样本数据，供测试用 |
| [samples/sample_skills_parsed.txt](samples/sample_skills_parsed.txt) | 样本技能数据 |
| [samples/sample_342方案.json](samples/sample_342方案.json) | 样本排班方案，供 --check 验证 |

### 参考资料

| 文件 | 说明 |
|------|------|
| [references/error-log.md](references/error-log.md) | 常见错误记录（设计前必读） |
| [references/skill-glossary.md](references/skill-glossary.md) | 特殊机制/中间产物/等效效率词典 |
| [references/sources.md](references/sources.md) | 排班信息源和学习路径 |

## 快速开始

### 1. 计算干员组合效率

```bash
# 计算贸易站组合效率
python scripts/efficiency_calculator.py 贸易站 "巫恋,龙舌兰,但书"

# 计算制造站组合效率（冬时归零体系）
python scripts/efficiency_calculator.py 制造站 "清流,温蒂,冬时" 贵金属

# 查看内置技能数据库
python scripts/efficiency_calculator.py --list-skills
```

### 2. 生成完整排班方案

```bash
# 三班倒纯赚钱方案（自动推荐最优布局）
python scripts/schedule_generator.py --roster 干员练度表.txt --goal 纯赚钱 --shifts 3

# 全力搓玉，指定 252 布局，输出 JSON 文件
python scripts/schedule_generator.py --roster 干员练度表.txt --goal 全力搓玉 --shifts 3 --layout 252 --output 搓玉方案.json

# 赚钱+搓玉混合，两班倒
python scripts/schedule_generator.py --roster 干员练度表.txt --goal 赚钱+搓玉 --shifts 2
```

### 3. 验证排班方案

```bash
python scripts/efficiency_calculator.py --check 排班文件.json
```

### 4. 试用样本数据

```bash
# 计算样本数据中的贸易站组合
python scripts/efficiency_calculator.py 贸易站 "鸿雪,图耶,绮良"

# 生成样本数据排班
python scripts/schedule_generator.py --roster samples/sample_干员练度表.txt --goal 纯赚钱 --shifts 3

# 检查样本排班方案
python scripts/efficiency_calculator.py --check samples/sample_342方案.json
```

## 排班目标说明

| 目标 | 生产策略 | 推荐布局 |
|------|----------|----------|
| 纯赚钱 | 全赤金 → 全龙门币 | 243 |
| 纯搓玉 | 全源石碎片 → 全合成玉 | 252 |
| 全力搓玉 | 最大化源石产出，建议 2 贸易 | 252 |
| 赚钱+经验书 | 赤金 + 作战记录混合 | 243 |
| 赚钱+搓玉 | 赤金 + 源石碎片混合 | 243 |

## 输出格式说明

生成器输出两部分：

1. **文字表格** — 打印到终端，直接展示每个班次的房间分配和干员出勤统计
2. **JSON 文件** — 可导入效率计算器验证，支持二次编辑

JSON 结构：

```json
{
  "name": "243 纯赚钱 三班倒排班方案",
  "layout": "243",
  "goal": "all_gold",
  "shifts_per_day": 3,
  "shifts": {
    "A班 (08:00-20:00)": {
      "rooms": {
        "贸易站#1": ["但书"],
        "制造站#1": ["清流", "温蒂", "冬时"],
        ...
      },
      "product": "Pure Gold"
    },
    ...
  }
}
```
