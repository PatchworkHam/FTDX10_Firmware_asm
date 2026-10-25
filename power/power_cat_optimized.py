#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
power_cat_optimized.py — Yaesu FTDX10 功率参数 CAT 读写工具（仅适配 FTDX10；静态逆向）

适用范围
------------------------------------------------------------
本脚本按 Yaesu FTDX10 / MAIN V01-15 的静态逆向结果编写；未做真机写入验证。
不要直接用于其它机型、其它固件版本或未确认的 FRAM 布局。
所有常量集中在文件头部其余机型自行适配

设计目标
------------------------------------------------------------
1. 完全按 MAIN V01-15 的逆向结论读写，不猜测：
     配置字节 FRAM[0x0007]：TS(bit0-2) PWR(bit3-4) EXP(bit5) AUX(bit6-7)
     功率档 0x18=100W / 0x00=50W / 0x08=20W / 0x10=10W
     RF POWER   F = FRAM[0x0335..0x0338]（HF/50M/70M/AM）
     MAX POWER  AC = FRAM[0x046A..0x046D]（HF/50M/70M/AM）
     实际上限 = min(F, AC)
     档位区间表 0x159F6（4 频段 × 4 档，band-major，档序 100/50/20/10）
     档位默认值 0x810C（重建设置时按档写入）
2. 所有写操作都夹到「目标档位在该频段允许的区间」内，保证固件后续保存/菜单
   操作不会把值夹回去，也不会因越界被拒绝。
3. 不检查 FRAM[9] 设置版本：该字节只读取/展示，不参与兼容性判断，也不写入。
   当前镜像 0x3F650 为 0x00；真机实际值尚未确认，本工具不把它当作期望值。
4. 默认 dry-run；`--apply` 才写；写后回读校验；自动避开 0x3B 帧尾陷阱。

用法（详见同目录 power_cat_使用说明.md）
------------------------------------------------------------
    python3 power/power_cat_optimized.py list              # 列出 FRAM 表/档位区间
    python3 power/power_cat_optimized.py -p /dev/ttyUSB0 read
    python3 power/power_cat_optimized.py -p /dev/ttyUSB0 cap
    python3 power/power_cat_optimized.py -p /dev/ttyUSB0 dump A.json
    python3 power/power_cat_optimized.py diff A.json B.json
    python3 power/power_cat_optimized.py -p /dev/ttyUSB0 set-power 100 --apply
    python3 power/power_cat_optimized.py -p /dev/ttyUSB0 restore A.json --apply
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

try:
    import serial  # pyserial
except ImportError:  # pragma: no cover
    serial = None


# ==========================================================================
# FTDX10 固定布局（仅适配 FTDX10 / MAIN V01-15；静态逆向，未真机验证）
# ==========================================================================
class CatError(Exception):
    """CAT/FRAM 操作失败。"""


MODEL_KEY = "ftdx10"
MODEL_NAME = "Yaesu FTDX10 (MAIN V01-15)"
TARGET_MODEL = "FTDX10"
VERIFICATION_BASIS = "static-reverse-engineering"
HARDWARE_VERIFIED = False
MODEL_MAX_W = 100

# 配置字节对：FRAM[6] 为高字节，FRAM[7] 为 TS/PWR/EXP/AUX。
# SPW 一次写两个字节，所以配置字节必须和它的高字节一起读-改-写。
CONFIG_PAIR = 0x0006
CONFIG_ADDR = 0x0007
CLASS_MASK = 0x18

# 每频段 RF POWER（F）与面板 MAX POWER（AC）。
F_ADDR = 0x0335
AC_ADDR = 0x046A
BANDS: Tuple[str, ...] = ("HF", "50M", "70M", "AM")

# 档位编码、区间表（MAIN 0x159F6）与按档默认值（MAIN 0x810C）。
CLASS_WATTS: Dict[int, int] = {
    0x18: 100,
    0x00: 50,
    0x08: 20,
    0x10: 10,
}
CLASS_RANGES: Dict[int, Tuple[Tuple[int, int], ...]] = {
    0x18: ((5, 100), (5, 100), (5, 50), (5, 25)),
    0x00: ((5, 50), (5, 50), (5, 50), (5, 25)),
    0x08: ((5, 10), (5, 20), (5, 20), (5, 20)),
    0x10: ((5, 10), (5, 10), (5, 10), (5, 10)),
}
CLASS_DEFAULTS: Dict[int, Tuple[int, ...]] = {
    0x18: (100, 100, 50, 25),
    0x00: (50, 50, 50, 25),
    0x08: (10, 20, 20, 20),
    0x10: (10, 10, 10, 10),
}
CLASS_BITS: Dict[int, int] = {w: b for b, w in CLASS_WATTS.items()}

# 固件开机校验的 FRAM 有效标记；两份都要是 A5 5A。
MAGIC_ADDRS: Tuple[int, ...] = (0x0002, 0x0004)
MAGIC = b"\xA5\x5A"
INIT_FLAG_ADDR = 0x0008
INIT_FLAG = 0xA5

# FRAM[9] 只读取/展示，不检查、不写入。
# 当前镜像 0x3F650 为 0x00；真机实际值尚未确认，本工具不推断期望值。
VERSION_ADDR = 0x0009

TS_MASK = 0x07
EXP_MASK = 0x20
AUX_MASK = 0xC0
TS_VALID: Tuple[int, ...] = (0, 2, 3, 6)
TS_SPECIAL = "ts1_non10w"


def _validate_layout() -> None:
    """启动时检查全局布局常量，防止手改常量后悄悄写错地址/区间。"""
    if CONFIG_ADDR != CONFIG_PAIR + 1:
        raise RuntimeError("CONFIG_ADDR 必须等于 CONFIG_PAIR + 1")
    if not BANDS or len(set(BANDS)) != len(BANDS):
        raise RuntimeError("BANDS 不能为空或重复")
    if len(MAGIC) != 2:
        raise RuntimeError("MAGIC 必须是 2 字节")
    for name, addr, length in (
        ("CONFIG_PAIR", CONFIG_PAIR, 2),
        ("CONFIG_ADDR", CONFIG_ADDR, 1),
        ("F_ADDR", F_ADDR, len(BANDS)),
        ("AC_ADDR", AC_ADDR, len(BANDS)),
        ("INIT_FLAG_ADDR", INIT_FLAG_ADDR, 1),
        ("VERSION_ADDR", VERSION_ADDR, 1),
    ):
        if not 0 <= addr < 0x8000 or addr + length > 0x8000:
            raise RuntimeError(f"{name}=0x{addr:04X} 超出 FRAM 0x0000-0x7FFF")
    for addr in MAGIC_ADDRS:
        if not 0 <= addr < 0x8000 or addr + len(MAGIC) > 0x8000:
            raise RuntimeError(f"MAGIC_ADDR=0x{addr:04X} 超出 FRAM 0x0000-0x7FFF")
    if not CLASS_WATTS or len(set(CLASS_WATTS.values())) != len(CLASS_WATTS):
        raise RuntimeError("CLASS_WATTS 不能为空或瓦数重复")
    for bits, watts in CLASS_WATTS.items():
        if bits & ~CLASS_MASK:
            raise RuntimeError(f"档位 0x{bits:02X} 超出 CLASS_MASK 0x{CLASS_MASK:02X}")
        if bits not in CLASS_RANGES or bits not in CLASS_DEFAULTS:
            raise RuntimeError(f"档位 0x{bits:02X} 缺少区间或默认值")
        ranges = CLASS_RANGES[bits]
        defaults = CLASS_DEFAULTS[bits]
        if len(ranges) != len(BANDS) or len(defaults) != len(BANDS):
            raise RuntimeError(f"档位 0x{bits:02X} 的区间/默认值数量与 BANDS 不一致")
        for (lo, hi), value in zip(ranges, defaults):
            if not 0 <= lo <= hi <= 255:
                raise RuntimeError(f"档位 0x{bits:02X} 区间 {lo}-{hi} 非法")
            if not lo <= value <= hi:
                raise RuntimeError(
                    f"档位 0x{bits:02X} 默认值 {value} 不在 {lo}-{hi}")
        if watts > MODEL_MAX_W:
            raise RuntimeError(f"档位 {watts}W 超过机型上限 {MODEL_MAX_W}W")


