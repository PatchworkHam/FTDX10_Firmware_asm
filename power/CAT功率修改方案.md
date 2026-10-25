# FTDX10 用 CAT 命令改功率（功率等级 / 各频段 MAX POWER）

> 目标固件：`decoded/firmware/FTDX10_MAIN_V0115.bin`（MAIN，H8SX/1645 = `R5F61645N50FPV`）
> 命令来源：`../cat/隐藏CAT命令列表.md`（126 条命令表里 24 条未公开，`SP` 是其中权限最高的一条；取证过程见 `../cat/分析过程.md`）
> 功率链取证：`../hardware/可复现分析过程.md`、`功率解锁方案.md`
> 工具：`power/power_cat_optimized.py`（本方案专用）；通用读写见 `../cat/ftdx10_hidden_cat_gui.py`
> 所有**固件代码地址** = `decoded/firmware/FTDX10_MAIN_V0115.bin` 的**文件偏移**（本文档内即运行时地址）；FRAM 地址单独以 `FRAM[...]` 标注。

---

## 0. 结论速览

| 项 | 结论 |
|---|---|
| 能不能用 CAT 改功率 | **能**。用未公开命令 `SP` 的 `SPW`/`SPR` 直接读写外部 FRAM |
| 改的是什么 | 是外部非易失存储器 FRAM 的**配置字节 `FRAM[0x0007]`**（功率等级）与 **`FRAM[0x0335..0x0338]`/`FRAM[0x046A..0x046D]`**（各频段 RF POWER=`F` / MAX POWER=`AC`） |
| 与 hardware 方案 M 的关系 | **目标效果一致；实现与副作用不同**。方案 M 是打补丁强制那几处读到 `0x18`；本方案是把真正的配置字节改成 100W 机应有的值 |
| 需要刷固件吗 | **不需要**（不写 Flash、不动 SFL、不改短路点） |
| 可逆吗 | **可逆**。先读原值，随时 `restore` 写回 |
| 生效条件 | **档位** `FRAM[7]` 只在开机装载到 RAM `0xFF2007` → 必须整机断电重开；**RF POWER/MAX POWER**（`F`/`AC`）在每次 `SPW` 写后由 `0x7D4A → 0x7E2E` 立即重载，进入菜单/刷新即可见（断电重开必然一致） |
| 之后还要做什么 | 推荐直接用 `set-power 100 --apply`：档位、RF POWER（`F`）、MAX POWER（`AC`）一次写入并按档夹取。若只用 `set-class`：面板 RF POWER / `PC` 写 `F`，面板 MAX POWER / `EX` 写 `AC`，两处都要设到 100 |
| 主要风险 | 用的是未公开服务命令（仅静态逆向，未真机验证）；写错 `FRAM[7]` 的配置位（bit3-4）可能导致功率/开机异常 → **必须读-改-写，只动 bit3/4** |

---
### 0.1 功率修改的核心

| 项 | 结论 |
|---|---|
| 功率档 | 外部 FRAM `0x0007` 的 bit3-4（掩码 `0x18`） |
| 档位编码 | `0x18=100 W`、`0x00=50 W`、`0x08=20 W`、`0x10=10 W` |
| RF POWER `F` | 外部 FRAM `0x0335..0x0338`，顺序 HF/50M/70M/AM |
| MAX POWER `AC` | 外部 FRAM `0x046A..0x046D`，顺序 HF/50M/70M/AM |
| 有效上限 | `min(F, AC)` |
| 区间表 | 固件 `0x159F6`，4 频段 × 4 档，band-major |
| 档位默认值 | 固件 `0x810C`，按档写入的默认 F/AC |
| 生效时机 | 档位需整机断电重开；`SPW` 写 F/AC 后会调用重载路径，菜单刷新即可见 |

## 1. 为什么可行：功率参数到底存在哪

### 1.1 功率等级的完整链路（每一跳都有代码证据）

