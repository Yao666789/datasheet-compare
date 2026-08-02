# datasheet-compare v1 验收记录

> 2026-08-02 · 面向 8/13-8/20 汇报窗口的收尾验收
> 承诺方：姚振宇（工艺工程师）→ 赵总

## 三笔欠账的处置结果

### ① b_constant 空值 → 结论翻案，schema 已修正 ✅

三份功率型/浪涌抑制型 NTC 规格书原文实测：

| 规格书 | B 值原文 | 结论 |
|---|---|---|
| 南京时恒 MF72-10D7 规格承认书（2023-11-13） | **有**：电气性能表 2.2 `B值 B25/50 K 2800±10%`；技术要求 `B25/50数值：2800K±10%` | 厂商标 B 值 |
| Cantherm MF72 Power NTC（英文规格书） | 无：Main Techno-Parameter 表无 B 列，仅特性栏 "High material constant (B value)" 形容词 | 厂商不标 |
| 兴勤 SCK 系列（W1 已验收） | 无 | 厂商不标 |

**原 schema note "浪涌抑制型 NTC 厂商常不标 B 值" 是错的**，已改为"B 值厂商而异（时恒标 B25/50，兴勤/Cantherm 不标）——原文有则必须提取，无则 not_in_datasheet 溯源"。

### ② 数值丢单位 → 已完成（W1 关闭时验收） ✅

### ③ packaging 截断 → 已完成（W1 关闭时验收）+ 本日再验证 ✅

- NTC 22 页（SCK）head+tail 策略下包装信息完整
- X2 R52 重跑后 packaging 完整：`Bulk (Bag/Tray), Pizza Pack, Ammo Pack, Tape & Reel (ø355 mm / ø500 mm), per IEC 60286-2`
- 时恒 10D7 包装：`散装 + 自封口袋 11×12mm + 包装盒 335×240×50mm`

## 实测成绩单（5 份，4 份首次跑）

| Datasheet | 品类 | 厂商 | 有值 | 合理空 | 文档级判定 |
|---|---|---|---|---|---|
| MF72-10D7（新下载） | NTC | 南京时恒 | 10/10 | — | ✅ |
| MF72 Power NTC（新下载） | NTC | Cantherm | 7/10 | B值/功率/包装（原文确无） | ✅ |
| F863H（首次跑） | X2 | KEMET | 13/13 | — | ✅ |
| R52（重跑） | X2 | KEMET | 13/13 | — | ✅ |
| B82794C0（重跑） | CMC | TDK | 7/9 | 阻抗（原文无列）/DCR（按料号标） | ✅ |

**字段命中率 50/55 = 90.9%；文档级正确率 5/5 = 100%**（零编造，全部空值带溯源）。

### 额外验证到的能力

1. **扫描件识别**：时恒 MF72-20D15（图片型 PDF，仅 31 字符文本层）被工具拒绝并提示"疑似扫描件，请先 OCR"——不瞎编 ✅
2. **系列表识别**：`--part MF72`（系列名）时正确返回 extraction_uncertain 而非猜值 ✅
3. **排障记录**：CMC 提取遇 DeepSeek API Connection error（Clash 代理拦截）→ 清代理 + NO_PROXY 后恢复 ✅

## 遗留

- 时恒 MF72-20D15 扫描件如需入库，需 OCR（WPS/PaddleOCR）后重跑
- CBB 模板未建（W2 窗口 8/3-8/9）
