#!/usr/bin/env python3
"""通过 CAT SPR/SPW 读写 FTDX10 FRAM[0x0007] 的 TS / EXP 位。

布局（FRAM[0x0007]）：
    bit0-2  TS    选择 0x32B2A 的 8 张波段表
    bit3-4  PWR   功率档（保持不动）
    bit5    EXP   选择 0x32B54（1.8-30 / 50-54 / 70-70.5）
    bit6-7  AUX   本版未使用（保持不动）

优先级：服务模式 > EXP > TS。EXP=1 会整体替换 TS 表。

用法：
    python3 yaesu_ts_cat.py --table all
    python3 yaesu_ts_cat.py -p /dev/ttyUSB0 --read
    python3 yaesu_ts_cat.py -p /dev/ttyUSB0 --ts 2
    python3 yaesu_ts_cat.py -p /dev/ttyUSB0 --ts 2 --apply
    python3 yaesu_ts_cat.py -p /dev/ttyUSB0 --exp on --apply

安全：默认只预览；写入后自动回读校验；改完必须整机断电重启。
"""
import argparse
import sys
import time

try:
    import serial
except ImportError:
    serial = None

# ---------------------------------------------------------------- 机型
# ID; 返回的机型 ID。新增机型前先核实 CAT 手册与 FRAM 布局。
MODELS = {
    b"0761": "FTDX10 MAIN V01-15",
}

# ---------------------------------------------------------------- FRAM 布局
CONFIG = 0x0006          # 一次读写 2 字节：FRAM[0x0006]、FRAM[0x0007]
TS_MASK = 0x07           # FRAM[0x0007] bit0-2
PWR_MASK = 0x18          # bit3-4
EXP_BIT = 0x20           # bit5

PWR_WATTS = {0x18: 100, 0x00: 50, 0x08: 20, 0x10: 10}

# ---------------------------------------------------------------- 频段表
# 单位 MHz；数据来自 MAIN V01-15 的 0x32B6E..0x32DCC（end 已按“上限-1”折算）。
TS_TABLES = {
    0: [(1.8, 2.0), (3.5, 4.0), (7.0, 7.3), (10.1, 10.15),
        (14.0, 14.35), (18.068, 18.168), (21.0, 21.45),
        (24.89, 24.99), (28.0, 29.7), (50.0, 54.0)],
    1: [(1.8, 2.0), (3.5, 4.0), (7.0, 7.3), (10.1, 10.15),
        (14.0, 14.35), (18.068, 18.168), (21.0, 21.45),
        (24.89, 24.99), (28.0, 29.7), (50.0, 54.0)],
    2: [(1.81, 2.0), (3.5, 3.8), (5.25, 5.4065), (7.0, 7.2),
        (10.1, 10.15), (14.0, 14.35), (18.068, 18.168),
        (21.0, 21.45), (24.89, 24.99), (28.0, 29.7),
        (50.0, 52.0), (70.0, 70.5)],
    3: [(1.8, 2.0), (3.5, 3.8), (5.3515, 5.3665), (7.0, 7.2),
        (10.1, 10.15), (14.0, 14.35), (18.068, 18.168),
        (21.0, 21.45), (24.89, 24.99), (28.0, 29.7), (50.0, 52.0)],
    4: [(1.83, 1.85), (3.5, 3.8), (7.0, 7.1), (10.1, 10.15),
        (14.0, 14.35), (18.068, 18.168), (21.0, 21.45),
        (24.89, 24.99), (28.0, 29.7), (50.0, 52.0)],
    5: [(1.8, 2.0), (3.5, 4.0), (7.0, 7.3), (10.1, 10.15),
        (14.0, 14.35), (18.068, 18.168), (21.0, 21.45),
        (24.89, 24.99), (28.0, 29.7), (50.0, 54.0)],
    6: [(1.8, 2.0), (3.5, 4.0), (7.0, 7.3), (10.1, 10.15),
        (14.0, 14.35), (18.068, 18.168), (21.0, 21.45),
        (24.89, 24.99), (28.0, 29.7), (50.0, 54.0)],
    7: [(1.8, 2.0), (3.5, 4.0), (5.3515, 5.3665), (7.0, 7.5),
        (10.0, 10.5), (14.0, 14.5), (18.0, 18.5),
        (21.0, 21.5), (24.5, 25.0), (28.0, 30.0), (50.0, 54.0)],
}

EXP_TABLE = [(1.8, 30.0), (50.0, 54.0), (70.0, 70.5)]
SERVICE_TABLE = [(1.8, 75.0)]

# 5 MHz 预设（初始化 0x8522；仅 TS=0/2 写 13 条，TS=1 写 1 条专用 VFO）
TS_PRESETS = {
    0: [5.3305, 5.3465, 5.3570, 5.3715, 5.4035,
        5.3320, 5.3480, 5.3585, 5.3730, 5.4050],
    2: [5.2585, 5.2785, 5.2885, 5.3665, 5.3715, 5.3985, 5.4035],
}