```
  外部 FRAM[0x0007]  ←── 出厂/维修写入（就是我们要改的地方）
        │  开机装载：0x7F8  jsr 0x7D04 → 0x8F0C  mov.w #0x6,r1 ; jsr 0xa4a8 (读 FRAM[6])
        │                                       0x7D1C  mov.b r2l,@0xFF2006
        ↓                                       0x7D22  mov.b r2h,@0xFF2007   ← ★
  RAM 0xFF2007（配置字节：bit0-2 TS / bit3-4 PWR / bit5 EXP / bit6-7 AUX）
        │
        ├─ 0x1502 / 0x153A  拆分：TS→0xFF98C7、PWR→0xFF98C8（并置 0xFF204A.7「TS 有效」）
        ├─ 0x1594C  RF POWER 菜单区间取档（表 0x159F6）
        ├─ 0x159CC  MAX POWER 菜单区间取档（表 0x159F6）
        ├─ 0x3BCA0  功率等级钳位按档取上限
        ├─ 0x3BB6E  20W 档时把 HF 硬限到 10W
        └─ 0x8112  设置初始化时按档写 RF POWER/MAX POWER（F/AC）默认值
        │
  保存回写：0x8F1E  mov.w #0x6,r1 ; 取 RAM 0xFF2006/0xFF2007 ; jsr 0xa528（写回 FRAM[6]/[7]）
```

配套的三个"旁证"，说明 `0x0006/0x0007` 就是这一对配置字节：

| 证据 | 地址 | 内容 |
|---|---|---|
| FRAM 魔数 | `0x8E86`/`0x8E9E` | 往 `r1=2`（或 `4`）写 `A5`,`5A` / `FF`,`FF`；开机 `0x8EB6`/`0x8EBC` 校验 `FRAM[2..3] == FRAM[4..5] == 0xA55A` |
| 设置版本 | `0x13C2/0x13C6` | `mov.w #0x9,r1` 后调用 FRAM 读原语读取 `FRAM[9]`，与固件 `0x3F650` 比较；不符 → `0x1570` 触发设置重初始化 |
| 菜单保存映射 | `0x159B4` | `sub.l #0xFFAABC,er1 ; add.l #0x300,er1 ; jsr 0xa528` ⇒ **RAM 镜像 `0xFFAABC+n` ↔ `FRAM[0x300+n]`** |

**比特含义与等级编码**

| 位 | 含义 |
|---|---|
| bit0-2 | TS（机型/规格）：固件认可 `{0,2,3,6}`；`1` 仅在档位≠10W 时认可；`{4,5,7}` 无效 |
| bit3-4 | **PWR 功率等级**：`0x18`=100W、`0x00`=50W、`0x08`=20W、`0x10`=10W |
| bit5 | EXP（`0x32B06` 等处 `btst #5`；由 `0xA38` 从 KR 跳线刷新，本方案不动它） |
| bit6-7 | AUX0/AUX1（无读取点，未使用） |

### 1.2 两个每频段功率值：RF POWER（F）/ MAX POWER（AC）

```
  FRAM[0x0335..0x0338] = HF / 50M / 70M / AM  F（RF POWER）    （出厂写入：0x826C→0x82A0）
  FRAM[0x046A..0x046D] = HF / 50M / 70M / AM  AC（MAX POWER）  （0x825C 写 0x46D）
        │  装载/重载（0x7E2E 例程内；开机与每次 SPW 写后都会调用）：
        │    0x7E40 → 0x8F7E  读 FRAM[0x300..0x3C7] (200 B) → RAM 0xFFAABC..0xFFAB83
        │    0x7E44 → 0x8FA6  读 FRAM[0x3C8..0x48F] (200 B) → RAM 0xFFAB84..0xFFAC4B
        ↓    （F 落在前半 0xFFAAF1..F4、AC 落在后半 0xFFAC26..29）
  RAM（有效发射功率 = min(RF POWER, MAX POWER)，固件 `0x3BBC6`）
```

- **RF POWER 写的是 F**：标准 CAT `PCnnn;` 的置值分支（跳转表入口 `0x2B4D6`）解析数值后，
  经 `0x1594C` 取当前频段与区间、`0x159B4` 把 1 字节写进 `0xFFAAF1..F4`
  （↔ FRAM[0x335..338]）。面板 RF POWER 菜单的 ±1 处理（`0x15920`）走同一条
  `0x1594C → 0x159B4` 路径。
- **MAX POWER 写的是 AC**：菜单 "HF/50M/70M/AM MAX POWER"（字符串在 `0x1DE2A..`）
  对应的处理函数 `0x20DB4/0x20DE8/0x20DFA/0x20E0C` 读写 `0xFFAC26..29`
  （↔ FRAM[0x46A..46D]），并按 `0x159F6` 的档位上限夹取。
