# FTDX10 FRAM 分析

对 FTDX10 MAIN 固件（V01-15）做静态 FRAM 逆向，重建 32 KiB 分区、访问点和验证记录。

## 目标固件

| 项 | 值 |
|---|---|
| 文件 | `decoded/firmware/FTDX10_MAIN_V0115.bin` |
| SHA-256 | `370986128f5f1fde1e65bed3ceecb98b88b1fcde9ab13ce2703b4d0b8e34feef` |
| 大小 | `259668` 字节（`0x3F654`） |
| FRAM | MB85RS256A，32 KiB，`0x0000–0x7FFF` |
| 地址约定 | 固件地址 = 文件偏移（已在四个原语处逐字节核对） |

## 文件

| 文件 | 内容 |
|---|---|
| `FRAM_MAP.md` | 完整分区、字段表、90B 记录、证据与待确认项 |
| `fram_map.csv` | 27 行机器可读分区表 |
| `evidence/access_sites.csv` | 246 个 FRAM 访问点明细 |
| `evidence/verification.md` | 哈希、边界、公式、原语一致性验证与二次复核结果 |
| `tools/fram_scan.py` | 扫描器；生成 CSV，支持 `--verify` |
| `tools/test_fram_scan.py` | 回归测试 |

## 快速开始

在项目根目录执行：

```bash
python3 fram/tools/fram_scan.py --disasm hardware/files/disasm/main_disasm.txt --verify
FTDX10_DISASM=hardware/files/disasm/main_disasm.txt python3 fram/tools/test_fram_scan.py -v
```

`--disasm` 是必填参数，指向 H8/300H 反汇编文本。本环境使用的反汇编位于 `hardware/files/disasm/main_disasm.txt`；扫描器自身不硬编码该路径。

扫描器默认读取：

- 固件：`decoded/firmware/FTDX10_MAIN_V0115.bin`
- 输出：`fram/evidence/access_sites.csv`、`fram/fram_map.csv`

可用参数覆盖：

```bash
python3 fram/tools/fram_scan.py \
  --disasm path/to/main_disasm.txt \
  --bin path/to/FTDX10_MAIN_V0115.bin \
  --out fram/evidence/access_sites.csv \
  --map fram/fram_map.csv \
  --verify
```

退出码：`0` 表示分区与二进制检查全部通过，`1` 表示有检查失败，`2` 表示输入文件缺失。

## 访问原语

| 地址 | 操作 | 输入 |
|---|---|---|
| `0xA4A8` | 字节读 | `r1` = FRAM 地址；返回 `r0l`，`r1 += 1` |
| `0xA528` | 字节写 | `r0l` = 数据，`r1` = FRAM 地址；`r1 += 1` |
| `0xA4E2` | 块读 | `r1` = FRAM 地址，`er3` = RAM 目标，`r2` = 长度 |
| `0xA5C2` | 块写 | `r1` = FRAM 地址，`er3` = RAM 源，`r2` = 长度 |

## 扫描结果

- 246 个访问点：162 字节写、21 字节读、38 块写、25 块读。
- 基址解析：112 个立即数、88 个函数参数、31 个运行时计算、15 个未知。
- 最高常量基址：`0x5ADB`。
- 27 个区间连续覆盖 `0x0000–0x7FFF`，合计 32768 字节。
- 14 条边界公式全部通过。
- 底层 SPI 调用 `0xA662`/`0xA69A` 只出现在 FRAM 驱动区 `0xA4A8–0xA64C`。

## 解析规则

`fram_scan.py` 在回溯寄存器时坚持“不猜”：

- 只把真正的写操作当作定义；`cmp`、`btst` 等只读指令不算写。
- 遇到分支或非 FRAM 调用就停止，避免跨基本块取到失效值。
- 对四个原语建模 `r1 += size` 的副作用，因此能解析出厂初始化中的连续单字节写。
- 函数入口之前从未写过该寄存器时，标为 `parameter`，而不是编造基址。
- 其余情况标为 `computed` 或 `unknown`，并在 CSV 的 `evidence` 列给出最近一条相关指令。

## 限制

- 纯静态分析，没有真实 FRAM 转储；字段的运行时值未验证。
- 业务名称只在有交叉引用或既有分析支撑时给出，其余标为“名称待补”。
- 88 个参数型访问点的具体调用者基址尚未逐个传播；这影响访问点明细，不影响分区边界。
- 31 个计算型、15 个未知型访问点需要动态跟踪或转储才能落到具体地址。

## todo

1. 用真实 FRAM 转储验证 `0x0300` 块和 117 条信道记录的字段偏移。
2. 对 `0x8580`、`0x9D54`、`0xA0F4`、`0x318xx` 等辅助函数做调用图传播，补全 `parameter` 行。
3. 对 `0x0300` 块做 RAM `0xFFAABC` 的交叉引用，补齐菜单字段名。
4. 对 `0x0A80` 日志做写入路径分析，确认 4B 条目的位域。
