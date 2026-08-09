#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
datasheet_v2.py — 通用化提取（V2，2026-08-05 设计定稿）

用法：
  # 1. 通用提取：任意规格书 → 全参数 JSON（带页码溯源 + 中英双语参数名）
  python datasheet_v2.py extract xxx.pdf [--category ntc] [-o out.json] [--part 14D471K]

设计要点（DESIGN_V2_general.md）：
  - 去 schema 硬约束：LLM 自由识别全部参数，由参数本体 ontology/params.yaml 统一认识
  - 保留反幻觉：not_in_datasheet / extraction_uncertain / evidence 溯源
  - 保留页码溯源、四字段分离（value/unit/tolerance/qualifier）
  - key=英文 canonical（机器对齐），zh=中文名（人读展示）
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from datasheet_tool import (  # 复用 v1 的 PDF 解析 / LLM 调用 / 规范化
    MIN_TEXT_CHARS,
    call_llm,
    norm,
    pdf_to_text,
)

SCRIPT_DIR = Path(__file__).resolve().parent
ONTOLOGY_DIR = SCRIPT_DIR.parent / "ontology"
ONTOLOGY_FILE = ONTOLOGY_DIR / "params.yaml"

LLM_FALLBACK_BATCH = 40  # LLM 兜底分批上限（一次最多问 40 个参数，超了拆批，控成本控时长）

# 单位量级族（用于抓"抓错列/抄错单位"类幻觉：hint 是 mH 却提取 nH → 量级冲突）
UNIT_FAMILIES = [
    ["nh", "uh", "µh", "mh", "h", "kh"],
    ["pf", "nf", "uf", "µf", "mf", "f"],
    ["mohm", "mω", "ohm", "ω", "kohm", "kω"],
]


def norm_unit(s: str) -> str:
    return re.sub(r"[^a-z0-9ωµμ°]", "", str(s or "").lower())


def unit_mismatch(extracted_unit: str, hint: str):
    """同族不同量级的单位 → 提示（如 mH vs nH）。跨族不判（避免 V/sec/℃ 变体误报）。"""
    eu, hu = norm_unit(extracted_unit), norm_unit(hint)
    if not eu or not hu or eu == hu:
        return None
    for fam in UNIT_FAMILIES:
        if eu in fam and hu in fam:
            return f"⚠ 单位量级疑点：提取「{extracted_unit}」vs 参考「{hint}」——可能抓错列/抄错单位，请核对原文"
    return None


# ==================== 参数本体 ====================

def load_ontology() -> dict:
    """读 ontology/params.yaml → {key: {zh, category, unit_hint, aliases}}"""
    import yaml
    with open(ONTOLOGY_FILE, encoding="utf-8-sig") as f:
        raw = yaml.safe_load(f) or {}
    ont = {}
    for key, meta in raw.items():
        ont[key] = {
            "zh": meta.get("zh", key),
            "category": meta.get("category", []),
            "unit_hint": meta.get("unit_hint", ""),
            "aliases": meta.get("aliases", []),
        }
    return ont


def ontology_ref_lines(ont: dict, category: str = "") -> list:
    """生成参考清单行：- key 中文名(单位)。category 匹配该品类 + 通用参数；无 category 时全部列出"""
    lines = []
    for key in sorted(ont):
        m = ont[key]
        if category and m["category"] and category not in m["category"]:
            continue
        unit = f"（{m['unit_hint']}）" if m["unit_hint"] else ""
        lines.append(f'- {key}：{m["zh"]}{unit}')
    return lines


# ==================== 提取 prompt（V2 通用版） ====================

