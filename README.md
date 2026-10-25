# FTDX10 固件逆向工作区

对象：**Yaesu FTDX10**（MAIN = H8SX/1645 `R5F61645N50FPV`；DISPLAY = ARM）。
固件为 2024-02 发布的更新包（MAIN V01-15 2024-02-16、DISPLAY V01-06 2024-02-27；AFCPU/SDR 为 2020-10、IFDSP 为 2023-03）。工作区按十个目录组织，各有一级入口；跨目录依赖在文中标注。

> 本工作区结论由本地部署的 **DeepSeek V4.1 Flash** 与 **REA** 生成，并经人工复核；尚未进行实机验证。

```
./
├── original/     原始素材：5 个 SFL 原件（固定密钥混淆）+ 手册 PDF
├── decoded/      固件解码：SFL 混淆/解码分析与工具；firmware/ 存 5 个明文固件，verify/ 存往返测试
├── fram/         FRAM 逆向：32 KiB 分区、246 个访问点、验证记录与扫描器
├── cat/          CAT 命令分析：未公开命令逆向、GUI 工具
├── service_menu/ 服务菜单：项目表、校准映射、SPN 分析与 CSV
├── hardware/     硬件/寄存器逆向：短接点、升级/保底、可复现流程
│   └── files/    反汇编 disasm/、证据 evidence/、参考手册 reference/、复现脚本 scripts/、工具 tools/
├── power/        功率修改汇总：固件补丁 M + CAT SPW/SPR、成品与复核脚本
├── TS/           TS 波段方案、5 MHz 发射与最大范围（研究 + CAT 脚本）
├── verify/       固件结论复核脚本
└── publication/  对外总结：MAIN V01-15 逆向分析（汇总稿）
```

---

## 块① `original/` — 原始素材

未改动的分析输入。

| 路径 | 内容 |
|---|---|
| `firmware/*.SFL` | 5 个固件更新包（固定密钥混淆；AFCPU / MAIN / DISPLAY / IFDSP / SDR） |
| `manuals/Firmware_Ver_Up_Manual_ENG_FTDX10_2104-B.pdf` | Yaesu 官方固件升级手册 |
| `manuals/FTDX10_Technical_Supplement_manualslib-2215359.pdf` | FTDX10 技术补充手册（含整页原理图，`p32` 出处） |
| `manuals/dx10-cat.pdf` | CAT 参考手册（ManualsLib 抓取，未公开命令的对照基准） |

详见 [`original/README.md`](original/README.md)。

## 块② `decoded/` — 固件解码

* 报告：[`decoded/ANALYSIS.md`](decoded/ANALYSIS.md)（SFL 容器、逐字节固定密钥减法、CRC32 算法还原、往返一致性）。
* 明文产物：`decoded/firmware/` 下的各 `*.srec` / `*.bin` / `*.ihex` / `*.rawhex`（由块①的 `.SFL` 解码还原）。
* 工具：`decoded/yaesu_sfl_tool.py`（统一编解码 + SHA-256 往返校验）；`decoded/verify/`（`roundtrip_test.py` / `modify_test.py`）。

## 块③ `fram/` — FRAM 逆向

* [`fram/README.md`](fram/README.md) — 目标固件、访问原语、扫描器用法与结果概览。
* [`fram/FRAM_MAP.md`](fram/FRAM_MAP.md) — 32 KiB 完整分区表、字段表、90B 记录、置信度与待确认项。
* [`fram/evidence/verification.md`](fram/evidence/verification.md) — 哈希、边界公式、原语一致性、回归测试记录。
* 数据与工具：`fram/fram_map.csv`、`fram/evidence/access_sites.csv`（246 个访问点）、`fram/tools/fram_scan.py`、`fram/tools/test_fram_scan.py`。

## 块④ `cat/` — CAT 命令分析

