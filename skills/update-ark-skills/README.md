# 更新基建技能一览

从一图流网站抓取最新基建技能数据，更新本地干员技能数据表。

## 数据来源

| 来源 | 网址 | 说明 |
|------|------|------|
| 一图流基建技能一览 | [ark.yituliu.cn/information/logistics](https://ark.yituliu.cn/information/logistics) | 全干员基建技能数据，随版本更新 |
| PrTS Wiki | [prts.wiki](https://prts.wiki) | 精确的基建技能描述和机制说明 |

## 文件说明

| 文件 | 说明 |
|------|------|
| [scripts/parse_skills.py](scripts/parse_skills.py) | 从一图流切片数据解析为结构化技能数据 |
| [scripts/build_final_all.py](scripts/build_final_all.py) | 合并干员练度表和技能数据，生成全干员一览表 |

## 数据格式说明

### 干员练度表（`干员练度表.txt`）

Tab 分隔，列依次为：

```
干员名称  是否已招募  星级  等级  精英化等级  潜能  信赖
但书      TRUE        5     90    2          6     200
```

### 解析后的技能数据（`skills_parsed.txt`）

Pipe 分隔，字段依次为：

```
干员名|精等级|设施|技能名|技能描述
但书|E2|贸易站|违约体验·β|每有4赤金订单中的赤金交付数-1...
```

## 流程

### Step 1: 获取原始数据

打开 [一图流基建技能一览页](https://ark.yituliu.cn/information/logistics)，通过浏览器开发者工具或自动化工具获取页面中基建技能列表的文本内容，保存为 `cells_clean.txt`。

### Step 2: 解析为结构化数据

```bash
python scripts/parse_skills.py --input cells_clean.txt --output skills_parsed.txt
```

输出：`skills_parsed.txt`（格式：`干员名|精等级|设施|技能名|技能描述`）

### Step 3: 生成最终全干员表

```bash
python scripts/build_final_all.py \
  --operators 干员练度表.txt \
  --skills skills_parsed.txt \
  --output 基建技能一览_全干员.txt
```

### Step 4: 验证

```bash
wc -l 基建技能一览_全干员.txt
```
