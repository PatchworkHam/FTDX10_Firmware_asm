# power/ — FTDX10 功率修改汇总

本目录集中两种**目标一致、实现与副作用不同**的功率修改方式，以及对应脚本、成品与验证件。

> 目标范围：HF/50M 最高 100 W；70M 仍 50 W、AM 25 W。
>
> 状态：全部基于固件/反汇编的**静态逆向**，**未在真机验证**。
> 所有写入都必须先备份、后执行；默认 dry-run，确认后再加 `--apply`。
> 不修改 `original/`、`decoded/` 或任何 SFL 原件。

---

## 1. 两种方案

| 方案 | 文件 | 做法 | 可逆 |
|---|---|---|---|
| **方案 M**（刷固件） | [`功率解锁方案.md`](功率解锁方案.md) | 对 MAIN 固件打 5 处单字节补丁，把 `and.b #0x18` 改成 `mov.b #0x18` | 重刷官方固件 |
| **方案 CAT**（不刷固件） | [`CAT功率修改方案.md`](CAT功率修改方案.md) | 用未公开 CAT 命令 `SP` 的 `SPW/SPR` 改外部 FRAM 的配置字节与功率值 | 用原快照 `restore` 写回 |

方案 CAT 的 FRAM 目标：

- 配置字节 `FRAM[0x0007]` 的 bit3-4（功率档 `0x18` = 100W）；
- 各频段 RF POWER `F`：`FRAM[0x0335..0x0338]`（HF/50M/70M/AM）；
- 各频段 MAX POWER `AC`：`FRAM[0x046A..0x046D]`；
- 有效上限 = `min(F, AC)`；档位需整机断电重开，F/AC 写入后由固件重载路径刷新。

---

## 2. 脚本与成品

| 路径 | 内容 |
|---|---|
| [`power_cat_optimized.py`](power_cat_optimized.py) | 方案 CAT 的 FTDX10 专用工具：`list/read/cap/dump/diff/set-power/set-class/set-rf/set-max/restore/ex-read/ex-set` |
| [`power_cat_使用说明.md`](power_cat_使用说明.md) | 上表的连接、命令、快照 v3、固定布局与故障排查 |
| [`patched/MAIN_V0115_M_mask100W.SFL`](patched/MAIN_V0115_M_mask100W.SFL) | 方案 M 推荐刷机件（完整固件，非差分补丁） |
| [`patched/MAIN_V0115_M_mask100W.srec`](patched/MAIN_V0115_M_mask100W.srec) | 对应 S-record |
| [`patched/srec_patch.py`](patched/srec_patch.py) | S-record 字节补丁器（自动重算校验和） |
| [`verify/v_kr2.py`](verify/v_kr2.py) | 逐行比对原厂与补丁 S-record，并要求地址空间中恰好只有 5 个预期字节差异（`0x8112`/`0x1595C`/`0x159D4`/`0x3BB8A`/`0x3BCA6`） |
| [`verify/verify_kr.sh`](verify/verify_kr.sh) | 静态复核：PORTK 读取点、`0xFF2007` 读者、`and.b #0x18` 站点、EXP 位与成品指纹 |

离线查看方案 CAT 的地址表：

```bash
# 在仓库根目录执行
python3 power/power_cat_optimized.py list
```

---

## 3. 安全边界

1. **静态结论**：命令帧、FRAM 地址、补丁位置均来自固件字节与反汇编；真机写入效果尚未验证。
2. **先只读后写入**：先 `read` / `dump` 备份，再 dry-run 看计划，最后才 `--apply`。
3. **方案 CAT 不碰 `FRAM[9]`**：该字节只读记录，不参与写入门槛，也不写入；
   固件开机在版本不符时仍可能按自身逻辑改写它。
4. **写坏 `FRAM[0x0007]` 的配置位（尤其 bit3-4/bit5）可能导致开机或功率行为异常**：工具一律读-改-写，只动 bit3-4。
5. **硬件能力未知**：把软件档位改成 100W 不等于末级功放/驱动一定支持 100W。

---

## 4. 交叉引用

- 命令来源与帧格式：[`../cat/隐藏CAT命令列表.md`](../cat/隐藏CAT命令列表.md)
- 命令表定位、差集推导与验证：[`../cat/分析过程.md`](../cat/分析过程.md)
- 结论与代码逐项对照：[`../cat/结论与代码对照.md`](../cat/结论与代码对照.md)
- 通用隐藏命令 GUI：[`../cat/GUI工具使用说明.md`](../cat/GUI工具使用说明.md)
- FRAM 全空间分区：[`../fram/FRAM_MAP.md`](../fram/FRAM_MAP.md)
- TS/EXP 波段方案与 5 MHz 开启：[`../TS/README.md`](../TS/README.md)
- 功率链取证与可复现流程：[`../hardware/可复现分析过程.md`](../hardware/可复现分析过程.md)
- 升级/保底恢复：[`../hardware/固件升级.md`](../hardware/固件升级.md)

