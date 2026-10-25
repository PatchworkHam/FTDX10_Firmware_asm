# cat/ — FTDX10 CAT 命令分析

> 对象固件：`../decoded/firmware/FTDX10_MAIN_V0115.bin`（MAIN，H8SX/1645）。
> 反汇编依据：`../hardware/files/disasm/main_disasm.txt`；对照手册：`../original/manuals/dx10-cat.pdf`。
>
> 状态：全部基于**静态逆向**，**未在真机验证**；所有写入请先备份、后执行。

本目录按“**分析过程**”和“**隐藏命令列表**”两条主线重新整理：

- 想知道**结论是怎么来的** → 看 [`分析过程.md`](分析过程.md)
- 想直接**查命令、抄帧格式** → 看 [`隐藏CAT命令列表.md`](隐藏CAT命令列表.md)
- 想核对**代码与固件结论的对应关系** → 看 [`结论与代码对照.md`](结论与代码对照.md)
- 想查**服务菜单** → 见独立目录 [`../service_menu/README.md`](../service_menu/README.md)（附 294 项 CSV）

---

## 1. 阅读顺序

| 顺序 | 文件 | 内容 | 适合谁 |
|---|---|---|---|
| ① | [`分析过程.md`](分析过程.md) | 完整取证链：定位命令表（`0x27CF2`/`0x27AFA`）→ 解析/派发 → 与手册 102 条比对 → 24 条差集 → 13 桩 + 11 有效 → 逐条逆向 → 复现命令 → 四类交叉验证 → 局限 | 想复核结论、关心方法 |
| ② | [`隐藏CAT命令列表.md`](隐藏CAT命令列表.md) | 速查：24 条命令总览、风险分级、串口参数、帧格式、11 条命令的语法/回包/示例、`SP` 二进制子协议、安全提示 | 要实际发命令 |
| ③ | [`结论与代码对照.md`](结论与代码对照.md) | 审计入口：把固件结论、外部 FRAM 地址、常量、两份脚本的实现逐项对齐，并列出未验证边界 | 改脚本、查映射 |
| ④ | [`GUI工具使用说明.md`](GUI工具使用说明.md) | `ftdx10_hidden_cat_gui.py` 的安装、界面、逐按钮说明、备份格式与 CLI | GUI 使用者 |

> 服务菜单项目表、校准映射与 CSV 见独立目录：[`../service_menu/`](../service_menu/README.md)。

## 2. 目录结构

```text
cat/
├── README.md                  # 本文件：导航
├── 分析过程.md                 # ① 分析过程（证据链 / 逐条逆向 / 复现 / 验证 / 局限）
├── 隐藏CAT命令列表.md          # ② 隐藏命令列表（总览 / 语法 / 回包 / SP 协议）
├── 结论与代码对照.md           # ③ 结论与代码审计
├── GUI工具使用说明.md          # ④ GUI 使用说明
├── rig_profiles.json           # 机型配置：CAT ID、VE/JP/ZZ 魔术串、备注、FRAM 区域中文名
└── ftdx10_hidden_cat_gui.py    # 通用隐藏命令 GUI / CLI
```

## 3. 一句话结论

固件命令分派表共 **126** 条，CAT 手册 **102** 条，差集 **24** 条：

```text
AN BG DP DS EK EO FD FK FR FS KC KT MK PE RO SY VF VT SP VE JP ZZ E0 E8
```

- **13 条禁用桩**（处理函数首 4 字节改成 `bra 0x27A5E`，永远回 `?;`）：
  `AN BG DP DS FK FR FS KC MK RO SY VF VT`
- **11 条有效逻辑**：`EK EO FD KT PE SP VE JP ZZ E0 E8`
- 其中 `SP` 的 `SPW/SPR` 可读写外部 FRAM，是 [`../power/CAT功率修改方案.md`](../power/CAT功率修改方案.md) 的基础；
  `SPw/SPr` 访问的是内部设置表 `0xFFA948`，**不是**外部 FRAM。

## 4. 脚本与资料

| 文件 | 作用 |
|---|---|
| [`ftdx10_hidden_cat_gui.py`](ftdx10_hidden_cat_gui.py) | 单文件 GUI（tkinter + pyserial），读写全部 11 条有效隐藏命令；机型与魔术串预设、功率/频段、FRAM 全表、294 项服务菜单、PE 校准；带无界面备份/恢复 |
| [`rig_profiles.json`](rig_profiles.json) | FTDX10 / FT-891 机型配置：CAT ID、VE/JP/ZZ 魔术串、证据状态、备注、FRAM 区域中文名；由 GUI 编辑保存。FT-891 值来自 `AH065_M_V0110` 静态研究 |
| [`../power/power_cat_optimized.py`](../power/power_cat_optimized.py) | FTDX10 专用功率工具（**仅适配 FTDX10 / MAIN V01-15；静态逆向，未真机验证**）：固定布局常量、一键读取/设功率、快照对照/回滚；默认 **dry-run**，`--apply` 才真写 |
| [`../original/manuals/dx10-cat.pdf`](../original/manuals/dx10-cat.pdf) | CAT 参考手册（ManualsLib 抓取，27 页），未公开命令的对照基准 |
| [`../verify/verify_hidden_cat.py`](../verify/verify_hidden_cat.py) | 直接读固件字节，复核命令表 / 隐藏集合 / 桩 / 关键处理函数；当前离线复核 22/22 通过 |

> 本目录集中存放可直接使用的工具、说明与分析文档。验证/处理脚本统一放在 [`../verify/`](../verify/)。

## 5. 离线查看

```bash
# 在仓库根目录执行
python3 power/power_cat_optimized.py list
python3 cat/ftdx10_hidden_cat_gui.py --help
```

---

## 6. 与其它块的关系

* 功率链路取证（两道软件闸门、`0xFF2007`、配置字节存储）：见 `../power/功率解锁方案.md`、`../hardware/可复现分析过程.md`。
* FRAM 全空间分区与访问点：见 `../fram/FRAM_MAP.md`、`../fram/evidence/verification.md`。
* 目标固件与反汇编：`../decoded/`、`../hardware/files/`。