def class_label(bits: int) -> str:
    watts = CLASS_WATTS.get(bits)
    return f"{watts}W" if watts is not None else f"未知(0x{bits:02X})"


def class_max_w(bits: int) -> int:
    return CLASS_WATTS.get(bits, 0)


def max_class_bits() -> int:
    return max(CLASS_WATTS, key=lambda b: CLASS_WATTS[b])


def band_index(key: str) -> int:
    k = key.strip().lower()
    aliases = {"m50": "50m", "m70": "70m", "hf": "hf", "50m": "50m",
               "70m": "70m", "am": "am"}
    k = aliases.get(k, k)
    for i, band in enumerate(BANDS):
        if band.lower() == k:
            return i
    raise CatError(f"未知频段 {key!r}（可用：{'/'.join(BANDS)}）")


def range_for(bits: int, band: int) -> Tuple[int, int]:
    try:
        return CLASS_RANGES[bits][band]
    except KeyError:
        raise CatError(f"档位 0x{bits:02X} 未定义区间")


def decode(byte: int) -> dict:
    bits = byte & CLASS_MASK
    ts = byte & TS_MASK
    exp = byte & EXP_MASK
    aux = byte & AUX_MASK
    valid = ts in TS_VALID
    # FTDX10 的附加规则：TS==1 且档位不是 10W 也算有效。
    if not valid and ts == 1 and bits != CLASS_BITS.get(10):
        valid = TS_SPECIAL == "ts1_non10w"
    return {
        "byte": byte,
        "class_bits": bits,
        "class_w": CLASS_WATTS.get(bits),
        "class_name": class_label(bits),
        "class_known": bits in CLASS_WATTS,
        "ts": ts,
        "ts_valid": valid,
        "exp": 1 if exp else 0,
        "aux": aux,
    }


def model_info() -> dict:
    """给 JSON/诊断用的固定机型信息。"""
    return {
        "model": MODEL_KEY,
        "name": MODEL_NAME,
        "verification_basis": VERIFICATION_BASIS,
        "hardware_verified": HARDWARE_VERIFIED,
        "model_max_w": MODEL_MAX_W,
        "config_pair": CONFIG_PAIR,
        "config_addr": CONFIG_ADDR,
        "class_mask": CLASS_MASK,
        "f_addr": F_ADDR,
        "ac_addr": AC_ADDR,
        "bands": list(BANDS),
        "magic_addrs": list(MAGIC_ADDRS),
        "magic_hex": MAGIC.hex(),
        "init_flag_addr": INIT_FLAG_ADDR,
        "init_flag": INIT_FLAG,
        "version_addr": VERSION_ADDR,
        "version_checked": False,
        "classes": [
            {"bits": b, "watts": CLASS_WATTS[b],
             "ranges": [list(x) for x in CLASS_RANGES[b]],
             "defaults": list(CLASS_DEFAULTS[b])}
            for b in sorted(CLASS_WATTS, key=lambda x: -CLASS_WATTS[x])
        ],
    }


_validate_layout()


# ==========================================================================
# 帧构造 / 回包解析
# ==========================================================================
def sp_frame(body: bytes) -> bytes:
    """body 不含开头的 'S''P'。返回 'SP'+body+chk+';'。"""
    head = b"SP" + body
    return head + bytes([sum(head) & 0xFF]) + b";"


def frame_has_semicolon(frame: bytes) -> bool:
    """帧内（不含结尾 ';'）是否出现 0x3B。"""
    return 0x3B in frame[:-1]


def parse_spr_reply(r: bytes, addr: int) -> bytes:
    """校验 SPR 回包，返回 bytes([d0,d1])。

    固件 `0x2D0C2`：回包 `SPR ah al d0 d1 chk ;`，
    `chk = sum('S','P','R',ah,al,d0,d1) & 0xFF`（7 字节），不是请求的 5 字节。
    """
    if len(r) != 9 or r[0:3] != b"SPR" or r[8:9] != b";":
        raise CatError(f"SPR 应答长度、前缀或结束符异常: {r.hex(' ') if r else '(空)'}")
    if r[3:5] != addr.to_bytes(2, "big"):
        raise CatError("SPR 回显地址不符")
    if r[7] != (sum(r[0:7]) & 0xFF):
        raise CatError("SPR 应答校验错")
    return bytes(r[5:7])


# ==========================================================================
# 串口
# ==========================================================================
class Cat:
    def __init__(self, port: str, baud: int, timeout: float = 0.8):
        if serial is None:
            raise SystemExit("需要 pyserial：pip install pyserial")
        self.ser = serial.Serial(port=port, baudrate=baud, bytesize=8,
                                 parity="N", stopbits=1, timeout=timeout)
        self.quiet = False

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # -- 低层 --------------------------------------------------------------
    def _raw(self, frame: bytes, expect: int = 0, wait: float = 0.12) -> bytes:
        if frame_has_semicolon(frame):
            raise CatError("帧内出现 0x3B，会被设备当成帧尾：" + frame.hex(" "))
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        time.sleep(wait)
        n = max(expect, self.ser.in_waiting)
        data = self.ser.read(n) if n else b""
        if not self.quiet:
            print(f"    → {frame.hex(' ')}")
            print(f"    ← {data.hex(' ') if data else '(无回包)'}")
        return data

    def spr(self, addr: int) -> bytes:
        """读 2 字节，返回 bytes([d0,d1])。"""
        if not 0 <= addr < 0x8000:
            raise CatError(f"SPR 地址越界: 0x{addr:04X}")
        r = self._raw(sp_frame(b"R" + addr.to_bytes(2, "big")), expect=9)
        return parse_spr_reply(r, addr)

    def spw_pair(self, addr: int, d0: int, d1: int) -> None:
        """向 addr / addr+1 各写 1 字节。"""
        if not 0 <= addr < 0x8000:
            raise CatError(f"SPW 地址越界: 0x{addr:04X}")
        r = self._raw(sp_frame(b"W" + addr.to_bytes(2, "big") + bytes([d0, d1])),
                      expect=2)
        if r != b"A;":
            raise CatError(f"SPW 应答不是严格的 A; ：{r.hex(' ') if r else '(空)'}")

    def read_bytes(self, addr: int, n: int) -> bytes:
        out = bytearray()
        a = addr
        while len(out) < n:
            out += self.spr(a)
            a += 2
        return bytes(out[:n])

    # -- 高层：任意长度读-改-写 -------------------------------------------
    @staticmethod
    def _layouts(addr: int, n: int):
        """生成 (base, 总长) 候选：尽量少改动相邻字节，用于避开 0x3B。"""
        out = []
        for lead in (0, 1, 2):
            for trail in (0, 1, 2):
                total = n + lead + trail
                if total % 2:
                    continue
                base = addr - lead
                if base < 0 or base + total - 1 > 0x7FFF:
                    continue
                out.append((base, total, lead, trail))
        out.sort(key=lambda x: (x[2] + x[3], x[0] != addr))
        return out

    def plan_write(self, addr: int, data: bytes):
        """返回可行写方案（已读回原值并组装好 2 字节帧）。

        按“改动相邻字节最少”的顺序尝试；一旦找到不含 0x3B 的方案就立即返回，
        避免无谓的串口读。只有全部方案都冲突时才把所有方案返回给上层报错。
        """
        n = len(data)
        if n == 0:
            return []
        if addr + n - 1 > 0x7FFF:
            raise CatError("超出 FRAM 地址空间")
        plans = []
        for base, total, lead, _trail in self._layouts(addr, n):
            cur = bytearray(self.read_bytes(base, total))
            cur[lead:lead + n] = data
            pairs = [(base + i, [cur[i], cur[i + 1]])
                     for i in range(0, total, 2)]
            bad = [hex(p[0]) for p in pairs
                   if frame_has_semicolon(sp_frame(
                       b"W" + p[0].to_bytes(2, "big") + bytes(p[1])))]
            plan = {"base": base, "total": total, "lead": lead,
                    "pairs": pairs, "bad": bad}
            plans.append(plan)
            if not bad:
                return [plan]
        return plans

    def apply_write(self, addr: int, data: bytes) -> None:
        plans = self.plan_write(addr, data)
        good = [p for p in plans if not p["bad"]]
        if not good:
            raise CatError("无法避开 0x3B：请改用面板菜单设置该项")
        plan = good[0]
        if not self.quiet:
            print(f"  [写] FRAM[0x{addr:04X}..0x{addr + len(data) - 1:04X}] "
                  f"{len(data)} 字节（起始 0x{plan['base']:04X}，"
                  f"{len(plan['pairs'])} 帧）")
        for base, d in plan["pairs"]:
            self.spw_pair(base, d[0], d[1])
        back = self.read_bytes(addr, len(data))
        if back != data:
            raise CatError(
                f"回读校验失败：写入 {data.hex(' ')}，回读 {back.hex(' ')}")
        if not self.quiet:
            print(f"  [✓] 回读一致：{back.hex(' ')}")


