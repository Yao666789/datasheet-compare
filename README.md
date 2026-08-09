# datasheet-compare

> 元器件 datasheet PDF → 结构化参数 → 选型对比表。**让 AI 不编参数。**

[![Python](https://img.shields.io/badge/Python-3.11+-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

## V2 通用提取（推荐）

任意规格书直接丢，全参数提取 + 归一化对齐 + 对比：

```bash
# 1. 提取：任意规格书 → 全参数 JSON（页码溯源 + 中英双语参数名）
python scripts/datasheet_v2.py extract 任意规格书.pdf [--category ntc] -o result.json

# 2. 归一化：不同厂商同一参数自动对齐（词典为主 + LLM 兜底）
python scripts/datasheet_v2.py normalize result.json [-o result.norm.json] [--llm-fallback]

# 3. 对比：多家 → 一份 Excel（差异标黄 + 页码）
python scripts/datasheet_v2.py compare a.norm.json b.norm.json -o compare.xlsx

# 4. 本体工具链：词典健康检查 / 建议一键入典 / LLM 调用计数（每次 extract/normalize 自动打印）
python scripts/datasheet_v2.py lint-ontology   # 重别名/key 命名/空别名/品类标签校验
python scripts/datasheet_v2.py promote         # suggestions.yaml 审核通过 → 入典 params.yaml
```

参数本体 `ontology/params.yaml`（109 参数，中英别名）：提取出的新参数自动生成入典建议（`ontology/suggestions.yaml`），审核后 `promote` 一键入典，词典越跑越准。

### 能力边界（实测声明）

| 场景 | 适用模式 | 说明 |
|---|---|---|
| 单型号 / 单表规格书 | V2 通用提取 ✅ | 覆盖率 90.9~100%，页码溯源 100% |
| 零 ontology 新品类（二极管等） | V2 通用提取 ✅ | 1N4007 实测 91.7%，核心参数全中 |
| 多列系列特性表（每行多型号多列+容差） | **v1 品类模式复核** ⚠ | 值归列不稳（如 TDK B82794C0 电感），unit 量级校验器自动标 ⚠，建议 v1 schema 模式提取对照 |

这是设计边界不是缺陷：V2 管"通用识别"，v1 管"品类精准"，两模式并存。

## v1 品类模式（兼容保留）

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
| 二极管 | VF/trr/IFSM/反向耐压 | ✅ 已验证 |
| TVS 瞬态抑制 | V_BR/钳位电压/峰值脉冲功率/回流焊曲线 | ✅ 已验证 ×2 |
| CBB 薄膜电容 | dv/dt/ESR/寿命/稳态湿热/耐久性判据 | ✅ 已验证 ×2 |

## V2 实测成绩单（2026-08-06，双指标）

**已知参数覆盖率**（ontology 品类参考参数中提取/合理拒绝比例）+ **页码溯源率**（提取参数带页码比例，零幻觉代理指标）。

| Datasheet | 品类 | 覆盖率 | 页码溯源 | 备注 |
|---|---|---|---|---|
| MF72-10D7（时恒） | NTC | 100%（26/26） | 100% | 10 个 v1 字段全中 + 17 个新参数 |
| MF72（Cantherm） | NTC | 100%（26/26） | 100% | B 值正确拒绝（厂商不标） |
| SCK10054（兴勤） | NTC | 96.2%（25/26） | 100% | 额定电压原文确无，LLM 未主动标注 |
| F863H（KEMET） | X2 | 90.9%（20/22） | 100% | 新增 msl/hs_code 未覆盖，材料结构细节已提 |
| R52（KEMET） | X2 | 90.9%（20/22） | 100% | 同上 |
| B82794C0（TDK） | CMC | 100%（17/17） | 100% | 漏感 1300nH 正确区分；主电感值归列不稳→v1 复核 |
| **1N4007（Diotec）** | **二极管** | **91.7%（55/60）** | 100% | **零 ontology 基础新品类**，VF/trr/IFSM 全中 |
| 14D 系列（Bourns，1 页 demo） | MOV | 不计 | 100% | 演示件，不纳入正式成绩 |

- **8 份实测，正式 7 份全部 ≥90.9%**，页码溯源率 198/198 = **100%**
- **零幻觉抽查**：核心参数逐条回原文核对一致（时恒 R25=10Ω±20%、B 值 2800K；1N4007 VF<1.1V、trr=1500ns、IFSM=30A）；唯一发现 CMC 电感字段错位（unit 校验器自动标 ⚠ 拦截，未静默通过）
- 新品类实测（二极管）证明零 ontology 基础可用，12 个二极管参数已反哺入典

### V2 品类毕业补测（2026-08-09，二轮评审后）

| Datasheet | 品类 | 对齐 | 备注 |
|---|---|---|---|
| **SMBJ 系列（Vishay）** | **TVS** | **30/30** | 全词典命中，P0-2 归一化警告消失 |
| **SMBJ 系列（Littelfuse）** | **TVS** | **44/44** | 含全套回流焊曲线参数，V_BR 温度系数归位 |
| **MKP1848（Vishay）** | **CBB** | **40/40** | DC-Link 大功率，暴露 15 条 CBB 专属参数反哺入典 |
| **CBB21B（SRD 国产）** | **CBB** | **29/29** | 暴露 8 条 GB 稳态湿热/耐久性试验判据反哺入典 |

- TVS 双厂商对比表 2×44 项、CBB 双厂商对比表 2×49 项，**零未归一化 ⚠，页码溯源 100%**
- 词典本轮 86 → 109 参数；verify_fixes 回归 **19/19 零回归**，8 份老数据 223/223 不受影响

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

## 快速部署（5 步）

```bash
# 1. 拿代码
git clone https://github.com/Yao666789/datasheet-compare.git
cd datasheet-compare

# 2. 装依赖（仅 4 个）
pip install -r requirements.txt

# 3. 配 LLM 密钥（DeepSeek，几块钱；OpenAI 兼容接口可用 LLM_BASE_URL 切换）
export DEEPSEEK_API_KEY=sk-your-key

# 4. 跑（V2 通用提取 / 对比）
python scripts/datasheet_v2.py extract your_datasheet.pdf -o result.json
python scripts/datasheet_v2.py normalize result.json -o result.norm.json
python scripts/datasheet_v2.py compare a.norm.json b.norm.json -o compare.xlsx

# 5. 挂载为 skill（AI 平台）：把本仓库放入平台的 skills 目录即可（SKILL.md 是入口）
```

项目结构：

```
datasheet-compare/
├── SKILL.md                 # skill 入口（Hermes/Claude 等 AI 平台直接挂载）
├── ontology/params.yaml     # 参数本体：109 参数中英别名词典（归一化核心）
├── ontology/suggestions.yaml  # 新参数入典建议（promote 审核后移入 params.yaml）
├── scripts/
│   ├── datasheet_v2.py      # V2：通用提取 + 归一化 + 对比 + lint/promote（推荐）
│   └── datasheet_tool.py    # v1：品类 schema 严格提取（兼容保留）
├── schemas/                 # 品类参数模板（v1 用，V2 作提示参考）
├── requirements.txt
└── README.md
```

## 谁做的

我在制造业产线上做工艺工程师。每天看 datasheet、选元器件、手抄参数做对比表。这个工具解决的就是我自己的痛点。非科班出身，但我造的东西是跑在真实工厂里用的。

项目已开源至 GitHub，已覆盖七个品类（MOV/NTC/X2/CMC/二极管/TVS/CBB），正在向更多品类扩展。欢迎提 issue、补 schema、一起做。

## License

MIT
