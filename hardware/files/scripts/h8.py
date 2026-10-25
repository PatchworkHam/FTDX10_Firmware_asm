#!/usr/bin/env python3
"""Robust reader for main_disasm.txt: reconstruct full instructions
(the file splits >4-byte instructions across a continuation line).
"""

import os
import re
import sys
from pathlib import Path

# 反汇编清单默认取本目录的上一级 disasm/
# 可通过环境变量 FTDX10_DISASM 覆盖
PATH = Path(
    os.environ.get(
        "FTDX10_DISASM",
        str(Path(__file__).resolve().parents[1] / "disasm" / "main_disasm.txt"),
    )
)

# 地址 + 冒号 + 机器码 + 可选的制表符和汇编文本
LINE_RE = re.compile(
    r"^\s*([0-9a-fA-F]{3,16}):\s*([0-9a-fA-F ]*?)\s*(?:\t(.*))?$"
)


def load():
    """Load instructions and merge continuation lines."""
    ins = []
    cur = None

    try:
        with PATH.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\r\n")
                m = LINE_RE.match(line)

                if not m:
                    continue

                addr = int(m.group(1), 16)
                by = m.group(2).strip()
                mn = (m.group(3) or "").strip()

                if mn:
                    if cur is not None:
                        ins.append(cur)
                    cur = [addr, by, mn]
                elif cur is not None and by:
                    # 续行：将机器码追加到上一条指令
                    cur[1] = (cur[1] + " " + by).strip()

        if cur is not None:
            ins.append(cur)

    except OSError as exc:
        raise RuntimeError(f"无法读取反汇编文件 {PATH}: {exc}") from exc

    return ins


INS = load()
BYADDR = {i[0]: i for i in INS}


def show(a, b):
    """Print instructions with address in [a, b)."""
    for i in INS:
        if a <= i[0] < b:
            print(f"  {i[0]:6x}:  {i[1]:<22} {i[2]}")
    print()


def find(pat, limit=200):
    """Search instructions using a regular expression."""
    if limit <= 0:
        print("limit 必须大于 0", file=sys.stderr)
        return

    try:
        rx = re.compile(pat, re.IGNORECASE)
    except re.error as exc:
        print(f"无效的正则表达式: {exc}", file=sys.stderr)
        return

    n = 0
    for i in INS:
        if rx.search(i[2]):
            print(f"  {i[0]:6x}:  {i[1]:<22} {i[2]}")
            n += 1
            if n >= limit:
                break

    print(f"  [{n} shown]")


def ctx(addr, before=0, after=0):
    """Print instructions surrounding an address."""
    if before < 0 or after < 0:
        print("before 和 after 不能为负数", file=sys.stderr)
        return

    idx = next(
        (k for k, i in enumerate(INS) if i[0] == addr),
        None,
    )

    if idx is None:
        print(f"  no instruction at {addr:x}")
        return

    start = max(0, idx - before)
    end = min(len(INS), idx + after + 1)

    for k in range(start, end):
        mark = ">>" if k == idx else "  "
        print(f"{mark}{INS[k][0]:6x}:  {INS[k][1]:<22} {INS[k][2]}")


def main():
    if len(sys.argv) < 2:
        print(
            "Usage:\n"
            "  script.py show START END\n"
            "  script.py find REGEX [LIMIT]\n"
            "  script.py ctx ADDRESS [BEFORE] [AFTER]",
            file=sys.stderr,
        )
        return 2

    cmd = sys.argv[1]

    try:
        if cmd == "show" and len(sys.argv) == 4:
            show(int(sys.argv[2], 16), int(sys.argv[3], 16))

        elif cmd == "find" and len(sys.argv) in (3, 4):
            limit = int(sys.argv[3]) if len(sys.argv) == 4 else 200
            find(sys.argv[2], limit)

        elif cmd == "ctx" and 3 <= len(sys.argv) <= 5:
            addr = int(sys.argv[2], 16)
            before = int(sys.argv[3]) if len(sys.argv) >= 4 else 0
            after = int(sys.argv[4]) if len(sys.argv) >= 5 else 0
            ctx(addr, before, after)

        else:
            print("参数错误。", file=sys.stderr)
            print(
                "Usage: script.py show START END | "
                "find REGEX [LIMIT] | ctx ADDRESS [BEFORE] [AFTER]",
                file=sys.stderr,
            )
            return 2

    except ValueError as exc:
        print(f"参数格式错误: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
