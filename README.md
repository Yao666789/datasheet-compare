# datasheet-compare

> 元器件 datasheet PDF → 结构化参数 → 选型对比表。**让 AI 不编参数。**

[![Python](https://img.shields.io/badge/Python-3.11+-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

```bash
# 提取单份 datasheet → JSON
python scripts/datasheet_tool.py extract sck_ntc.pdf --category ntc --part SCK10054 -o result.json

# 多家供应商对比 → 一份 Excel（选型对比表差异自动标黄 + 每料号「提取明细」sheet 含溯源）
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
| NTC 热敏电阻 | R25/稳态电流/B值/热时间常数 | ✅ 已验证 ×2 |
| X2 安规电容 | 安规等级/气候类别/湿热等级 | ✅ 已验证 ×2 |
| 共模电感 | 电感量/额定电流/DCR/耐压 | ✅ 已验证 |
| CBB 电容 | dv/dt/耐压余量 | 📋 计划中 |

## 实测成绩单（2026-08-02）

5 份 datasheet 实测，**4 份为首次跑（换厂商/换元件类型），1 份重跑**。所有空值都有溯源标注，零编造。

| Datasheet | 品类 | 厂商 | 有值 | 合理空（原文确无，附溯源） |
|---|---|---|---|---|
| MF72-10D7 规格承认书 | NTC | 南京时恒 | 10/10 | — |
| MF72 Power NTC | NTC | Cantherm | 7/10 | B值/额定功率/包装（英文规格书确实不列） |
| F863H | X2 | KEMET | 13/13 | — |
| R52 | X2 | KEMET | 13/13 | — |
| B82794C0 | CMC | TDK | 7/9 | 阻抗（原文无该列）/DCR（按料号单独标，系列无单一值） |

- **字段命中率 50/55（90.9%）**，**文档级正确率 5/5（100%）**：凡原文有参数全部提出，凡原文没有的参数全部标注 not_in_datasheet + 检索页码，未出现一次编造。
- **B 值结论翻案**：实测时恒 MF72 规格书**标 B 值**（B25/50=2800K±10%），Cantherm/兴勤 SCK 不标 → schema 已按"厂商而异"修正。
- **能力边界**：扫描件 datasheet（无文本层）会被工具拒绝而非瞎编——如时恒 MF72-20D15（图片型 PDF）直接报"疑似扫描件，请先 OCR"。

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
