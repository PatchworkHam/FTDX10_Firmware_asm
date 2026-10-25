# power_cat_optimized.py 使用说明

> 适用机型：Yaesu **FTDX10**（MAIN V01-15，`decoded/firmware/FTDX10_MAIN_V0115.bin`）
> 适用范围：**按 FTDX10 的静态逆向结果编写**；未做真机写入验证，也未验证其它机型。
> 配套分析：[`CAT功率修改方案.md`](CAT功率修改方案.md)、[`../cat/隐藏CAT命令列表.md`](../cat/隐藏CAT命令列表.md)
> 默认 **dry-run**；只有加 `--apply` 才会写设备。

---

## 1. 一分钟上手

```bash
# 在仓库根目录执行
pip install pyserial

python3 power/power_cat_optimized.py list              # 查看 FRAM 地址/档位区间

python3 power/power_cat_optimized.py -p /dev/ttyUSB0 read
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 dump A_50W.json --note "原机50W"

python3 power/power_cat_optimized.py -p /dev/ttyUSB0 set-power 100        # 先 dry-run
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 set-power 100 --apply
# 断电重开（功率档需要重启；F/AC 写入后会立即重载）
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 read
```

照抄另一台 100W 机：

```bash
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 dump B_100W.json --note "100W机"
python3 power/power_cat_optimized.py diff A_50W.json B_100W.json
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 restore B_100W.json --apply
```

回滚：

```bash
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 restore A_50W.json --apply
```

---

## 2. 它做什么、不做什么

### 2.1 做什么

| 能力 | 命令 |
|---|---|
| 一键读取全部相关 FRAM 字段并解释 | `read` / `cap` |
| 一键设功率：自动选档 + 按档夹取 + 同步 `F`/`AC` | `set-power` |
| 只改功率档（配置字节 bit3/4） | `set-class` |
| 只改 `F`（RF POWER），不动 `AC`（MAX POWER） | `set-rf`（旧名 `set-max`） |
| 快照备份 / 两台机对照 / 精确回滚 | `dump` / `diff` / `restore` |
| 直接调用文档命令 `EX` 读写菜单项 | `ex-read` / `ex-set` |
| 打印固定布局、档位区间、默认值 | `list` |

它只会写这些字节：

```text
FRAM[0x0006]          配置字节对高字节（原样写回）
FRAM[0x0007]          TS(bit0-2) / PWR(bit3-4) / EXP(bit5) / AUX(bit6-7)
FRAM[0x0335..0x0338]  F ：RF POWER（各频段发射功率；面板 RF POWER / PC 写这里）
FRAM[0x046A..0x046D]  AC：MAX POWER（各频段上限；面板菜单 / EX 写这里）
                     有效上限 = min(F, AC)
```

**绝不写**：

- 魔数 `FRAM[0x0002..0x0005]`
- 初始化标志 `FRAM[0x0008]`
- 设置版本 `FRAM[0x0009]`
- 400 字节设置块的其它内容、Flash、SFL

---

## 3. 安全边界

1. **先备份**：第一次连接先 `dump`，JSON 存两份。
2. **默认不写**：所有写命令默认 dry-run；`--apply` 才发写帧。
3. **只改档位位**：`set-power` / `set-class` 用 `(old & ~0x18) | 新档`，TS/EXP/AUX 原样保留。
4. **按档夹取**：每频段 `F`/`AC` 都夹到 `[档位下限, min(100, 档位该频段上限)]`。
   越界会打印 `★` 警告并夹取。
5. **写后回读**：每笔写入后重新读取比对，不一致立即报错。
6. **避开 `0x3B`**：帧内出现 `0x3B` 会被设备当帧尾；工具会自动换对齐/扩帧，
   仍无法避开时中止。
7. **档位要断电重开**：`FRAM[0x0007]` 只在开机时装载到 RAM。
8. **改档后先别动菜单**：重启前不要进菜单改功率，避免旧档位区间把显示值夹回去。
9. **TS/EXP/AUX 异常时**：工具只警告，不擅自修改；先只读核对。
10. **风险自负**：这是未公开服务命令 + 直接改 FRAM，风险高于面板菜单。

---

## 4. 连接与串口

