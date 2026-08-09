---
name: datasheet-compare
description: 元器件规格书（datasheet PDF）→ 结构化参数提取 → 选型对比表。反幻觉提取（不编参数、带页码溯源），任意规格书直接丢，多份自动对齐对比。用于元器件选型、供应商对比、工艺参数核对。当用户提供元器件规格书 PDF、需要提取参数、多家供应商横向对比、做来料检验或选型评估时使用。支持中文工艺视角输出。
version: 2.0.0
author: Yao666789
license: MIT
platforms: [windows, linux]
metadata:
  hermes:
    tags: [电子元器件, datasheet, 规格书, 参数提取, 选型对比, 反幻觉, NTC, MOV, X2, CMC]
    related_skills: []
required_environment_variables:
  - name: DEEPSEEK_API_KEY
    prompt: "Enter your DeepSeek API key"
    help: "Get one at https://platform.deepseek.com"
    required_for: "LLM 参数提取与归一化（可用 LLM_BASE_URL/LLM_MODEL 切换其他 OpenAI 兼容接口）"
---

# Datasheet Compare — 元器件规格书通用提取与对比（V2）

管道式工具：PDF 进，对比表出。不要让用户写 prompt、不要多轮对话。

## 工作流程

1. **提取**（每份 PDF）：
   ```bash
   python scripts/datasheet_v2.py extract <文件.pdf> [--category <品类>] [--part <型号>] -o <输出.json>
   ```
   - `--category` 可省（mov/ntc/x2/cmc/cbb），省则全参数参考；给出更贴合该品类
   - 系列料表必须带 `--part` 指定目标型号
   - 环境变量 `DEEPSEEK_API_KEY` 必填；可用 `LLM_BASE_URL` / `LLM_MODEL` 换其他 OpenAI 兼容模型

2. **归一化**（对齐不同厂商的同一参数；建议每条结果都跑）：
   ```bash
   python scripts/datasheet_v2.py normalize <输出.json> [--llm-fallback] -o <输出.norm.json>
   ```
   - 词典为主（`ontology/params.yaml`，36+ 参数中英别名）；新参数加 `--llm-fallback` 让 LLM 归类
   - 新参数建议写入 `ontology/suggestions.md`，审核后移入 params.yaml（词典越跑越准）

3. **对比**（≥2 份归一化结果 → 一份 Excel）：
   ```bash
   python scripts/datasheet_v2.py compare *.norm.json -o compare.xlsx
   ```
   黄色单元格 = 各家存在差异的参数（选型关注点）；"—" = 未标注；差异单元格带溯源页码。

4. **呈现**：先给用户看控制台 markdown 速览表，再给 Excel 文件（选型对比 + 每料号提取明细 sheet）。

## 维护规则

- `ontology/params.yaml` 是**参数本体活文档**：发现新参数（如新认证、特殊降额曲线），审核 `ontology/suggestions.md` 后入典，再重跑提取。
- 新增品类模板（提示参考用）：在 `schemas/` 加 yaml，`scripts/datasheet_tool.py` 的 `CATEGORY_FILES` 注册（v1 兼容路径）。
- 提取结果以 datasheet 原文为准，值不换算、不推测；LLM 返回与原文矛盾时信原文。
- 扫描件（无文字层）会被工具拒绝并提示先 OCR——不编造是设计，不是缺陷。

## 安装到 Hermes Agent

本仓库遵循 [agentskills.io](https://agentskills.io/specification) 开放标准（Hermes Agent / Claude 通用）：

```bash
# 从 GitHub 仓库安装（Hermes Agent 官方方式）
hermes skills install --source https://github.com/Yao666789/datasheet-compare.git

# 或平台 API（hermes-agent-team 管理端）
# POST /api/agents/<agent_id>/skills/install  {"source_url": "https://github.com/Yao666789/datasheet-compare.git"}

# 或发布到 Skills Hub
hermes skills publish skills/datasheet-compare --to github --repo Yao666789/datasheet-compare
```

安装后自动注册斜杠命令 `/datasheet-compare`；`DEEPSEEK_API_KEY` 由 Hermes 从 `~/.hermes/.env` 自动注入，无需写进代码。

## 兼容

- v1 品类 schema 流程仍可用：`scripts/datasheet_tool.py extract/compare`（按品类严格提取）。
- Python API：`from scripts.datasheet_v2 import load_ontology, match_ontology` 等函数可直接内嵌调用。
