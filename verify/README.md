# verify/ — 固件结论复核

本目录只放**不连接设备、不修改固件**的复核脚本。

| 文件 | 作用 |
|---|---|
| `verify_hidden_cat.py` | 直接读取 `../decoded/firmware/FTDX10_MAIN_V0115.bin`，复核 `cat/分析过程.md` 与 `cat/隐藏CAT命令列表.md` 中的命令表/隐藏命令/关键处理函数结论。 |
| `verify_short_jumper.py` | 复核 `hardware/可复现分析过程.md` §4.2 与 `hardware/短接点功能说明.md` §5：`0x7D2E` 死代码三扫描、阳性对照，以及 `0xA26` 死路径 / `0xA38`+`0xA58` 活路径。 |

运行：

```bash
# 在仓库根目录执行
python3 verify/verify_hidden_cat.py
python3 verify/verify_short_jumper.py
```

`verify_hidden_cat.py` 当前包含 **22 项检查**，覆盖：

- 126 条 ASCII 命令表、唯一性、处理函数指针表对齐；
- 24 条未公开命令集合、13 条禁用桩 + 11 条有效逻辑；
- 两套解析器计数与内联表上限；
- `VE` 魔术串 `RAH076H`；
- `ZZ` 前缀 `PE0010`、长度 8、末尾 `jmp 0x6EE`；
- `JP` 前缀 `0010`、长度 4/0xC、无复位跳转；
- `E0` 229 字节、`E8` 10573 字节及源地址；
- `SPW` 长度 6、校验 7 字节、地址阈值 `0x8000`、写原语调用。

`verify_short_jumper.py` 当前包含 **16 项检查**，覆盖：

- `0x7D2E` 的文本引用、4 字节长字、3 字节地址、分支/调用目标四类扫描；
- `0x15920`/`0x20DB4`/`0x20D80` 的阳性对照；
- `0x7D2E → 0x7E8E … → 0x7E9E → 0xA26` 死路径；
- `0x1570 → 0x8E50 → 0x8E54 → 0xA38` 活路径，以及 `0xA38` 的 `0xFF9F/0x20` 掩码和 `0xA58` 写回。

边界：

- 只验证**固件镜像中的静态结论**，不验证真机串口行为、写入效果或硬件能力。
- 不修改 `decoded/`、固件源文件或 SFL 文件。
