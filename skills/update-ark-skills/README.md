---
name: update-base-skills
description: 从一图流基建技能一览页面抓取最新数据，更新 D:\workspace\PRTS 下的基建技能一览_全干员.txt
tools: Bash, Read, Write, Glob, WebFetch
---

# 更新基建技能一览

从 `https://ark.yituliu.cn/information/logistics` 抓取最新基建技能数据，更新 `基建技能一览_全干员.txt`。

## 工作目录

WORKDIR = `D:\workspace\PRTS`（每次运行命令前先切过去：`cd /d/workspace/PRTS &&`）

## 前置条件

- `agent-browser` 可通过 `npx agent-browser` 使用
- 项目目录下已有 `干员练度表.txt`（格式：tab 分隔，列依次为 干员名称、是否已招募、星级、等级、精英化等级……）

## 流程

### Step 1: 打开一图流基建技能一览页，获取原始数据

```bash
npx agent-browser open "https://ark.yituliu.cn/information/logistics"
npx agent-browser wait 2000
npx agent-browser press Escape; npx agent-browser wait 500
npx agent-browser press Escape; npx agent-browser wait 500
# 关闭导航遮罩并点击"工作场所"按钮展开所有筛选
npx agent-browser eval "(function(){var s=document.querySelector('.v-navigation-drawer__scrim');if(s)s.remove();})()"
npx agent-browser wait 500
# 切片保存到文件
cd /d/workspace/PRTS
npx agent-browser snapshot 2>&1 | grep "^          - cell " | sed 's/^          - cell "//;s/" \[ref=.*\]$//' > cells_clean.txt
```

### Step 2: 解析为结构化数据

```bash
cd /d/workspace/PRTS
python parse_skills.py
```
如果 `parse_skills.py` 不存在，说明是第一次运行，直接从第三步的内联脚本走。

输出：`skills_parsed.txt`（格式：`干员名|精等级|设施|技能名|技能描述`）

### Step 3: 生成最终全干员表

```bash
cd /d/workspace/PRTS
python build_final_all.py
```

如果 `build_final_all.py` 不存在（首次运行），用内联脚本代替：

```bash
cd /d/workspace/PRTS && python -c "
import os
os.chdir('D:\\workspace\\PRTS')

skills = {}
# 尝试加载 parse_skills.py 的输出
try:
    with open('skills_parsed.txt', 'r', encoding='utf-8') as f:
        for line in f:
            parts = line.strip().split('|')
            if len(parts) >= 5:
                name = parts[0].strip()
                if name not in skills:
                    skills[name] = []
                skills[name].append((parts[1].strip(), parts[2].strip(), parts[3].strip(), parts[4].strip()))
except FileNotFoundError:
    pass

# 如果 skills_parsed.txt 不存在，尝试直接从 cells_clean.txt 解析
if not skills:
    with open('cells_clean.txt', 'r', encoding='utf-8') as f:
        raw_lines = [l.strip() for l in f if l.strip()]
    raw_lines = raw_lines[5:]
    current_name = None
    for i in range(0, len(raw_lines) - 4, 5):
        block = raw_lines[i:i+5]
        first = block[0]
        is_name = (len(first) <= 6 and not first.startswith(('精', '无', '进驻', '当与', '宿舍', '如果下笔', '贸易站', '制造站', '发电站', '控制中枢', '会客室', '加工站', '办公室', '训练室', '人力办公室', '控制', '制造')))
        if is_name:
            current_name = first
        if current_name and block[-1].startswith(('进驻', '当与', '宿舍', '如果下笔')):
            if is_name:
                name, elite, facility, skill_name, desc = block
            else:
                name, elite, facility, skill_name, desc = current_name, block[0], block[1], block[2], block[3]
            if name not in skills:
                skills[name] = []
            skills[name].append((elite, facility, skill_name, desc))

all_ops = {}
with open('干员练度表.txt', 'r', encoding='utf-8') as f:
    for line in f.readlines()[1:]:
        parts = line.strip().split('\t')
        if len(parts) >= 5:
            name = parts[0].strip()
            all_ops[name] = {'star': parts[2].strip(), 'level': parts[3].strip(), 'elite': parts[4].strip(), 'owned': parts[1].strip().upper() == 'TRUE'}

with open('基建技能一览_全干员.txt', 'w', encoding='utf-8') as f:
    f.write('基建技能一览（全干员）\n共 ' + str(len(all_ops)) + ' 名干员\n' + '=' * 80 + '\n\n')
    for name in sorted(all_ops.keys()):
        info = all_ops[name]
        tag = '[已招募]' if info['owned'] else '[未招募]'
        f.write(tag + ' 【' + name + '】星级' + info['star'] + ' Lv' + info['level'] + ' E' + (info['elite'] if info['elite'] else '0') + '\n')
        if name in skills:
            for sk in skills[name]:
                f.write('  ' + sk[0] + ' | ' + sk[1] + ' | ' + sk[2] + ' | ' + sk[3] + '\n')
        else:
            f.write('  (无基建技能数据)\n')
        f.write('\n')
print('Done: ' + str(len(all_ops)) + ' operators, ' + str(sum(len(v) for v in skills.values())) + ' skills')
"
```

### Step 4: 验证

```bash
cd /d/workspace/PRTS && wc -l 基建技能一览_全干员.txt
```
