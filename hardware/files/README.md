# hardware/files/ — FTDX10 逆向「反编译代码 + 复现脚本 + 证据」归档

本目录把本次分析（`hardware/` 各报告的支撑材料）从早期临时分析现场留档到项目内，
目的是不依赖任何外部临时目录即可**独立复算**全部结论。现位于 `hardware/files/`（随 `hardware` 报告归档在一起）。

* 归档日期：2026-10-17（原 `files/`）；整理归档：2026-10-17（并入 `hardware/` 并精简）
* 当前大小：约 106 MB
* 原则：复现脚本保留原始逻辑，路径全部改为仓库内相对路径。
  `h8.py` 默认读取 `hardware/files/disasm/main_disasm.txt`，可用 `FTDX10_DISASM` 覆盖；
  不再需要还原任何临时工作区。

> 原始输入（SFL 原件与手册 PDF；SFL 为固定密钥混淆）在仓库根的 `../../original/`；
> 结论、方案与操作流程以 `hardware/` 下（本目录的上一级）的报告为准；本目录只是证据与工具。

---

## 1. 目录结构

```
hardware/files/
├── README.md                    本文件
├── disasm/          47M         反编译代码（反汇编清单）
├── scripts/         8K          通用复现脚本（h8.py）；功率专用复核脚本在 ../../power/verify/
├── evidence/        25M         证据
│   ├── schematic/   24M         原理图矢量原件 p32.svg + 23 张关键裁剪 PNG
│   └── text/       864K         网络表、字符串扫描、配置点清单、CRC 样本、排查日志
├── reference/       21M         器件/整机手册（文本 + PDF）
└── tools/           15M         独立反汇编器 objdump/objcopy、混淆穷举助手
```

---

## 2. `disasm/` — 反编译代码

| 文件 | 大小 | 内容 | 用途 |
|---|---|---|---|
| `main_disasm.txt` | 3.3 MB | MAIN 固件 S-record 的反汇编（`objdump -D -b srec -m h8300sx`，节名 `.sec1`；与 `-m h8300` 仅寄存器写法不同，反汇编语义一致） | **主力反汇编**；`h8.py` 的输入；报告中所有地址引用都由它解析 |
| `main.asm` | 3.3 MB | MAIN `.bin` 的反汇编（`-b binary`，节名 `.data`） | 交叉核对（另一种渲染，列宽不同） |
| `display_disasm.txt` | 40 MB | DISPLAY 固件（ARM，`-b binary`）的反汇编 | **升级逻辑 / boot loader / SFL 混淆/解码**分析的依据 |

镜像对应关系：`main_disasm.txt` ↔ `decoded/firmware/FTDX10_MAIN_V0115.srec`；`display_disasm.txt` ↔ `decoded/firmware/FTDX10_DISPLAY_V0106.bin`。

---

## 3. `scripts/` — 保留的通用复现脚本

整理时删除了 60 个一次性排查脚本（`callers*`、`xref*`、`crc_*`、`gcd_*`、`z3*`、`brute*`、`tea*`、
`analyze_sfl.py`、`bdtic*.js` 等）。它们只用于推导中间结论，结论本身已固化进
`hardware/可复现分析过程.md`、`decoded/ANALYSIS.md` 与 `decoded/yaesu_sfl_tool.py`（当前权威）。

> **功率专用脚本已移至 [`../../power/`](../../power/README.md)：**
> `srec_patch.py` 随成品放在 `../../power/patched/`；
> `verify_kr.sh`、`v_kr2.py` 放在 `../../power/verify/`。

保留的通用文件：

| 文件 | 作用 |
|---|---|
| `h8.py` | H8SX 指令边界解析器。读出被折行拆开的多字节指令；子命令 `show a b` / `find 正则` / `ctx 地址 前 后`。默认读取 `hardware/files/disasm/main_disasm.txt`，可用 `FTDX10_DISASM=<路径>` 覆盖 |

---

## 4. `evidence/` — 证据

### 4.1 `schematic/` — 硬件证据

* **`p32.svg`**（15.1 MB）：MAIN 单元电路图**整页矢量原件**，最高保真、可无限放大，是短接点 / 二极管矩阵 / CPU 引脚结论的最终依据。
  由 `original/manuals/FTDX10_Technical_Supplement_manualslib-2215359.pdf` 第 32 页导出（该页标题 `MAIN Unit Circuit Diagram (MAIN Unit/CNTL)`）。