def build_prompt_v2(raw_text: str, category: str = "", part: str = "", ont: dict = None) -> str:
    ont = ont or load_ontology()
    ref_lines = ontology_ref_lines(ont, category)
    ref_block = "\n".join(ref_lines) if ref_lines else "（暂无参考清单，自由识别）"

    cat_note = f"品类：{category}。" if category else ""
    part_note = ""
    if part:
        part_note = f'\n这份 datasheet 是系列料表，请只提取型号 "{part}" 那一行/那一组的数据。注意：料表中的型号可能带通配符/占位符后缀（如 □、■、*，表示容差或脚型代码），请按主体型号匹配。'

    return f"""你是电子元器件工艺工程师。下面是一份 datasheet 的完整文本（含正文与表格）。{cat_note}
请识别文中出现的全部电气/工艺/通用参数并提取，严格输出一个 JSON 对象，不要输出任何其他文字。

JSON 结构（必填）：
{{"parameters": [
  {{"key": "参数规范英文名", "zh": "参数中文名", "original": "datasheet 原文中的参数名",
    "value": "数值", "unit": "单位", "tolerance": "容差", "qualifier": "限定词",
    "page": "页码与位置", "status": "extracted",
    "evidence": {{"searched_sections": ["..."], "uncertainty_reason": "..."}}}}
]}}

字段说明：
- key：参数规范英文名（snake_case），**优先用参考清单里的 key**；清单外的参数用简洁英文 snake_case 自命名（如 max_surge_current）
- zh：参数中文名（表格展示用，要准确）
- original：datasheet 原文里该参数的名称（按原文，中英皆可）
- value/unit：数值与单位分离，单位按原文保留不换算（原文写 mW 就存 mW）；原文无单位的比值/等级/认证类直接放 value
- tolerance：容差（如 ±10%）；qualifier：限定词（Approx./Typ./≤/≈/~ 等）
- page：值在 datasheet 中的页码与位置（文本以 === 第N页 === 标记分页），用于工艺核对溯源。**严禁编造**，实在无法确定写"不确定"
- status：extracted（默认）| not_in_datasheet（参考清单里的参数，查证后确认原文没有）| extraction_uncertain（不确定，附 uncertainty_reason）
- evidence：仅 status 非 extracted 时填写。not_in_datasheet → searched_sections 列出查过的章节页码，证明真的找过；extraction_uncertain → uncertainty_reason 说明原因

参考清单（该品类常见参数及其规范 key，供命名对齐参考，**不是硬约束**）：
{ref_block}

规则：
1. 提取文中出现的所有参数，包括参考清单外的（尺寸、认证、包装、存贮温度等通用信息也要提取）
2. **反幻觉铁律**：参考清单里的参数若查证后原文确实没有 → not_in_datasheet + evidence；未出现的参数绝不编造值——宁缺毋假
3. packaging 完整保留原文描述（包装方式/数量/规格），不得截断；若同时有封装尺寸和包装方式，拆成 dimension 和 packaging 两个参数
4. 同一参数有多条件/多行数据时，取与目标型号相符的那一行，测试条件用括号附在 value 后
5. 单位写在表头时（如 "Thermal Time Constant (s)"），单位也要填进 unit
6. 系列料表：区分"系列范围值"与"单型号值"，能对到具体型号就取型号行，否则注明范围
7. **多列表格防错位（重要）**：表格一行出现多个数值时（如 "68 1300 200 3400 750"），必须依据表头行（=== 第N页 表格N === 首行）确定每列对应哪个参数，再归列取值。表头无法对应某列时，把该列值合并进 original 注明，**禁止凭位置猜测归列**——宁缺毋错
8. 只输出一个 JSON 对象，不要 markdown 代码块包裹{part_note}

datasheet 全文如下：
{raw_text}"""


# ==================== 输出解析（兼容新旧结构） ====================

def _clean_param(p: dict) -> dict:
    """清洗单条参数：统一 value/unit/tolerance/qualifier/page/status/evidence 七件套"""
    out = {}
    for k in ("key", "zh", "original"):
        v = str(p.get(k, "")).strip()
        if v:
            out[k] = v
    out["value"] = p.get("value")
    if isinstance(out["value"], (dict, list)):
        out["value"] = json.dumps(out["value"], ensure_ascii=False)
    elif out["value"] is not None:
        out["value"] = str(out["value"]).strip()
    for k in ("unit", "tolerance", "qualifier"):
        v = str(p.get(k, "")).strip()
        if v:
            out[k] = v
    v_page = str(p.get("page", "")).strip()
    if v_page:
        out["page"] = v_page
    st = str(p.get("status", "")).strip() or "extracted"
    out["status"] = st if st in ("extracted", "not_in_datasheet", "extraction_uncertain") else "extracted"
    ev = p.get("evidence")
    if isinstance(ev, dict) and ev:
        out["evidence"] = ev
    return out


