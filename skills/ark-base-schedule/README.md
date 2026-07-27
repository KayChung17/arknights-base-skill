# 明日方舟排班设计指南

设计最优基建排班方案，基于等效效率计算与七条核心链路。

## 什么时候用

- 需要设计/优化基建排班
- 计算贸易站/制造站等效效率
- 分配三班（A/B/C）干员
- 验证排班方案的正确性

## 文件说明

| 文件 | 说明 |
|------|------|
| [schedule-guide.md](schedule-guide.md) | 主指南：核心认知 + 七条链路 + 设计流程 |
| [schedule-checklist.md](schedule-checklist.md) | 排班完整性校验表 |
| [scripts/efficiency_calculator.py](scripts/efficiency_calculator.py) | 效率计算器（五层叠加模型） |
| [samples/sample_干员练度表.txt](samples/sample_干员练度表.txt) | 样本干员数据，供测试用 |
| [samples/sample_skills_parsed.txt](samples/sample_skills_parsed.txt) | 样本技能数据，供测试用 |
| [samples/sample_342方案.json](samples/sample_342方案.json) | 样本排班方案，供 --check 验证 |
| [references/error-log.md](references/error-log.md) | 常见错误记录（设计前必读） |
| [references/skill-glossary.md](references/skill-glossary.md) | 特殊机制/中间产物/等效效率词典 |
| [references/sources.md](references/sources.md) | 排班信息源和学习路径 |

## 快速开始

1. 确保项目目录下有 `干员练度表.txt` 和 `skills_parsed.txt`
2. 读 [error-log.md](references/error-log.md) 避免常见错误
3. 按 [schedule-guide.md](schedule-guide.md) 的五步流程设计
4. 用效率计算器验证：

```bash
# 计算贸易站组合效率
python scripts/efficiency_calculator.py 贸易站 "巫恋,龙舌兰,但书"

# 计算制造站组合效率
python scripts/efficiency_calculator.py 制造站 "清流,温蒂,冬时" 贵金属

# 验证完整排班方案
python scripts/efficiency_calculator.py --check 排班文件.json
```

5. 用 [schedule-checklist.md](schedule-checklist.md) 逐项校验

## 试用样本数据

仓库提供了样本数据，可以直接体验完整流程：

```bash
# 计算样本数据中的贸易站组合
python scripts/efficiency_calculator.py 贸易站 "鸿雪,图耶,绮良"

# 检查样本排班方案
python scripts/efficiency_calculator.py --check samples/sample_342方案.json

# 查看内置技能数据库（44名干员）
python scripts/efficiency_calculator.py --list-skills
```
