# hardware/ — FTDX10 固件逆向成果目录

> 对象固件：`FTDX10_MAIN_V0115`（**H8SX/1645** ＝ `R5F61645N50FPV`，512 KB Flash / 40 KB RAM）+ `FTDX10_DISPLAY_V0106`（ARM，仅用于升级逻辑分析）。
> MAIN 固件代码地址 = **文件偏移**；RAM/外设寄存器按运行时地址标注；DISPLAY 地址按 `固件升级.md` 的运行时基址换算。所有结论均可按 `可复现分析过程.md` 独立复算。

---

## 目录内容（当前权威件）

| 文件 | 主题 | 说明 |
|---|---|---|
| `短接点功能说明.md` | 短接点 | KR0–KR7 / JP1001–JP1008 **逐个**的功能、读取方式、极性、有效性 |
| `../power/功率解锁方案.md` | 功率修改 | 方案 M 的权威说明：补丁、EXP 最小风险、刷机/回滚、风险与未决；方案 CAT 另见 `../power/CAT功率修改方案.md` |
| `可复现分析过程.md` | 方法论 | 完整可复现流程：工具链、每步命令、判据、实跑输出、验证清单 |
| `固件升级.md` | 升级/保底 | 升级逻辑在 DISPLAY 侧、完整镜像 vs 差异补丁、boot loader 恢复、砖机流程 |
| `../power/patched/MAIN_V0115_M_mask100W.SFL` | 成品 | 推荐刷机件（738,986 B，MD5 `c5f58f0641f9226447b8b4ff7575be72`） |
| `../power/patched/MAIN_V0115_M_mask100W.srec` | 成品 | 对应 S-record（738,410 B，MD5 `c1c0cb05519d990d56171e2773d1d178`） |
| `../power/patched/srec_patch.py` | 工具 | S-record 字节补丁器（自动重算校验和） |
| `../power/CAT功率修改方案.md` | 方案 CAT | **不刷固件**的备选方案：用未公开 CAT 命令 `SP` 的 `SPW/SPR` 改外部 FRAM（配置字节 `0x0007` + 各频段 RF POWER `0x0335–0x0338` / MAX POWER `0x046A–0x046D`），含两台机对照与回滚 |
| `../power/power_cat_optimized.py` | 工具 | 方案 CAT 的 FTDX10 专用脚本（读/写/对照/回滚，默认 dry-run） |

---

## 一句话结论

* **跳线（短接点）不影响功率**：配置列（KSC）的唯一读法所在路径 `0xA26` 是死代码；
  唯一活路径 `0xA38` 只刷新 bit5（EXP）。功率档来自**外部 FRAM[7]**。
* **50W 出不了 100W 是两道软件闸门**：菜单区间表 `0x159F6`（HF 上限 50）与等级钳位 `0x3BCA0`。
* **推荐方案 M**：5 处、每处 1 字节（指令长 2 字节），把 `and.b #0x18,Rn` 改成 `mov.b #0x18,Rn`，不碰配置字节。
* **备选方案 CAT（不刷机）**：用未公开命令 `SP` 的 `SPW` 把配置字节 `FRAM[0x0007]` 的 bit3/4 改成 `0x18`，
  目标效果与方案 M 一致，但实现与副作用不同，可逆；详见 `../power/CAT功率修改方案.md`。
* **只刷 MAIN、不刷 DISPLAY**：保留出厂 boot loader 恢复通道。

---

## 快速自检

```bash
# 在仓库根目录执行

# h8.py 自动读取 hardware/files/disasm/main_disasm.txt
python3 hardware/files/scripts/h8.py find 'and\.b\s+#0x18' 9999 | tail -1   # 应 12 处

# 短接点/升级逻辑证据
python3 hardware/files/scripts/h8.py show 0xa26 0xa60
```

> 功率成品（`MAIN_V0115_M_mask100W.*`）复核见 `../power/README.md` 与
> `../power/verify/`；完整流程见 `可复现分析过程.md`（§5–§6 为功率链；§7 为 EXP，§8 为升级）；补丁生成与校验见 `../power/功率解锁方案.md` §2。

---

## 原始材料归档

本目录各报告所依据的**反编译代码、复现脚本与证据**已归档到本目录下的 `files/`：

| 目录 | 内容 |
|---|---|
| `files/disasm/` | MAIN（H8SX）、DISPLAY（ARM）完整反汇编清单 |
| `files/scripts/` | 通用复现脚本 `h8.py`；功率专用复核脚本在 `../power/verify/` |
| `files/evidence/` | 原理图/PCB 截图与 `p32.svg`（矢量原件）、网络表、配置点清单 `cfg_sites.txt` |
| `files/reference/` | H8SX/1645、H8SX/1650、FTDX10 手册 |
| `files/tools/` | 独立反汇编器 `objdump`（binutils 2.42, h8300） |

说明与用法见 `files/README.md`。`h8.py` 默认读取 `files/disasm/main_disasm.txt`，无需还原临时工作区。

> 原始输入（SFL 原件与手册 PDF；SFL 为固定密钥混淆）在上一级的 `../original/`。
