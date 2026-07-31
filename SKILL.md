---
name: datasheet-compare
description: 把元器件 datasheet PDF 变成结构化参数和选型对比表。当用户提供元器件规格书（压敏电阻/X2安规电容/共模电感/NTC/CBB），或需要提取参数、多家供应商横向对比、做来料检验或选型评估时使用。支持中文工艺视角输出。
---

# Datasheet Compare — 元器件选型对比工具

管道式工具：PDF 进，对比表出。不要让用户写 prompt、不要多轮对话。

## 工作流程

1. **定品类**：从用户给的 datasheet 判断品类，映射到 `--category`：
   - 压敏电阻/MOV/Varistor → `mov`
   - X2 安规电容/安规X电容 → `x2`
   - 共模电感/Common Mode Choke → `cmc`
   - NTC 功率热敏电阻/浪涌抑制 → `ntc`
   - CBB/金属化聚丙烯薄膜电容 → `cbb`

2. **提取**：对每份 PDF 执行（系列料表必须带 `--part` 指定目标型号）：
   ```bash
   python scripts/datasheet_tool.py extract <文件.pdf> --category <品类> --part <型号> -o <输出.json>
   ```
   环境变量 `DEEPSEEK_API_KEY` 必填；可用 `LLM_BASE_URL` / `LLM_MODEL` 换其他 OpenAI 兼容模型。
   提取后**核对控制台列出的"未提取到"字段**——datasheet 真的没有就正常，有但没抓到就把对应页文本贴出来重提。

3. **对比**：≥2 份 JSON 生成 Excel 对比表：
   ```bash
   python scripts/datasheet_tool.py compare *.json --category <品类> -o compare.xlsx
   ```
   黄色单元格 = 各家存在差异的参数（选型关注点），"—" = datasheet 未标注。

4. **呈现**：先给用户看控制台 markdown 速览表，再给 Excel 文件。

## 维护规则

- schema 文件（`schemas/*.yaml`）是**品类参数模板**，是活文档：发现 datasheet 里有 schema 没覆盖的关键参数（如新认证、特殊降额曲线），先加进对应 yaml 再重跑提取。
- 新增品类：在 `schemas/` 加一个 yaml（仿照现有格式），并在 `scripts/datasheet_tool.py` 的 `CATEGORY_FILES` 注册。
- 提取结果以 datasheet 原文为准，值不换算、不推测；LLM 返回与原文矛盾时信原文。