| 项 | 要求 |
|---|---|
| 串口 | USB 虚拟串口的 **Enhanced COM Port**（或后板 RS-232C） |
| 参数 | 8N1、无流控；波特率等于菜单 **08 CAT RATE** |
| 默认 | `-b 9600`（可选 4800/9600/19200/38400） |
| 权限 | Linux 下加入 `dialout` 组，或临时 `sudo` |
| Python | 3.8+；仅依赖 `pyserial` |

```bash
ls /dev/ttyUSB*                     # Enhanced 通常是第二个（ttyUSB1）
sudo usermod -aG dialout "$USER"    # 重新登录后生效
python3 power/power_cat_optimized.py -p /dev/ttyUSB1 read
```

> 无回包时：确认 CAT RATE、换另一个 COM 口、检查 CAT 是否被菜单禁用、
> 以及是否有其它软件占用串口。

---

## 5. 命令参考

### 5.1 全局选项

| 选项 | 说明 |
|---|---|
| `-p, --port PORT` | 串口；`list` / `diff` 可不填 |
| `-b, --baud N` | 波特率，默认 9600 |
| `--timeout S` | 串口超时，默认 0.8 s |
| `-q, --quiet` | 不打印收发原始字节 |
| `--apply` | 真正写入；不加则 dry-run |

选项可写在子命令前或后：

```bash
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 set-power 100 --apply
python3 power/power_cat_optimized.py --apply -p /dev/ttyUSB0 set-power 100
```

### 5.2 `list`

```bash
python3 power/power_cat_optimized.py list
```

`list` 打印 FTDX10 固定 FRAM 表、四档区间与默认值。

### 5.3 `read` / `cap`

```bash
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 read
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 read --json
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 cap
```

`read` 输出示例：

```text
机型：Yaesu FTDX10 (MAIN V01-15)（静态逆向，未真机验证；最高 100 W）
FRAM 魔数 [OK] 0x0002=a5 5a, 0x0004=a5 5a   初始化标志 0xA5 [OK]   设置版本 FRAM[0x0009]=0（只读，不判断）
配置字节 FRAM[0x0006]=0x00  FRAM[0x0007]=0x00  ->  50W（0x00）, TS=0, EXP=0, AUX=0

频段    F(0x335+)  AC(0x46A+)  有效上限   本档允许区间
HF      50         50          50         5-50
50M     50         50          50         5-50
70M     50         50          50         5-50
AM      25         25          25         5-25

注：有效上限 = min(F, AC)；F 是面板 RF POWER（发射功率设定），AC 是面板 MAX POWER（菜单 04-04 写它）。
```

- **有效上限 = `min(F, AC)`**。两者是独立的每频段设置：
  `F`（0x335+）是 RF POWER，`AC`（0x46A+）是 MAX POWER。
  只写 `F` 而 `AC` 仍为 50，实际还是 50W；反之亦然。
- `cap` 在 `read` 基础上多打印“当前档位最高多少 / 可升级到多少”。
- `FRAM[0x0009]` 只展示，不比较、不写入。

### 5.4 `dump` / `diff`

```bash
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 dump A.json --note "50W原机"
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 dump B.json --note "100W机"
python3 power/power_cat_optimized.py diff A.json B.json
```

- `dump` 生成 v3 快照，包含配置字节、`F`、`AC`、区间、魔数、初始化标志和 `FRAM[9]` 读数。
- `max_power` 是历史字段名，实际存 **RF POWER（F）**；`ac` 存 **MAX POWER（AC）**。
- `diff` 只比较功率相关字段，并区分“只差档位”与“TS/EXP/AUX 也不同”。

### 5.5 `set-power`（核心）

```bash
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 set-power 100            # dry-run
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 set-power 100 --apply
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 set-power 100 --band am=25 --apply
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 set-power 100 --ac keep --apply
```

它一次完成：

1. 读配置字节，解码当前档位；
2. 选择**能覆盖目标瓦数的最小档位**（例如 50W 机上设 70W → 100W 档）；
   **不会自动降档**：当前档位已经 ≥ 目标时保持当前档位；
   确需降档请显式 `set-class`；
3. 每频段计算
   `新 F = clamp(请求值/该频段覆写值, 5, min(目标档位该频段上限, 100))`；
4. 默认 `--ac auto`：**新 AC 跟随新 F**，保证菜单上限与 F 一致；
5. 检查档位已知、每个 F/AC 都在区间内；
6. dry-run 打印计划；`--apply` 后写配置字节、`F`、`AC`，每笔回读校验；
7. 档位变化时提示断电重开。

