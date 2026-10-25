# FTDX10 固件（.SFL）混淆与解码分析报告

本报告说明本工作区内固件更新包的**混淆方式**，以及**源固件（明文）**的提取过程与结果。

---

## 1. 结论摘要

- `.SFL` 文件是 **Yaesu 现代机型（测试文件FTDX10）** 的 SD 卡固件更新包。
- 混淆算法并不是现代分组密码，而是**逐字节的固定密钥“维吉尼亚减法”（mod 256）**：
  数据段每个字节减去一个 **43 字节、周期性循环**的密钥（该密钥恰好是 1..43 的一个置换）。
- 5 个 `.SFL` 均已成功解码/还原；S-record/hex 载荷输出文本，裸二进制载荷输出 `.bin` 视图（见第 5 节）。
- 解码结果由 **三重独立证据** 交叉验证，确定性极高（见第 4 节）。
- 第 9 行元数据 `<CRC32>` 校验算法已完全还原/确认（CRC-32/ISO-HDLC + 固定 4 字节尾，见第 7 节）；
  打包脚本会**自动重算**该字段，因此改动固件后重新打包可保持校验自洽（见第 9 节）。

---

## 2. 文件结构

```
偏移          长度        内容
------------------------------------------------------------
0   .. 575    576 字节    文本标头/元数据（CRLF 行）
576 .. END    剩余        混淆数据段（存储态密文）
```

标头共 9 行（每行以 `CRLF` 结尾）：

```
[FILE NAME]   ...
[PRODUCT NAME]...
[FIRM VERSION]...
[DATE]        ...
COPYRIGHT ...
<TYPE>        <SIZE>        <CRC32HEX>     ← 第 9 行为元数据
```

- `<TYPE>` 取值：`AD`(AFCPU) / `MC`(MAIN) / `PC`(DISPLAY) / `ID`(IFDSP) / `SR`(SDR)
- `<SIZE>`：整个 `.SFL` 文件字节数
- `<CRC32HEX>`：厂商校验字段，= CRC-32/ISO-HDLC(密文数据段 ‖ `A6 EC 9E 13`)（见第 7 节）

---

## 3. 混淆算法

> 术语说明：本工作区沿用“加密/解密/密文”作为习惯说法；按密码学定义，这是固定密钥逐字节减法**混淆**，不是加密。

```python
HEADER_LEN = 576                      # 数据段起始偏移

# 43 字节固定密钥（1..43 的一个置换）
KEY = [30,39,27,18,17,22,25,23,36,37,41,28,13,11,6,4,3,1,2,5,7,
       12,14,15,24,29,32,34,42,43,8,9,10,16,19,20,21,26,31,33,40,35,38]

def decrypt_sfl(raw: bytes) -> bytes:
    body = raw[HEADER_LEN:]
    out = bytearray(len(body))
    for i, b in enumerate(body):
        out[i] = (b - KEY[i % 43]) & 0xFF     # 逐字节减法，索引按 43 循环
    return bytes(out)
```

要点：
- 密钥长度 **43**，是 1..43 的置换（无重复、无遗漏）。
- 变换是**长度保持不变**的逐字节操作，因此数据段长度 ≈ 明文长度。
- 这是可逆且无扩散的弱混淆，不是 AES/流密码级的安全机制。

**为什么 43 是关键线索**：明文固件里存在大量连续相同字节（如擦除后的 `0xFF` / `0x00` 填充）。减去周期性密钥后，这些区域会表现为**周期 43 的序列**。实测 SDR 数据段对 43 的周期性匹配率达 94.6%，与算法完全吻合。

---

## 4. 解码正确性验证（三重证据）

| 文件 | 解码后内容 | 验证证据 |
|---|---|---|
| **AFCPU** | Motorola **S-record 文本** | 全部 **5,175** 条数据记录校验和 **100% 通过**（另 S0/S7 各 1 条）；字符集恰好只含 `S`/`0-9`/`A-F`/`CR`/`LF` 共 19 种 |
| **MAIN** | Motorola **S-record 文本** | 全部 **16,230** 条数据记录校验和 **100% 通过**（另 S0/S9 各 1 条）；S0 头为 `HEW`（Renesas HEW 工具链） |
| **DISPLAY** | **ARM 二进制** | 起始即标准 ARM 中断向量表（`09 00 00 EA`…），含 12486 条真实字符串，如 `I:\FTDX10\PlayList`、`VK_RETURN`、`sdTask` |
| **IFDSP** | 二进制 | 含 `TIPA` / `YSX` 标记；256 种字节值（高熵，真实代码/数据） |
| **SDR** | **十六进制文本** | 100% 均为 hex 字符（`0-9a-f` + CRLF）→ 即固件的 hex 编码交付形式 |

S-record 校验和全部通过（0 条失败）是**决定性证据**：它逐条校验每条记录的字节，只有密钥完全正确才可能全部通过。