def parse_llm_output(data, category: str = "") -> list:
    """LLM 原始输出 → parameters 列表。兼容：
    A. V2 新结构 {"parameters": [...]}
    B. v1 旧结构 {"key": {"value": ...}}（LLM 不听话时兜底）"""
    params = data.get("parameters") if isinstance(data, dict) else None
    if isinstance(params, list):
        return [_clean_param(p) for p in params if isinstance(p, dict)]
    # 兜底：旧结构 → 转 parameters
    out = []
    if isinstance(data, dict):
        for key, v in data.items():
            if key == "parameters":
                continue
            if isinstance(v, dict):
                out.append(_clean_param({**v, "key": key}))
            elif v:
                out.append(_clean_param({"key": key, "value": v}))
    return out


def _has_value(p: dict) -> bool:
    return p.get("value") is not None and str(p.get("value")).strip() != ""


# ==================== 归一化层（M2：词典为主 + LLM 兜底 + 回写建议） ====================

def norm_key(s: str) -> str:
    """规范化匹配串：小写、去空格/下划线/连字符/斜杠/括号"""
    return re.sub(r"[\s_\-\./()（）·]+", "", str(s or "")).lower()


def build_ont_lookup(ont: dict) -> dict:
    """本体 → {规范别名: key} 查找表（key 本身 + 全部别名）"""
    lookup = {}
    for key, m in ont.items():
        for a in [key] + m["aliases"]:
            na = norm_key(a)
            if na and na not in lookup:
                lookup[na] = key
    return lookup


def match_ontology(params: list, ont: dict) -> tuple:
    """词典匹配：每个参数 → (key, 匹配方式)。返回 (对齐后的参数列表, 未命中参数列表)。
    匹配优先级：完全匹配 > 子串匹配（长别名优先，仅中文/长英文别名参与，防误伤）。"""
    lookup = build_ont_lookup(ont)
    # 子串匹配候选：中文别名或英文长度 ≥ 4，按长度降序（长别名优先）
    substr_cands = sorted(
        ((na, k) for na, k in lookup.items() if len(na) >= 4 or re.search(r"[一-鿿]", na)),
        key=lambda x: -len(x[0]),
    )
    aligned, unknown = [], []
    for p in params:
        cands = [p.get(k) for k in ("key", "original", "zh")]
        cand_norms = [norm_key(c) for c in cands if c]
        hit = None
        for c in cand_norms:
            if c in lookup:  # 完全匹配
                hit = lookup[c]
                break
        if hit is None:
            for c in cand_norms:
                for na, k in substr_cands:
                    if na in c:
                        hit = k
                        break
                if hit:
                    break
        if hit:
            m = ont[hit]
            item = {
                **p,
                "key": hit,
                "zh": m["zh"],           # zh 以本体为准（统一展示名）
                "unit_hint": m.get("unit_hint", ""),
            }
            warn = unit_mismatch(p.get("unit", ""), m.get("unit_hint", ""))
            if warn:
                item["unit_warning"] = warn
                print(f"  {warn}（{hit}：{p.get('value')} {p.get('unit')}）", file=sys.stderr)
            aligned.append(item)
        else:
            unknown.append(p)
    return aligned, unknown


def key_similarity(k1: str, k2: str) -> float:
    """轻量相似度：snake_case 拆词后的 token 共享比例（0~1）。
    用于检测 LLM 自造新 key 与已有 key 的重复（如 max_clamping_voltage vs clamping_voltage），
    防 ontology 越跑越碎。零成本，不上 embedding。"""
    def toks(k: str):
        out = []
        for p in re.split(r"[_\-]", k):
            out.extend(re.findall(r"[a-z]+|\d+", p.lower()))
        return set(out)
    t1, t2 = toks(k1), toks(k2)
    if not t1 or not t2:
        return 0.0
    return len(t1 & t2) / max(len(t1), len(t2))