# 专用 VFO（FRAM 0x5A4D）：TS=0 → 0x89BA（5.1675）；TS=1 → 0x89C8（4.6300）
TS_VFO = {0: 5.1675, 1: 4.6300}

# 0x1502 / 0x153A 的“配置有效”检查：{0,2,3,6} 直接接受；
# TS==1 需要 PWR != 0x10 才接受；{4,5,7} 不置 0xFF204A.7。
TS_NORMAL = {0, 2, 3, 6}


class CatError(RuntimeError):
    pass


def fmt_ranges(segments):
    return " / ".join(f"{a:g}-{b:g}" for a, b in segments)


def effective_table(ts, exp):
    if exp:
        return EXP_TABLE, "EXP=1（覆盖 TS）"
    return TS_TABLES.get(ts), f"TS={ts}"


def print_tables(which="all"):
    if which in ("all", "ts"):
        print("TS 频段表（MHz，来源 MAIN V01-15）")
        print("=" * 78)
        for ts in range(8):
            print(f"TS={ts}: {fmt_ranges(TS_TABLES[ts])}")
        print()
    if which in ("all", "exp"):
        print(f"EXP   : {fmt_ranges(EXP_TABLE)}")
    if which in ("all", "service"):
        print(f"服务  : {fmt_ranges(SERVICE_TABLE)}（0xFF2020.7；入口 ZZPE001000; 软复位）")
    if which in ("all", "ts"):
        print()
        print("5 MHz 预设（初始化 0x8522；改 TS 后需恢复设置/初始化才重写）")
        for ts, freqs in TS_PRESETS.items():
            print(f"  TS={ts}: {', '.join(f'{f:.4f}' for f in freqs)}")
        print("  TS=3/4/5/6/7: 不初始化 5 MHz 预设")
        print("  专用 VFO（FRAM 0x5A4D）：TS=0 → 5.1675；TS=1 → 4.6300")


class Cat:
    def __init__(self, port, baud, timeout):
        if serial is None:
            raise CatError("需要 pyserial：pip install pyserial")
        self.s = serial.Serial(port, baudrate=baud, bytesize=8, parity="N",
                               stopbits=2, timeout=timeout)

    def close(self):
        self.s.close()

    def exchange(self, request, expected, wait=0.12):
        self.s.reset_input_buffer()
        self.s.write(request)
        time.sleep(wait)
        n = max(expected, self.s.in_waiting or 0)
        return self.s.read(n)

    def read(self, addr):
        r = self.exchange(frame(b"SPR", addr), 9)
        if (len(r) != 9 or r[:3] != b"SPR" or r[3:5] != addr.to_bytes(2, "big")
                or r[7] != (sum(r[:7]) & 0xFF) or r[8:] != b";"):
            raise CatError(f"FRAM 读取/校验失败，地址 0x{addr:04X}: {r.hex(' ')}")
        return r[5:7]

    def write_pair(self, addr, data):
        if len(data) != 2 or addr + 1 >= 0x8000:
            raise CatError("SPW 必须写入两个字节，且地址范围必须位于 0x0000–0x7FFF")
        r = self.exchange(frame(b"SPW", addr, data), 2)
        if r != b"A;":
            raise CatError(f"FRAM 写入未确认: {r.hex(' ')}")

    def identify(self):
        r = self.exchange(b"ID;", 7, 0.15)
        if len(r) != 7 or not r.startswith(b"ID") or not r.endswith(b";"):
            return None, r
        return MODELS.get(r[2:6]), r


def frame(cmd, addr, data=b""):
    if not 0 <= addr < 0x8000:
        raise CatError(f"FRAM 地址越界：0x{addr:04X}")
    body = cmd + addr.to_bytes(2, "big") + data
    checksum = sum(body) & 0xFF
    # SP 是二进制子协议；0x3B 会与 ASCII 帧结束符 ';' 冲突。
    if 0x3B in body[2:] or checksum == 0x3B:
        raise CatError("SP 帧参数或校验和包含 0x3B (';')，为避免帧截断已拒绝发送")
    return body + bytes((checksum,)) + b";"


def safe_write_pair(cat, addr, data):
    """优先写 addr；若帧里含 0x3B，退回写 addr+1（保留其后一字节）。

    返回 (实际写入地址, 期望回读的 2 字节)。
    """
    try:
        cat.write_pair(addr, data)
        return addr, data
    except CatError as e:
        if "0x3B" not in str(e):
            raise
        alt = addr + 1
        old_alt = cat.read(alt)          # (FRAM[alt], FRAM[alt+1])
        alt_data = bytes((data[1], old_alt[1]))
        cat.write_pair(alt, alt_data)
        return alt, alt_data