* **23 张 PNG**（约 9 MB，人工挑选的关键裁剪）：

  | 组 | 文件 | 内容 |
  |---|---|---|
  | 二极管矩阵与 KR 走线 | `kmatrix.png` `kredge.png` `krpin.png` `krpin2.png` `kr_bottom.png` `kblock.png` `kernet.png` | KR0–KR7、KSC 列线、47K 下拉、二极管朝向 |
  | CPU 引脚 | `chipbody.png` `cpu1.png` `cpu2.png` `cpuright.png` `cpu_bottom2.png` `cpupins.png` `pinnum.png` | H8SX 封装与引脚编号 |
  | 整页/局部视图 | `overview.png` `cntl-032.png` `cntl_small.png` `krz-032.png` | 全页、CNTL 区、KR 条带 |
  | 接地/边缘/排线 | `zoom_gnd.png` `be2.png` `bottomedge.png` `disp_conn.png` | 接地、板边、显示排线 |
  | 升级章节 | `upd_p2-2.png` | 说明书升级步骤页 |

  > 整理时删除了系统性逐步裁剪中间件目录 `p32/`（21 MB）；`bottomedge_big.png` 与 `bottomedge.png` 逐字节相同，已去重。

### 4.2 `text/` — 网络表与扫描结果

| 文件 | 大小 | 内容 |
|---|---|---|
| `net_us.txt` | 302 K | **升级工具（Windows .NET 程序）的托管字符串转储**（`#Strings`/`#US` 流），与电路图无关 |
| `sm.txt` | 338 K | **FTDX10 技术补充手册（ManualsLib）文本**：含整机元件表（CPU `R5F61645N50FPV`＝H8SX/1645 即由此确认）与手册正文；原理图页为位图、无文本层 |
| `cfg_sites.txt` | 7 K | **`0xFF2007` 全部 46 条引用的上下文**（读/写/位测试分类的原始清单） |
| `io_all.txt` `regmap.json` | 49 K / 19 K | 引脚清单、寄存器映射摘录 |
| `strscan.txt` | 16 K | 固件字符串扫描结果 |
| `upd.txt` | 6 K | 升级相关站点/资料摘录 |
| `samples_all.json` `samples_all.log` `tk_samples.json` `tk_*.log` | — | SFL 容器判定的 CRC 样本与回归日志 |
| `gcd_v*.log` `brute_affxor.log` `polyfit.txt` `crctable.txt` | — | 混淆算法排查过程记录（含负结果） |
| `i2.txt` `i2err.txt` `out.txt` `parsed.txt` `pj_scan.txt` `jcfkq_scan.txt` `hs_src.txt` `p32bbox.xml` | — | 中间结果，**按原样保留**（`i2.txt` = 各固件字节分布统计、`out.txt` = 93 字节差异 dump、`i2err.txt` = 脚本报错记录；其 traceback 保留历史临时路径，仅作证据，不是复现依赖） |

### 4.3 资料来源（溯源）

* 原 `evidence/web/`（20 个 HTML 网页存档 + `jc_zips/` 共 11 个 JC 配件说明书压缩包）为**第三方网络公开资料**，
  整理时按「删除复现用不到的东西」删除；结论不依赖其内容本身。
* 原理图的保留出处即 `original/manuals/` 下的 **FTDX10 Technical Supplement**（ManualsLib 2215359）PDF 原件，
  `evidence/schematic/p32.svg` 与各 PNG 裁剪皆源于其第 32 页；页码出处见 `text/p32bbox.xml`。
* `reference/h8sx1645_download_failed.html` 保留以说明 H8SX/1645 手册页的来源；其中提取的 `bdtic.js` 已随一次性脚本清理删除。

---

## 5. `reference/` — 手册

| 文件 | 说明 |
|---|---|
| `h8sx1645_manual.txt` | **H8SX/1645 硬件手册文本**。寄存器地址/位定义核对的原始依据。注意手册用 5 位十六进制（`H'FFBA3`），固件 24 位地址 = 前缀一个 `F` → `0xFFFBA3` |
| `h8sx1650_manual.txt` `h8sx1650.txt` | H8SX/1650 手册（同族对照） |
| `ftdx10_manual.txt` | FTDX10 整机说明书文本 |
| `h8sx1645_rene.pdf` | H8SX/1645 手册 PDF 原件（Renesas） |
| `h8sx1650.pdf` `h8sx_digchip.pdf` | 相关器件 PDF |
| `h8sx1645_download_failed.html` | **注意**：原名 `h8sx1645.pdf`，实为 BDTIC 下载页存成的 HTML（8 K，非 PDF），保留以说明 `bdtic.js` 的来源 |

---

## 6. `tools/` — 工具

| 文件 | 说明 |
|---|---|
| `objdump` `objcopy` | **binutils 2.42**（本机现场编译，已启用 `elf32-h8300` 目标）。独立于系统 `objdump` 的**第二反汇编来源**，用于交叉核对 H8SX 反汇编 |
| `brute_affxor` | 仿射 XOR 穷举 C 助手（混淆排查用）。源码 `scripts/brute_affxor.c` 已随一次性脚本清理删除，二进制保留 |

运行时依赖（Linux x86-64 动态链接）：`objdump`/`objcopy` 需要 `libc`、`libzstd`；`brute_affxor` 需要 `libc`、`libgomp`。

---

## 7. 使用方法