- **两者独立**：出厂/设置重建时 `0x810C` 把 F、AC 写成同一档默认值；之后可以分别调整。
  50W 机通常两者都是 50；只把其中一个推到 100，另一个仍是 50，实际仍是 50W。
  这就是 `set-power` 两处都写、`set-rf`（旧名 `set-max`）只写 F 通常不够的原因。
- 设置块**保存**（RAM→FRAM）：`0x8F6A`（200 B，RAM `0xFFAABC`→FRAM`[0x300]`）、
  `0x8F92`（200 B，RAM `0xFFAB84`→FRAM`[0x3C8]`）；单个菜单项的改动先写 RAM，
  再由对应的保存例程写回 FRAM。
- **出厂默认值**：flash `0x82A6` 的 400 字节整块写进 FRAM`[0x300]`（`0x8074`），紧接着
  `0x810C` 按档覆盖 F/AC、`0x80E4` 写机型/版本相关字节。这条链在**设置重初始化**例程 `0x7E8E`
  里（`0x7EC2`）——它是"整块重建设置"的道路，与我们要改的单个字节互不冲突。
- 因为持久化由固件负责，**面板 RF POWER / `PC` 等价于写 F 的两字节；面板 MAX POWER / `EX`
  等价于写 AC 的两字节**，只是多一道"按档夹取"的校验。

### 1.3 档位 → 区间表（`0x159F6`，固件中 32 字节）

`0x1594C`/`0x159CC` 把配置字节 `and.b #0x18` 取档 → 档索引 `0x18→0、0x00→1、0x08→2、其余→3`，
再加频段偏移（`0/4/8/12`），得到下标 `档 + 4×频段`，查表得 `(最小值, 最大值)`：

```
0x159F6: 0564 0532 050a 050a  0564 0532 0514 050a
          0532 0532 0514 050a  0519 0519 0514 050a
```

| 档 | HF | 50M | 70M | AM |
|---|---|---|---|---|
| **100W（0x18）** | **100** | **100** | 50 | 25 |
| 50W（0x00） | 50 | 50 | 50 | 25 |
| 20W（0x08） | 10 | 20 | 20 | 20 |
| 10W（0x10） | 10 | 10 | 10 | 10 |

> **交叉验证**：CAT 手册（`../original/manuals/dx10-cat.pdf`）里 `EX` 命令的 MAX POWER 允许区间写的正是
> **HF 5–100 / 50M 5–100 / 70M 5–50 / AM 5–25** —— 与 100W 档这一行**完全一致**，
> 说明手册描述的是 100W 机，而 50W 机的差别就是这一个配置字节。
>
> 同一张表也说明：**`EX` 命令能设的 MAX POWER 被档位夹住**，
> 等级还是 50W 时 HF 最多只能设到 50 —— 所以必须**先改等级**。

按档写默认值的例程 `0x810C`（`and.b #0x18` @ `0x8112`）在固件中为：

| 档 | 写入 F（`0xFFAAF1..F4`） | 写入 AC（`0xFFAC26..29`） |
|---|---|---|
| 100W | `64 64 32 19` | `64 64 32 19` |
| 50W | `32 32 32 19` | `32 32 32 19` |
| 20W | `0a 14 14 14` | `0a 14 14 14` |
| 10W | `0a 0a 0a 0a` | `0a 0a 0a 0a` |

---

## 2. 地址速查表（外部 FRAM：MB85RS256A，256 Kbit = 32 KB，`0x0000–0x7FFF`）

| FRAM 地址 | 内容 | 说明 |
|---|---|---|
| `0x0002–0x0005` | 魔数 `A5 5A`（两份） | **不要改**（开机校验） |
| `0x0006` | 配置字节对的**高字节** | 只读出来原样写回，不要单独改 |
| `0x0007` | **配置字节**（TS/PWR/EXP/AUX） | **本方案主目标** |
| `0x0008` | 设置"已初始化"标志（`0xA5`） | **不要改** |
| `0x0009` | 设置版本（与固件 `0x3F650` 比） | **不要改**（改错触发设置重初始化 `0x1570`） |
| `0x0300–0x048F` | 400 字节设置块 | ↕ RAM `0xFFAABC..0xFFAC4B` |
| `0x0335–0x0338` | **F：RF POWER** HF/50M/70M/AM | 面板 RF POWER / `PC` 命令写这里 |
| `0x046A–0x046D` | **AC：MAX POWER** HF/50M/70M/AM | 面板菜单 / `EX` 写这里；有效值 = `min(F, AC)` |
| `0x02F0–0x02F4` | 出厂参数区 | 不要动（`JP` 命令会连带搬运这块） |
| `0x03FE` | 发射超时（TOT）分钟数（低 5 位，默认 `0x0A`=10） | 不要动（与功率无关） |

