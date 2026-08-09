#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
datasheet_tool.py — 元器件 datasheet → 结构化参数 → 选型对比表

用法：
  # 1. 提取单份 datasheet（需要 DEEPSEEK_API_KEY 环境变量）
  python datasheet_tool.py extract xxx.pdf --category mov --part 14D471K -o mov_14d471k.json

  # 2. 对比多份提取结果，生成 Excel 选型对比表
  python datasheet_tool.py compare mov_*.json --category mov -o mov_compare.xlsx

  # 3. 只看 prompt、不调 API（验证 schema 与 PDF 解析质量）
  python datasheet_tool.py extract xxx.pdf --category mov --dry-run

依赖：pip install pdfplumber openai pyyaml xlsxwriter
"""

import argparse
import glob
import json
import os
import re
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCHEMA_DIR = SCRIPT_DIR.parent / "schemas"

CATEGORY_FILES = {
    "mov": "mov.yaml",
    "x2": "x2_capacitor.yaml",
    "cmc": "common_mode_choke.yaml",
    "ntc": "ntc.yaml",
    "cbb": "cbb_capacitor.yaml",
}

# LLM 配置改为 call_llm 内实时读取（二轮圆桌 #6：模块级绑定会缓存旧值，长驻进程改配置不生效）
_LLM_CALLS = {"n": 0}  # 二轮圆桌 #7：本次运行 API 调用计数（成本可观测）


def llm_call_count() -> int:
    """返回本次进程累计发起的 LLM API 请求次数"""
    return _LLM_CALLS["n"]
MAX_CHARS_HEAD = 60000  # 前段：覆盖电气参数
MAX_CHARS_TAIL = 20000  # 后段：抓 packaging / mechanical / ordering info
MIN_TEXT_CHARS = 500  # 低于此长度判定为扫描件（无文本层）


def load_schema(category: str) -> dict:
    import yaml
    path = SCHEMA_DIR / CATEGORY_FILES[category]
    with open(path, encoding="utf-8-sig") as f:
        return yaml.safe_load(f)


def pdf_to_text(pdf_path: str):
    """pdfplumber 提取每页正文 + 表格（表格转 pipe 文本，LLM 易读）。
    返回 (文本, 总页数, 是否被截断, 已收录前段页数, 已收录后段页数)。
    head+tail 策略：前 MAX_CHARS_HEAD 字符覆盖电气参数，后 MAX_CHARS_TAIL 抓 packaging/mechanical info。
    表格内容若已包含在正文里则跳过，避免字符翻倍。"""
    import pdfplumber
    chunks = []
    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        for i, page in enumerate(pdf.pages, 1):
            page_lines = [f"=== 第{i}页 正文 ==="]
            text = page.extract_text() or ""
            page_lines.append(text)
            text_squashed = re.sub(r"\s+", "", text)
            for t_idx, table in enumerate(page.extract_tables(), 1):
                rows = [" | ".join("" if c is None else str(c).replace("\n", " ") for c in row) for row in table]
                table_text = "\n".join(rows)
                squashed = re.sub(r"[\s|]+", "", table_text)
                if squashed and squashed in text_squashed:
                    continue
                page_lines.append(f"=== 第{i}页 表格{t_idx} ===\n" + table_text)
            chunks.append("\n".join(page_lines))

    # head+tail 策略：前段覆盖头部，后段抓末尾 packaging/ordering info
    full_text = "\n\n".join(chunks)
    if len(full_text) <= MAX_CHARS_HEAD + MAX_CHARS_TAIL:
        # 无需截断
        result = full_text
        truncated = False
        pages_head = total_pages
        pages_tail = 0
    else:
        head = full_text[:MAX_CHARS_HEAD]
        tail = full_text[-MAX_CHARS_TAIL:]
        result = head + "\n\n[...中间内容已截断...]\n\n" + tail
        truncated = True
        # 估算前段/后段覆盖的页数
        head_chars = 0
        pages_head = 0
        for chunk in chunks:
            if head_chars + len(chunk) > MAX_CHARS_HEAD:
                break
            pages_head = pages_head + 1 if chunk else pages_head
            head_chars += len(chunk) + 2
        tail_chars = 0
        pages_tail = 0
        for chunk in reversed(chunks):
            if tail_chars + len(chunk) > MAX_CHARS_TAIL:
                break
            pages_tail += 1
            tail_chars += len(chunk) + 2

    return result, total_pages, truncated, pages_head, pages_tail


def build_prompt(schema: dict, raw_text: str, part: str = "") -> str:
    field_lines = []
    for f in schema["fields"]:
        line = f'- "{f["key"]}"：{f["name"]}'
        if f.get("unit"):
            line += f'（单位 {f["unit"]}）'
            line += '。输出格式：{"value": "数值部分", "unit": "单位部分（按原文保留）"}。无容差时 tolerance 为空字符串'
        if f.get("hint"):
            line += f'。提取提示：{f["hint"]}'
        field_lines.append(line)
    fields_block = "\n".join(field_lines)

    part_note = ""
    if part:
        part_note = f'\n这份 datasheet 是系列料表，请只提取型号 "{part}" 那一行/那一组的数据。注意：料表中的型号可能带通配符/占位符后缀（如 □、■、*，表示容差或脚型代码），请按主体型号匹配。'

    return f"""你是电子元器件工艺工程师。下面是一份「{schema["category"]}」datasheet 的完整文本（含正文与表格）。