* [`cat/结论与代码对照.md`](cat/结论与代码对照.md) — **总览/审计入口**：固件结论、FRAM 地址、两份工具的代码映射。
* [`cat/分析过程.md`](cat/分析过程.md) — 分析过程：固件命令分派表 126 条 vs 手册 102 条 → 24 条未公开命令，13 条桩 + 11 条有效逻辑，含逐条逆向与四类交叉验证。
* [`cat/隐藏CAT命令列表.md`](cat/隐藏CAT命令列表.md) — 隐藏命令列表：24 条总览、语法/回包/示例、`SP` 二进制子协议与安全提示。
* [`cat/GUI工具使用说明.md`](cat/GUI工具使用说明.md) — 隐藏命令读写 GUI（`cat/ftdx10_hidden_cat_gui.py`）：机型与魔术串、功率/频段、FRAM 全表、294 项服务菜单、PE 校准。
* [`cat/rig_profiles.json`](cat/rig_profiles.json) — FTDX10 / FT-891 机型配置（CAT ID、VE/JP/ZZ 魔术串、证据状态、备注、FRAM 区域中文名）。
* 对照手册：`original/manuals/dx10-cat.pdf`。
* 验证脚本：[`verify/verify_hidden_cat.py`](verify/verify_hidden_cat.py)、[`verify/verify_short_jumper.py`](verify/verify_short_jumper.py)（与日常工具分开存放）。
* **功率修改**（方案 CAT、固件补丁）已集中到 [`power/`](power/README.md)。

## 块④附 `service_menu/` — 服务菜单

* [`service_menu/README.md`](service_menu/README.md) — 服务菜单项目编号 `N`、`0x27A8` 10 B 项目表、`0x3324` 标签表、`0x1FE2` 校准映射与 `SPN` 语义。
* [`service_menu/服务菜单项目表.csv`](service_menu/服务菜单项目表.csv) — 294 项完整导出（含 `item_*` / `apply_*` 地址）。
* [`service_menu/gen_service_table.py`](service_menu/gen_service_table.py) — CSV 生成脚本。

## 块⑤ `hardware/` — 硬件 / 寄存器逆向

* 报告索引：[`hardware/README.md`](hardware/README.md)。
* [`hardware/短接点功能说明.md`](hardware/短接点功能说明.md) — KR0–KR7 / JP1001–JP1008 逐个功能与有效性。
* [`hardware/固件升级.md`](hardware/固件升级.md) — 升级逻辑归属、完整镜像、boot loader 恢复。
* [`hardware/可复现分析过程.md`](hardware/可复现分析过程.md) — 完整可复现流程与 §9 一键脚本。
* 支撑归档：[`hardware/files/`](hardware/files/README.md)（反汇编 `disasm/`、复现脚本 `scripts/`、工具 `tools/`；功率复核脚本在 `power/verify/`）。
* **功率修改方案与成品已移至 [`power/`](power/README.md)。**

## 块⑥ `power/` — 功率修改汇总

集中两种功率修改方式与脚本（方案 M 来自固件补丁、方案 CAT 来自未公开命令）：

* [`power/README.md`](power/README.md) — 方案索引与安全边界。
* [`power/功率解锁方案.md`](power/功率解锁方案.md) — **方案 M**：5 处单字节固件补丁（`and.b #0x18`→`mov.b #0x18`），含刷机/回滚。
* [`power/CAT功率修改方案.md`](power/CAT功率修改方案.md) — **方案 CAT**：不刷固件，用 `SP` 的 `SPW/SPR` 改 FRAM。
* [`power/power_cat_使用说明.md`](power/power_cat_使用说明.md) — `power_cat_optimized.py` 使用说明。
* 脚本/成品：`power/power_cat_optimized.py`、`power/patched/`（`MAIN_V0115_M_mask100W.{SFL,srec}` + `srec_patch.py`）、`power/verify/`（`v_kr2.py` / `verify_kr.sh`）。
* 原理取证：`hardware/可复现分析过程.md` §5–§6、`cat/结论与代码对照.md`。

## 块⑦ `verify/` — 固件结论复核

