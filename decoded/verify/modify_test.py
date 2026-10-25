#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
改动后重新打包 + 校验自洽测试 (modify -> repack -> recompute CRC)
=================================================================

模拟真实需求：**修改解密后的固件，再重新加密打包，并自动重算元数据校验字段**，
然后验证：

  1. 对**原始** .SFL，复算的校验 == 标头声明值（算法正确）；
  2. 修改明文任意字节后重打包，新文件的校验字段 == 新密文按算法复算的值；
  3. 新文件解密回来 == 修改后的明文（改动确实生效、无损）；
  4. 若**不重算**，校验字段会停留在旧值 -> 与数据不符（反证）。

用法
----
    python3 decoded/verify/modify_test.py
    python3 decoded/verify/modify_test.py --repo /path/to/original_firmware
    python3 decoded/verify/modify_test.py --plain-dir /path/to/firmware
"""

import argparse
import hashlib
import sys

from _sfl_common import (
    DEFAULT_PLAIN_DIR,
    DEFAULT_REPO,
    HEADER_LEN,
    PAIRS,
    TOOL,
    build_sfl,
    decode_sfl,
    read_declared_crc,
)


def modify(plain: bytes, n: int = 8) -> bytes:
    """在数据段中段翻转若干字节，模拟一次改动。"""
    out = bytearray(plain)
    mid = len(out) // 3
    for k in range(n):
        i = (mid + k * 4099) % len(out)
        out[i] ^= 0xA5
    return bytes(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="SFL 改动重打包 + 校验自洽测试")
    ap.add_argument("--repo", default=str(DEFAULT_REPO),
                    help=f"含原始 *.SFL 的目录（默认 {DEFAULT_REPO}）")
    ap.add_argument("--plain-dir", default=str(DEFAULT_PLAIN_DIR),
                    help=f"含回打包明文的目录（默认 {DEFAULT_PLAIN_DIR}）")
    args = ap.parse_args(argv)

    print(f"原始 SFL 目录: {args.repo}")
    print(f"明文目录:      {args.plain_dir}\n")
    allok = True
    for sfl_name, plain_name in PAIRS:
        sfl_path = f"{args.repo}/{sfl_name}"
        plain_path = f"{args.plain_dir}/{plain_name}"
        try:
            orig = open(sfl_path, "rb").read()
            plain = open(plain_path, "rb").read()
        except OSError as exc:
            print(f"SKIP {sfl_name} ({exc})")
            allok = False
            continue

        header = orig[:HEADER_LEN]

        # (1) 原始文件：复算校验 == 声明值
        calc = TOOL.compute_crc(TOOL.encode_payload(plain))
        declared = read_declared_crc(orig)
        c1 = (calc == declared)

        # (2)(3) 修改 -> 重打包(自动重算校验) -> 解密验证
        plain2 = modify(plain)
        rebuilt = build_sfl(header, plain2)              # 默认自动重算 CRC
        new_crc = read_declared_crc(rebuilt)
        expect_crc = TOOL.compute_crc(rebuilt[HEADER_LEN:])
        c2 = (new_crc == expect_crc)
        c3 = (decode_sfl(rebuilt) == plain2)

        # (4) 反证：不重算则校验字段仍是旧值(与数据不符)
        stale = build_sfl(header, plain2, fix_crc=False)
        c4 = (read_declared_crc(stale) == declared) and (declared != expect_crc)

        ok = c1 and c2 and c3 and c4
        allok &= ok
        print(f"[{'OK' if ok else 'BAD'}] {sfl_name}")
        print(f"      原始复算={calc:08X} 声明={declared:08X} {'OK' if c1 else 'FAIL'}   "
              f"改动后新校验={new_crc:08X} (复算={expect_crc:08X}) {'OK' if c2 else 'FAIL'}")
        print(f"      改动解密回读一致 {'OK' if c3 else 'FAIL'}   "
              f"未重算则为旧值/失配 {'OK' if c4 else 'FAIL'}   "
              f"新MD5={hashlib.md5(rebuilt).hexdigest()}")

    print("\n结论: " + ("改动后重打包、校验自动重算且自洽" if allok else "存在失败项"))
    return 0 if allok else 2


if __name__ == "__main__":
    sys.exit(main())
