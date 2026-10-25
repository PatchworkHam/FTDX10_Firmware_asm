# decoded/ — 固件解码产物与分析

对象：`original/firmware/` 里的 5 个 `.SFL`（固定密钥混淆）。
**完整分析见 [`ANALYSIS.md`](ANALYSIS.md)**（容器格式、混淆算法、CRC32 算法还原、三重证据、往返一致性）。
本文件只做目录索引。

---

## 目录结构

```
decoded/
├── README.md              本文件
├── ANALYSIS.md            SFL 混淆/解码分析报告
├── yaesu_sfl_tool.py      统一编解码工具（decode / encode / verify）
├── firmware/              5 个固件的解码明文
└── verify/                往返与改动自洽验证脚本
    ├── _sfl_common.py     共用加载器
    ├── roundtrip_test.py  解码→重打包逐字节回环（对 5 个原厂 SFL）
    └── modify_test.py     改动→重打包→头部 CRC32 自洽测试
```

## 明文固件（`firmware/`，由 `../original/firmware/*.SFL` 解码得到）

| 固件 | 文件 |
|---|---|
| AFCPU V01-00 | `firmware/FTDX10_AFCPU_V0100.srec`（248,158 B）、`.bin`（25,207,552 B）、`.ihex`（232,620 B） |
| MAIN V01-15 | `firmware/FTDX10_MAIN_V0115.srec`（738,410 B）、`.bin`（259,668 B，地址 `0x0..0x3F653`）、`.ihex`（730,428 B） |
| DISPLAY V01-06 | `firmware/FTDX10_DISPLAY_V0106.bin`（3,373,532 B） |
| IFDSP V01-20 | `firmware/FTDX10_IFDSP_V0120.bin`（138,460 B） |
| SDR V01-00 | `firmware/FTDX10_SDR_V0100.rawhex`（4,392,218 B，原始 hex 文本）、`.bin`（2,196,108 B） |

> `.srec` / `.rawhex` 是**可回打包的数据段原文**；`.bin` 是装配/解码视图。
> 没有地址记录的载荷（DISPLAY / IFDSP）只输出 `.bin`，不伪造 HEX 基址。

## 脚本

| 文件 | 作用 |
|---|---|
| `yaesu_sfl_tool.py` | 统一编解码工具：`decode` / `encode` / `verify`（SHA-256 往返自检）。SFL 头不记录裸二进制载荷的加载地址，默认不为 BIN 生成 HEX，需显式 `--hex-base` |
| `verify/roundtrip_test.py` | 用 `firmware/` 里的可回打包明文重新打包，与 `original/firmware/` 原件逐字节比对（应全部一致） |
| `verify/modify_test.py` | 改动→重打包→校验自洽测试（并反证不重算头 CRC32 则失配） |

## 快速验证

```bash
# 在仓库根目录执行

# 解码全部固件到 firmware/
python3 decoded/yaesu_sfl_tool.py decode --all original/firmware -o decoded/firmware

# SHA-256 往返自检（5 个原厂 SFL）
python3 decoded/yaesu_sfl_tool.py verify --all original/firmware

# 回环与改动自洽测试
python3 decoded/verify/roundtrip_test.py
python3 decoded/verify/modify_test.py
```

> 相对本目录的命令示例：`python3 yaesu_sfl_tool.py decode ../original/firmware/FTDX10_MAIN_V0115.SFL -o firmware`。
