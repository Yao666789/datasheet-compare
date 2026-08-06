#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
report_v2.py — V2 成绩单（M5 双指标回归）

用法：
  python report_v2.py <norm.json> [<norm.json> ...] [--category ntc]

指标定义（2026-08-05 评审定稿）：
  1. 已知参数覆盖率：该品类参考参数（ontology 中 category 匹配 + 通用参数）中，
     被提取（extracted 有值）或合理拒绝（not_in_datasheet + evidence）的比例。
     extraction_uncertain 或未出现 = 未覆盖。
  2. 零幻觉率（机器侧）：extracted 参数中有页码溯源的比例 + 未归一化参数占比。
     人工抽查见输出文件核对结论。
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from datasheet_v2 import _has_value, load_ontology


def main():
    ap = argparse.ArgumentParser(description="V2 成绩单：已知参数覆盖率 + 零幻觉率")
    ap.add_argument("jsons", nargs="+", help="V2 归一化 JSON")
    ap.add_argument("--category", default="", help="品类口径（mov/ntc/x2/cmc/cbb）")
    args = ap.parse_args()

    ont = load_ontology()
    # 品类参考参数集：category 匹配 + 通用参数（category 空/全品类）
    ref_keys = []
    if args.category:
        ref_keys = [k for k, m in ont.items() if not m["category"] or args.category in m["category"]]
    else:
        ref_keys = sorted(ont)
    ref_keys = sorted(set(ref_keys))

    total_cover = total_ref = total_extracted = total_has_page = total_params = 0
    rows = []
    for pat in args.jsons:
        for p in Path(".").glob(pat) if ("*" in pat or "?" in pat) else [Path(pat)]:
            if not p.exists():
                print(f"跳过（不存在）：{p}", file=sys.stderr)
                continue
            with open(p, encoding="utf-8-sig") as f:
                d = json.load(f)
            params = d.get("parameters", [])
            pmap = {}
            for q in params:
                k = q.get("key")
                if k:
                    pmap.setdefault(k, q)
            cat = d.get("category", "") or args.category
            refs = [k for k in ref_keys if not ont[k]["category"] or cat in ont[k]["category"]] if cat else ref_keys

            covered = 0
            for k in refs:
                v = pmap.get(k)
                if v and (_has_value(v) or v.get("status") == "not_in_datasheet"):
                    covered += 1
            n_ref = len(refs)
            extracted = [v for v in pmap.values() if _has_value(v)]
            n_page = sum(1 for v in extracted if v.get("page") and v.get("page") != "不确定")
            unnorm = [k for k in pmap if k not in ont]

            rows.append((p.name, cat, covered, n_ref, len(extracted), n_page, len(pmap), unnorm))
            total_cover += covered
            total_ref += n_ref
            total_extracted += len(extracted)
            total_has_page += n_page
            total_params += len(pmap)

    print("| 文件 | 品类 | 覆盖率(已知参数) | 提取参数 | 有页码 | 未归一化 |")
    print("|---|---|---|---|---|---|---|")
    for name, cat, covered, n_ref, n_ext, n_page, n_all, unnorm in rows:
        print(f"| {name} | {cat} | {covered}/{n_ref} ({covered / n_ref:.1%}) | {n_ext} | {n_page} | "
              f"{len(unnorm)} ({'、'.join(unnorm[:5]) or '—'}) |")
    print()
    if total_ref:
        print(f"已知参数覆盖率：{total_cover}/{total_ref} = {total_cover / total_ref:.1%}")
    if total_extracted:
        print(f"页码溯源率（机器侧零幻觉代理）：{total_has_page}/{total_extracted} = {total_has_page / total_extracted:.1%}")
        print("注：零幻觉最终判定需人工抽查——回原文核对每个 extracted 值（本次成绩单附抽查结论）")


if __name__ == "__main__":
    main()