def llm_fallback(unknown: list, ont: dict) -> dict:
    """LLM 兜底：未命中参数批量归类 → {"mapped": [{original, key, confidence}], "new_params": [...]}"""
    known_lines = [f"- {k}：{m['zh']}" for k, m in sorted(ont.items())]
    unknown_lines = [
        f"- {i}. key={p.get('key')} zh={p.get('zh')} original={p.get('original')} value={p.get('value')}"
        for i, p in enumerate(unknown)
    ]
    prompt = f"""你是元器件参数归一化专家。以下是 datasheet 提取中未被识别的新参数，请把它们映射到最合适的规范参数 key，或判定为真正的新参数。

规范参数清单（key：中文名）：
{chr(10).join(known_lines)}

待归类参数：
{chr(10).join(unknown_lines)}

规则：
- 语义相同/近义 → 映射到规范 key（confidence: high）；仅部分相关 → low
- 确为清单没有的新参数 → 放 new_params，key 用简洁英文 snake_case，zh 给中文名，aliases 给中英别名（含原文名）
- 拿不准的 → 放 mapped 且 confidence: low

严格输出 JSON：{{"mapped": [{{"original": "...", "key": "...", "confidence": "high|low"}}], "new_params": [{{"key": "...", "zh": "...", "aliases": ["..."]}}]}}，不要其他文字。"""
    data = call_llm(prompt)
    return data if isinstance(data, dict) else {"mapped": [], "new_params": []}


def write_suggestions(new_params: list, out_path: Path, source: str = "") -> None:
    """回写建议（并发安全：只写建议队列，不碰主词典）：suggestions.yaml 结构化 + 按 key 去重。
    人工审核后移入 params.yaml。"""
    import yaml
    if not new_params:
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if out_path.exists():
        try:
            with open(out_path, encoding="utf-8-sig") as f:
                existing = yaml.safe_load(f) or {}
        except Exception:
            existing = {}
    items = list(existing.get("suggestions", []) or [])
    seen = {it.get("key") for it in items if it.get("key")}
    added = 0
    for p in new_params:
        k = (p.get("key") or "").strip()
        if not k or k in seen:
            continue
        items.append({
            "key": k,
            "zh": p.get("zh", ""),
            "source": source,
            "aliases": p.get("aliases", []) or [],
            "merge_hint": p.get("merge_hint", ""),
        })
        seen.add(k)
        added += 1
    if not added:
        return
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# 新参数入典建议（人工审核后移入 params.yaml；重复建议已自动去重）\n")
        yaml.dump({"suggestions": items}, f, allow_unicode=True, sort_keys=False)
    print(f"新参数建议已写入 {out_path.name}（{added} 条新增，累计 {len(items)} 条，待人工审核入典）")


def cmd_normalize(args):
    ont = load_ontology()
    with open(args.json, encoding="utf-8-sig") as f:
        data = json.load(f)
    params = data.get("parameters")
    if not isinstance(params, list):
        sys.exit(f"错误：{args.json} 不是 V2 格式（缺少 parameters 数组）")

    aligned, unknown = match_ontology(params, ont)
    mapped_meta = {}

    if unknown and args.llm_fallback:
        print(f"LLM 兜底归类 {len(unknown)} 个未命中参数（分批 ≤ {LLM_FALLBACK_BATCH}/次）…", file=sys.stderr)
        res = {"mapped": [], "new_params": []}
        for i in range(0, len(unknown), LLM_FALLBACK_BATCH):
            batch = unknown[i:i + LLM_FALLBACK_BATCH]
            try:
                partial = llm_fallback(batch, ont)
                for k in ("mapped", "new_params"):
                    res.setdefault(k, []).extend(partial.get(k, []) or [])
            except SystemExit:
                # 兜底失败降级：本次未命中参数保持原样，不阻塞
                print("⚠️ LLM 兜底调用失败，降级为纯词典结果（未命中参数保持原样，可在对比表中看到 ⚠ 标注）", file=sys.stderr)
                break
        mapped = res.get("mapped", []) or []
        new_params = res.get("new_params", []) or []
        for m in mapped:
            if m.get("key") in ont:
                mapped_meta[m.get("original", "")] = m["key"]
        # 应用映射
        aligned2 = []
        for p in aligned:
            if p.get("original") in mapped_meta:
                k = mapped_meta[p["original"]]
                aligned2.append({**p, "key": k, "zh": ont[k]["zh"], "unit_hint": ont[k].get("unit_hint", "")})
            else:
                aligned2.append(p)
        aligned = aligned2
        still_unknown = [p for p in unknown if p.get("original") not in mapped_meta and p.get("key") not in ont]
        if new_params:
            # 相似度检测：新 key 与已有 key 高相似 → merge_hint 提示人工确认（防 ontology 碎片化）
            ont_keys = list(ont)
            for np_ in new_params:
                np_key = norm_key(np_.get("key", ""))
                sims = sorted(
                    ((k, key_similarity(np_key, norm_key(k))) for k in ont_keys if key_similarity(np_key, norm_key(k)) >= 0.5),
                    key=lambda x: -x[1],
                )
                if sims:
                    np_["merge_hint"] = f"⚠ 疑似与 {sims[0][0]} 重复（相似度 {sims[0][1]:.0%}），请人工确认是否合并"
            write_suggestions(new_params, ONTOLOGY_DIR / "suggestions.yaml", data.get("source_file", ""))
        unknown = still_unknown

    result = {
        "category": data.get("category", ""),
        "source_file": data.get("source_file", ""),
        "parameters": aligned + [p for p in unknown],
    }
    out = args.output or Path(args.json).with_suffix(".norm.json").name
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    n_hit = sum(1 for p in aligned if p.get("key"))
    print(f"已写入 {out}：{len(aligned)} 个已对齐（词典/LLM）+ {len(unknown)} 个未命中待人工处理")
    if unknown:
        for p in unknown:
            print(f"  未命中：key={p.get('key')} zh={p.get('zh')} original={p.get('original')}")