`--ac` 三种取值（`AC` = MAX POWER）：

| 值 | 行为 |
|---|---|
| `auto`（默认） | `新 AC = 新 F`（每频段夹取后），推荐 |
| `keep` | 不动 AC；若 AC < F，有效上限仍受 AC 限制（会提示） |
| 数字（如 `100`） | 所有频段 AC 设为该值（夹取后） |

`--band` 可重复，频段名支持 `hf` / `50m` / `70m` / `am`
（也接受旧写法 `m50` / `m70`）。

**50W 原机执行 `set-power 100` 的夹取结果**：

| 频段 | 当前 F | 当前 AC | 新 F | 新 AC | 100W 档区间 |
|---|---|---|---|---|---|
| HF | 50 | 50 | 100 | 100 | 5-100 |
| 50M | 50 | 50 | 100 | 100 | 5-100 |
| 70M | 50 | 50 | 50 | 50 | 5-50 |
| AM | 25 | 25 | 25 | 25 | 5-25 |

请求 100W 不会把 70M 写成 100，而是按固件表夹到 50W（AM 25W）。

### 5.6 `set-class` / `set-rf`

```bash
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 set-class 100 --apply
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 set-rf hf=100 50m=100 70m=50 am=25 --apply
```

- `set-class` 只改配置字节 bit3/4；不会改 F/AC。
  如果现有值超出新档区间，会警告并建议改用 `set-power`。
- `set-rf`（旧名 `set-max`）只改 **F**，按当前档位夹取；不会动 AC。
  若 F > AC，会提示有效值仍受 AC 限制；要提高实际功率请用 `set-power`。
- `set-class` 需断电重开；`set-rf` 写 F 后即时重载（断电重开更稳）。

### 5.7 `restore`

```bash
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 restore A.json           # dry-run
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 restore A.json --apply
```

- 写前校验：快照档位必须已知；每个 F/AC 必须落在**该快照档位**的区间内。
  越界直接拒绝，不会边写边夹。
- 只写配置字节、`F`、`AC`；魔数、初始化标志、`FRAM[9]` 一律不碰。
- 快照必须是当前 v3 格式且机型为 `ftdx10`；旧格式或未知机型拒绝。

### 5.8 `ex-read` / `ex-set`

```bash
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 ex-read --p1 4 --p2 4 --p3 1
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 ex-set 100 --p1 4 --p2 4 --p3 1 --apply
```

- `EX` 写的是 **MAX POWER（AC）**，由固件自己保存，并按当前档位夹取；
  等级仍是 50W 时 HF 最多只能设到 50。
- F 由面板 RF POWER 旋钮或标准 CAT `PCnnn;` 设置；本工具的 `set-rf`/`set-power`
  用 SPW 直接写四个频段。
- 不同固件/版本的菜单编号可能不同；`ex-read` 回 `?;` 就查手册或改用面板。

### 5.9 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功（含 dry-run） |
| 2 | 参数/频段/端口未指定等用法错误 |
| 3 | 校验失败、拒绝写入、回读不一致、CAT 协议错误 |
| 4 | 串口打开/读写失败 |

---

## 6. 数值模型

### 6.1 FRAM 字段

| 地址 | 含义 | 备注 |
|---|---|---|
| `0x0002–0x0005` | 魔数 `A5 5A` 两份 | 开机校验，不要改 |
| `0x0006` | 配置字节对高字节 | 原样写回 |
| `0x0007` | TS / PWR / EXP / AUX | `PWR=bit3-4`，掩码 `0x18` |
| `0x0008` | 初始化标志 `0xA5` | 不要改 |
| `0x0009` | 设置版本 | **只读展示；不检查、不写入** |
| `0x0335–0x0338` | `F`：RF POWER | 面板 RF POWER / `PC` 写它 |
| `0x046A–0x046D` | `AC`：MAX POWER | 面板菜单 / `EX` 写它；有效值 = `min(F, AC)` |

档位编码（配置字节 bit3-4）：

| 值 | 档位 |
|---|---|
| `0x18` | 100 W |
| `0x00` | 50 W |
| `0x08` | 20 W |
| `0x10` | 10 W |

### 6.2 档位区间（固件 `0x159F6`）