> 注意：`0x0007`（等级）属于配置字节对，**不在** 400 字节设置块内；`0x0335..0338`（RF POWER=F）与
> `0x046A..046D`（MAX POWER=AC）在设置块内。两条路径分开：面板 RF POWER / `PC` 写 F，
> 面板 MAX POWER / `EX` 写 AC；有效值 = `min(F, AC)`。
> 这两处写入本身不碰 `0x0008` 初始化标志与 `0x0009` 设置版本；但开机固件仍可能按自己的版本比较逻辑重建配置（见下）。

> **设置版本值尚未定论**：当前 `decoded/firmware/FTDX10_MAIN_V0115.bin` 的 `0x3F650` 为 `0x00`；
> 真机 `FRAM[9]` 的实际值尚未在真机上确认。脚本只记录 `FRAM[9]`，不把 `0x00`
> 或任何推测值断言成期望值，也不做版本兼容判断。
>
> 静态代码还显示：`0x13C2/0x13C6` 读取 `FRAM[9]` 后，`0x13CA` 比较失败时先调 `0x1570`，随后 `0x13D8/0x13E2` 会把固件内
> `0x3F650`（`0x00`）写回 `FRAM[9]`。因此脚本自身不写 `FRAM[9]`，但开机固件可能改写它。

---

## 3. 用的命令：`SP` 的 `SPW` / `SPR`

来源：`../cat/分析过程.md` §3.2 / `../cat/隐藏CAT命令列表.md` §7（已按这两份文档复核 `0x2CF86` 分派与
`0x2D016`/`0x2D076` 两个处理函数，帧格式一致）。

- `SP` 是**二进制子协议**：`SP` 之后的字节按**原始二进制**解释（**不是** ASCII 十六进制），
  以 `;`（`0x3B`）结尾。**以 `SP` 开头的帧不做大小写转换**，所以 `SPW`≠`SPw`。
- 底层就是固件保存设置用的原语：`0xA4A8`（读 1 字节并 `addr++`）、`0xA528`（写 1 字节并 `addr++`）。
- **无门控、无需口令**（`SPN`/`SPA*` 才需要 `0xFF2020` bit7）。

| 子命令 | 帧（除结尾 `;` 外，全为原始字节） | 作用 | 回包 |
|---|---|---|---|
| `SPR` | `'S' 'P' 'R' ah al chk` | 读 `addr=ah<<8\|al` 与 `addr+1` 两字节 | 9 字节 `SPR ah al d0 d1 chk ;` |
| `SPW` | `'S' 'P' 'W' ah al d0 d1 chk` | 写 `d0→addr`、`d1→addr+1`（`addr` 须 `< 0x8000`） | `A;` |

`chk = ('S' + 'P' + 其后所有字节) & 0xFF`（**原始字节和**，含 `d0 d1`；`SPW` 请求即 7 字节）。
回包的 `chk` 则覆盖 **7 字节**：`'S'+'P'+'R'+ah+al+d0+d1`（固件 `0x2D0C2` 对 `0xFF8A30..36` 求和，
存 `0xFF8A37`），**不是** 5 字节——只按 5 字节校验会导致每次读都报"校验错"。

### 3.1 本方案用到的帧（可直接照抄）

| 用途 | 帧字节 |
|---|---|
| 读配置字节对 `FRAM[6..7]` | `53 50 52 00 06 FB 3B` |
| 读 F（RF POWER）HF/50M | `53 50 52 03 35 2D 3B` |
| 读 F（RF POWER）70M/AM | `53 50 52 03 37 2F 3B` |
| 读 AC（MAX POWER）HF/50M | `53 50 52 04 6A 63 3B` |
| 读 AC（MAX POWER）70M/AM | `53 50 52 04 6C 65 3B` |
| 写配置字节 `0x0006=0x00, 0x0007=0x18`（功率档改为 100 W） | `53 50 57 00 06 00 18 18 3B` |
| 写 F `HF=100, 50M=100`（`0x0335`） | `53 50 57 03 35 64 64 FA 3B` |
| 写 F `70M=50, AM=25`（`0x0337`） | `53 50 57 03 37 32 19 7F 3B` |