---

## 5. TODO

> 本节记录 **校准 DAC 表**（决定“实际出多少瓦”）的静态结论与待检查项。
> 方案 M 与方案 CAT 都**不读、不写**这张表；软件可达 ≠ 实际输出达标。

### 5.1 已确认的结论

1. **真正的输出上限是每波段 3 字节 DAC 校准表，不是 `0x3BD16`。**
   - 档位换算在 `0x3BC26`；`0x3BA44` 按等级选表项，`0x3BCF0` 用 3 字节项做插值。
   - 表基址：
     - `band < 0xA`：`0xFFA98D + 15*band` → FRAM `0x004F + 15*band`（10 带 × 15 B）；
     - `band >= 0xA`：`0xFFAA23 + 12*(band-0xA)` → FRAM `0x00E5 + ...`（12 B/带）。
   - 每个表项 3 字节，**首字节是 DAC 值**，后两字节是插值/步进参数。
2. **`0x3BD16` 是下限钳位，不是上限。**
   - 它取同一张表的**最后一项（5W 校准点）**：`0xFFA999/9B`、`0xFFAA2C/2E`，
     只在 `0xFF23C7/9` 低于该项时才抬高，作用方向是“保底”。
   - `功率解锁方案.md` §6 风险项 1 把 `0x3BD16` 说成“决定实际出多少瓦”，方向不对，它不封顶。
3. **默认镜像的 HF 表是 100W 量程。**
   - 第 0 带 3 字节项首字节：`0xCD, 0x82, 0x42, 0x25, 0x10`；
   - 第 1–9 带首字节：`0xD2, 0x87, 0x43, 0x24, 0x10`；
   - 看起来对应 **100W/50W/20W/10W/5W** 五个校准点。也就是说**默认镜像**的校准本身包含 100W 档；
     真机 FRAM 是否被工厂改成 50W 量程，见 §5.2 第 1 项。
4. **另一张 5/4 字节表不是 DAC 上限。**
   - `0x3BA80` 用的每波段表（band `<0xA` 在 `0xFFAA7E`，即 FRAM `0x0140`；
     band `>=0xA` 在 `0xFFAAAF`，即 FRAM `0x0171`）写入的是 `0xFF23C8`（功率表/ALC），
     与 `0xFF23C7/9` 的 DAC 输出是两条路。
5. **两个工具都没碰 DAC 表。**
   - 方案 M 只改 5 个指令字节；方案 CAT 只写 `FRAM[7]`、`0x0335..0x0338`、`0x046A..0x046D`。
   - 若某台 50W 机的工厂 FRAM 把 DAC 表首项写成 50W 量程（约 `0x87`），
     则菜单/软件档位会显示 100，但实际输出仍会被表卡在 50W 附近。

### 5.2 待检查项

- [ ] **读 50W 机与 100W 机的校准表**（只读 `SPR`，不写入）：
  - HF：`FRAM 0x004F..0x00E4`（10 带 × 15 B）；
  - VHF/UHF：`FRAM 0x00E5..0x013F`（12 B/带）；
  - 功率表：`FRAM 0x0140..0x017C`（5 B/带，辅助判断）。
  - 判据：逐带比较 3 字节项的**首字节**——`0xD2` 附近 = 100W 量程；`0x87` 附近 = 50W 量程。
- [ ] **给工具加只读 FRAM 命令**：当前 CLI 没有任意地址读；`Cat.read_bytes` 已能读但未暴露。
  建议加 `read-fram` / `dump-cal`，并把该块加入 `diff` 对照。
- [ ] **若目标机校准表是 50W 量程**：不要盲改。
  这是工厂校准，乱改可能过驱动 PA；应记录为“软件可达、校准/硬件受限”，或按服务手册重新校准。
- [ ] **真机功率计实测**：50Ω 假负载 + 外置功率计；
  HF/50M 目标 100W，70M 仍 ≤50W，AM ≤25W；
  同时观察 PA 电流、温度、谐波/杂散，不要用机器自带功率表作为唯一依据。
- [ ] **复核文档**：把 `功率解锁方案.md` §6 风险项 1 改成
  “`0x3BD16` 是下限；上限在 `0x3BC26` 的 3 字节表”。

只读读取示例（不改设备；端口/波特率按实际修改）：

```bash
python3 - <<'PY'
import sys
sys.path.insert(0, "power")
import power_cat_optimized as p

c = p.Cat("/dev/ttyUSB0", 38400)
c.quiet = True
for base, n, name in (
    (0x004F, 0x96, "HF DAC 0x004F..0x00E4"),
    (0x00E5, 0x5B, "VHF/UHF DAC 0x00E5..0x013F"),
    (0x0140, 0x3D, "meter 0x0140..0x017C"),
):
    print(name)
    print(c.read_bytes(base, n).hex(" "))
c.close()
PY
```