| 档位 | HF | 50M | 70M | AM |
|---|---|---|---|---|
| 100W（0x18） | 5-100 | 5-100 | 5-50 | 5-25 |
| 50W（0x00） | 5-50 | 5-50 | 5-50 | 5-25 |
| 20W（0x08） | 5-10 | 5-20 | 5-20 | 5-20 |
| 10W（0x10） | 5-10 | 5-10 | 5-10 | 5-10 |

设置重建时按档写入的默认值（固件 `0x810C`）：

| 档位 | 默认 F / AC |
|---|---|
| 100W | 100 / 100 / 50 / 25 |
| 50W | 50 / 50 / 50 / 25 |
| 20W | 10 / 20 / 20 / 20 |
| 10W | 10 / 10 / 10 / 10 |

> 出厂/重建时 F 与 AC 写成同一个默认值。之后：
> **RF POWER**（F）由面板 RF POWER / 标准 CAT `PC` 改；
> **MAX POWER**（AC）由面板 MAX POWER 菜单 / `EX` 改。
> 只把其中一个推到 100，另一个仍是 50，实际还是 50W。

### 6.3 生效时机

- **档位** `FRAM[0x0007]`：开机时读入 RAM，之后不再从 FRAM 重读 → **必须断电重开**。
- **F / AC 所在的设置块**：SPW 写完后固件会重新读入 RAM，菜单刷新即可看到；
  断电重开则必然一致。
- 推荐流程：`set-power ... --apply` → **立即断电重开** → `read` 复核。

### 6.4 为什么不会被固件夹回去

- 写入前用固件区间表校验；
- 档位位读-改-写，TS/EXP/AUX 不变；
- 目标值不超过 FTDX10 上限 100W，也不超过目标档位区间；
- 本工具不写魔数、初始化标志、FRAM[9]；
- 每笔写后回读，SPR 回包按 7 字节 `chk` 校验。

> 工具保证“写入的是固件在该档位下认可的值”，但不保证硬件一定能输出。
> 硬件是否支持 100W，取决于末级功放/驱动是否与 100W 机相同。

---

## 7. 快照 JSON（v3）

```json
{
  "format": "yaesu-power-cat",
  "format_version": 3,
  "model": "ftdx10",
  "model_name": "Yaesu FTDX10 (MAIN V01-15)",
  "verification_basis": "static-reverse-engineering",
  "hardware_verified": false,
  "model_max_w": 100,
  "created": "2026-10-20T16:30:00",
  "note": "原机50W",
  "config_pair": {"0x0006": 0, "0x0007": 0},
  "config_byte": 0,
  "class_bits": 0,
  "class": "50W",
  "class_known": true,
  "ts": 0,
  "ts_valid": true,
  "exp": 0,
  "aux": 0,
  "max_power": {"HF": 50, "50M": 50, "70M": 50, "AM": 25},
  "ac":        {"HF": 50, "50M": 50, "70M": 50, "AM": 25},
  "effective": {"HF": 50, "50M": 50, "70M": 50, "AM": 25},
  "ranges":    {"HF": [5, 50], "50M": [5, 50], "70M": [5, 50], "AM": [5, 25]},
  "magic": ["a5 5a", "a5 5a"],
  "magic_addrs": [2, 4],
  "magic_ok": true,
  "init_flag": 165,
  "init_ok": true,
  "fram_version": 0,
  "fram_version_note": "只读记录，不检查、不写入"
}
```

- `restore` 要求快照含 `config_byte`、`config_pair`、`max_power`、`ac`；写入时使用
  `config_pair` 的高字节 + `config_byte`、`max_power`、`ac`，其余字段用于展示/审计。
- `max_power` = F（RF POWER），`ac` = AC（MAX POWER）。
- `fram_version` 只是设备读数，不参与判断；固件镜像为 `0`，真机实际值未确认。

### 7.1 兼容性

| 快照 | 行为 |
|---|---|
| v3，`model: "ftdx10"` | 接受 |
| v2 / v1 / 缺少 `model` | 拒绝，不提供兼容层 |
| 其它机型 | 拒绝 |
| `format_version != 3` | 拒绝 |

> 本项目不再保留旧快照兼容。旧快照请自行核对字段，
> 不要指望当前脚本自动推导。

---

## 8. 故障排查