校验手算（`SPW 0x0335 = 0x64,0x64`）：`0x53+0x50+0x57+0x03+0x35+0x64+0x64`
`= 83+80+87+3+53+100+100 = 506`，`506 & 0xFF = 0xFA`。

### 3.2 三个必须知道的坑

1. **`0x3B` 陷阱**：帧内任何字节（含校验）等于 `0x3B` 会被设备当成帧尾 `;`，帧被截断 → 回 `?;`。
   本方案用到的地址 `0x0006/0x0007/0x0335..38/0x046A..6D` 的地址字节都不是 `0x3B`；
   数据字节通常是 `0x00/0x64/0x32/0x19` 之类，也安全。脚本会自动检测并在必要时换对齐。
2. **档位要断电重开，RF POWER/MAX POWER 会即时重载**：`FRAM` 是即时写入；档位 RAM `0xFF2007`
   只在开机 `0x7D04 → 0x8F0C` 装载，所以**改档必须整机断电重开**
   （不要用 `ZZPE001001;` 代替——它会写 `0xFF2002=1` 并经过 `0x1570`→`0x8E50`→`0x7EA2`→`0x8074`，用默认表重写 400B 用户设置并应用 JP1006，等价于 ALL RESET）。
   而 400 字节设置块（RF POWER=F 与 MAX POWER=AC 都在其中）在每次 `SPW` 写后都会由
   `0x7D4A → 0x7E2E → 0x8F7E/0x8FA6` 从 FRAM 重载到 RAM，所以 **两者写完后
   进入菜单/刷新即可看到**。为了档位与两者一次性一致，推荐写完直接断电重开。
3. **只读-改-写**：绝不要把整个字节写死成 `0x18`。原字节的低 3 位是 TS、bit5 是 EXP，
   写错可能开不了机。正确做法：`new = (old & ~0x18) | 0x18`。

---

## 4. 操作流程

### 4.1 先只读巡检 + 两台机对照（零风险，强烈建议）

如果手上有 50W 机和 100W 机，**先各读一次做对照**，这是风险最低、也最有说服力的路径：

```bash
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 dump A_50W.json  --note "50W机"
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 dump B_100W.json --note "100W机"
python3 power/power_cat_optimized.py diff A_50W.json B_100W.json
```

- 若输出显示"**差别只在 PWR 档：0x00 vs 0x18**"，即证实本方案的判断；
  直接 `restore B_100W.json --apply` 就是「照抄 100W 机」——连校验都不用自己算。
- 若 100W 机在 `MAX POWER` / `AC` 上还有别的差异，`diff` 会一起列出来，照抄即可。

### 4.2 改功率等级（核心一步）

```bash
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 read                 # 先看现状
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 set-class 100        # dry-run：只打印
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 set-class 100 --apply # 真写（读-改-写 + 回读校验）
```

脚本会：读 `FRAM[6..7]` → `new = (old & ~0x18) | 0x18` → 写回两字节 → 回读比对。
**然后整机断电重开。**

> `set-class` 只改档位，不动 RF POWER/MAX POWER；所以改完后各频段实际功率仍取决于原来的两者。
> **想一步到位请用 §4.3 的 `set-power 100 --apply`**：它会一次写入档位、RF POWER、MAX POWER，
> 并按目标档位区间夹取。

### 4.3 设置 RF POWER 与 MAX POWER（任选一条路径）

| 方式 | 命令 | 特点 |
|---|---|---|
| **一键（推荐）** | `set-power 100 --apply` | 自动选档 + 同步 RF POWER/MAX POWER + 按档夹取；HF/50M=100、70M=50、AM=25 |
| 面板 RF POWER | 面板 RF POWER 旋钮（或标准 CAT `PCnnn;`） | **写的是 F**；由固件按档夹取；每次只改当前频段 |
| 面板 MAX POWER | 菜单 → MAX POWER（HF/50M/70M/AM） | **写的是 AC**；最稳妥，由固件自己保存并夹取 |
| 文档命令 `EX` | `EX040401100;`（HF=100）等，见 §5 | **写的是 AC**（MAX POWER）；需先确认菜单编号 |
| 本脚本 | `set-rf hf=100 50m=100 70m=50 am=25 --apply` | 只写 **RF POWER（F）**；不动 MAX POWER（AC）；单独用通常不会提高实际功率 |