# ==========================================================================
# 读取 / 快照
# ==========================================================================

def check_fram_identity(cat: Cat) -> None:
    """写前检查固件开机使用的 FRAM 标识。

    只检查两份魔数和初始化标志。FRAM[9] 设置版本只读展示，不参与判断：
    当前镜像 0x3F650=0x00；真机实际值尚未确认，本工具不推断期望值。
    """
    copies = [(addr, cat.read_bytes(addr, len(MAGIC)))
              for addr in MAGIC_ADDRS]
    bad = [(addr, value.hex(" ")) for addr, value in copies
           if value != MAGIC]
    if bad:
        detail = ", ".join(f"0x{addr:04X}={value}" for addr, value in bad)
        raise CatError(
            f"FRAM 魔数副本校验失败（期望 {MAGIC.hex(' ')}）：{detail}；拒绝继续")
    init = cat.read_bytes(INIT_FLAG_ADDR, 1)[0]
    if init != INIT_FLAG:
        raise CatError(
            f"FRAM 初始化标志[0x{INIT_FLAG_ADDR:04X}]={init}，"
            f"期望 {INIT_FLAG}；设置可能未初始化，拒绝继续")


def read_snapshot(cat: Cat, note: str = "") -> dict:
    pair = cat.read_bytes(CONFIG_PAIR, 2)
    f = tuple(cat.read_bytes(F_ADDR, len(BANDS)))
    ac = tuple(cat.read_bytes(AC_ADDR, len(BANDS)))
    magic_copies = [cat.read_bytes(addr, len(MAGIC)) for addr in MAGIC_ADDRS]
    magic_ok = all(copy == MAGIC for copy in magic_copies)
    init = cat.read_bytes(INIT_FLAG_ADDR, 1)[0]
    ver = cat.read_bytes(VERSION_ADDR, 1)[0]
    d = decode(pair[1])
    if d["class_known"]:
        ranges = {b: list(range_for(d["class_bits"], i))
                  for i, b in enumerate(BANDS)}
    else:
        ranges = {b: None for b in BANDS}
    return {
        "format": "yaesu-power-cat",
        "format_version": 3,
        "model": MODEL_KEY,
        "model_name": MODEL_NAME,
        "verification_basis": VERIFICATION_BASIS,
        "hardware_verified": HARDWARE_VERIFIED,
        "model_max_w": MODEL_MAX_W,
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "note": note,
        "config_pair": {f"0x{CONFIG_PAIR:04X}": pair[0],
                        f"0x{CONFIG_ADDR:04X}": pair[1]},
        "config_byte": pair[1],
        "class_bits": d["class_bits"],
        "class": d["class_name"],
        "class_known": d["class_known"],
        "ts": d["ts"],
        "ts_valid": d["ts_valid"],
        "exp": d["exp"],
        "aux": d["aux"],
        "max_power": {b: f[i] for i, b in enumerate(BANDS)},
        "ac": {b: ac[i] for i, b in enumerate(BANDS)},
        "effective": {b: min(f[i], ac[i]) for i, b in enumerate(BANDS)},
        "ranges": ranges,
        "magic": [copy.hex(" ") for copy in magic_copies],
        "magic_addrs": list(MAGIC_ADDRS),
        "magic_ok": magic_ok,
        "init_flag": init,
        "init_ok": init == INIT_FLAG,
        "fram_version": ver,
        "fram_version_note": "只读记录，不检查、不写入",
    }


def _fmt_row(cells: Sequence[str], widths: Sequence[int]) -> str:
    return "  ".join(str(c).ljust(w) for c, w in zip(cells, widths))