def main():
    p = argparse.ArgumentParser(
        description="读写 FRAM[0x0007] 的 TS/EXP；保留 PWR 与 AUX 位")
    p.add_argument("-p", "--port", help="串口，例如 /dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=38400)
    p.add_argument("--timeout", type=float, default=0.8)
    p.add_argument("--table", choices=["all", "ts", "exp", "service"],
                   help="离线打印频段表，不连接设备")
    p.add_argument("--read", action="store_true",
                   help="只读取并显示当前 TS/EXP/PWR 与覆盖")
    p.add_argument("--ts", type=int, choices=range(8), metavar="0-7",
                   help="目标 TS（0-7）")
    p.add_argument("--exp", choices=["keep", "on", "off"], default="keep",
                   help="EXP 位：keep（默认）/ on / off")
    p.add_argument("--apply", action="store_true", help="实际写入；默认只预览")
    p.add_argument("--force", action="store_true",
                   help="ID 未识别时仍继续；要求已确认该机型使用相同 FRAM 布局")
    a = p.parse_args()

    if a.table:
        print_tables(a.table)
        return 0
    if not a.port:
        p.error("请指定 -p/--port，或使用 --table 离线查看")
    if a.read and (a.ts is not None or a.exp != "keep" or a.apply):
        p.error("--read 不能与 --ts / --exp / --apply 同时使用")
    if not a.read and a.ts is None and a.exp == "keep":
        p.error("请指定 --read，或指定 --ts / --exp")

    cat = Cat(a.port, a.baud, a.timeout)
    try:
        profile, id_response = cat.identify()
        if profile is None:
            if not a.force:
                raise CatError(
                    f"ID; 未识别或响应格式异常：{id_response.hex(' ')}；"
                    "请核实机型 ID 后更新 MODELS，或使用 --force 强制继续")
            print(f"警告：ID 未识别 ({id_response.hex(' ')}); --force 已启用。")
            print("警告：将按现有 FRAM 布局操作；确认该机型布局相同后再使用 --apply。")
        else:
            print(f"机型识别：{profile} (CAT {id_response.decode('ascii', 'replace')})")

        old = cat.read(CONFIG)          # old[0]=FRAM[0x0006], old[1]=FRAM[0x0007]
        ts = old[1] & TS_MASK
        exp = bool(old[1] & EXP_BIT)
        pwr_bits = old[1] & PWR_MASK
        pwr = PWR_WATTS.get(pwr_bits)

        print(f"FRAM[0x0006..0x0007] = {old.hex(' ')}")
        print(f"当前 TS={ts}，EXP={1 if exp else 0}，"
              f"PWR=0x{pwr_bits:02X}" + (f"（{pwr} W）" if pwr else "（未知档）"))
        segs, why = effective_table(ts, exp)
        print(f"当前覆盖（{why}）：{fmt_ranges(segs)}")

        if a.read:
            return 0

        new_ts = ts if a.ts is None else a.ts
        new_exp = exp
        if a.exp == "on":
            new_exp = True
        elif a.exp == "off":
            new_exp = False

        new_byte = old[1] & ~(TS_MASK | EXP_BIT)
        new_byte |= (new_ts & TS_MASK)
        if new_exp:
            new_byte |= EXP_BIT

        print()
        print(f"目标：TS={new_ts}，EXP={1 if new_exp else 0}，"
              f"PWR=0x{pwr_bits:02X}（保持不变），AUX=0x{old[1] & 0xC0:02X}（保持不变）")
        segs2, why2 = effective_table(new_ts, new_exp)
        print(f"目标覆盖（{why2}）：{fmt_ranges(segs2)}")
        print(f"FRAM[0x0007]: 0x{old[1]:02X} -> 0x{new_byte:02X}")

        if new_ts not in TS_NORMAL and new_ts != 1:
            print(f"警告：TS={new_ts} 不在常规有效集合 {{0,2,3,6}}；"
                  "0x1502 不会置“配置有效”标志（0xFF204A.7）。表仍存在，建议实机验证。")
        if new_ts == 1 and pwr_bits == 0x10:
            print("警告：TS=1 需要 PWR != 0x10 才被 0x153A 接受；当前是 10 W 档。")
        if new_exp:
            print("警告：EXP=1 会整体覆盖 TS 表；恢复/初始化设置时 bit5 会被 JP1006 覆盖。")
        if new_ts in (2, 3, 7):
            print("提示：该 TS 含 5 MHz 段；5 MHz 预设只在初始化时按 TS 重写，"
                  "已有预设不会自动改变。")
        if new_ts in TS_VFO:
            print(f"提示：TS={new_ts} 的专用 VFO 预设为 {TS_VFO[new_ts]:.4f} MHz"
                  "（初始化时写入 FRAM 0x5A4D）。")

        if new_byte == old[1]:
            print("已经是目标值，无需修改。")
            return 0
        if not a.apply:
            print("预览模式：确认无误后添加 --apply。")
            return 0

        addr, expected = safe_write_pair(cat, CONFIG, bytes((old[0], new_byte)))
        back = cat.read(addr)
        if back != expected:
            raise CatError(f"写后回读不一致：{back.hex(' ')}")
        print(f"写入 0x{addr:04X} 并回读验证成功；请整机断电重启后确认。")
        return 0
    finally:
        cat.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (CatError, OSError, ValueError) as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)
