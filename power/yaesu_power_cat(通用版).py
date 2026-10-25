#!/usr/bin/env python3
"""通过 CAT ID 识别 Yaesu 机型，并修改已知布局的功率等级。"""
import argparse
import sys
import time

try:
    import serial
except ImportError:
    serial = None

# ID; 返回的机型 ID。新增机型前，先核实官方 CAT 手册中的 ID。
MODELS = {
    b"0650": "FT-891 AH065_M_V0110",
    b"0761": "FTDX10 MAIN V01-15",
}
CONFIG = 0x0006                 # 写入两个字节，功率等级在第二字节
MASK = 0x18                     # FRAM[0x0007] bit 3-4
WATTS = {100: 0x18, 50: 0x00, 20: 0x08, 10: 0x10}
BITS_TO_WATTS = {value: watts for watts, value in WATTS.items()}


class CatError(RuntimeError):
    pass


def frame(cmd, addr, data=b""):
    if not 0 <= addr < 0x8000:
        raise CatError(f"FRAM 地址越界：0x{addr:04X}")
    body = cmd + addr.to_bytes(2, "big") + data
    checksum = sum(body) & 0xFF
    # SP 是二进制子协议；0x3B 会与 ASCII 帧结束符 ';' 冲突。
    if 0x3B in body[2:] or checksum == 0x3B:
        raise CatError("SP 帧参数或校验和包含 0x3B (';')，为避免帧截断已拒绝发送")
    return body + bytes((checksum,)) + b";"


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
        # 一次 CAT 查询；例如 ID0650; 或 ID0761;
        r = self.exchange(b"ID;", 7, 0.15)
        if len(r) != 7 or not r.startswith(b"ID") or not r.endswith(b";"):
            return None, r
        return MODELS.get(r[2:6]), r


def main():
    p = argparse.ArgumentParser(description="仅修改 FRAM[0x0007] 的功率等级位")
    p.add_argument("-p", "--port", required=True)
    p.add_argument("--baud", type=int, default=38400)
    p.add_argument("--timeout", type=float, default=0.8)
    p.add_argument("--watts", type=int, choices=WATTS,
                   help="目标功率档位：100、50、20 或 10 W")
    p.add_argument("--read", action="store_true", help="只读取并显示当前功率档位")
    p.add_argument("--apply", action="store_true", help="实际写入；默认只预览")
    p.add_argument("--force", action="store_true",
                   help="ID 未识别时仍继续；要求该机型已确认使用相同 FRAM 布局")
    a = p.parse_args()
    if a.read and (a.watts is not None or a.apply):
        p.error("--read 不能与 --watts 或 --apply 同时使用")
    if not a.read and a.watts is None:
        p.error("请指定 --read，或指定 --watts 选择目标功率")

    cat = Cat(a.port, a.baud, a.timeout)
    try:
        profile, id_response = cat.identify()
        if profile is None:
            if not a.force:
                raise CatError(
                    f"ID; 未识别或响应格式异常：{id_response.hex(' ')}；"
                    "请核实机型 ID 后更新 MODELS，或使用 --force 强制继续"
                )
            print(f"警告：ID 未识别 ({id_response.hex(' ')}); --force 已启用。")
            print("警告：将按现有 FRAM 布局操作；确认该机型布局相同后再使用 --apply。")
        else:
            print(f"机型识别：{profile} (CAT {id_response.decode('ascii', 'replace')})")

        old = cat.read(CONFIG)
        current_watts = BITS_TO_WATTS.get(old[1] & MASK)
        if current_watts is None:
            raise CatError(f"当前功率档位编码未知：FRAM[0x{CONFIG + 1:04X}] = 0x{old[1]:02X}")
        print(f"当前功率档位：{current_watts} W")
        if a.read:
            return 0

        new_hi = (old[1] & ~MASK) | WATTS[a.watts]
        print(f"FRAM[0x{CONFIG:04X}..0x{CONFIG + 1:04X}]: {old.hex(' ')} -> "
              f"{old[0]:02X} {new_hi:02X}；目标 {a.watts} W")
        if new_hi == old[1]:
            print("已经是目标档位，无需修改。")
            return 0
        if not a.apply:
            print("预览模式：确认无误后添加 --apply。")
            return 0

        cat.write_pair(CONFIG, bytes((old[0], new_hi)))
        back = cat.read(CONFIG)
        if back != bytes((old[0], new_hi)):
            raise CatError(f"写后回读不一致：{back.hex(' ')}")
        print("写入并回读验证成功；请整机断电重启后确认档位生效。")
        return 0
    finally:
        cat.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (CatError, OSError, ValueError) as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)