> 关于"改完等级后功率值会不会自动变成 100"：不会，但原因不是"重建例程不会跑"。
> 按档写 F/AC 默认值的 `0x810C` 位于整块重建链 `0x7EA2 → 0x7EA6 → … → 0x7EC2 → 0x8074 → 0x8086 → 0x810C` 上；
> 由设置版本不符触发的 `0x1570 → 0x8E50` 会 `bra 0x7EA2` **进入同一条链**
> （`0x8E50` 先把 FRAM[4..5] 写 `0xFF`，`0xA38` 在 RAM 副本里置 EXP 位，随后 `0x7EA2` 处由 `0x8F1E` 用 RAM 值回写 FRAM[6..7]），
> 因此它**会**按档重写 F/AC 默认值（100W 档 = 100/100/50/25）。
> 改档之所以不会自动改功率值，是因为写 `FRAM[7]` 不改变设置版本字节 `FRAM[9]`，
> 开机时 `0x13CA` 与固件内 `0x3F650` 比较通过，根本不会进 `0x1570`。
> 所以：**只用 `set-class` 时仍要手工设 RF POWER 和 MAX POWER**：
> 面板 RF POWER / `PC` 写 F，面板 MAX POWER / `EX` 写 AC；
> 想一次完成就用 `set-power`（档位、RF POWER、MAX POWER 一并写入）。
> 特别提醒：50W 机通常 **F 和 AC 都是 50**；只把 MAX POWER 菜单推到 100，
> 有效值仍只有 50。要真正到 100，必须把两处都抬到 100 —— 这正是 `set-power` 做的事。
> 反过来说：**固件升级等导致版本字节不符时，两者会被重置为该档默认值**——这是本结论的例外。

### 4.4 复核与回滚

```bash
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 read          # 等级应为 100W（改档后需先断电重开）
# 面板：菜单里 HF/50M 的 MAX POWER 区间应从 5-50 变成 5-100（可观察证据）
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 restore A_50W.json --apply   # 回滚
```

---

## 5. 文档命令 `EX`（设置菜单项，包括 MAX POWER）

手册 `EX` 语法：`EX P1P1 P2P2 P3P3 P4 ;`（P1 01-05、P2 01-07、P3 01-23、P4 见手册 Table 2）。
手册 Table 2 中（按表格列推断）`P1=04 / P2=04 (TX GENERAL)` 下有：

| P3 | 项 | 区间 |
|---|---|---|
| 01 | HF MAX POWER | 5 ~ 100 |
| 02 | 50M MAX POWER | 5 ~ 100 |
| 03 | 70M MAX POWER | 5 ~ 50 |
| 04 | AM MAX POWER | 5 ~ 25 |

```bash
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 ex-read --p1 4 --p2 4 --p3 1   # 读 HF MAX POWER
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 ex-set 100 --p1 4 --p2 4 --p3 1 --apply
```

- 若 `ex-read` 回 `?;`，说明你这台机器的菜单编号不同：查手册 Table 2（MENU Chart）核对
  P1/P2/P3，或干脆用面板菜单（结果一样）。
- **`EX` 的这几项写的是 AC（MAX POWER）**，并且会被档位区间夹取，所以顺序必须是：
  先改等级 → 重启 → 再用 `EX`/菜单设 100。此时 F（RF POWER）若仍是 50W 档的 50，
  有效值仍只有 50；要同时抬高 F，用 `set-power`（或面板 RF POWER / `PCnnn;`）。
- **RF POWER（F）不在 `EX` 的这几项里**：标准 CAT 的 `PCnnn;` 设置当前频段的 RF POWER，
  面板 RF POWER 旋钮同理。本工具的 `set-rf`/`set-power` 用 SPW 直接写四个频段。
- 若用 `set-power`，档位与 RF POWER/MAX POWER 已一起写，不需要再走 `EX`。

---

## 6. 脚本 `power/power_cat_optimized.py`

> 完整用法（连接、命令详解、快照 v3、固定布局、故障排查）见
> [`power_cat_使用说明.md`](power_cat_使用说明.md)。

```bash
python3 power/power_cat_optimized.py list                          # 地址表 + 档位区间表
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 read [--json]  # 一键读取
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 cap            # 读能力/区间
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 dump A.json --note "50W机"
python3 power/power_cat_optimized.py diff A.json B.json
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 set-power 100 [--band am=25] [--ac auto|keep|N] [--apply]
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 set-class {100|50|20|10} [--apply]
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 set-rf hf=100 50m=100 70m=50 am=25 [--apply]
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 restore A.json [--apply]
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 ex-read [--p1 4 --p2 4 --p3 1]
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 ex-set 100 [--p1 4 --p2 4 --p3 1] [--apply]
```

