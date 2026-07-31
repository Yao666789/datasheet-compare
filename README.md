# datasheet-compare

> 元器件 datasheet PDF → 结构化参数 → 选型对比表。**让 AI 不编参数。**

[![Python](https://img.shields.io/badge/Python-3.11+-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

```bash
# 提取单份 datasheet → JSON
python scripts/datasheet_tool.py extract sck_ntc.pdf --category ntc --part SCK10054 -o result.json

# 多家供应商对比 → Excel（差异自动标黄）
python scripts/datasheet_tool.py compare bourns.json junyao.json --category mov -o compare.xlsx
```

## 为什么做这个

**通用 LLM 读元器件规格书会编造参数。** 一个假的压敏电压、一个漏掉的 B 值，在工艺选型里就是一颗定时炸弹。

这个工具是 **工艺工程师视角的反幻觉提取 Agent**——它知道每个品类应该抠哪些参数，原文没有的参数**绝不编造**，标注 "not_in_datasheet" 并附溯源页码。

## 核心设计

### 🛡️ 反幻觉机制
```
原文没有 B 值 → {"b_constant": {"value": null, "status": "not_in_datasheet",
                 "evidence": {"searched_sections": ["第3-6页电气特性表格", "..."]}}}
```
每一个空值都有溯源。不是"没提取到"，是"我找了，原文没有"。

### 📐 value/unit 分离
```json
{"varistor_voltage": {"value": "470", "unit": "V", "tolerance": "±10%"}}
```
不会出现 "470V" 这种单位绑在值里的糨糊输出。数值、单位、容差、限定词四个独立字段，对比时直接对齐。

### 📄 head+tail 截断
22 页 datasheet？不把末尾的包装信息丢掉。前段 60K 字符覆盖电气参数，后段 20K 字符抓 packaging / ordering info，中间的重复规格表不要。

## 品类覆盖

| 品类 | 关键参数 | 状态 |
|---|---|---|
| 压敏电阻 (MOV) | 压敏电压/通流量/能量耐量/限制电压 | ✅ 已验证 |
| NTC 热敏电阻 | R25/稳态电流/B值/热时间常数 | ✅ 已验证 |
| X2 安规电容 | 安规等级/气候类别/湿热等级 | 🚧 模板已建 |
| 共模电感 | 阻抗/DCR/耐压 | 🚧 模板已建 |
| CBB 电容 | dv/dt/耐压余量 | 📋 计划中 |

## 安装

```bash
pip install pdfplumber openai pyyaml xlsxwriter
export DEEPSEEK_API_KEY=sk-your-key
python scripts/datasheet_tool.py extract your_datasheet.pdf --category mov
```

## 谁做的

我在制造业产线上做工艺工程师。每天看 datasheet、选元器件、手抄参数做对比表。这个工具解决的就是我自己的痛点。非科班出身，但我造的东西是跑在真实工厂里用的。

项目计划开源至 GitHub，正在扩展至五品类。欢迎提 issue、补 schema、一起做。

## License

MIT
