# datasheet-compare V2 通用化设计（2026-08-05 定稿方向）

> 主人拍板：词典归一化（效果不好再换纯 LLM）；参数名中英双语（英文 canonical 机器用 + 中文标注人看）；打包成 skill 包可被公司 AI 快速调用，任何开发者下载后简单部署即可用。
> 背景：赵总 8/5 约见，v1 将升级为通用 skill 部署到公司 Hermes AI 平台。

## 一句话目标

**丢任何规格书 → 自动提取全部参数（带页码溯源）→ 多份自动对齐对比 → 一个 skill 包，任何 AI 平台 / 开发者都能直接调用。**

## 核心变化：从"品类 schema 驱动"到"参数本体驱动"

v1 用品类 schema 硬编码参数清单（NTC 抠 R25/B 值……）；V2 去掉硬约束，LLM 自由识别参数，由**参数本体（ontology）**负责统一认识：

```
PDF ─→ ① 提取层：LLM 自由识别全部参数（保留反幻觉 + 页码 + 四字段分离）
     ─→ ② 归一化层：原始参数名 → canonical key（词典为主 + LLM 兜底）
     ─→ ③ 汇总/对比层：所有参数 × 所有料号 矩阵，差异标黄 + 页码
     ─→ ④ 接口层：CLI / Python API / HTTP 三端
```

## ② 归一化层（墨斗所指难点的解法）

**词典为主 + LLM 兜底**：

1. 查 `ontology/params.yaml` 别名表：`压敏电压` / `Varistor Voltage` / `UN` → `varistor_voltage`
2. 命中 → 用 canonical；未命中 → LLM 判断"最接近哪个 canonical 或这是新参数"，结果**回写词典**（越跑越准，护城河）
3. 新参数自动登记：生成建议条目（中文名 + 英文别名），人工审核后入典

## 参数本体格式（中英双语）

```yaml
# ontology/params.yaml
varistor_voltage:
  zh: 压敏电压
  category: [mov]
  unit_hint: V
  aliases: [压敏电压, 压敏电压UN, UN, Varistor Voltage, Variator Voltage, バリスタ電圧]
r25:
  zh: 零功率电阻值（25℃）
  category: [ntc]
  unit_hint: Ω
  aliases: [R25, 零功率电阻, Resistance at 25℃, R25@25℃]
```

## 提取输出格式（V2 通用版）

```json
{
  "source_file": "xxx.pdf",
  "parameters": [
    {"key": "varistor_voltage", "zh": "压敏电压",
     "original": "压敏电压(V)", "value": "470", "unit": "V",
     "tolerance": "±10%", "page": "第3页 电气性能表",
     "status": "extracted"}
  ]
}
```

- `key` = canonical 英文（机器对齐用）；`zh` = 中文标注（Excel 展示用）
- 对比表按 `key` 对齐，中文列方便人读

## ④ 接口层（三端 + skill 包）

### skill 包结构（部署核心）

```
datasheet-compare/
├── SKILL.md            # skill 入口：触发词、用法、参数说明（Hermes/Claude 直接挂载）
├── ontology/params.yaml # 参数本体词典
├── scripts/
│   ├── datasheet_tool.py  # CLI + Python API 同一入口
│   └── server.py          # 可选 HTTP 服务（FastAPI/Flask）
├── schemas/            # 品类参考清单（降级为提示参考，非硬约束）
├── requirements.txt
└── README.md           # 5 步部署说明
```

### 三端调用

| 方式 | 命令 | 用途 |
|---|---|---|
| CLI | `python scripts/datasheet_tool.py extract x.pdf -o x.json` | 人工 / shell |
| Python API | `from datasheet_tool import extract_pdf, compare` | 平台代码内嵌 |
| HTTP | `POST /extract` `POST /compare` | 平台服务调用 |

### 快速部署（开发者视角）

```bash
git clone https://github.com/Yao666789/datasheet-compare.git
pip install -r requirements.txt
# 跑
python scripts/datasheet_tool.py extract 任意规格书.pdf
# 或挂载为 skill：把 SKILL.md 放进平台的 skills 目录即可
```

## 复用什么（不推倒重来）

- ✅ 反幻觉机制（null / not_in_datasheet / evidence.searched_sections）
- ✅ 页码溯源（=== 第N页 === 分页标记 + prompt 规则 10）
- ✅ value/unit/tolerance/qualifier 四字段分离
- ✅ head+tail 截断、扫描件拒绝、系列表 uncertain
- ✅ compare 差异标黄 + 提取明细 sheet

## 新增什么

1. `ontology/params.yaml` 参数本体（从已跑 8+ 份规格书积累首批条目：MOV/NTC/X2/CMC ~40 参数）
2. 提取 prompt 改造：去 schema 硬约束 → 自由识别 + "给每个参数起 canonical 名 + 中文名"规则
3. 归一化器：词典匹配 → LLM 兜底 → 回写建议
4. 输出格式升级：`parameters` 数组 + key/zh/original
5. SKILL.md + server.py + requirements.txt

## 里程碑

| 步骤 | 内容 | 依赖 |
|---|---|---|
| M1 | 参数本体 v0（~40 参数，从已跑规格书积累）+ 提取 prompt 改造 | 无 |
| M2 | 归一化器（词典 + LLM 兜底 + 回写） | M1 |
| M3 | 输出/对比适配 key 对齐 + 中文标注 Excel | M1/M2 |
| M4 | skill 包（SKILL.md + requirements + README 部署说明） | M1-M3 |
| M5 | 实测回归：旧 8 份规格书重跑，命中率不低于 90.9% | M1-M4 |

## 风险与对策

- **自由识别可能提太多噪音参数** → 阈值过滤（status 权重 + 长度/单位合理性）+ canonical 白名单优先展示
- **LLM 兜底偶发错配** → 错配结果不进词典自动生效，人工审核位
- **schema 降级后品类质量下降** → schemas 仍作提示参考注入 prompt，不删除