请逐项提取以下参数，严格输出 JSON，不要输出任何其他文字。

参数清单（JSON 的 key 必须与引号内完全一致）：
{fields_block}
{part_note}
规则：
1. 有单位（unit）的字段必须拆成 value 和 unit 两个独立字段：{{"key": {{"value": "...", "unit": "..."}}}}。数值部分填 value，单位按 datasheet 原文保留填 unit。不要 value 和 unit 拼接成一个字符串。
2. Values prefixed with qualifiers like Approx. / Typ. / Nominal / ~ / Max / Min are valid values. Extract the numeric part into value, put the qualifier into a qualifier field. Do NOT return null just because a value has a prefix. Example: "Approx. 43 s" → {{"value": "43", "unit": "s", "qualifier": "Approx."}}.
3. 单位按原文保留，不做换算、不做归一化（原文写 mW/℃ 就存 mW/℃，不要换成 W/℃）。
4. 原文数值本身无单位的（如比值、倍数、安规等级），直接输出字符串值，不拆 value/unit。
4. datasheet 中没有的参数：输出 {{"key": {{"value": null, "status": "not_in_datasheet", "evidence": {{"searched_sections": ["..."]}}}}}}，在 evidence.searched_sections 里列出你查阅过的章节/页码，证明你真的找过。绝对禁止编造值——宁缺毋假。
5. 不确定是原文缺还是漏提的：输出 {{"key": {{"value": null, "status": "extraction_uncertain", "evidence": {{"uncertainty_reason": "..."}}}}}}，诚实标注不确定原因。
6. packaging 字段完整保留原文描述（如"编带包装，5000pcs/盘，每盘 13 英寸"），允许多行、跨表格单元格的内容合并提取。不得截断——如果原文写了一段包装说明，就把整段完整提取。如果 datasheet 同时有封装尺寸（如 0603、0805）和包装方式（编带/散装），拆成两个字段：package_type 和 packaging。
7. 同一参数有多个条件/多行数据时，取与指定型号相符的那一行，并在 value 后括号注明测试条件。
8. 单位写在表头时（如 "Thermal Time Constant (s)"），提取的值必须把单位找到填进 unit。
9. 只输出一个 JSON 对象，不要 markdown 代码块包裹。
10. 每个有值的字段必须附加 page 字段：该值在 datasheet 中的页码与位置（用于工艺核对溯源），如 {{"value": "10", "unit": "Ω", "page": "第3页 电气性能表 2.1"}}。页码必须来自原文文本（文本以 === 第N页 === 标记分页），严禁编造；实在无法确定写 "不确定"。

