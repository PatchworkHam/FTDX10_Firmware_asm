# original/ — 原始素材（分析输入）

本目录是**未改动的原始输入**：固件更新包与厂商手册。
除本目录外，工作区其余内容都是基于这些输入的产出（解码件、报告、脚本、证据）。

---

## 1. `firmware/` — 固件更新包（5 个 `.SFL`，固定密钥混淆）

Yaesu SD 卡升级包原文件，未做任何修改。解码方法与产物见 `../decoded/ANALYSIS.md`。

| 文件 | TYPE | 大小 (B) | MD5 | 解码产物（`../decoded/firmware/`） |
|---|---|---|---|---|
| `FTDX10_AFCPU_V0100.SFL` | AD | 248734 | `23955900f828934af1eeea6ffd66ed2e` | `FTDX10_AFCPU_V0100.{srec,ihex,bin}` |
| `FTDX10_MAIN_V0115.SFL` | MC | 738986 | `46643631f7514b42548b030404da99ac` | `FTDX10_MAIN_V0115.{srec,ihex,bin}` |
| `FTDX10_DISPLAY_V0106.SFL` | PC | 3374108 | `2fcff420337a902fc6a5984858cfdcbc` | `FTDX10_DISPLAY_V0106.bin` |
| `FTDX10_IFDSP_V0120.SFL` | ID | 139036 | `80129346cfa2382ec9b0d5793699ce64` | `FTDX10_IFDSP_V0120.bin` |
| `FTDX10_SDR_V0100.SFL` | SR | 4392794 | `e556854d4cc9083e01d1c315ca1709f8` | `FTDX10_SDR_V0100.{rawhex,bin}` |

> 上表 MD5 即 `../decoded/ANALYSIS.md` §9「往返一致」中的原厂指纹——重新打包应与之一致。
> `../power/功率解锁方案.md` 与 `../hardware/固件升级.md` 的回滚说明要求单独留存 `FTDX10_MAIN_V0115.SFL`；本目录即是其权威存放点。

## 2. `manuals/` — 手册 PDF

| 文件 | 说明 | MD5 |
|---|---|---|
| `Firmware_Ver_Up_Manual_ENG_FTDX10_2104-B.pdf` | Yaesu 官方**固件升级手册**（2021-04）。升级步骤与排错；`../hardware/固件升级.md` 的依据之一 | `0b0f0058b582fc5934201055d7413e7d` |
| `FTDX10_Technical_Supplement_manualslib-2215359.pdf` | **FTDX10 Technical Supplement**（ManualsLib 2215359，124 页）。含整页原理图 | `74b4a12e39e4b6e48675b790a4c350ad` |

### 关于原 `74b4a1.pdf`

第二份 PDF 原文件名 `74b4a1.pdf`（内容无关的随机名），实为上述 **FTDX10 Technical Supplement**：

* 第 1 页标题：`YAESU FTDX10 TECHNICAL SUPPLEMENT`；
* 第 32 页标题：`MAIN Unit Circuit Diagram (MAIN Unit/CNTL)` ——即 `../hardware/短接点功能说明.md` 引用的 `p32` 原理图。

因此它同时是 `../hardware/files/evidence/schematic/` 里 **`p32.svg`（整页矢量原件）** 与全部
PNG 裁剪的**唯一矢量出处**，必须与本工作区一同保留。

---

## 3. 相关

* `../decoded/ANALYSIS.md` — `.SFL` 容器格式、混淆算法、CRC32 字段、往返验证。
* `../hardware/固件升级.md` — 升级流程与 `.SFL` 用法。
* `../hardware/files/README.md` §4.3 — 原理图裁剪与出处。
