<div align="center">

# Arknights Base Skill 🏭

> 明日方舟基建排班设计指南与工具集

![license](https://img.shields.io/badge/license-MIT-blue)

</div>

## 📦 内容

| 目录 | 说明 | 状态 |
|------|------|------|
| [ark-base-schedule](skills/ark-base-schedule/) | 排班设计指南：七条核心链路、等效效率计算、三班分配 | ✅ 稳定 |
| [update-ark-skills](skills/update-ark-skills/) | 从一图流抓取最新基建技能数据，更新本地干员技能表 | ✅ 稳定 |

## 📁 仓库结构

```
├── README.md
├── LICENSE
├── CONTRIBUTING.md
├── CHANGELOG.md
├── .gitignore
├── scripts/
│   └── validate-all.sh           # 批量校验所有技能完整性
├── templates/
│   └── skill-template/           # 新技能脚手架
│       └── README.md
└── skills/
    ├── ark-base-schedule/
    │   ├── schedule-guide.md      # 主指南
    │   ├── schedule-checklist.md  # 校验表
    │   ├── CHANGELOG.md
    │   └── references/
    │       ├── error-log.md
    │       ├── skill-glossary.md
    │       └── sources.md
    └── update-ark-skills/
        ├── README.md              # 数据更新流程
        └── CHANGELOG.md
```

## 📄 License

MIT