def print_snapshot(s: dict) -> None:
    basis = ("静态逆向，未真机验证"
             if s.get("verification_basis", VERIFICATION_BASIS)
             == "static-reverse-engineering" else str(s.get("verification_basis", "?")))
    print(f"  机型：{s.get('model_name', MODEL_NAME)}"
          f"（{basis}；最高 {s.get('model_max_w', MODEL_MAX_W)} W）")
    if s.get("magic") is not None:
        ok = "OK" if s.get("magic_ok") else "★不符"
        magic_values = s["magic"] if isinstance(s["magic"], list) else [s["magic"]]
        copies = ", ".join(
            f"0x{addr:04X}={value}"
            for addr, value in zip(s.get("magic_addrs", []), magic_values))
        if not copies:
            copies = ", ".join(magic_values)
        init = s.get("init_flag")
        init_txt = f"0x{init:02X}" if isinstance(init, int) else "?"
        init_status = "OK" if s.get("init_ok") else "★不符"
        ver = s.get("fram_version")
        ver_txt = str(ver) if ver is not None else "?"
        print(f"  FRAM 魔数 [{ok}] {copies}   初始化标志 {init_txt} "
              f"[{init_status}]   设置版本 FRAM[0x{VERSION_ADDR:04X}]={ver_txt}"
              f"（只读，不判断）")
    cp = s["config_pair"]
    cfg_byte = s["config_byte"]
    d = decode(cfg_byte)
    bits = s.get("class_bits", d["class_bits"])
    name = s.get("class", d["class_name"])
    ts = s.get("ts", d["ts"])
    exp = s.get("exp", d["exp"])
    aux = s.get("aux", d["aux"])
    ts_valid = s.get("ts_valid", d["ts_valid"])
    cp_hi = cp[f"0x{CONFIG_PAIR:04X}"]
    print(f"  配置字节 FRAM[0x{CONFIG_PAIR:04X}]=0x{cp_hi:02X}  "
          f"FRAM[0x{CONFIG_ADDR:04X}]=0x{cfg_byte:02X}  ->  {name}"
          f"（0x{bits:02X}）, TS={ts}"
          f"{'' if ts_valid is not False else ' ★无效'}, "
          f"EXP={exp}, AUX={aux}")
    print()
    width = [6, 8, 9, 10, 12]
    print("  " + _fmt_row(("频段", f"F(0x{F_ADDR:03X}+)",
                           f"AC(0x{AC_ADDR:03X}+)",
                           "有效上限", "本档允许区间"), width))
    for i, b in enumerate(BANDS):
        v, a = s["max_power"][b], s["ac"][b]
        rng = s.get("ranges", {}).get(b)
        if not rng and s.get("class_known", True):
            try:
                rng = list(range_for(bits, i))
            except CatError:
                rng = None
        if rng:
            lo, hi = rng
            mark = "" if lo <= v <= hi and lo <= a <= hi else "  ★越界"
            cell = f"{lo}-{hi}{mark}"
        else:
            cell = "未知档位"
        print("  " + _fmt_row((b, v, a, min(v, a), cell), width))
    print("\n  注：有效上限 = min(F, AC)；F 是面板 RF POWER（发射功率设定），"
          "AC 是面板 MAX POWER（菜单 04-04 写它）。")
    top = class_max_w(bits)
    if not s.get("class_known", True):
        print("\n  ★ 当前档位值不在已知档位表中：只读结果仅供参考，"
              "写入前请先确认 FRAM 布局/档位编码。")
    elif bits != max_class_bits():
        print(f"\n  提示：当前档位最高 {top} W；本机上限 "
              f"{MODEL_MAX_W} W。一键升级：set-power "
              f"{MODEL_MAX_W} --apply（需断电重开）")


# ==========================================================================
# 功率计划（纯函数）
# ==========================================================================
@dataclass
class PowerPlan:
    old_pair: Tuple[int, int]
    old_cfg: int
    new_cfg: int
    old_f: Tuple[int, ...]
    new_f: Tuple[int, ...]
    old_ac: Tuple[int, ...]
    new_ac: Tuple[int, ...]
    requested: int
    target: int
    notes: List[str]
    warnings: List[str]

    @property
    def class_changed(self) -> bool:
        return self.new_cfg != self.old_cfg

    @property
    def f_changed(self) -> bool:
        return self.new_f != self.old_f

    @property
    def ac_changed(self) -> bool:
        return self.new_ac != self.old_ac

    @property
    def changed(self) -> bool:
        return self.class_changed or self.f_changed or self.ac_changed

    def effective(self) -> Tuple[int, ...]:
        return tuple(min(a, b) for a, b in zip(self.new_f, self.new_ac))


def class_for_watts(watts: int) -> Optional[int]:
    """选择能覆盖 watts 的最小档位；没有则 None。"""
    cands = [b for b, w in CLASS_WATTS.items() if w >= watts]
    if not cands:
        return None
    return min(cands, key=lambda b: CLASS_WATTS[b])


def make_power_plan(pair: Tuple[int, int], f: Sequence[int],
                    ac: Sequence[int], watts: int,
                    overrides: Optional[Dict[int, int]] = None,
                    ac_mode="auto") -> PowerPlan:
    """生成"一键设功率"计划：选档 + 夹取 + F/AC 同步。

    overrides: {band_index: watts}
    ac_mode: "auto"（AC 跟随 F，等于夹取后的新 F）/ "keep"（不动 AC）/ 整数（指定）
    """
    overrides = overrides or {}
    notes: List[str] = []
    warnings: List[str] = []

    old_cfg = pair[1]
    d = decode(old_cfg)
    target = int(watts)
    if target <= 0:
        raise CatError("功率必须 > 0")
    if target > MODEL_MAX_W:
        warnings.append(
            f"请求 {target} W 超过本机上限 {MODEL_MAX_W} W，已按上限处理")
        target = MODEL_MAX_W

    required = class_for_watts(target)
    if required is None:
        raise CatError(
            f"没有能覆盖 {target} W 的档位"
            f"（已定义：{sorted(CLASS_WATTS.values())} W）")
    cur_w = CLASS_WATTS.get(d["class_bits"])
    if cur_w is not None and cur_w >= target:
        # 不自动降档：50W 目标不应把 100W 机改成 50W 档
        bits = d["class_bits"]
        if required != bits:
            notes.append(f"保持当前档位 {class_label(bits)}（不自动降档；"
                         f"如需降档请用 set-class {CLASS_WATTS[required]}）")
    else:
        bits = required
    if bits != d["class_bits"]:
        notes.append(f"功率档 {d['class_name']} -> {class_label(bits)}"
                     f"（FRAM[0x0007] bit 掩码 0x{CLASS_MASK:02X}，需断电重开）")
    new_cfg = (old_cfg & ~CLASS_MASK) | bits
    d_new = decode(new_cfg)
    if d_new["ts_valid"] is False:
        warnings.append(f"TS={d_new['ts']} 与目标档位 "
                        f"{class_label(bits)} 的组合不在固件已知市场表内"
                        "（本工具不改 TS 位，请自行确认）")

    new_f = list(f)
    for i, band in enumerate(BANDS):
        want = int(overrides.get(i, target))
        lo, hi = range_for(bits, i)
        hi = min(hi, MODEL_MAX_W)
        if want > hi:
            warnings.append(f"{band}: {want} W 超过 {class_label(bits)} 档上限 "
                            f"{hi} W，已夹到 {hi} W")
        if want < lo:
            warnings.append(f"{band}: {want} W 低于本档下限 {lo} W，已抬到 {lo} W")
        new_f[i] = max(lo, min(hi, want))

    new_ac = list(ac)
    if ac_mode == "keep":
        notes.append("面板 MAX POWER（AC）保持不变（有效值 = min(F, AC)）")
    elif ac_mode == "auto":
        for i in range(len(BANDS)):
            new_ac[i] = new_f[i]
    else:
        av = int(ac_mode)
        if av > MODEL_MAX_W:
            warnings.append(f"AC 请求 {av} W 超过本机上限，已按上限处理")
            av = MODEL_MAX_W
        for i, band in enumerate(BANDS):
            lo, hi = range_for(bits, i)
            hi = min(hi, MODEL_MAX_W)
            new_ac[i] = max(lo, min(hi, av))
            if new_ac[i] < new_f[i]:
                warnings.append(f"{band}: AC={new_ac[i]} W 低于 F={new_f[i]} W，"
                                f"有效上限将受 AC 限制")

    for i, band in enumerate(BANDS):
        eff = min(new_f[i], new_ac[i])
        if eff < new_f[i]:
            notes.append(f"{band}: 有效上限 = min(F={new_f[i]}, AC={new_ac[i]}) = {eff} W")

    return PowerPlan(tuple(pair), old_cfg, new_cfg,
                     tuple(f), tuple(new_f), tuple(ac), tuple(new_ac),
                     int(watts), target, notes, warnings)


