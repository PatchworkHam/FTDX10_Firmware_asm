#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
往返一致性测试 (round-trip test)
================================

用 `decoded/firmware/` 里的**可回打包明文文件**重新加密打包成 *.SFL，
再与 `original/firmware/` 里的**原始 *.SFL** 逐字节比较，并打印 MD5。

预期：每个文件字节完全一致、MD5 完全相同 —— 证明解密/加密完全可逆且无损。

用法
----
    python3 decoded/verify/roundtrip_test.py
    python3 decoded/verify/roundtrip_test.py --repo /path/to/original_firmware
    python3 decoded/verify/roundtrip_test.py --plain-dir /path/to/firmware
"""

import argparse
import hashlib
import sys

from _sfl_common import DEFAULT_PLAIN_DIR, DEFAULT_REPO, HEADER_LEN, PAIRS, build_sfl


def main(argv=None):
    ap = argparse.ArgumentParser(description="Yaesu SFL 往返一致性测试")
    ap.add_argument("--repo", default=str(DEFAULT_REPO),
                    help=f"含原始 *.SFL 的目录（默认 {DEFAULT_REPO}）")
    ap.add_argument("--plain-dir", default=str(DEFAULT_PLAIN_DIR),
                    help=f"含回打包明文的目录（默认 {DEFAULT_PLAIN_DIR}）")
    args = ap.parse_args(argv)

    print(f"原始 SFL 目录: {args.repo}")
    print(f"明文目录:      {args.plain_dir}")
    print(f"{'结果':<4} {'文件':<26} {'大小':>9}  {'原始 MD5':<32} {'重建 MD5'}")
    print("-" * 108)
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
        rebuilt = build_sfl(orig[:HEADER_LEN], plain)
        m_orig = hashlib.md5(orig).hexdigest()
        m_new = hashlib.md5(rebuilt).hexdigest()
        ok = (orig == rebuilt) and (m_orig == m_new)
        allok &= ok
        print(f"{'OK' if ok else 'BAD':<4} {sfl_name:<26} {len(orig):>9}  {m_orig:<32} {m_new}")
    print("-" * 108)
    print("结论: 全部字节级一致，往返无损" if allok else "结论: 存在差异")
    return 0 if allok else 2


if __name__ == "__main__":
    sys.exit(main())