| 现象 | 可能原因 / 处理 |
|---|---|
| `(无回包)` | 串口选错、CAT RATE 不匹配、CAT 被菜单禁用、端口被占用 |
| 回 `?;` | 帧格式/命令不支持；`SP` 是二进制子协议，确认没有混入 ASCII 十六进制 |
| `SPR 应答校验错` | 回包 `chk` 覆盖 7 字节（`S P R ah al d0 d1`），不是 5 字节 |
| `帧内出现 0x3B` | 地址/数据/校验撞上 `;`；工具会自动换对齐，仍失败就改用面板菜单 |
| `SPW 未得到 A;` | 写入被拒（地址越界/设备忙）；先 `read` 确认通信正常 |
| 值写进去了但面板没变 | F/AC 需重新进菜单/刷新；档位需断电重开；`AC < F` 时有效值仍受 AC 限制 |
| 档位没变 | `FRAM[7]` 只在开机装载，必须整机断电重开（不要用软复位） |
| 重启后 F/AC 回到默认 | 固件因设置版本不符触发设置重建；本工具不检查 `FRAM[9]`，重新 `set-power` 即可 |
| `restore` 被拒绝：不在区间 | 快照来自另一档位/另一机型；先 `diff` 核对，或改用 `set-power` |
| `快照格式版本 ... 不受支持` / `快照缺少 model` | 旧版或非本工具快照；当前只接受 v3 |
| `需要 -p/--port` | `read`/`dump`/`set-power` 等必须指定串口；`list`/`diff` 不需要 |
| `需要 pyserial` | `pip install pyserial` |
| 权限拒绝 | `sudo usermod -aG dialout "$USER"` 后重新登录 |

---

## 9. 回滚与最坏情况

**正常回滚**：

```bash
python3 power/power_cat_optimized.py -p /dev/ttyUSB0 restore A_50W.json --apply
```

`restore` 会把配置字节、F、AC 原样写回，然后建议断电重开。

**如果写错配置字节导致开机异常**：

- 只要 CAT 还能通信，用 `restore` 写回原快照即可。快照本身不需要魔数/版本字段参与，
  但脚本仍会检查设备当前的两份魔数和初始化标志；`FRAM[9]` 只记录、不检查。
- 如果 CAT 已不可用，只能按维修流程处理外部 FRAM（`MB85RS256A`）。
  这也是为什么反复强调“先 dump、只改 bit3/4”。

**如果升级了固件**：

- 固件可能因设置版本不符触发设置重建，F/AC 回到该档默认值；档位字节通常保留。
- 本工具不检查 `FRAM[9]`，所以不会拦截这种写入；重新 `read` + `set-power` 即可。

---

## 10. 其它机型 / 200W

当前脚本**仅按 FTDX10 逆向编写**（未真机验证），不提供 profile。FTDX10 的档位只有 4 个
（100/50/20/10W），编码在 bit3-4；区间表也只有 4 档。

200W 机型大概率是另一套固件、另一张区间表、另一组 FRAM 地址，甚至档位编码不同。
直接把脚本头部的 `MODEL_MAX_W` 改成 200 不会凭空多出 200W 档，还可能写错地址。

如果要支持新机型，正确顺序是：

1. 先拿到该机型固件并完成逆向；
2. 确认配置字节地址、档位掩码、档位编码、区间表、F/AC 地址；
3. 复制一份脚本，只改文件头部的全局常量；
4. 先只读 `read`/`cap`/`dump` 核对，再考虑写入；
5. 不要在当前脚本里直接改常量后用于真机。


---

## 11. 相关文档

| 文档 | 内容 |
|---|---|
| [`CAT功率修改方案.md`](CAT功率修改方案.md) | 原理、地址、帧、两台机对照 |
| [`../cat/隐藏CAT命令列表.md`](../cat/隐藏CAT命令列表.md) | `SP` 等未公开命令的语法、回包与帧格式 |
| [`../cat/GUI工具使用说明.md`](../cat/GUI工具使用说明.md) | 通用隐藏命令 GUI（整片 FRAM 备份/恢复、PE 校准等） |
| [`功率解锁方案.md`](功率解锁方案.md) | 功率链取证、方案 M（改 SFL） |
| [`../decoded/ANALYSIS.md`](../decoded/ANALYSIS.md) | 固件整体分析 |

分工：GUI 是“通用隐藏命令 + 整片备份”；本工具只做**功率语义**——
解码/编码、档位选择、按档夹取、两台机对照、一键写入与回滚。