```bash
# 在仓库根目录执行

# 直接用归档内的 disasm/（h8.py 默认路径）
python3 hardware/files/scripts/h8.py show 0xa26 0xa60        # 例：查看 0xFF2007 刷新路径
python3 hardware/files/scripts/h8.py find '0xff2007'         # 例：列出全部 46 条引用

# 如需改用其它反汇编清单
FTDX10_DISASM=path/to/main_disasm.txt python3 hardware/files/scripts/h8.py show 0xa26 0xa60

# 功率成品独立复核（已移至 power/）
python3 power/verify/v_kr2.py
bash    power/verify/verify_kr.sh

# 用归档的独立反汇编器交叉核对
hardware/files/tools/objdump -D -b binary -m h8300sx \
    decoded/firmware/FTDX10_MAIN_V0115.bin | less
```

---

## 8. 关键文件校验值

| 文件 | MD5 |
|---|---|
| `disasm/main_disasm.txt` | `9995983a867541e4185a86f014ff86c4` |
| `disasm/display_disasm.txt` | `053d6d72b9f954b5a861b62b5ff9be56` |
| `tools/objdump` | `8a05454103377370c57b22ef975b32e8` |
| `scripts/h8.py` | `ac61fca64d37f6d0344efb5fa2558b3b` |
| `evidence/text/cfg_sites.txt` | `3bbcf5b0c13a9403699b359e8f2c3039` |
| `evidence/schematic/p32.svg` | `15bbc860a47b0aac530ba62b4ff20ce2` |

---

## 9. 明确未收录（及原因）

| 未收录 | 大小 | 原因 |
|---|---|---|
| `binutils-2.42/` 构建树 | 513 MB | 只需其中的 `objdump`/`objcopy`，已单独收录 |
| `binutils.tar.xz` | 26 MB | 源码包，可从 GNU 镜像重取 |
| `__pycache__/` | — | 派生缓存（当前已清理，运行脚本时可能重新生成） |
| `out/` | 39 MB | 与工作目录 `decoded/`（5 个固件解码件）内容重复 |
| `decM/` `final/` `vrun/rt/` | 各 ~1 MB | 与 `power/patched/` 成品重复 |
| `r1645.txt` | 2.8 MB | 与 `h8sx1645_manual.txt` 逐字节相同 |
| `manual.txt` | 653 K | 与 `ftdx10_manual.txt` 逐字节相同 |
| `r1645.pdf` | 6.2 MB | 与 `h8sx1645_rene.pdf` 逐字节相同 |
| `io_footprint.txt` `out3.txt` | 0 | 空文件 |

### 整理时删除（本项目内）

| 已删除 | 原因 |
|---|---|
| `evidence/web/`（20 HTML + `jc_zips/`） | 第三方网站存档，结论不依赖；出处见 §4.3 |
| `evidence/schematic/p32/`（21 MB 逐步裁剪） | 派生中间件，关键件已挑选为顶层 PNG |
| `evidence/schematic/bottomedge_big.png` | 与 `bottomedge.png` 逐字节相同 |
| `evidence/text/gcd_v4c.log` `gcd_v6b.log` | 分别与 `gcd_v4b.log` / `gcd_v6.log` 逐字节相同 |
| `scripts/krpatch.py` | 与 `power/patched/srec_patch.py` 逐字节相同（已随功率脚本移出） |
| Python 缓存目录（原 `decoded/` 内） | 派生缓存 |

### 已作废的成品：**刻意不收录，切勿使用**

以下文件为**未归档的历史作废件**。若在别处遇到，均为**已被取代**的版本；现用件只有
`power/patched/MAIN_V0115_M_mask100W.SFL`（MD5 `c5f58f0641f9226447b8b4ff7575be72`）。

| 文件 | MD5 | 状态 |
|---|---|---|
| 旧 M（有 bug）的 `.SFL` | `f41cd1c115366ac367abb16dd2c15cd1` | **作废**（同族 `.srec` `7acdc4ef538825e9b3d133cbe12aba8c`） |
| 方案 A `.SFL` | `9154294c280fe858474871c8f18d32ed` | **作废**（`.srec` `1c2f5afd87bc5c7861b9087ddeb64aa5`） |
| 方案 B `.SFL` | `b8c4d3978a6247ae0884879092cec43e` | **作废**（`.srec` `1c75de031ec9e7ec589d12c71e1cd232`） |

---

## 10. 相关文件

* `../../original/` — 原始 `.SFL` 原件（固定密钥混淆）与手册 PDF（分析输入）
* `../README.md` — hardware 报告索引与一句话结论
* `../../power/README.md` — 功率修改汇总（方案 M / 方案 CAT / 成品 / 复核脚本）
* `../../power/功率解锁方案.md` — 功率修改权威方案（方法 M / EXP / 刷机 / 回滚）
* `../短接点功能说明.md` — KR0–KR7 / JP1001–JP1008 逐个说明
* `../可复现分析过程.md` — 完整可复现流程与 §9 一键脚本
* `../固件升级.md` — 升级逻辑归属、完整镜像、boot loader 恢复
* `../../decoded/ANALYSIS.md` — SFL 容器与混淆/解码