# ==================== 命令 ====================

# ==================== 对比层（M3：key 对齐 + 中文标注 Excel） ====================

def cmd_compare(args):
    import glob as _glob
    import xlsxwriter
    ont = load_ontology()

    files = sorted(set(p for pat in args.jsons for p in _glob.glob(pat)))
    if len(files) < 2:
        sys.exit("至少需要 2 个 JSON 才能对比")
    items = []  # (列名, {key: 参数})
    for p in files:
        with open(p, encoding="utf-8-sig") as f:
            d = json.load(f)
        params = d.get("parameters")
        if not isinstance(params, list):
            sys.exit(f"错误：{p} 不是 V2 格式（缺少 parameters 数组），请先 extract + normalize")
        pmap = {}
        for q in params:
            k = q.get("key")
            if k:
                pmap.setdefault(k, q)
        pn = str(pmap.get("part_number", {}).get("value", "") or "").strip()
        sup = str(pmap.get("supplier", {}).get("value", "") or "").strip()
        col = f"{sup} {pn}".strip() or Path(p).stem
        items.append((col, pmap))

    # key 排序：本体顺序优先，其余按字母
    ont_order = {k: i for i, k in enumerate(ont)}
    all_keys = sorted({k for _, pm in items for k in pm}, key=lambda k: (ont_order.get(k, 10**9), k))

    def disp(p: dict, show_page: bool = False) -> str:
        if not p or not _has_value(p):
            return ""
        parts = []
        if p.get("qualifier"):
            parts.append(str(p["qualifier"]))
        parts.append(str(p["value"]))
        if p.get("unit"):
            parts.append(str(p["unit"]))
        if p.get("tolerance"):
            parts.append(str(p["tolerance"]))
        if show_page and p.get("page"):
            parts.append(f"({p['page']})")
        return " ".join(parts)

    # 控制台速览（markdown 表）
    header = ["参数"] + [c for c, _ in items]
    print("| " + " | ".join(header) + " |")
    print("|" + "---|" * len(header))
    for key in all_keys:
        in_ont = key in ont
        zh = ont.get(key, {}).get("zh") or next(
            (pm[key].get("zh") for _, pm in items if key in pm and pm[key].get("zh")), key)
        row = [f"{zh} ({key})" + ("" if in_ont else " ⚠未归一化")]
        vals = [pm.get(key) for _, pm in items]
        shown = [disp(v) for v in vals]
        has_diff = len({norm(s) for s in shown if s}) > 1
        row += [disp(v, show_page=has_diff) or "—" for v in vals]
        print("| " + " | ".join(row) + " |")

    # Excel：参数行 × 料号列
    out = args.output or "compare_v2.xlsx"
    wb = xlsxwriter.Workbook(out)
    ws = wb.add_worksheet("选型对比")
    f_head = wb.add_format({"bold": True, "bg_color": "#1F4E79", "font_color": "white", "border": 1, "align": "center", "valign": "vcenter", "text_wrap": True})
    f_name = wb.add_format({"bg_color": "#F2F2F2", "border": 1, "valign": "vcenter"})
    f_cell = wb.add_format({"border": 1, "valign": "vcenter", "text_wrap": True})
    f_diff = wb.add_format({"border": 1, "bg_color": "#FFE699", "bold": True, "valign": "vcenter", "text_wrap": True})
    f_na = wb.add_format({"border": 1, "font_color": "#999999", "align": "center", "valign": "vcenter"})

    ws.write(0, 0, "参数（中文名 | 规范 key）", f_head)
    for c, (name, _) in enumerate(items, 1):
        ws.write(0, c, name, f_head)
    ws.set_column(0, 0, 34)
    ws.set_column(1, len(items), 30)
    ws.freeze_panes(1, 1)

    for r, key in enumerate(all_keys, 1):
        in_ont = key in ont
        zh = ont.get(key, {}).get("zh") or next(
            (pm[key].get("zh") for _, pm in items if key in pm and pm[key].get("zh")), key)
        unit_hint = ont.get(key, {}).get("unit_hint", "")
        label = f"{zh}\n({key})" + (f"\n单位参考 {unit_hint}" if unit_hint else "")
        if not in_ont:
            label += "\n⚠ 未归一化"
        ws.write(r, 0, label, f_name)
        vals = [pm.get(key) for _, pm in items]
        shown = [disp(v) for v in vals]
        has_diff = len({norm(s) for s in shown if s}) > 1
        for c, v in enumerate(vals, 1):
            dv = disp(v, show_page=has_diff)
            if not dv:
                st = v.get("status", "") if isinstance(v, dict) else ""
                ws.write(r, c, f"— ({st})" if st else "—", f_na)
            elif has_diff:
                ws.write(r, c, dv, f_diff)
            else:
                ws.write(r, c, dv, f_cell)

    # 每个料号一个「提取明细」sheet
    for i, (name, pm) in enumerate(items, 1):
        ws2 = wb.add_worksheet(f"提取明细{i}")
        f2_head = wb.add_format({"bold": True, "bg_color": "#1F4E79", "font_color": "white", "border": 1, "align": "center", "valign": "vcenter"})
        f2_name = wb.add_format({"bg_color": "#F2F2F2", "border": 1, "valign": "vcenter"})
        f2_cell = wb.add_format({"border": 1, "text_wrap": True, "valign": "vcenter"})
        f2_mut = wb.add_format({"border": 1, "font_color": "#999999", "text_wrap": True, "valign": "vcenter"})
        heads = ["规范 key", "中文名", "值", "单位", "容差/限定词", "页码", "状态", "溯源"]
        for c, h in enumerate(heads):
            ws2.write(0, c, h, f2_head)
        ws2.set_column(0, 0, 26)
        ws2.set_column(1, 7, 26)
        ws2.freeze_panes(1, 0)
        r = 1
        for key in sorted(pm):
            p = pm[key]
            zh = ont.get(key, {}).get("zh") or p.get("zh", "")
            has = _has_value(p)
            ws2.write(r, 0, key, f2_name)
            ws2.write(r, 1, zh or "—", f2_cell if has else f2_mut)
            ws2.write(r, 2, p.get("value") if has else "—", f2_cell if has else f2_mut)
            ws2.write(r, 3, p.get("unit") or "—", f2_cell)
            tol_q = " ".join(x for x in (p.get("tolerance"), p.get("qualifier")) if x)
            ws2.write(r, 4, tol_q or "—", f2_cell)
            ws2.write(r, 5, p.get("page") or "—", f2_cell if has else f2_mut)
            ws2.write(r, 6, p.get("status") or ("有值" if has else "—"), f2_cell)
            ev = p.get("evidence")
            ev_txt = ""
            if isinstance(ev, dict):
                if ev.get("searched_sections"):
                    ev_txt = "检索：" + "；".join(ev["searched_sections"])
                elif ev.get("uncertainty_reason"):
                    ev_txt = ev["uncertainty_reason"]
            ws2.write(r, 7, ev_txt or "—", f2_cell)
            r += 1
    wb.close()
    print(f"\n已生成 {out}：选型对比（{len(items)} 个料号 × {len(all_keys)} 项参数，黄色=存在差异，—=未标注）+ {len(items)} 个提取明细 sheet（含页码溯源）")