def check_acceptance(plan: PowerPlan) -> None:
    """写前检查：确保目标值在固件认可的档位区间内。"""
    bits = plan.new_cfg & CLASS_MASK
    if bits not in CLASS_WATTS:
        raise CatError(f"目标档位 0x{bits:02X} 不在已知档位表中，拒绝写入")
    errs = []
    for i, band in enumerate(BANDS):
        lo, hi = range_for(bits, i)
        hi = min(hi, MODEL_MAX_W)
        for label, arr in (("F", plan.new_f), ("AC", plan.new_ac)):
            v = arr[i]
            if not lo <= v <= hi:
                errs.append(f"{band} {label}={v} 不在 {lo}-{hi}")
    if errs:
        raise CatError("以下取值会被固件拒绝/夹取，已中止：" + "；".join(errs))


def apply_plan(cat: Cat, plan: PowerPlan) -> None:
    if plan.class_changed:
        cat.apply_write(CONFIG_PAIR, bytes([plan.old_pair[0], plan.new_cfg]))
    if plan.f_changed:
        cat.apply_write(F_ADDR, bytes(plan.new_f))
    if plan.ac_changed:
        cat.apply_write(AC_ADDR, bytes(plan.new_ac))


def print_plan(plan: PowerPlan) -> None:
    bits = plan.new_cfg & CLASS_MASK
    print(f"\n  目标：{plan.requested} W"
          + (f"（按本机上限 {MODEL_MAX_W} W 处理）"
             if plan.requested > plan.target else "")
          + f"  ->  档位 {class_label(bits)}（0x{bits:02X}）")
    width = [6, 8, 9, 9, 10, 12]
    print("  " + _fmt_row(("频段", "RF POWER", "MAX POWER", "新RF", "新MAX", "本档区间"),
                          width))
    for i, band in enumerate(BANDS):
        lo, hi = range_for(bits, i)
        hi = min(hi, MODEL_MAX_W)
        print("  " + _fmt_row((band, plan.old_f[i], plan.old_ac[i],
                               plan.new_f[i], plan.new_ac[i],
                               f"{lo}-{hi}"), width))
    print(f"  配置字节：0x{plan.old_cfg:02X} -> 0x{plan.new_cfg:02X}"
          f"（TS/EXP/AUX 位保持不变）")
    for n in plan.notes:
        print(f"  · {n}")
    for w in plan.warnings:
        print(f"  ★ {w}")


# ==========================================================================
# 子命令
# ==========================================================================
def cmd_list(args) -> None:
    print(f"机型：{MODEL_NAME}（静态逆向，未真机验证；最高 {MODEL_MAX_W} W）\n")
    rows = [
        (f"0x{CONFIG_PAIR:04X}", "配置字节对高字节", "读取后原样写回，不要单独改"),
        (f"0x{CONFIG_ADDR:04X}", "配置字节",
         f"TS/EXP/AUX + PWR（掩码 0x{CLASS_MASK:02X}）"),
        (f"0x{F_ADDR:04X}", "RF POWER（F，" + "/".join(BANDS) + "）",
         "各频段发射功率；面板 RF POWER / PC 写它"),
        (f"0x{AC_ADDR:04X}", "面板 MAX POWER（AC，" + "/".join(BANDS) + "）",
         "菜单 04-04 写这个值；有效上限 = min(F, AC)"),
    ]
    for addr in MAGIC_ADDRS:
        rows.append((f"0x{addr:04X}", "FRAM 魔数",
                     f"{MAGIC.hex(' ')}（开机校验，不要改）"))
    rows.append((f"0x{INIT_FLAG_ADDR:04X}", "设置初始化标志",
                 f"0x{INIT_FLAG:02X}=已初始化，不要改"))
    rows.append((f"0x{VERSION_ADDR:04X}", "设置版本",
                 "只读记录，不检查、不写入；固件镜像为 0x00，真机实际值未确认"))
    for a, name, note in rows:
        print(f"  {a:8s} {name:34s} {note}")

    print("\n  档位与区间（来源：固件区间表；默认值来源：按档写默认值的例程）：")
    width = [8, 8, 30, 26]
    print("  " + _fmt_row(("档位", "最高", "各频段允许区间", "默认 F"), width))
    for bits in sorted(CLASS_WATTS, key=lambda b: -CLASS_WATTS[b]):
        rr = "  ".join(f"{b} {lo}-{hi}" for b, (lo, hi) in
                       zip(BANDS, CLASS_RANGES[bits]))
        dd = "/".join(str(x) for x in CLASS_DEFAULTS[bits])
        print("  " + _fmt_row((f"0x{bits:02X}", f"{CLASS_WATTS[bits]} W",
                               rr, dd), width))
    print("\n  写入规则：目标值 = min(请求值, 本机上限, 目标档位区间上限)，"
          "并保留 TS/EXP/AUX。")
    print(f"  ★ 本脚本仅适配 {TARGET_MODEL}（静态逆向，未真机验证）；其它机型/固件不要直接使用。")


def cmd_read(args) -> None:
    with Cat(args.port, args.baud, timeout=args.timeout) as cat:
        cat.quiet = args.quiet
        s = read_snapshot(cat, args.note)
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
    else:
        print_snapshot(s)


def cmd_cap(args) -> None:
    with Cat(args.port, args.baud, timeout=args.timeout) as cat:
        cat.quiet = args.quiet
        s = read_snapshot(cat)
    cw = class_max_w(s["class_bits"])
    summary = (f"本机上限 {MODEL_MAX_W} W；当前档位 {s['class']}"
               + (f"（最高 {cw} W）" if cw else "（未知档位）")
               + f"；可升级到 {class_label(max_class_bits())}"
               f"（{MODEL_MAX_W} W）。")
    if args.json:
        print(json.dumps({"model": model_info(), "snapshot": s,
                          "summary": summary},
                         ensure_ascii=False, indent=2))
        return
    print_snapshot(s)
    print(f"\n  能力摘要：{summary}")


def cmd_dump(args) -> None:
    with Cat(args.port, args.baud, timeout=args.timeout) as cat:
        cat.quiet = args.quiet
        s = read_snapshot(cat, args.note)
    with open(args.file, "w", encoding="utf-8") as fh:
        json.dump(s, fh, ensure_ascii=False, indent=2)
    print_snapshot(s)
    print(f"\n  已保存快照：{args.file}")


