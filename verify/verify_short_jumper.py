#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Short-jumper / dead-code verification for MAIN V01-15.

This is the single Python implementation of the evidence used by
``hardware/可复现分析过程.md`` section 4.2 and ``power/verify/verify_kr.sh``.

It verifies both sides of the corrected conclusion:

* ``0xA26`` is dead: its only entry, ``0x7D2E``, has no textual reference,
  no 4-byte or 3-byte absolute pointer, and no branch/call target.
* ``0xA58`` is *not* an independent function. It is the write-back at the
  tail of the live ``0xA38`` routine; the live caller chain and the bit mask
  are checked explicitly.

Run from the repository root (or pass explicit paths):

    python3 verify/verify_short_jumper.py
    python3 verify/verify_short_jumper.py --bin ... --disasm ...
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BIN = ROOT / "decoded" / "firmware" / "FTDX10_MAIN_V0115.bin"
DEFAULT_DISASM = ROOT / "hardware" / "files" / "disasm" / "main_disasm.txt"

# H8SX branch mnemonics.  The disassembler prints the absolute target in
# parentheses, e.g. ``bra .+6 (0x7d2e)``.
BRANCHES = {
    "bra", "bsr", "jmp", "jsr",
    "bcc", "bcs", "beq", "bne", "bhi", "bls", "bmi", "bpl",
    "bge", "blt", "bgt", "ble", "bt", "bf", "brn",
    "bvc", "bvs", "bhs", "blo",
}
LINE_RE = re.compile(
    r"^\s*[0-9a-fA-F]+:\s+(?:[0-9a-fA-F]{2}\s+)+([a-z][a-z0-9.]*)\s*(.*)$"
)


def branch_targets(disasm: str, target: int) -> list[int]:
    """Return line numbers whose instruction operand contains *target*."""
    hits: list[int] = []
    needle = re.compile(rf"0x0*{target:x}\b", re.I)
    for line_no, line in enumerate(disasm.splitlines(), 1):
        m = LINE_RE.match(line)
        if m and m.group(1).lower() in BRANCHES and needle.search(m.group(2)):
            hits.append(line_no)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bin", default=str(DEFAULT_BIN), help="MAIN firmware binary")
    ap.add_argument("--disasm", default=str(DEFAULT_DISASM), help="MAIN disassembly text")
    args = ap.parse_args()

    bin_path = Path(args.bin)
    disasm_path = Path(args.disasm)
    if not bin_path.is_file():
        sys.exit(f"missing binary: {bin_path}")
    if not disasm_path.is_file():
        sys.exit(f"missing disassembly: {disasm_path}")

    b = bin_path.read_bytes()
    disasm = disasm_path.read_text(encoding="utf-8", errors="replace")

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    # ---- The three independent scans for 0x7D2E -------------------------
    text_hits = len(re.findall(r"0x0*7d2e\b", disasm, re.I))
    long4 = b.count((0x7D2E).to_bytes(4, "big"))
    long3 = b.count((0x7D2E).to_bytes(3, "big"))
    branch_hits = branch_targets(disasm, 0x7D2E)

    check("scan A: textual 0x7d2e references = 0", text_hits == 0, f"n={text_hits}")
    check("scan B: 4-byte 00 00 7D 2E pointers = 0", long4 == 0, f"n={long4}")
    check("scan B2: 3-byte 00 7D 2E pointers = 0", long3 == 0, f"n={long3}")
    check("scan C: branch/call targets 0x7D2E = 0", not branch_hits,
          f"lines={branch_hits}")

    # ---- Positive control: live routines do have 4-byte table entries ----
    for addr in (0x15920, 0x20DB4, 0x20D80):
        n = b.count(addr.to_bytes(4, "big"))
        check(f"positive control 0x{addr:X} has one 4-byte pointer", n == 1, f"n={n}")

    # ---- The dead branch A26: 0x7D2E -> 0x7E8E ... -> 0x7E9E -> A26 ----
    check("0x7D2E calls 0x7E8E",
          b[0x7D2E:0x7D32] == bytes.fromhex("5e007e8e"),
          b[0x7D2E:0x7D32].hex(" "))
    check("0x7E9E calls 0xA26",
          b[0x7E9E:0x7EA2] == bytes.fromhex("5e000a26"),
          b[0x7E9E:0x7EA2].hex(" "))

    # ---- The live branch A38 (and its tail write-back at 0xA58) ----------
    check("0x1570 calls 0x8E50",
          b[0x1570:0x1574] == bytes.fromhex("5e008e50"),
          b[0x1570:0x1574].hex(" "))
    check("0x8E50 calls 0x8EA4 (then falls through to 0x8E54)",
          b[0x8E50:0x8E54] == bytes.fromhex("5e008ea4"),
          b[0x8E50:0x8E54].hex(" "))
    check("0x8E54 calls 0xA38",
          b[0x8E54:0x8E58] == bytes.fromhex("5e000a38"),
          b[0x8E54:0x8E58].hex(" "))
    check("A38 uses AND #0xFF9F (clear bit5/bit6, preserve bit3/4)",
          b[0xA48:0xA4C] == bytes.fromhex("7960ff9f"),
          b[0xA48:0xA4C].hex(" "))
    check("A38 uses AND #0x20 / OR to adopt only bit5",
          b[0xA4C:0xA52] == bytes.fromhex("796100206401"),
          b[0xA4C:0xA52].hex(" "))
    check("A58 writes r1l to 0xFF2007 (live tail write-back)",
          b[0xA58:0xA5E] == bytes.fromhex("6aa900ff2007"),
          b[0xA58:0xA5E].hex(" "))
    check("A58 has no independent callers",
          not branch_targets(disasm, 0xA58),
          f"lines={branch_targets(disasm, 0xA58)}")

    print(f"# binary: {bin_path} ({len(b)} bytes)")
    print(f"# disasm: {disasm_path}")
    print(f"text=0x{text_hits:x} long4=0x{long4:x} long3=0x{long3:x} "
          f"branch={len(branch_hits)}")
    for name, ok, detail in checks:
        if not ok:
            print(f"[FAIL] {name}" + (f" ({detail})" if detail else ""))
    failed = [name for name, ok, _ in checks if not ok]
    if failed:
        print(f"==== FAIL: {len(failed)}/{len(checks)} checks failed ====")
        return 1
    print(f"==== PASS: {len(checks)}/{len(checks)} checks passed ====")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