---

## 5. 输出文件清单（`decoded/firmware/`）

| 文件 | 大小 | 说明 |
|---|---|---|
| `FTDX10_AFCPU_V0100.srec` | 248158 | S-record 文本（回打包原文） |
| `FTDX10_AFCPU_V0100.bin` | 25207552 | 装配二进制，地址 `0xFE7F5D00..0xFFFFFFFF`（含 0xFF 填充） |
| `FTDX10_AFCPU_V0100.ihex` | 232620 | 带绝对地址的 Intel HEX（分析视图） |
| `FTDX10_MAIN_V0115.srec` | 738410 | S-record 文本（回打包原文） |
| `FTDX10_MAIN_V0115.bin` | 259668 | 装配二进制，地址 `0x0..0x3F653`（复位向量 `0x00000754`） |
| `FTDX10_MAIN_V0115.ihex` | 730428 | 带绝对地址的 Intel HEX（分析视图） |
| `FTDX10_DISPLAY_V0106.bin` | 3373532 | ARM 二进制（向量表在 0；SFL 不记录加载地址，不生成 HEX） |
| `FTDX10_IFDSP_V0120.bin` | 138460 | 二进制（SFL 不记录加载地址，不生成 HEX） |
| `FTDX10_SDR_V0100.rawhex` | 4392218 | 解码后的 hex 文本（原始交付形式；回打包原文） |
| `FTDX10_SDR_V0100.bin` | 2196108 | 由 hex 还原的二进制 |

工具与验证脚本：`decoded/yaesu_sfl_tool.py` 与 `decoded/verify/`（见 [`README.md`](README.md)）。

各 `.SFL` 元数据（类型 / 文件大小 / 声明 CRC）：

| TYPE | 文件 | SIZE | 声明 CRC32 |
|---|---|---|---|
| AD | AFCPU V01-00 | 248734 | `917F68A5` |
| MC | MAIN V01-15 | 738986 | `DEE1F62A` |
| PC | DISPLAY V01-06 | 3374108 | `8B764DC9` |
| ID | IFDSP V01-20 | 139036 | `695023C7` |
| SR | SDR V01-00 | 4392794 | `77AC2625` |

---

## 6. 工具用法

```bash
# 在仓库根目录执行

# 处理单个文件（输出到 decoded/firmware/）
python3 decoded/yaesu_sfl_tool.py decode original/firmware/FTDX10_MAIN_V0115.SFL -o decoded/firmware

# 批量处理某目录下所有 *.SFL
python3 decoded/yaesu_sfl_tool.py decode --all original/firmware -o decoded/firmware

# SHA-256 往返自检
python3 decoded/yaesu_sfl_tool.py verify --all original/firmware
```

脚本会按解码内容**自动识别格式**并输出文本 + 二进制视图：
- S-record → `.srec`（文本） + `.ihex`（绝对地址视图） + `.bin`（装配后的连续二进制）
- Intel HEX → `.ihex`（文本） + `.bin`（还原二进制）
- 纯 hex 文本 → `.rawhex`（文本） + `.bin`（还原二进制）
- 其它二进制 → `.bin`（二进制）；如显式指定 `--hex-base`，另生成 `.hex` 文本导出

---

## 7. 元数据 `<CRC32>` 字段（已还原）

### 7.1 结论

第 9 行元数据里的 8 位十六进制 `<CRC32>` 已完全确定，算法为：

```
CRC32HEX = CRC-32/ISO-HDLC( 混淆数据段（存储态密文） ‖ A6 EC 9E 13 )
```

即：对**混淆后（存储态）的数据段**（偏移 576 起的原始密文字节）做标准 CRC-32/ISO-HDLC
（多项式 `0xEDB88320`、初值 `0xFFFFFFFF`、输出取反），末尾再拼接 4 个固定字节
`A6 EC 9E 13`。用 Python 表示即：

```python
import zlib
SFL_CRC_TRAILER = bytes.fromhex("a6ec9e13")   # 固件常量 0x139EECA6 的小端字节序
crc = zlib.crc32(ciphertext + SFL_CRC_TRAILER) & 0xFFFFFFFF
```

要点：
- 校验覆盖的是**密文数据段**（不含 576 字节标头），这一点与最初的直觉（以为是明文校验）不同；
- 末尾 4 字节 `A6 EC 9E 13` 是**固定常量**，与文件内容/长度无关；
- 校验字段位于标头偏移 **544**（第 9 行元数据行内，起始 512，字段相对偏移 +32）。

### 7.2 验证

对全部 5 个 `.SFL` 复算，与标头声明值**逐一精确相等**：

