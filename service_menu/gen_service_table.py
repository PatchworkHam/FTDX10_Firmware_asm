#!/usr/bin/env python3
"""生成 service_menu/服务菜单项目表.csv（294 项服务菜单项目）。

镜像：decoded/firmware/FTDX10_MAIN_V0115.bin
表：0x27A8 + N*10（10 B 项目描述），0x3324 + N*25（25 B ASCII 标签）
"""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIRMWARE = ROOT / "decoded/firmware/FTDX10_MAIN_V0115.bin"
OUT = ROOT / "service_menu/服务菜单项目表.csv"

A, B, COUNT = 0x27A8, 0x3324, 0x126


def group_of(n: int) -> tuple[int, int, int]:
    """复现 0x1FE2 的 (r4l, r2, r3l)：组号、组内偏移、子组。"""
    bounds = [
        (0x00, 0x08), (0x08, 0x0D), (0x0D, 0x12), (0x12, 0x17),
        (0x17, 0x22), (0x22, 0x2A), (0x2A, 0x2C), (0x2C, 0x31),
        (0x31, 0x3A), (0x3A, 0x45), (0x45, 0x54), (0x54, 0x63),
        (0x63, 0x72), (0x72, 0x81), (0x81, 0x90), (0x90, 0x9F),
        (0x9F, 0xAE), (0xAE, 0xBD), (0xBD, 0xCC), (0xCC, 0xDB),
        (0xDB, 0xE7), (0xE7, 0xF2), (0xF2, 0xF5), (0xF5, 0x101),
        (0x101, 0x105), (0x105, 0x126),
    ]
    sub = {4: 1, 7: 2}
    for idx, (lo, hi) in enumerate(bounds):
        if lo <= n < hi:
            return idx, n - lo, sub.get(idx, 0)
    raise ValueError(n)


def main() -> None:
    b = FIRMWARE.read_bytes()
    rows = []
    for n in range(COUNT):
        e = b[A + n * 10 : A + n * 10 + 10]
        b0, b1, b2, b3, b4, b5 = e[0], e[1], e[2], e[3], e[4], e[5]
        u = int.from_bytes(e[6:10], "big")
        label = b[B + n * 25 : B + n * 25 + 25].decode("ascii", "replace").rstrip()
        grp, grp_off, subgrp = group_of(n)

        item_ram = 0xFFA948 + n
        item_fram = 0x000A + n

        if n == 0x000:
            apply_ram, apply_fram, kind = 0xFFAAAF, 0x0171, "meter_vdd"
        elif n == 0x0F2:
            apply_ram, apply_fram, kind = 0xFFAAB0, 0x0172, "meter_alc"
        elif n == 0x101:
            apply_ram, apply_fram, kind = 0xFFAAB1, 0x0173, "meter_idd"
        elif 0x17 <= n <= 0x21:
            apply_ram = 0xFFAA6E + (n - 0x17)
            apply_fram = 0x0130 + (n - 0x17)
            kind = "special_11"
        elif 0x45 <= n <= 0xE6:
            apply_ram = 0xFFAA79 + (n - 0x45) // 3
            apply_fram = 0x013B + (n - 0x45) // 3
            kind = "shared_54"
        else:
            apply_ram, apply_fram, kind = item_ram, item_fram, "item"

        rows.append(
            {
                "code_hex": f"0x{n:03X}",
                "code_dec": n,
                "label": label,
                "b0": f"0x{b0:02X}",
                "b1": f"0x{b1:02X}",
                "b2": f"0x{b2:02X}",
                "b3_band": f"0x{b3:02X}",
                "b4_mode": f"0x{b4:02X}",
                "b5_sub": f"0x{b5:02X}",
                "band_hz": u,
                "band_mhz": f"{u / 1e6:.4f}",
                "item_ram": f"0x{item_ram:05X}",
                "item_fram": f"0x{item_fram:04X}",
                "apply_ram": f"0x{apply_ram:05X}",
                "apply_fram": f"0x{apply_fram:04X}",
                "apply_kind": kind,
                "grp": f"0x{grp:02X}",
                "subgrp": subgrp,
                "grp_off": grp_off,
            }
        )

    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {OUT} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