def _snapshot_byte(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFF:
        raise CatError(f"快照字段 {label} 必须是 0..255 的整数: {value!r}")
    return value


def _snapshot_model(s: dict) -> str:
    """只接受本工具当前格式 v3，且机型必须是 ftdx10。"""
    if s.get("format_version") != 3:
        raise CatError(
            f"快照格式版本 {s.get('format_version')!r} 不受支持；只接受 v3")
    model = s.get("model")
    if not model:
        raise CatError("快照缺少 model 字段；只接受本工具 v3 快照")
    if model != MODEL_KEY:
        raise CatError(f"快照机型 {model!r} 不是 {MODEL_KEY!r}，拒绝使用 FTDX10 布局")
    for key in ("config_byte", "config_pair", "max_power", "ac"):
        if key not in s:
            raise CatError(f"快照缺少字段 {key!r}，不是本工具的 v3 快照")
    _snapshot_byte(s["config_byte"], "config_byte")
    if not isinstance(s["config_pair"], dict):
        raise CatError("快照字段 config_pair 必须是对象")
    for key in ("max_power", "ac"):
        if not isinstance(s[key], dict):
            raise CatError(f"快照字段 {key} 必须是对象")
        for band in BANDS:
            if band not in s[key]:
                raise CatError(f"快照字段 {key} 缺少频段 {band}")
            _snapshot_byte(s[key][band], f"{key}[{band}]")
    return model


def cmd_diff(args) -> None:
    with open(args.a, encoding="utf-8") as fh:
        a = json.load(fh)
    with open(args.b, encoding="utf-8") as fh:
        b = json.load(fh)
    ma, mb = _snapshot_model(a), _snapshot_model(b)
    print(f"  A: {args.a}  {a.get('note', '')!r}  [{ma}]")
    print(f"  B: {args.b}  {b.get('note', '')!r}  [{mb}]\n")
    diff_found = False

    def cfg_of(s: dict) -> int:
        return s["config_byte"]

    cfg_a, cfg_b = cfg_of(a), cfg_of(b)
    for label, s, cfg in (("A", a, cfg_a), ("B", b, cfg_b)):
        cls = s.get("class") or class_label(cfg & CLASS_MASK)
        print(f"  {label}: 配置 0x{cfg:02X} -> {cls}"
              f"（TS={s.get('ts', '-')}, EXP={s.get('exp', '-')}, "
              f"AUX={s.get('aux', '-')}）")
    if cfg_a != cfg_b:
        diff_found = True
        da, db = decode(cfg_a), decode(cfg_b)
        if da["ts"] == db["ts"] and da["exp"] == db["exp"] and da["aux"] == db["aux"]:
            print(f"    -> 差别只在功率档：{da['class_name']} vs {db['class_name']}")
        else:
            print("    -> 除功率档外还有 TS/EXP/AUX 位不同，请逐位核对")

    def _hex(value) -> str:
        return f"0x{value:02X}" if isinstance(value, int) else "?"

    def _magic_desc(s: dict) -> str:
        magic = s.get("magic")
        if magic is None:
            return "?"
        if isinstance(magic, list):
            magic = ", ".join(str(item) for item in magic)
        return f"{magic}（{'OK' if s.get('magic_ok') else '★不符'}）"

    hi_a = (a.get("config_pair") or {}).get(f"0x{CONFIG_PAIR:04X}")
    hi_b = (b.get("config_pair") or {}).get(f"0x{CONFIG_PAIR:04X}")
    if hi_a != hi_b:
        diff_found = True
        print(f"  配置对高字节 FRAM[0x{CONFIG_PAIR:04X}]: "
              f"A={_hex(hi_a)}  B={_hex(hi_b)}")

    if a.get("magic") != b.get("magic") or a.get("magic_ok") != b.get("magic_ok"):
        diff_found = True
        print(f"  FRAM 魔数: A={_magic_desc(a)}  B={_magic_desc(b)}")

    if a.get("init_flag") != b.get("init_flag") or a.get("init_ok") != b.get("init_ok"):
        diff_found = True
        print(f"  初始化标志 FRAM[0x{INIT_FLAG_ADDR:04X}]: "
              f"A={_hex(a.get('init_flag'))}"
              f"（{'OK' if a.get('init_ok') else '★不符'}）  "
              f"B={_hex(b.get('init_flag'))}"
              f"（{'OK' if b.get('init_ok') else '★不符'}）")

    if a.get("fram_version") != b.get("fram_version"):
        diff_found = True
        print(f"  设置版本 FRAM[0x{VERSION_ADDR:04X}]: "
              f"A={_hex(a.get('fram_version'))}  "
              f"B={_hex(b.get('fram_version'))}")

    for band in BANDS:
        va, vb = a.get("max_power", {}).get(band), b.get("max_power", {}).get(band)
        aa, ab = a.get("ac", {}).get(band), b.get("ac", {}).get(band)
        if (va, aa) != (vb, ab):
            diff_found = True
            print(f"  {band:4s}  A: F={str(va):>3} AC={str(aa):>3} "
                  f"(有效 {min(va, aa):3d})"
                  f"   B: F={str(vb):>3} AC={str(ab):>3} "
                  f"(有效 {min(vb, ab):3d})")
    if not diff_found:
        print("\n  两台机在这些字段上完全一致。")
    else:
        bw = CLASS_WATTS.get(b.get("class_bits"), None)
        print(f"\n  建议：以 B 为准。两种做法：")
        print(f"    restore {args.b} --apply                # 精确照抄 B")
        if bw:
            print(f"    set-power {bw} --apply                # 按 B 的档位一键设置")
        else:
            print("    set-power <瓦数> --apply              # 档位未知，先核对 B")


def _confirm(args) -> bool:
    if not args.apply:
        print("\n  [dry-run] 未写设备。确认无误后加 --apply 再执行一次。")
        return False
    return True


def cmd_set_power(args) -> None:
    overrides: Dict[int, int] = {}
    for kv in args.band or []:
        if "=" not in kv:
            raise SystemExit(f"--band 需要 band=value（收到 {kv!r}）")
        k, v = kv.split("=", 1)
        try:
            value = int(v, 0)
        except ValueError:
            raise SystemExit(f"--band {kv!r} 的瓦数无效；请输入整数")
        try:
            idx = band_index(k)
        except CatError as e:
            raise SystemExit(str(e))
        overrides[idx] = value
    ac_mode = args.ac
    if ac_mode not in ("auto", "keep"):
        try:
            ac_mode = int(ac_mode, 0)
        except ValueError:
            raise SystemExit("--ac 只接受 auto / keep / 数字")

    with Cat(args.port, args.baud, timeout=args.timeout) as cat:
        cat.quiet = args.quiet
        check_fram_identity(cat)
        pair = tuple(cat.read_bytes(CONFIG_PAIR, 2))
        f = tuple(cat.read_bytes(F_ADDR, len(BANDS)))
        ac = tuple(cat.read_bytes(AC_ADDR, len(BANDS)))
        plan = make_power_plan(pair, f, ac, args.watts,
                               overrides=overrides, ac_mode=ac_mode)
        print_plan(plan)
        if not plan.changed:
            print("\n  已经是目标状态，无需写入。")
            return
        check_acceptance(plan)
        if not _confirm(args):
            return
        apply_plan(cat, plan)
        s = read_snapshot(cat)
    print("\n  写入后回读：")
    print_snapshot(s)
    bad = [(b, s["max_power"][b], s["ac"][b]) for b in BANDS
           if s["max_power"][b] != plan.new_f[band_index(b)]
           or s["ac"][b] != plan.new_ac[band_index(b)]]
    if bad:
        raise CatError(f"回读与计划不一致：{bad}")
    if plan.class_changed and s["config_byte"] != plan.new_cfg:
        raise CatError(f"配置字节回读不一致：0x{s['config_byte']:02X} "
                       f"!= 0x{plan.new_cfg:02X}")
    if plan.class_changed:
        print("\n  ★ 功率档已改变：请整机断电重开，档位才会在 RAM/菜单中生效。")
    print("  完成。有效上限 = min(F, AC)（AC 即面板 MAX POWER）。")
    if s.get("magic_ok") is False:
        print("  ★ 魔数校验异常：固件可能在重启时重建设置；请勿假定写入值会保留。")
    print(f"  注意：本脚本仅适配 {TARGET_MODEL}（静态逆向，未真机验证）；FRAM[9] 未检查。")


def cmd_set_class(args) -> None:
    bits = CLASS_BITS.get(args.watts)
    if bits is None:
        raise SystemExit(f"没有 {args.watts} W 档"
                         f"（可用：{sorted(CLASS_BITS)}）")
    with Cat(args.port, args.baud, timeout=args.timeout) as cat:
        cat.quiet = args.quiet
        check_fram_identity(cat)
        pair = cat.read_bytes(CONFIG_PAIR, 2)
        old = pair[1]
        new = (old & ~CLASS_MASK) | bits
        d_old, d_new = decode(old), decode(new)
        print(f"  当前 FRAM[0x{CONFIG_ADDR:04X}] = 0x{old:02X}"
              f"（{d_old['class_name']}, TS={d_old['ts']}, EXP={d_old['exp']}）")
        print(f"  目标 = 0x{new:02X}（{d_new['class_name']}, TS={d_new['ts']}, "
              f"EXP={d_new['exp']}, AUX={d_new['aux']}）；只改档位掩码位")
        if d_new["ts_valid"] is False:
            print(f"  ★ 警告：TS={d_new['ts']} 不在固件认可范围 {TS_VALID}，"
                  f"请先确认原值")
        f = tuple(cat.read_bytes(F_ADDR, len(BANDS)))
        ac = tuple(cat.read_bytes(AC_ADDR, len(BANDS)))
        over = []
        for i, b in enumerate(BANDS):
            lo, hi = range_for(bits, i)
            if not (lo <= f[i] <= hi) or not (lo <= ac[i] <= hi):
                over.append((b, f[i], ac[i], lo, hi))
        if over:
            print("  ★ 现有 F/AC 至少一项超出目标档位区间：")
            for b, fv, av, lo, hi in over:
                print(f"      {b}: F={fv} AC={av}，本档 {lo}-{hi}")
            print("     建议改用：set-power <瓦数> --apply（会同时夹取 F/AC）")
        if not _confirm(args):
            return
        cat.apply_write(CONFIG_PAIR, bytes([pair[0], new]))
    print(f"\n  完成。请整机断电重开；重开后档位变为 {class_label(bits)}。")
    print("  若还要改各频段 F/MAX POWER，用：set-power "
          f"{CLASS_WATTS[bits]} --apply（可加 --band hf=..）")


def cmd_set_max(args) -> None:
    ops: Dict[int, int] = {}
    for kv in args.kv:
        if "=" not in kv:
            raise SystemExit(f"参数格式应为 band=value，例如 hf=100（收到 {kv!r}）")
        k, v = kv.split("=", 1)
        k = k.strip().lower()
        try:
            val = int(v, 0)
        except ValueError:
            raise SystemExit(f"参数 {kv!r} 的瓦数无效；请输入整数")
        if k == "all":
            for i in range(len(BANDS)):
                ops[i] = val
        else:
            try:
                idx = band_index(k)
            except CatError as e:
                raise SystemExit(str(e))
            ops[idx] = val

    with Cat(args.port, args.baud, timeout=args.timeout) as cat:
        cat.quiet = args.quiet
        check_fram_identity(cat)
        pair = cat.read_bytes(CONFIG_PAIR, 2)
        d = decode(pair[1])
        bits = d["class_bits"]
        if not d["class_known"]:
            raise CatError(f"当前档位 0x{bits:02X} 未知，先运行 cap/read 核对")
        cur = tuple(cat.read_bytes(F_ADDR, len(BANDS)))
        ac = tuple(cat.read_bytes(AC_ADDR, len(BANDS)))
        buf = list(cur)
        for i, v in sorted(ops.items()):
            lo, hi = range_for(bits, i)
            hi = min(hi, MODEL_MAX_W)
            if v > hi:
                print(f"  ★ {BANDS[i]}: {v} W 超过 {d['class_name']} 档上限 "
                      f"{hi} W，已夹到 {hi} W")
            if v < lo:
                print(f"  ★ {BANDS[i]}: {v} W 低于下限 {lo} W，已抬到 {lo} W")
            buf[i] = max(lo, min(hi, v))
        print(f"  当前档位 {d['class_name']}；RF POWER："
              + "  ".join(f"{b}={buf[i]}" for i, b in enumerate(BANDS)))
        if tuple(buf) == cur:
            print("  无需修改。")
            return
        for i, b in enumerate(BANDS):
            if buf[i] > ac[i]:
                print(f"  ★ {b}: F={buf[i]} > AC={ac[i]}（面板 MAX POWER），"
                      f"有效上限仍为 {ac[i]} W；如需提高请用 set-power（会同步 AC）")
        if not _confirm(args):
            return
        cat.apply_write(F_ADDR, bytes(buf))
    print("\n  完成。有效上限 = min(F, AC)（AC 即面板 MAX POWER）；"
          "断电重开后菜单显示一致。")


def cmd_restore(args) -> None:
    with open(args.file, encoding="utf-8") as fh:
        s = json.load(fh)
    model = _snapshot_model(s)
    hi_key = f"0x{CONFIG_PAIR:04X}"
    ck = f"0x{CONFIG_ADDR:04X}"
    if ck not in s["config_pair"]:
        raise CatError(f"快照缺少配置字节 {ck}")
    cfg_byte = _snapshot_byte(s["config_pair"][ck], f"config_pair[{ck}]")
    pair_hi_raw = s["config_pair"].get(hi_key)
    if pair_hi_raw is None:
        raise CatError(f"快照缺少配置字节对高字节 {hi_key}")
    pair_hi = _snapshot_byte(pair_hi_raw, f"config_pair[{hi_key}]")
    cfg_doc = _snapshot_byte(s["config_byte"], "config_byte")
    if cfg_byte != cfg_doc:
        raise CatError(
            f"快照的 config_byte=0x{cfg_doc:02X} 与 "
            f"config_pair[{ck}]=0x{cfg_byte:02X} 不一致，拒绝写入")
    d = decode(cfg_byte)
    print(f"  载入快照 {args.file}"
          f"（{s.get('created', '')} {s.get('note', '')}，机型 {model}）")
    print_snapshot(s)
    if not d["class_known"]:
        raise CatError(f"快照档位 0x{d['class_bits']:02X} 未知，拒绝写入")
    errs = []
    for i, b in enumerate(BANDS):
        rlo, rhi = range_for(d["class_bits"], i)
        rhi = min(rhi, MODEL_MAX_W)
        for label, key in (("F", "max_power"), ("AC", "ac")):
            v = _snapshot_byte(s[key].get(b), f"{key}[{b}]")
            if not rlo <= v <= rhi:
                errs.append(f"{b} {label}={v} 不在 {rlo}-{rhi}")
    if errs:
        raise CatError("快照数值超出该档位区间，拒绝写入（可先用 set-power 生成合规值）："
                       + "；".join(errs))
    if not _confirm(args):
        return
    with Cat(args.port, args.baud, timeout=args.timeout) as cat:
        cat.quiet = args.quiet
        check_fram_identity(cat)
        cat.apply_write(CONFIG_PAIR, bytes([pair_hi, cfg_byte]))
        cat.apply_write(F_ADDR, bytes(s["max_power"][b] for b in BANDS))
        cat.apply_write(AC_ADDR, bytes(s["ac"][b] for b in BANDS))
        now = read_snapshot(cat)
    print_snapshot(now)
    print(f"\n  已恢复。{HINTS_REBOOT}")


def cmd_ex_read(args) -> None:
    frame = f"EX{args.p1:02d}{args.p2:02d}{args.p3:02d}".encode()
    with Cat(args.port, args.baud, timeout=args.timeout) as cat:
        cat.quiet = args.quiet
        r = cat._raw(frame + b";", expect=0)
    txt = r.decode("ascii", "replace")
    print(f"  EX 读 P1={args.p1:02d} P2={args.p2:02d} P3={args.p3:02d} -> {txt!r}")
    if txt.startswith("?"):
        print("  -> 菜单编号无效：对照手册 Table 2(MENU Chart) 核对 P1/P2/P3，"
              "或直接用面板菜单。")


def cmd_ex_set(args) -> None:
    if not 0 <= args.value <= 999:
        raise SystemExit("EX 的 P4 是 1-4 位数字，本工具限定 0-999")
    frame = f"EX{args.p1:02d}{args.p2:02d}{args.p3:02d}{args.value:03d}".encode()
    print(f"  EX 写 P1={args.p1:02d} P2={args.p2:02d} P3={args.p3:02d} "
          f"P4={args.value}  -> {frame.decode()};")
    if not _confirm(args):
        return
    with Cat(args.port, args.baud, timeout=args.timeout) as cat:
        cat.quiet = args.quiet
        r = cat._raw(frame + b";", expect=0)
    print(f"  回包 {r.decode('ascii', 'replace')!r}（写类命令成功时可能无回包）")
    print("  提示：EX 由固件自己保存到 FRAM，且会按当前档位区间夹取；"
          "等级仍是 50W 时 HF 最多只能设到 50。")


HINTS_REBOOT = ("FRAM 已写入；功率档变更需整机断电重开，"
                "RF POWER / MAX POWER 会随 SPW 写入即时重载。")

EPILOG = """\
常用示例：
  python3 power/power_cat_optimized.py list              # 打印 FRAM 表/档位区间
  python3 power/power_cat_optimized.py -p /dev/ttyUSB0 read   # 一键读取
  python3 power/power_cat_optimized.py -p /dev/ttyUSB0 cap    # 读能力/区间（只读）
  python3 power/power_cat_optimized.py -p /dev/ttyUSB0 dump A.json --note 100W机
  python3 power/power_cat_optimized.py diff A.json B.json     # 两台机对照
  python3 power/power_cat_optimized.py -p /dev/ttyUSB0 set-power 100 --apply
  python3 power/power_cat_optimized.py -p /dev/ttyUSB0 restore A.json --apply
本脚本仅适配 FTDX10 / MAIN V01-15（静态逆向，未真机验证）；详见同目录 power_cat_使用说明.md。"""


# ==========================================================================
# CLI
# ==========================================================================
def _common_parser() -> argparse.ArgumentParser:
    """全局选项的父解析器。

    用 SUPPRESS 作为默认值，这样同一个选项写在子命令前或后都可以，
    且不会因为子命令的默认值把前面的值覆盖掉。
    """
    c = argparse.ArgumentParser(add_help=False)
    c.add_argument("-p", "--port", default=argparse.SUPPRESS,
                   help="串口，如 /dev/ttyUSB0（Enhanced COM Port）")
    c.add_argument("-b", "--baud", type=int, default=argparse.SUPPRESS,
                   help="波特率，须与菜单 CAT RATE 一致（默认 9600）")
    c.add_argument("--timeout", type=float, default=argparse.SUPPRESS,
                   help="串口超时秒数（默认 0.8）")
    c.add_argument("-q", "--quiet", action="store_true",
                   default=argparse.SUPPRESS,
                   help="不打印收发原始字节")
    c.add_argument("--apply", action="store_true", default=argparse.SUPPRESS,
                   help="真正写入（默认 dry-run）")
    return c


def build_parser() -> argparse.ArgumentParser:
    common = _common_parser()
    p = argparse.ArgumentParser(
        prog="power_cat_optimized.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="FTDX10 功率参数 CAT 读写（仅适配 FTDX10 / MAIN V01-15；静态逆向，未真机验证）",
        epilog=EPILOG,
        parents=[common],
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", parents=[common],
                   help="打印 FRAM 参数表与档位区间").set_defaults(func=cmd_list)

    q = sub.add_parser("read", parents=[common], help="一键读取全部相关字段")
    q.add_argument("--json", action="store_true", help="输出 JSON")
    q.add_argument("--note", default="", help="备注")
    q.set_defaults(func=cmd_read)

    q = sub.add_parser("cap", parents=[common], help="读取当前能力/区间（只读）")
    q.add_argument("--json", action="store_true", help="附带 JSON")
    q.set_defaults(func=cmd_cap)

    q = sub.add_parser("dump", parents=[common], help="读取并保存 JSON 快照")
    q.add_argument("file")
    q.add_argument("--note", default="", help="备注，例如 \"100W机\"")
    q.set_defaults(func=cmd_dump)

    q = sub.add_parser("diff", parents=[common], help="对比两个快照")
    q.add_argument("a")
    q.add_argument("b")
    q.set_defaults(func=cmd_diff)

    q = sub.add_parser("set-power", parents=[common],
                       help="一键设功率（自动选档 + 夹取 + 同步 F/AC）")
    q.add_argument("watts", type=int, help="目标瓦数，例如 100")
    q.add_argument("--band", action="append", metavar="band=value",
                   help="单独覆写某频段，可重复：--band hf=100 --band am=25")
    q.add_argument("--ac", default="auto",
                   help="面板 MAX POWER（AC）：auto（默认，跟随目标 F）/ keep / 数字")
    q.set_defaults(func=cmd_set_power)

    q = sub.add_parser("set-class", parents=[common],
                       help="只改功率档位（FRAM 配置字节 bit3/4）")
    q.add_argument("watts", type=int, help="档位瓦数：100/50/20/10")
    q.set_defaults(func=cmd_set_class)

    q = sub.add_parser("set-rf", aliases=["set-max"], parents=[common],
                       help="只改 RF POWER（F），不动 MAX POWER（AC）")
    q.add_argument("kv", nargs="+", metavar="band=value",
                   help="hf= / 50m= / 70m= / am= / all=")
    q.set_defaults(func=cmd_set_max)

    q = sub.add_parser("restore", parents=[common],
                       help="按快照精确回写（校验区间后写入）")
    q.add_argument("file")
    q.set_defaults(func=cmd_restore)

    q = sub.add_parser("ex-read", parents=[common], help="用文档命令 EX 读菜单项")
    q.add_argument("--p1", type=int, default=4)
    q.add_argument("--p2", type=int, default=4)
    q.add_argument("--p3", type=int, default=1, help="1=HF 2=50M 3=70M 4=AM")
    q.set_defaults(func=cmd_ex_read)

    q = sub.add_parser("ex-set", parents=[common], help="用文档命令 EX 设菜单项")
    q.add_argument("value", type=int)
    q.add_argument("--p1", type=int, default=4)
    q.add_argument("--p2", type=int, default=4)
    q.add_argument("--p3", type=int, default=1, help="1=HF 2=50M 3=70M 4=AM")
    q.set_defaults(func=cmd_ex_set)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    # 选项可写在子命令前或后；未出现的用这里的默认值补齐。
    args.port = getattr(args, "port", None)
    args.baud = getattr(args, "baud", 9600)
    args.timeout = getattr(args, "timeout", 0.8)
    args.quiet = getattr(args, "quiet", False)
    args.apply = getattr(args, "apply", False)

    need_port = args.cmd not in ("list", "diff")
    if need_port and not args.port:
        print("需要 -p/--port 指定串口（list/diff 可不连设备）",
              file=sys.stderr)
        return 2

    try:
        args.func(args)
    except CatError as e:
        print(f"\n[错误] {e}", file=sys.stderr)
        return 3
    except OSError as e:  # 串口打开/读写失败（pyserial.SerialException 是 OSError 子类）
        print(f"\n[串口错误] {e}", file=sys.stderr)
        return 4
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            return 2
        return int(e.code or 0)
    except ValueError as e:
        print(f"\n[参数错误] {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
