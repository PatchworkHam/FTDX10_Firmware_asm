#!/usr/bin/env python3
"""比对原厂 MAIN S-record 与 100W 补丁成品。

校验三层结论：

1. 每条 S-record 的计数字段与校验和全部有效；
2. 只有 5 行 S-record 不同；
3. 解出的地址空间中只有文档列出的 5 个字节不同，且旧/新值精确匹配。

在仓库根目录运行：
    python3 power/verify/v_kr2.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
ORIG = ROOT / "decoded" / "firmware" / "FTDX10_MAIN_V0115.srec"
PATCHED = ROOT / "power" / "patched" / "MAIN_V0115_M_mask100W.srec"
EXPECTED_DIFF_LINES = 5

# 文件偏移 -> (原厂字节, 补丁字节)
EXPECTED_BYTES = {
    0x8112: (0xE8, 0xF8),
    0x1595C: (0xE0, 0xF0),
    0x159D4: (0xE0, 0xF0),
    0x3BB8A: (0xEE, 0xFE),
    0x3BCA6: (0xEA, 0xFA),
}


def read_lines(path: pathlib.Path) -> list[str]:
    if not path.is_file():
        sys.exit(f"找不到文件: {path}")
    return path.read_text(encoding="utf-8").splitlines()


def load_srec(path: pathlib.Path) -> dict[int, int]:
    """把 S1/S2/S3 数据记录展开成 {地址: 字节}。"""
    image: dict[int, int] = {}
    for line in read_lines(path):
        if len(line) < 4 or line[0] != "S" or line[1] not in "123":
            continue
        raw = bytes.fromhex(line[2:])
        addr_len = {"1": 2, "2": 3, "3": 4}[line[1]]
        addr = int.from_bytes(raw[1:1 + addr_len], "big")
        payload = raw[1 + addr_len:-1]
        for offset, value in enumerate(payload):
            image[addr + offset] = value
    return image


def validate_srec(path: pathlib.Path) -> tuple[int, list[str]]:
    """校验每条 S-record 的计数字段与校验和，返回 (记录数, 错误列表)。

    S-record 校验和定义：除校验和自身外所有字节之和取低 8 位再取反。
    等价写法是 (sum(其余字节) + 校验和) & 0xFF == 0xFF。
    """
    errors: list[str] = []
    total = 0
    for line_no, line in enumerate(read_lines(path), 1):
        if len(line) < 4 or line[0] != "S" or line[1] not in "0123456789":
            continue
        try:
            raw = bytes.fromhex(line[2:])
        except ValueError:
            errors.append(f"第{line_no}行: 非法十六进制记录 {line!r}")
            continue
        total += 1
        if not raw:
            errors.append(f"第{line_no}行: 空记录")
            continue
        count = raw[0]
        if count != len(raw) - 1:
            errors.append(
                f"第{line_no}行: 计数 {count} 与记录长度 {len(raw) - 1} 不符")
        if (sum(raw[:-1]) + raw[-1]) & 0xFF != 0xFF:
            errors.append(
                f"第{line_no}行: 校验和错误"
                f"（sum=0x{sum(raw[:-1]) & 0xFF:02X}, chk=0x{raw[-1]:02X}）")
    return total, errors


def main() -> int:
    orig = read_lines(ORIG)
    patched = read_lines(PATCHED)
    print("行数 orig/patch:", len(orig), len(patched))
    if len(orig) != len(patched):
        print("错误: 两个 S-record 行数不一致", file=sys.stderr)
        return 1

    print("S-record 校验和:")
    checksum_ok = True
    for label, path in (("原厂", ORIG), ("补丁", PATCHED)):
        total, errors = validate_srec(path)
        if errors:
            checksum_ok = False
            print(f"  {label}: {total} 条 S-record，{len(errors)} 条错误",
                  file=sys.stderr)
            for err in errors[:20]:
                print(f"    {err}", file=sys.stderr)
            if len(errors) > 20:
                print(f"    ... 其余 {len(errors) - 20} 条省略",
                      file=sys.stderr)
        else:
            print(f"  {label}: {total} 条 S-record，校验和全部有效")
    if not checksum_ok:
        return 1

    diffs = [(i + 1, a, b) for i, (a, b) in enumerate(zip(orig, patched)) if a != b]
    print("差异行数:", len(diffs))
    for line_no, old, new in diffs:
        print(f"  第{line_no}行\n    原 {old}\n    新 {new}")
    if len(diffs) != EXPECTED_DIFF_LINES:
        print(
            f"错误: 预期 {EXPECTED_DIFF_LINES} 行差异，实际 {len(diffs)} 行",
            file=sys.stderr,
        )
        return 1

    orig_img = load_srec(ORIG)
    patched_img = load_srec(PATCHED)
    all_addrs = sorted(set(orig_img) | set(patched_img))
    byte_diffs = [
        (addr, orig_img.get(addr), patched_img.get(addr))
        for addr in all_addrs
        if orig_img.get(addr) != patched_img.get(addr)
    ]
    print("字节差异:", len(byte_diffs))
    for addr, old, new in byte_diffs:
        old_txt = f"{old:02X}" if old is not None else "--"
        new_txt = f"{new:02X}" if new is not None else "--"
        print(f"  0x{addr:05X}: {old_txt} -> {new_txt}")

    actual = {addr: (old, new) for addr, old, new in byte_diffs}
    if actual != EXPECTED_BYTES:
        print(
            "错误: 字节差异与预期不一致\n"
            f"  预期: {EXPECTED_BYTES}\n"
            f"  实际: {actual}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