* [`verify/README.md`](verify/README.md) — 复核脚本说明。
* `verify/verify_hidden_cat.py` — 直接读取 MAIN 固件，复核隐藏 CAT 命令、处理函数与帧格式，共 22 项检查。
* `verify/verify_short_jumper.py` — 复核短接点的死代码/活路径结论：`0x7D2E` 三扫描、`0xA26` 死路径、`0xA38`+`0xA58` 活路径，共 16 项检查。
* 本目录脚本只读固件，不连接设备、不修改固件或 SFL 原件。

## 块⑧ `publication/` — 对外总结

* [`publication/FTDX10_MAIN_V0115_逆向分析.md`](publication/FTDX10_MAIN_V0115_逆向分析.md) — 工作区结论的对外总结稿。
* 详细取证、脚本、验证记录与限制说明以各目录报告为准。

## 块⑨ `TS/` — TS 波段方案与 5 MHz 发射

* [`TS/README.md`](TS/README.md) — 一页结论、快速用法、目录索引。
* [`TS/TS频段表与5MHz发射.md`](TS/TS频段表与5MHz发射.md) — TS 0–7 全部边界、选择器优先级、5 MHz 预设、EXP/服务模式对比、服务模式入口 `ZZPE001000;`、证据与未定项。
* [`TS/yaesu_ts_cat.py`](TS/yaesu_ts_cat.py) — 用 CAT `SPR/SPW` 读写 `FRAM[0x0007]` 的 TS/EXP（默认预览、写入回读、离线查表）。
* [`TS/使用说明.md`](TS/使用说明.md) — 脚本用法、修改点、风险与实机验证清单。

---

## 运行环境与依赖

* Python 3.9+（本项目在 3.12 下验证）。
* `decoded/`、`fram/`、`verify/`、`hardware/files/scripts/h8.py` 只依赖标准库。
* 串口工具需要 `pyserial`；GUI 另需 `tkinter`（部分 Linux 发行版需安装 `python3-tk`）：
  ```bash
  pip install pyserial
  ```
* `hardware/files/tools/` 是 Linux x86-64 归档二进制：`objdump`/`objcopy` 依赖 `libc`、`libzstd`；`brute_affxor` 依赖 `libc`、`libgomp`。
* 文档中的项目路径均相对仓库根；Markdown 链接相对当前文件。串口示例中的 `/dev/ttyUSB0` 是设备节点，按实际串口替换。
* 如需避免生成 `__pycache__`，可在命令前加 `python3 -B` 或设置 `PYTHONDONTWRITEBYTECODE=1`。

---

## 快速开始

```bash
# 在仓库根目录执行

# 解码全部固件（块②）
python3 decoded/yaesu_sfl_tool.py decode --all original/firmware -o decoded/firmware

# 指令级检索（块⑤）；h8.py 自动定位 hardware/files/disasm/main_disasm.txt
python3 hardware/files/scripts/h8.py show 0xa26 0xa60
python3 hardware/files/scripts/h8.py find 'and\.b\s+#0x18' 9999 | tail -1   # 应 12 处

# FRAM 分区扫描与回归测试（fram/）
python3 fram/tools/fram_scan.py \
    --disasm hardware/files/disasm/main_disasm.txt \
    --out fram/evidence/access_sites.csv --map fram/fram_map.csv --verify
FTDX10_DISASM=hardware/files/disasm/main_disasm.txt python3 fram/tools/test_fram_scan.py -v

# CAT 功率工具离线查看（power/）
python3 power/power_cat_optimized.py list

# TS 波段表离线查看（TS/）
python3 TS/yaesu_ts_cat.py --table all

# 补丁成品独立复核（power/）
python3 power/verify/v_kr2.py
bash    power/verify/verify_kr.sh
```

> 地址约定：MAIN 固件代码地址为**文件偏移**；RAM/外设寄存器按**运行时地址**标注；DISPLAY 默认给**运行时地址**（= 文件偏移 + `0x20100000`），文中另有说明除外。
> 全部复现命令使用仓库内工具与清单，路径均相对仓库根；无需先还原任何临时工作区。