def cmd_extract(args):
    ont = load_ontology()
    raw, total_pages, truncated, pages_head, pages_tail = pdf_to_text(args.pdf)

    # P0 守卫：无文本层扫描件（复用 v1 守卫）
    if len(raw.strip()) < MIN_TEXT_CHARS:
        sys.exit(
            f"错误：从 PDF 只提取到 {len(raw.strip())} 字符（共 {total_pages} 页），疑似扫描件/图片型 PDF，无文本层。\n"
            "请先用 OCR 工具（如 WPS、Adobe、PaddleOCR）转成文字层 PDF 再试。"
        )
    if truncated:
        print(f"⚠️ 警告：PDF 共 {total_pages} 页，提取文本超过上限，已收录前 {pages_head} 页 + 末尾 {pages_tail} 页（head+tail 策略）——"
              "系列料表可能只覆盖了部分型号，请核对提取结果是否完整！", file=sys.stderr)

    prompt = build_prompt_v2(raw, args.category or "", args.part or "", ont)

    if args.dry_run:
        print(f"--- PDF 解析得到 {len(raw)} 字符，prompt 如下（未调 API）---\n")
        print(prompt)
        return

    data = call_llm(prompt)
    params = parse_llm_output(data, args.category or "")

    # --part 校验：part_number 参数应匹配（模糊比对）
    if args.part:
        def _pn(s):
            return re.sub(r"[\s□■\*]+", "", str(s or "")).upper()
        returned_pn = next((_pn(p.get("value")) for p in params if p.get("key") == "part_number"), "")
        want_pn = _pn(args.part)
        if returned_pn and want_pn not in returned_pn and returned_pn not in want_pn:
            print(f"⚠️ 警告：--part 指定型号「{args.part}」与提取到的 part_number「{returned_pn}」"
                  "不匹配，可能抓错了系列料表中的行，请人工核对！", file=sys.stderr)

    result = {
        "category": args.category or "",
        "source_file": Path(args.pdf).name,
        "parameters": params,
    }
    out = args.output or Path(args.pdf).with_suffix(".json").name
    with open(out, "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2)

    n_val = sum(1 for p in params if _has_value(p))
    n_absent = sum(1 for p in params if p.get("status") == "not_in_datasheet")
    n_unc = sum(1 for p in params if p.get("status") == "extraction_uncertain")
    print(f"已写入 {out}：识别 {len(params)} 个参数（{n_val} 有值 / {n_absent} 原文确无 / {n_unc} 不确定）")
    if args.category:
        keys = {p.get("key") for p in params}
        missed = [k for k in ont if ont[k]["category"] and args.category in ont[k]["category"] and k not in keys]
        if missed:
            print(f"参考清单中未识别到的品类参数：{'、'.join(sorted(missed))}")


def main():
    ap = argparse.ArgumentParser(description="datasheet-compare V2：任意规格书 → 全参数 JSON（通用提取）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="通用提取：任意规格书 → 结构化 JSON")
    e.add_argument("pdf")
    e.add_argument("--category", default="", help="品类提示（mov/ntc/x2/cmc/cbb，可省——省则全参数清单参考）")
    e.add_argument("--part", default="", help="系列料表中的目标型号，如 14D471K")
    e.add_argument("-o", "--output", default="")
    e.add_argument("--dry-run", action="store_true", help="只打印 prompt，不调 API")
    e.set_defaults(func=cmd_extract)

    n = sub.add_parser("normalize", help="归一化：V2 提取结果 → 参数 key 对齐本体（词典 + 可选 LLM 兜底）")
    n.add_argument("json", help="V2 提取结果 JSON")
    n.add_argument("--llm-fallback", action="store_true", help="词典未命中时用 LLM 兜底归类（烧 API）")
    n.add_argument("-o", "--output", default="")
    n.set_defaults(func=cmd_normalize)

    c = sub.add_parser("compare", help="V2 对比：多份归一化结果 → Excel（key 对齐 + 中文标注 + 差异标黄 + 页码）")
    c.add_argument("jsons", nargs="+", help="V2 归一化 JSON 文件或通配符")
    c.add_argument("-o", "--output", default="")
    c.set_defaults(func=cmd_compare)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