| 文件 | 声明 `<CRC32>` | 复算值 | 结果 |
|---|---|---|---|
| `FTDX10_AFCPU_V0100.SFL` | `917F68A5` | `917F68A5` | ✓ |
| `FTDX10_MAIN_V0115.SFL` | `DEE1F62A` | `DEE1F62A` | ✓ |
| `FTDX10_DISPLAY_V0106.SFL` | `8B764DC9` | `8B764DC9` | ✓ |
| `FTDX10_IFDSP_V0120.SFL` | `695023C7` | `695023C7` | ✓ |
| `FTDX10_SDR_V0100.SFL` | `77AC2625` | `77AC2625` | ✓ |

### 7.3 还原过程（简要）

1. 先排除：该字段不等于任何标准 CRC-32 变体对**解码后数据**（或整文件、或 `header+data`）的结果。
2. 在解码出的 **DISPLAY（ARM）固件**中定位到标准 CRC-32 例程：以 `0xEDB88320` 建表
   （文件偏移 `0xAB4` 处即该多项式字面量），核心循环 `crc = tab[(crc^b)&0xff] ^ (crc>>8)`。
3. 调用点的固定形态为 `init=0xFFFFFFFF` → 计算 → `mvn`（输出取反），即标准 CRC-32/ISO-HDLC；
   例如文件偏移 `0x2394–0x23A8`（`bl 0xa34` 建表、`bl 0xa7c` 对 48 字节迭代、`mvn r0,r0` 取反）。
4. 关键证据：函数 `0x14C28–0x14C94`（文件偏移）在算完数据后，**再追加一个 4 字节固定常量
   `0x139EECA6`** 继续 CRC——其内存小端字节恰为 `A6 EC 9E 13`（字面量位于文件偏移 `0x14CA0`；
   另一处校验点 `0xF9EC–0xFA1C` 使用同一常量，字面量在 `0xFBA8`）。
5. 用该常量对**密文数据段**做 CRC-32/ISO 复算，5 个文件全部命中，遂确认。

### 7.4 对"改动固件"的意义

`yaesu_sfl_tool.py encode` 现在会**默认自动重算**该字段：先混淆数据段，再按上式算出 CRC 并写回标头
偏移 544。因此**修改明文后重新打包得到的 `.SFL` 校验自洽**，可继续刷写流程。


---

## 8. 免责声明

本分析仅用于个人研究、互操作与学习目的。对解出的固件进行修改并刷入设备存在**变砖风险**，请自行承担后果。

---

## 9. 反向打包与往返一致性验证

`yaesu_sfl_tool.py encode` 与 `decode` 互为逆过程，把明文数据段重新混淆并拼回 576 字节标头：

```python
cipher[i] = (plain[i] + KEY[i % 43]) & 0xFF
sfl = header(576 字节) + cipher
```

用法：

```bash
# 在仓库根目录执行

# 用模板 SFL 的标头 + 新的明文数据段 生成新的 SFL
python3 decoded/yaesu_sfl_tool.py encode --template ORIG.SFL --plain PLAIN.bin -o NEW.SFL

# 往返自检(解码→回打包，逐字节比对 SHA-256)
python3 decoded/yaesu_sfl_tool.py verify original/firmware/FTDX10_MAIN_V0115.SFL
python3 decoded/yaesu_sfl_tool.py verify --all original/firmware

# 独立往返测试(用 decoded/firmware/ 的明文文件重新打包，与原文件比对)
python3 decoded/verify/roundtrip_test.py
```

这表明：

1. 解码算法与实现**完全正确且可逆**；
2. 交付的文本/二进制明文文件与原始混淆数据**一一对应、无任何信息损失**；
3. 因为映射是 256 上的双射、标头逐字节复制，重建必然与原文件全等——这是一条强于单纯"能解码出可读内容"的完备性证明。

> 说明：往返自检现在会**自动重算**元数据里的 `<CRC32>` 字段（见第 7 节）——重算结果与原值相等，
> 因此重建文件仍与原始逐字节一致。对于**改动过内容**的新固件，同一逻辑会自动写入正确的新校验：
>
> ```bash
> # 1) 修改解码得到的明文(如 decoded/firmware/FTDX10_XXXX.bin/.rawhex/.srec)
> # 2) 用原 .SFL 作模板重新打包(自动重算 CRC32)
> python3 decoded/yaesu_sfl_tool.py encode --template original/firmware/FTDX10_IFDSP_V0120.SFL \
>         --plain decoded/firmware/FTDX10_IFDSP_V0120.bin -o NEW.SFL
> # 3) 校验自洽测试
> python3 decoded/verify/modify_test.py   # 原始复算==声明、改动后校验自洽、解码回读一致、未重算则失配
> ```
>
> `decoded/verify/modify_test.py` 的实测结果：5 个文件在"改动→重打包"后，新校验字段与密文复算值完全一致，
> 且解码回读等于改动后的明文；作为反证，若不重算（内部 `fix_crc=False`，见该脚本的 stale 分支）
> 则校验字段会停留在旧值而与数据不符。