datasheet 全文如下：
{raw_text}"""


def call_llm(prompt: str) -> dict:
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("LLM_API_KEY")
    if not api_key:
        raise RuntimeError("错误：未设置 DEEPSEEK_API_KEY 环境变量（也可用 LLM_API_KEY）")
    # 清除残留代理——公司网络走 Clash 7897，但 Clash 经常不在线，直连 deepseek 即可
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
        os.environ.pop(k, None)
    os.environ["NO_PROXY"] = "*"  # 关键：urllib/httpx 读这个决定不走代理
    from openai import OpenAI
    base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")  # 实时读，支持部署时切换百炼等
    model = os.environ.get("LLM_MODEL", "deepseek-v4-flash")  # deepseek-chat 已于 2026-07-24 弃用
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=60)
    kwargs = dict(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    _LLM_CALLS["n"] += 1  # 二轮圆桌 #7：每次实际请求计数
    # 指数退避重试（Hermes 部署需求，2026-08-05 网络抖动/Connection error 为需求来源）：
    # 共 4 次尝试，间隔 2s/4s/8s——网络抖动或 API 限流时的最便宜兜底
    last_err = None
    for attempt in range(4):
        try:
            resp = client.chat.completions.create(**kwargs)
            content = resp.choices[0].message.content
            return json.loads(content)
        except Exception as e:
            last_err = e
            wait = 2 ** attempt
            print(f"API 调用失败（第 {attempt + 1} 次）：{e}；{wait}s 后重试", file=sys.stderr)
            if attempt < 3:
                time.sleep(wait)
    raise RuntimeError(f"API 连续 4 次调用失败：{last_err}")


def _wrap_value(value: str, unit: str, tolerance: str = None, qualifier: str = None, page: str = None) -> dict:
    """构造 value/unit 分离的字段值（向后兼容）"""
    d = {"value": value, "unit": unit}
    if tolerance:
        d["tolerance"] = tolerance
    if qualifier:
        d["qualifier"] = qualifier
    if page:
        d["page"] = page
    return d


def _display_value(v, unit_hint: str = "", show_page: bool = False) -> str:
    """将存储值转成展示字符串（dict → 'value unit'，string → 原样）；show_page 时附来源页码"""
    if isinstance(v, dict):
        val = v.get("value")
        if val is None:
            status = v.get("status", "")
            return f"— ({status})" if status else "—"
        q = v.get("qualifier", "")
        u = v.get("unit", "") or unit_hint
        tol = v.get("tolerance", "")
        parts = []
        if q:
            parts.append(q)
        parts.append(str(val))
        if u:
            parts.append(u)
        if tol:
            parts.append(f"±{tol}" if not tol.startswith("±") else tol)
        if show_page and v.get("page"):
            parts.append(f"({v['page']})")
        return " ".join(parts)
    return str(v) if v else "—"


def cmd_extract(args):
    schema = load_schema(args.category)
    raw, total_pages, truncated, pages_head, pages_tail = pdf_to_text(args.pdf)

    # P0 守卫：无文本层的扫描件会静默产出全空 JSON，必须先拦住
    if len(raw.strip()) < MIN_TEXT_CHARS:
        raise RuntimeError(
            f"错误：从 PDF 只提取到 {len(raw.strip())} 字符（共 {total_pages} 页），疑似扫描件/图片型 PDF，无文本层。\n"
            "请先用 OCR 工具（如 WPS、Adobe、PaddleOCR）转成文字层 PDF 再试。"
        )
    if truncated:
        print(f"⚠️ 警告：PDF 共 {total_pages} 页，提取文本超过上限，已收录前 {pages_head} 页 + 末尾 {pages_tail} 页（head+tail 策略）——"
              "系列料表可能只覆盖了部分型号，请核对提取结果是否完整！", file=sys.stderr)

    prompt = build_prompt(schema, raw, args.part or "")

    # 二轮圆桌 #4：token 预估（v1 同款）
    n_chars = len(prompt)
    print(f"prompt 长度 {n_chars:,} 字符 ≈ 预估 {n_chars // 3:,} tokens（PDF 文本 {len(raw):,} 字符）")
    if n_chars > 50000:
        print(f"⚠️ prompt 超过 5 万字符，模型 400/计费风险高——建议拆分 PDF 或先只提取关键页", file=sys.stderr)

    if args.dry_run:
        print(f"--- PDF 解析得到 {len(raw)} 字符，prompt 如下（未调 API）---\n")
        print(prompt)
        return

    data = call_llm(prompt)

    # --part 返回校验：型号可能带通配符后缀（SCK10054□），模糊比对，不匹配就响亮提醒
    if args.part:
        def _pn(s):
            return re.sub(r"[\s□■\*]+", "", str(s or "")).upper()
        returned_pn = _pn(data.get("part_number", ""))
        want_pn = _pn(args.part)
        if returned_pn and want_pn not in returned_pn and returned_pn not in want_pn:
            print(f"⚠️ 警告：--part 指定型号「{args.part}」与提取到的 part_number「{data.get('part_number')}」"
                  "不匹配，可能抓错了系列料表中的行，请人工核对！", file=sys.stderr)

    # 按 schema 字段顺序整理，多余 key 保留、缺失 key 补空
    # 兼容新格式 dict(value/unit/status/evidence) 和旧格式 string
    ordered = {}
    for f in schema["fields"]:
        v = data.pop(f["key"], "")
        if v is None or v == "":
            ordered[f["key"]] = {"value": None, "status": "extraction_uncertain"}
        elif isinstance(v, dict):
            d = {"value": v.get("value"), "unit": str(v.get("unit", "")).strip() or None}
            if v.get("status"):
                d["status"] = v["status"]
            if v.get("evidence"):
                d["evidence"] = v["evidence"]
            if v.get("tolerance"):
                d["tolerance"] = str(v["tolerance"]).strip()
            if v.get("qualifier"):
                d["qualifier"] = str(v["qualifier"]).strip()
            if v.get("page"):
                d["page"] = str(v["page"]).strip()
            ordered[f["key"]] = d
        else:
            ordered[f["key"]] = str(v).strip()
    for k, v in data.items():  # LLM 多给的字段不丢，附在后面
        if v:
            if isinstance(v, dict):
                ordered[k] = _wrap_value(
                    str(v.get("value", "")).strip(),
                    str(v.get("unit", "")).strip(),
                    page=str(v.get("page", "")).strip()
                )
            else:
                ordered[k] = str(v).strip()

    # 单位校验：值是不是 dict（已拆 value/unit），或者纯数字字符串但 schema 有 unit → 警告缺单位
    for f in schema["fields"]:
        raw = ordered.get(f["key"], "")
        if not isinstance(raw, dict) and f.get("unit") and raw and re.fullmatch(r"[\d.,\s~±%/-]+", raw):
            print(f"⚠️ 警告：字段 {f['key']}（{f['name']}）的值「{raw}」缺少单位，期望单位 {f['unit']}", file=sys.stderr)

    result = {
        "category": args.category,
        "source_file": Path(args.pdf).name,
        "values": ordered,
    }
    out = args.output or Path(args.pdf).with_suffix(".json").name
    with open(out, "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2)
    print(f"已写入 {out}（{sum(1 for v in ordered.values() if _has_value(v))} 项有值 / 共 {len(ordered)} 项）")
    missing = [k for k, v in ordered.items() if not _has_value(v)]
    if missing:
        print(f"未提取到：{'、'.join(missing)}")


def _has_value(v) -> bool:
    """判断字段是否有有效值（dict 且 value 非 None 为有效，string 非空为有效）"""
    if isinstance(v, dict):
        return v.get("value") is not None and v.get("value") != ""
    return bool(v)


def norm(s) -> str:
    """比较用规范化：dict 取 value+unit+tol，string 直接去空白；null 返回空"""
    if isinstance(s, dict):
        raw = s.get("value")
        if raw is None:
            return ""
        u = s.get("unit", "") or ""
        tol = s.get("tolerance", "") or ""
        q = s.get("qualifier", "") or ""
        return re.sub(r"\s+", "", f"{q}{raw}{u}{tol}").lower()
    return re.sub(r"\s+", "", (s or "")).lower()


def cmd_compare(args):
    import xlsxwriter
    schema = load_schema(args.category)

    files = sorted(set(p for pat in args.jsons for p in glob.glob(pat)))
    if len(files) < 2:
        raise RuntimeError("至少需要 2 个 JSON 才能对比")
    items = []
    for p in files:
        with open(p, encoding="utf-8-sig") as fp:
            d = json.load(fp)
        vals = d.get("values", d)
        col_name = _display_value(vals.get("part_number"), "") or Path(p).stem
        sup = _display_value(vals.get("supplier"), "")
        if sup:
            col_name = f"{sup} {col_name}".strip()
        items.append((col_name, vals))

    field_keys = [f["key"] for f in schema["fields"] if f["key"] not in ("supplier", "part_number")]
    extra_keys = [k for _, vals in items for k in vals if k not in field_keys and k not in ("supplier", "part_number")]
    field_meta = {f["key"]: f for f in schema["fields"]}

    # 控制台先打一张 markdown 速览表
    header = ["参数"] + [name for name, _ in items]
    print("| " + " | ".join(header) + " |")
    print("|" + "---|" * len(header))
    for key in field_keys + sorted(set(extra_keys)):
        name = field_meta.get(key, {}).get("name", key)
        unit_hint = field_meta.get(key, {}).get("unit", "")
        row = [name + (f"({unit_hint})" if unit_hint else "")]
        values = [vals.get(key, "") for _, vals in items]
        has_diff = len({norm(v) for v in values if v}) > 1
        row += [_display_value(v, unit_hint, show_page=has_diff) or "—" for v in values]
        print("| " + " | ".join(row) + " |")

    # 生成 Excel：参数行 × 料号列，差异高亮
    out = args.output or "compare.xlsx"
    wb = xlsxwriter.Workbook(out)
    ws = wb.add_worksheet("选型对比")
    f_head = wb.add_format({"bold": True, "bg_color": "#1F4E79", "font_color": "white", "border": 1, "align": "center", "valign": "vcenter", "text_wrap": True})
    f_name = wb.add_format({"bg_color": "#F2F2F2", "border": 1, "valign": "vcenter"})
    f_cell = wb.add_format({"border": 1, "valign": "vcenter", "text_wrap": True})
    f_diff = wb.add_format({"border": 1, "bg_color": "#FFE699", "bold": True, "valign": "vcenter", "text_wrap": True})
    f_na = wb.add_format({"border": 1, "font_color": "#999999", "align": "center", "valign": "vcenter"})

    ws.write(0, 0, f'{schema["category"]} · 参数', f_head)
    for c, (name, _) in enumerate(items, 1):
        ws.write(0, c, name, f_head)
    ws.set_column(0, 0, 22)
    ws.set_column(1, len(items), 30)
    ws.freeze_panes(1, 1)

    for r, key in enumerate(field_keys + sorted(set(extra_keys)), 1):
        name = field_meta.get(key, {}).get("name", key)
        unit = field_meta.get(key, {}).get("unit", "")
        ws.write(r, 0, name + (f"\n({unit})" if unit else ""), f_name)
        values = [vals.get(key, "") for _, vals in items]
        present = {norm(v) for v in values if v}
        has_diff = len(present) > 1
        for c, v in enumerate(values, 1):
            dv = _display_value(v, field_meta.get(key, {}).get("unit", ""), show_page=has_diff)
            if not v:
                ws.write(r, c, "—", f_na)
            elif has_diff:
                ws.write(r, c, dv, f_diff)
            else:
                ws.write(r, c, dv, f_cell)

    # 每个料号一个「提取明细」sheet：完整字段值 + 状态 + 溯源，与对比表同一份 Excel
    for i, (name, vals) in enumerate(items, 1):
        ws2 = wb.add_worksheet(f"提取明细{i}")
        f2_head = wb.add_format({"bold": True, "bg_color": "#1F4E79", "font_color": "white", "border": 1, "align": "center", "valign": "vcenter"})
        f2_name = wb.add_format({"bg_color": "#F2F2F2", "border": 1, "valign": "vcenter"})
        f2_cell = wb.add_format({"border": 1, "text_wrap": True, "valign": "vcenter"})
        f2_mut = wb.add_format({"border": 1, "font_color": "#999999", "text_wrap": True, "valign": "vcenter"})
        ws2.write(0, 0, "参数", f2_head)
        ws2.write(0, 1, "值", f2_head)
        ws2.write(0, 2, "单位", f2_head)
        ws2.write(0, 3, "容差/限定词", f2_head)
        ws2.write(0, 4, "页码", f2_head)
        ws2.write(0, 5, "状态", f2_head)
        ws2.write(0, 6, "溯源", f2_head)
        ws2.write(0, 7, f"来源：{name}", f2_head)
        ws2.set_column(0, 0, 22)
        ws2.set_column(1, 6, 26)
        ws2.freeze_panes(1, 0)
        detail_keys = [f["key"] for f in schema["fields"]] + sorted(k for k in vals if k not in field_keys and k not in ("supplier", "part_number"))
        r = 1
        for key in detail_keys:
            meta = field_meta.get(key, {})
            v = vals.get(key, "")
            if isinstance(v, dict):
                has = v.get("value") is not None and v.get("value") != ""
                ws2.write(r, 0, meta.get("name", key), f2_name)
                ws2.write(r, 1, v.get("value") if has else "—", f2_cell if has else f2_mut)
                ws2.write(r, 2, v.get("unit") or "—", f2_cell)
                tol_q = " ".join(x for x in (v.get("tolerance"), v.get("qualifier")) if x)
                ws2.write(r, 3, tol_q or "—", f2_cell)
                ws2.write(r, 4, v.get("page") or "—", f2_cell if has else f2_mut)
                ws2.write(r, 5, v.get("status") or ("有值" if has else "—"), f2_cell)
                ev = v.get("evidence")
                ev_txt = ""
                if isinstance(ev, dict):
                    if ev.get("searched_sections"):
                        ev_txt = "检索：" + "；".join(ev["searched_sections"])
                    elif ev.get("uncertainty_reason"):
                        ev_txt = ev["uncertainty_reason"]
                ws2.write(r, 6, ev_txt or "—", f2_cell)
            else:
                ws2.write(r, 0, meta.get("name", key), f2_name)
                ws2.write(r, 1, v or "—", f2_cell if v else f2_mut)
                for c in (2, 3, 4, 5, 6):
                    ws2.write(r, c, "—", f2_mut)
            r += 1
    wb.close()
    print(f"\n已生成 {out}：选型对比（{len(items)} 个料号 × {len(field_keys) + len(set(extra_keys))} 项参数，黄色=存在差异，—=datasheet 未标注）+ {len(items)} 个提取明细 sheet（含溯源）")


def main():
    ap = argparse.ArgumentParser(description="元器件 datasheet → 结构化参数 → 选型对比表")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="从单份 PDF 提取结构化参数")
    e.add_argument("pdf")
    e.add_argument("--category", required=True, choices=CATEGORY_FILES.keys())
    e.add_argument("--part", default="", help="系列料表中的目标型号，如 14D471K")
    e.add_argument("-o", "--output", default="")
    e.add_argument("--dry-run", action="store_true", help="只打印 prompt，不调 API")
    e.set_defaults(func=cmd_extract)

    c = sub.add_parser("compare", help="多份提取结果生成对比表")
    c.add_argument("jsons", nargs="+", help="JSON 文件或通配符，如 mov_*.json")
    c.add_argument("--category", required=True, choices=CATEGORY_FILES.keys())
    c.add_argument("-o", "--output", default="")
    c.set_defaults(func=cmd_compare)

    args = ap.parse_args()
    try:
        args.func(args)
    except RuntimeError as e:
        # 二轮圆桌 #1：错误一律结构化输出（Hermes 进程收到 JSON 错误，而非无输出退出）
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
