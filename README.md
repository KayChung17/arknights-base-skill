<div align="center">

# Arknights Base Schedule 🏭

> 明日方舟基建排班设计指南与自动化工具

生成最优排班方案 · 计算等效效率 · 管理干员技能数据

![license](https://img.shields.io/badge/license-MIT-blue)

</div>

---

## 它能做什么

| 场景 | 一句话 | 怎么做 |
|------|--------|--------|
| **我要最优排班** | 给干员列表 + 赚钱/搓玉目标 → 拿到排班表 | [排班生成器](#-生成完整排班方案) |
| **这个组合效率多少** | 输入几个干员 → 看到五层效率明细 | [效率计算器](#1-计算干员组合效率) |
| **验证方案对不对** | 已有排班 → 检查冲突 + 算每个房间效率 | [方案校验](#3-验证排班方案) |
| **更新干员技能数据** | 从一图流抓最新数据 → 更新本地表 | [数据管道](skills/update-ark-skills/) |

---

## 📦 目录

| 目录 | 说明 |
|------|------|
| [ark-base-schedule](skills/ark-base-schedule/) | 排班设计指南 + 效率计算器 + 排班生成器 |
| [update-ark-skills](skills/update-ark-skills/) | 从一图流抓取基建技能数据，更新本地干员表 |

## 🚀 快速开始

### 1. 准备干员练度表

创建一个 tab 分隔的 `干员练度表.txt`：

```
干员名称  是否已招募  星级  等级  精英化等级  潜能  信赖
但书      TRUE        5     90    2          6     200
龙舌兰    TRUE        5     90    2          6     200
鸿雪      TRUE        6     90    2          6     200
```

样本数据位于 `skills/ark-base-schedule/samples/sample_干员练度表.txt`。

### 2. 计算干员组合效率

进入技能目录：

```bash
cd skills/ark-base-schedule
```

```bash
# 贸易站组合
python scripts/efficiency_calculator.py 贸易站 "龙舌兰,巫恋,但书"

# 制造站组合（指定产品类型）
python scripts/efficiency_calculator.py 制造站 "清流,温蒂,冬时" 贵金属

# 查看内置技能数据库（44 名干员）
python scripts/efficiency_calculator.py --list-skills
```

### 3. 生成完整排班方案

```bash
# 三班倒纯赚钱（自动推荐最优布局）
python scripts/schedule_generator.py --roster 干员练度表.txt --goal 纯赚钱 --shifts 3

# 全力搓玉，指定 252 布局，输出 JSON
python scripts/schedule_generator.py --roster 干员练度表.txt --goal 全力搓玉 --shifts 3 --layout 252 --output 搓玉方案.json

# 支持的目标：纯赚钱 / 纯搓玉 / 全力搓玉 / 赚钱+经验书 / 赚钱+搓玉
# 支持 2 班或 3 班换班
```

### 4. 验证排班方案

```bash
python scripts/efficiency_calculator.py --check 搓玉方案.json
```

### 5. 试用样本数据

不用准备干员表，直接用仓库自带的样本体验：

```bash
# 计算赤金生产线组合效率
python scripts/efficiency_calculator.py 贸易站 "鸿雪,图耶,绮良"

# 生成样本排班
python scripts/schedule_generator.py --roster samples/sample_干员练度表.txt --goal 纯赚钱 --shifts 3

# 校验已有样本方案
python scripts/efficiency_calculator.py --check samples/sample_342方案.json
```

---

## 📋 输出示例

运行 `python scripts/schedule_generator.py --roster 干员练度表.txt --goal 纯赚钱 --shifts 3` 的终端输出：

```
================================================================================
  243 纯赚钱 三班倒排班方案
  布局: 243  目标: 纯赚钱
================================================================================

── A班 (08:00-20:00) ──
  目标产品: 赤金
  贸易站#1: 但书                  ← 核心贸易×1.556乘算
  贸易站#2: 龙舌兰                ← 独立收益+500
  贸易站#3: 巫恋                  ← 低语清空替代
  制造站#1: 清流 + 温蒂 + 冬时    ← 等效120%（冬时归零）
  制造站#2: 娜斯提 + 多萝西       ← 莱茵科技联动
  制造站#3: 砾 + 苍苔 + 引星棘刺  ← 金属工艺
  制造站#4: 斯卡蒂 + 幽灵鲨       ← 深海猎人
  控制中枢: 森蚺

── B班 (20:00-02:00) ──
  贸易站#1~#3: 复用A班             ← 核心干员连续工作
  制造站:   换第二梯队组合

── C班 (02:00-08:00) ──
  贸易站#1: 伺夜 + 贝洛内          ← 叙拉古独立链路
  贸易站#2: 推进之王               ← 格拉斯哥帮
  控制中枢: 歌蕾蒂娅               ← 激活深海猎人

干员出勤统计:
  但书: A班 (08:00-20:00), B班 (20:00-02:00)
  龙舌兰: A班 (08:00-20:00), B班 (20:00-02:00)
  清流: A班 (08:00-20:00), B班 (20:00-02:00), C班 (02:00-08:00)
  ...
```

---

## 🧠 排班目标说明

| 目标 | 生产链路 | 推荐布局 | 适用场景 |
|------|----------|----------|----------|
| **纯赚钱** | 赤金 → 龙门币 | 243 | 缺龙门币，全力赚钱 |
| **纯搓玉** | 源石碎片 → 合成玉 | 252 | 囤合成玉等新卡池 |
| **全力搓玉** | 最大化源石产出 | 252 | 极致搓玉，建议 2 贸易 |
| **赚钱+经验书** | 赤金 + 作战记录 | 243 | 均衡发展，兼顾经验和钱 |
| **赚钱+搓玉** | 赤金 + 源石碎片 | 243 | 部分赤金换钱，部分做源石 |

---

## 🧩 仓库结构

```
├── README.md
├── LICENSE                        
├── CONTRIBUTING.md                
├── CHANGELOG.md
├── .gitignore
├── scripts/
│   └── validate-all.sh            ← 批量校验技能完整性
├── templates/
│   └── skill-template/            ← 新技能脚手架
│       ├── README.md
│       └── CHANGELOG.md
└── skills/
    ├── ark-base-schedule/         ← ★ 主技能：排班设计
    │   ├── README.md              ← 技能使用说明
    │   ├── schedule-guide.md      ← 排班设计主指南（理论 + 流程）
    │   ├── schedule-checklist.md  ← 完整性校验表
    │   ├── scripts/
    │   │   ├── efficiency_calculator.py   ← 五层效率计算器
    │   │   └── schedule_generator.py      ← 排班方案生成器
    │   ├── samples/               ← 样本数据，直接可跑
    │   │   ├── sample_干员练度表.txt
    │   │   ├── sample_skills_parsed.txt
    │   │   └── sample_342方案.json
    │   └── references/            ← 深度参考
    │       ├── error-log.md
    │       ├── skill-glossary.md
    │       └── sources.md
    └── update-ark-skills/         ← 技能数据更新
        ├── README.md
        ├── scripts/
        │   ├── parse_skills.py
        │   └── build_final_all.py
        └── samples/
```