- 串口：USB 虚拟串口的 **Enhanced COM Port**（或后板 RS-232C），8N1 无流控；
  波特率必须等于菜单 **08 CAT RATE**（出厂默认 9600，用 `-b` 指定）。
- **默认 dry-run**，只打印计划/将发送的字节；加 `--apply` 才真正写。
- 所有写入都是**读-改-写 + 写后回读校验**，并自动避开 `0x3B`。
- `set-power` = 自动选档 + 按档夹取 + 同步 RF POWER/MAX POWER；目标值 =
  `clamp(请求值, 该档下限, min(机型上限, 目标档位该频段上限))`。
- 标准 CAT `PCnnn;` 也能设 RF POWER（F），但一次只改当前频段；本工具用 SPW 批量写四个频段。
- `dump` 出的 JSON 同时是**回滚备份**（含配置字节、RF POWER=`max_power` 字段、MAX POWER=`ac` 字段、区间）。
- `restore` 会先校验快照档位与区间，越界拒绝写入（不做"边写边夹"）。
- 不检查 `FRAM[9]` 设置版本；该字节只展示、不写入。
- 无 Profile 机制；FTDX10 的地址/档位/区间集中在脚本头部。其它机型不要直接套用。
- 依赖：`pyserial`（`pip install pyserial`）。

与 GUI 的分工：`../cat/ftdx10_hidden_cat_gui.py` 是**通用**隐藏命令工具（整片 FRAM 备份/恢复、
PE 校准、终端等）；本脚本只做**功率语义**——解码/编码、两台机对照、功率参数的一键回滚。

---

## 7. 风险、局限与未验证项

1. **来源是静态逆向**：`SP` 属于未公开服务命令，本文档与脚本**未在真机上执行过写操作**。
   请先用只读命令（`read` / `dump` / `ex-read`）验证链路，再考虑写入。
2. **写坏 `FRAM[7]` 的后果**：配置位（尤其 bit3-4）异常可能导致功率行为异常，极端情况下无法进入正常工作状态。缓解：
   ① 一律读-改-写；② 先 `dump` 备份；③ 出错立即 `restore`。
   （若彻底写坏，最终保底仍是重刷官方固件——见 `../hardware/固件升级.md`。）
3. **`0x3B` 截断**：见 §3.2；脚本会检测并尝试换对齐，实在避不开会报错而不写。
4. **等级 ≠ 实际功率**：软件上限只是必要条件。有效值 = `min(RF POWER, MAX POWER)`
   （F/AC），且最终受**功放硬件**限制。
   本方案不改校准值（`PE`）与出厂参数区（`0x02F0`）。
5. **未验证项**：① 50W 机的 `FRAM[7]` 出厂值是否就是 `0x00`（可能带其它 TS 位）；
   ② 100W 机与 50W 机在 `FRAM[0x0335..]`、`0x046A..` 上是否另有差异（用 `diff` 一看便知）；
   ③ `EX` 菜单编号（P1/P2/P3）在 50W 机上是否与手册一致。
6. **`JP0010<8hex>` 也能写配置字节**（`0x27F4A`：写 RAM `0xFF2006/07` + `0x8F1E` 存 FRAM），
   但它还会连带把 `0x2F0`×5 出厂参数区整体搬运（`0xA5C2`），**风险更高，不建议**。

---

## 附：一行命令版（照抄）

```bash
# ① 备份（只读）
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 dump my_50W.json --note "原始50W"

# ② 一键：档位 + RF POWER（F）+ MAX POWER（AC），先 dry-run 看计划
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 set-power 100
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 set-power 100 --apply

# ③ 整机断电重开（档位生效），然后复核
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 read
#    应显示 100W，HF/50M=100、70M=50、AM=25（RF POWER 与 MAX POWER 都是）

# 回滚（同样断电重开）
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 restore my_50W.json --apply
```

> 旧的分步流程 `set-class 100 --apply` → 重启 → `set-rf ... --apply` → 再重启 仍然可用，
> 但要多一次断电，而且 `set-rf`（旧名 `set-max`）只写 F；面板 RF POWER 也只写 F，
> 面板 MAX POWER/`EX` 只写 AC。`set-power` 一次写全，优先用它。
