#!/usr/bin/env python3
"""Static FRAM access scanner for the FTDX10 MAIN firmware.

The scanner reads an H8/300H disassembly, finds every call to the four
FRAM primitives, resolves the register arguments where the code makes it
possible, and validates the FRAM region map against the firmware image.

It deliberately does not guess: when a base address is computed or passed
in as a function parameter, the row is marked ``computed`` or ``unknown``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

FRAM_SIZE = 0x8000

# address -> (access name, fixed size or None when the size comes from r2)
PRIMITIVES: dict[int, tuple[str, Optional[int]]] = {
    0xA528: ("write", 1),
    0xA4A8: ("read", 1),
    0xA5C2: ("block_write", None),
    0xA4E2: ("block_read", None),
}

SPI_HELPERS = {0xA662, 0xA69A}
DRIVER_START = 0xA4A8
DRIVER_END = 0xA700

LINE_RE = re.compile(r"^\s*([0-9a-f]{3,6}):\t([0-9a-f ]*?)\s*(?:\t(.*))?$")
JSR_RE = re.compile(r"^jsr\s+@0x([0-9a-f]+):24$")
IMM_RE = re.compile(r"#0x([0-9a-f]+)")

# Instructions whose second operand is read, not written.  This list is
# deliberately explicit: the first version of this scanner treated
# ``cmp.w #0x8000,r1`` as a write to r1 and produced a bogus 0x8000 base.
NO_WRITE = {
    "cmp",
    "btst",
    "bra",
    "bne",
    "beq",
    "bcs",
    "bcc",
    "bhi",
    "bls",
    "bge",
    "blt",
    "bgt",
    "ble",
    "bmi",
    "bpl",
    "bvs",
    "bvc",
    "jmp",
    "rts",
    "jsr",
    "nop",
    "sleep",
    "andc",
    "orc",
    "xorc",
    "ldc",
    "bld",
}

SINGLE_WRITE = {
    "inc",
    "dec",
    "shll",
    "shlr",
    "shal",
    "shar",
    "rotl",
    "rotr",
    "rotxl",
    "rotxr",
    "not",
    "neg",
    "extu",
    "exts",
}


@dataclass(frozen=True)
class Instruction:
    addr: int
    raw: str
    text: str

    @property
    def op(self) -> str:
        return self.text.split(None, 1)[0].split(".")[0].lower() if self.text else ""


@dataclass(frozen=True)
class Region:
    start: int
    end: int
    name: str
    ram_mirror: str
    confidence: str
    evidence: str

    @property
    def size(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class Resolution:
    kind: str  # immediate | partial | computed | unknown
    value: Optional[int]
    evidence: str


@dataclass(frozen=True)
class Access:
    call_addr: int
    primitive: int
    access: str
    base: Optional[int]
    size: Optional[int]
    ram_ptr: Optional[int]
    base_resolution: str
    size_resolution: str
    ram_resolution: str
    region: str
    evidence: str


# The complete 32 KiB map.  Every range is half-open [start, end).
REGIONS: tuple[Region, ...] = (
    Region(0x0000, 0x0002, "reserved", "", "unknown", "no normal-path reference"),
    Region(0x0002, 0x0006, "magic", "", "confirmed", "boot validation"),
    Region(0x0006, 0x0008, "config pair", "0xFF2006/07", "confirmed", "0x8F0C / 0x8F1E"),
    Region(0x0008, 0x0009, "init flag", "", "confirmed", "0x8F38 read / 0x8F5A write"),
    Region(0x0009, 0x000A, "settings version", "", "confirmed", "boot compatibility check"),
    Region(0x000A, 0x017D, "internal settings (371B)", "0xFFA948-0xFFAABA", "confirmed", "0x7EFA default init; 0x7E3C read; 0x8F02 save; cmp #0x17D"),
    Region(0x017D, 0x02F0, "reserved", "", "unknown", "no normal-path reference"),
    Region(0x02F0, 0x02F5, "factory / destination params", "0xFF9886, 0xFFA934", "inferred", "0xDC74 / 0x27F76 write; 0x28034 read"),
    Region(0x02F5, 0x0300, "reserved", "", "unknown", "no normal-path reference"),
    Region(0x0300, 0x0490, "user settings (400B)", "0xFFAABC-0xFFAC4B", "confirmed", "init 0x8074/0x8082 from 0x82A6; 0x8F6A/0x8F7E front; 0x8F92/0x8FA6 back"),
    Region(0x0490, 0x0788, "message memory 5x152B", "", "inferred", "0x8A8C init 5x152; 0x3196A address; 0x31A00 read"),
    Region(0x0788, 0x088C, "message bank 5x52B (CW)", "", "inferred", "0x8B3E init; 0x8FD6 address; 0x901C read"),
    Region(0x088C, 0x0986, "message bank 5x50B (RTTY/PSK)", "", "inferred", "0x8C02 init; 0x90AE/0x90E0 250B; 0x90C2 address"),
    Region(0x0986, 0x0A80, "message bank 5x50B (PSK/RTTY)", "", "inferred", "0x90F4 address; 0x9120/0x9152 250B"),
    Region(0x0A80, 0x0C12, "circular log 100x4B", "0xFF96EE", "inferred", "0x3091A push; 0x309B6 full write; 0x7E6C read"),
    Region(0x0C12, 0x277E, "band stack 6x13x90B", "", "confirmed", "init 0x8436/0x856E 6x13; 0x9C94 index; 0xA0F4 read"),
    Region(0x277E, 0x278B, "band-stack control", "0xFFAC4C", "confirmed", "0x7E48 / 0xBF2E"),
    Region(0x278B, 0x2BC3, "per-band VFO snapshots 12x90B", "", "inferred", "0x9DE8 n*90; 0xBF2E save; init 0x84C0 writes 10 only"),
    Region(0x2BC3, 0x2C1D, "VFO-A current state 90B", "", "confirmed", "0x9D54 / 0xA240"),
    Region(0x2C1D, 0x2C77, "VFO-B current state 90B", "", "confirmed", "0x9D5A / 0xA262"),
    Region(0x2C77, 0x2C99, "memory/band control 34B (2+16+16)", "0xFFAC5D", "confirmed", "0x7E7A read 33B; 0x2944 block"),
    Region(0x2C99, 0x55BB, "memory channels 117x90B", "", "confirmed", "0xA326 / 0xA346; 0xA38E"),
    Region(0x55BB, 0x5A4D, "5MHz presets 13x90B", "", "inferred", "0x8540; templates 0x884E/0x8904"),
    Region(0x5A4D, 0x5AA7, "5MHz special VFO 1x90B", "", "inferred", "0x8564; templates 0x89BA/0x89C8"),
    Region(0x5AA7, 0x5ADB, "per-band aux 13x4B", "0xFF98A8", "inferred", "init 0x8CD8 writes 0x34; 0x8D20 / 0x8D60"),
    Region(0x5ADB, 0x5B29, "per-band aux 13x6B", "0xFF98AC", "inferred", "init 0x8DF2 writes 0x4E; 0x8D8E / 0x8DDA; 0x11C38"),
    Region(0x5B29, 0x8000, "unused", "", "inferred", "no normal-path reference; service write 0x2D054 can target any <0x8000"),
)


def parse_disasm_text(text: str) -> list[Instruction]:
    """Parse the objdump-style text, joining multi-line instructions."""
    insns: list[Instruction] = []
    current: Optional[list] = None
    for line in text.splitlines():
        m = LINE_RE.match(line)
        if not m:
            continue
        addr = int(m.group(1), 16)
        raw = m.group(2).strip()
        mnemonic = (m.group(3) or "").strip()
        if mnemonic:
            if current is not None:
                insns.append(Instruction(current[0], current[1], current[2]))
            current = [addr, raw, mnemonic]
        elif current is not None:
            current[1] += " " + raw
    if current is not None:
        insns.append(Instruction(current[0], current[1], current[2]))
    return insns


def parse_disasm(path: Path) -> list[Instruction]:
    return parse_disasm_text(path.read_text(encoding="utf-8", errors="replace"))


def _reg_aliases(reg: str) -> set[str]:
    if reg == "r1":
        return {"r1", "r1l", "r1h"}
    if reg == "r2":
        return {"r2", "r2l", "r2h"}
    if reg == "er3":
        return {"er3", "r3", "r3l", "r3h"}
    return {reg}


def writes_reg(text: str, reg: str) -> bool:
    """Return True when *text* actually writes *reg* (not just reads it)."""
    text = text.strip()
    if not text:
        return False
    parts = text.split(None, 1)
    mnemonic = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""
    op = mnemonic.split(".")[0]

    if op in NO_WRITE:
        return False

    # Post-increment addressing mutates the pointer even when it is a source.
    if re.search(rf"@{reg}\+", rest):
        return True

    aliases = _reg_aliases(reg)
    if "," in rest:
        dest = rest.rsplit(",", 1)[1].strip()
    else:
        if op not in SINGLE_WRITE:
            return False
        dest = rest.strip()

    # Strip H8 size suffixes such as @0xff2006:32 from the comparison target.
    return dest in aliases


def _immediate_write(text: str, reg: str) -> Optional[Resolution]:
    """Recognise mov.[bwl] #imm,reg and return a resolution if it is useful."""
    m = re.match(r"^mov\.([bwl])\s+#0x([0-9a-f]+),\s*(\S+)$", text.strip())
    if not m:
        return None
    width, value_s, dest = m.group(1), m.group(2), m.group(3)
    value = int(value_s, 16)
    if dest not in _reg_aliases(reg):
        return None
    if width == "b":
        return Resolution("partial", None, f"partial write: {text}")
    if reg in ("r1", "r2"):
        if value > 0xFFFF:
            return Resolution("computed", None, f"wide value: {text}")
        return Resolution("immediate", value, text)
    return Resolution("immediate", value, text)


def _function_start(insns: Sequence[Instruction], index: int, limit: int = 0x400) -> Optional[int]:
    """Return the instruction index just after the previous function boundary."""
    lower = max(0, index - limit)
    for j in range(index - 1, lower - 1, -1):
        if re.match(r"^(rts|jmp)\b", insns[j].text):
            return j + 1
    return 0 if lower == 0 else None


def _is_parameter(insns: Sequence[Instruction], index: int, reg: str) -> bool:
    """True when the function never writes *reg* before this call."""
    start = _function_start(insns, index)
    if start is None:
        return False
    for j in range(start, index):
        if writes_reg(insns[j].text, reg):
            return False
    return True


def _primitive_size(insns: Sequence[Instruction], index: int, target: int) -> Optional[int]:
    _, fixed = PRIMITIVES[target]
    if fixed is not None:
        return fixed
    r2 = resolve_register(insns, index, "r2")
    return r2.value if r2.kind == "immediate" else None


def resolve_register(
    insns: Sequence[Instruction],
    index: int,
    reg: str,
    max_back: int = 120,
    apply_effects: bool = False,
) -> Resolution:
    """Find the nearest preceding write to *reg* and classify it.

    The search stops at control-flow boundaries and at any call, because the
    callee may clobber the register.  With ``apply_effects`` the FRAM
    primitives' documented ``r1 += size`` side effect is propagated across
    straight-line sequences such as the factory-init loops.  Branches still
    stop the search, because a loop may have advanced r1 more than once.

    If the function never writes the register before the call, it is reported
    as a parameter.  Anything else unresolved is reported as ``unknown``
    rather than guessed.
    """
    offset = 0
    for j in range(index - 1, max(0, index - max_back) - 1, -1):
        ins = insns[j]
        text = ins.text
        if re.match(r"^(rts|jmp|bra|bne|beq|bcs|bcc|bhi|bls|bge|blt|bgt|ble|bmi|bpl|bvs|bvc)\b", text):
            break
        m = JSR_RE.match(text)
        if m:
            target = int(m.group(1), 16)
            if apply_effects and target in PRIMITIVES:
                size = _primitive_size(insns, j, target)
                if size is None:
                    break
                offset += size
                continue
            break
        if not writes_reg(text, reg):
            continue
        imm = _immediate_write(text, reg)
        if imm is not None:
            if imm.kind == "immediate" and imm.value is not None:
                value = imm.value + offset
                if value > 0xFFFF:
                    return Resolution("computed", None, f"wrapped value: {text}")
                evidence = imm.evidence
                if offset:
                    evidence = f"{evidence} (+0x{offset:X})"
                return Resolution("immediate", value, evidence)
            return imm
        return Resolution("computed", None, f"{ins.addr:#06x}: {text}")
    if _is_parameter(insns, index, reg):
        evidence = "function parameter"
        if offset:
            evidence += f" (+0x{offset:X})"
        return Resolution("parameter", None, evidence)
    return Resolution("unknown", None, "")


def region_for(addr: int) -> Optional[Region]:
    for region in REGIONS:
        if region.start <= addr < region.end:
            return region
    return None


def find_accesses(insns: Sequence[Instruction]) -> list[Access]:
    accesses: list[Access] = []
    for index, ins in enumerate(insns):
        m = JSR_RE.match(ins.text)
        if not m:
            continue
        target = int(m.group(1), 16)
        if target not in PRIMITIVES:
            continue
        access_name, fixed_size = PRIMITIVES[target]

        r1 = resolve_register(insns, index, "r1", apply_effects=True)
        base = r1.value if r1.kind == "immediate" else None
        base_resolution = r1.kind

        if fixed_size is None:
            r2 = resolve_register(insns, index, "r2")
            size = r2.value if r2.kind == "immediate" else None
            size_resolution = r2.kind
            er3 = resolve_register(insns, index, "er3")
            ram_ptr = er3.value if er3.kind == "immediate" else None
            ram_resolution = er3.kind
        else:
            size = fixed_size
            size_resolution = "fixed"
            ram_ptr = None
            ram_resolution = "n/a"

        region = region_for(base) if base is not None else None
        evidence_parts = [f"r1<-{r1.evidence or r1.kind}"]
        if fixed_size is None:
            evidence_parts.append(f"r2<-{r2.evidence or r2.kind}")
            evidence_parts.append(f"er3<-{er3.evidence or er3.kind}")

        accesses.append(
            Access(
                call_addr=ins.addr,
                primitive=target,
                access=access_name,
                base=base,
                size=size,
                ram_ptr=ram_ptr,
                base_resolution=base_resolution,
                size_resolution=size_resolution,
                ram_resolution=ram_resolution,
                region=region.name if region else "",
                evidence="; ".join(evidence_parts),
            )
        )
    return accesses


def write_access_csv(accesses: Iterable[Access], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(
            [
                "call_addr",
                "primitive",
                "access",
                "base",
                "size",
                "ram_ptr",
                "base_resolution",
                "size_resolution",
                "ram_resolution",
                "region",
                "evidence",
            ]
        )
        for a in sorted(accesses, key=lambda x: x.call_addr):
            writer.writerow(
                [
                    f"0x{a.call_addr:04X}",
                    f"0x{a.primitive:04X}",
                    a.access,
                    f"0x{a.base:04X}" if a.base is not None else "",
                    a.size if a.size is not None else "",
                    f"0x{a.ram_ptr:08X}" if a.ram_ptr is not None else "",
                    a.base_resolution,
                    a.size_resolution,
                    a.ram_resolution,
                    a.region,
                    re.sub(r"\s+", " ", a.evidence).strip(),
                ]
            )


def write_region_csv(regions: Iterable[Region], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["start", "end", "size", "name", "ram_mirror", "confidence", "evidence"])
        for r in regions:
            writer.writerow(
                [
                    f"0x{r.start:04X}",
                    f"0x{r.end:04X}",
                    r.size,
                    r.name,
                    r.ram_mirror,
                    r.confidence,
                    r.evidence,
                ]
            )


def verify_regions(regions: Sequence[Region] = REGIONS) -> list[str]:
    errors: list[str] = []
    if not regions:
        return ["empty region map"]
    if regions[0].start != 0:
        errors.append(f"map starts at 0x{regions[0].start:04X}, expected 0x0000")
    if regions[-1].end != FRAM_SIZE:
        errors.append(f"map ends at 0x{regions[-1].end:04X}, expected 0x{FRAM_SIZE:04X}")
    for left, right in zip(regions, regions[1:]):
        if left.end != right.start:
            errors.append(f"gap/overlap between {left.name} (0x{left.end:04X}) and {right.name} (0x{right.start:04X})")
    total = sum(r.size for r in regions)
    if total != FRAM_SIZE:
        errors.append(f"region sizes sum to {total}, expected {FRAM_SIZE}")
    if any(r.size <= 0 for r in regions):
        errors.append("non-positive region size")
    return errors


def formula_checks() -> list[tuple[str, int, int]]:
    return [
        ("0x2C99 + 117*90", 0x2C99 + 117 * 90, 0x55BB),
        ("0x55BB + 13*90", 0x55BB + 13 * 90, 0x5A4D),
        ("0x0C12 + 6*13*90", 0x0C12 + 6 * 13 * 90, 0x277E),
        ("0x278B + 12*90", 0x278B + 12 * 90, 0x2BC3),
        ("0x0300 + 400", 0x0300 + 400, 0x0490),
        ("0x0490 + 5*152", 0x0490 + 5 * 152, 0x0788),
        ("0x0788 + 5*52", 0x0788 + 5 * 52, 0x088C),
        ("0x088C + 5*50", 0x088C + 5 * 50, 0x0986),
        ("0x0986 + 5*50", 0x0986 + 5 * 50, 0x0A80),
        ("0x0A80 + 402", 0x0A80 + 402, 0x0C12),
        ("0x5AA7 + 13*4", 0x5AA7 + 13 * 4, 0x5ADB),
        ("0x5ADB + 13*6", 0x5ADB + 13 * 6, 0x5B29),
        ("0x000A + 0x173", 0x000A + 0x173, 0x017D),
        ("0x017D + 0x173", 0x017D + 0x173, 0x02F0),
    ]


def verify_binary(bin_path: Path, insns: Sequence[Instruction], accesses: Sequence[Access]) -> list[str]:
    errors: list[str] = []
    data = bin_path.read_bytes()
    if len(data) < FRAM_SIZE:
        errors.append(f"binary is only {len(data)} bytes")

    by_addr = {ins.addr: ins for ins in insns}
    for primitive in PRIMITIVES:
        ins = by_addr.get(primitive)
        if ins is None:
            errors.append(f"missing disassembly at primitive 0x{primitive:04X}")
            continue
        raw = bytes.fromhex(ins.raw)
        if data[primitive : primitive + len(raw)] != raw:
            errors.append(f"address/file-offset mismatch at 0x{primitive:04X}")

    # Low-level SPI helpers must only be called from inside the FRAM driver.
    for index, ins in enumerate(insns):
        m = JSR_RE.match(ins.text)
        if not m or int(m.group(1), 16) not in SPI_HELPERS:
            continue
        if not (DRIVER_START <= ins.addr < DRIVER_END):
            errors.append(f"SPI helper call outside FRAM driver at 0x{ins.addr:06X}: {ins.text}")

    # Every immediate base must be inside a known region.
    for a in accesses:
        if a.base is None:
            continue
        if not (0 <= a.base < FRAM_SIZE):
            errors.append(f"base outside FRAM at 0x{a.call_addr:04X}: 0x{a.base:04X}")
            continue
        region = region_for(a.base)
        if region is None:
            errors.append(f"base not in map at 0x{a.call_addr:04X}: 0x{a.base:04X}")
            continue
        if a.size is not None:
            # Multi-region block accesses are legitimate (the firmware does a
            # single 0x2944-byte copy from 0x2C77 through the memory channels).
            # Require the whole range to stay inside the mapped FRAM instead.
            end = a.base + a.size
            if end > FRAM_SIZE:
                errors.append(
                    f"access runs past FRAM at 0x{a.call_addr:04X}: "
                    f"0x{a.base:04X}+{a.size} = 0x{end:04X}"
                )
            elif region_for(end - 1) is None:
                errors.append(
                    f"access ends outside map at 0x{a.call_addr:04X}: "
                    f"0x{end - 1:04X}"
                )
    return errors


def access_summary(accesses: Sequence[Access]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for a in accesses:
        summary[a.access] = summary.get(a.access, 0) + 1
    return summary


def resolution_summary(accesses: Sequence[Access]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for a in accesses:
        summary[a.base_resolution] = summary.get(a.base_resolution, 0) + 1
    return summary


def print_summary(
    insns: Sequence[Instruction],
    accesses: Sequence[Access],
    bin_path: Path,
    region_errors: Sequence[str],
    binary_errors: Sequence[str],
) -> None:
    data = bin_path.read_bytes()
    print(f"binary          : {bin_path}")
    print(f"sha256          : {hashlib.sha256(data).hexdigest()}")
    print(f"size            : {len(data)} bytes (0x{len(data):X})")
    print(f"instructions    : {len(insns)}")
    print(f"FRAM call sites : {len(accesses)}")
    print(f"by access       : {access_summary(accesses)}")
    print(f"base resolution : {resolution_summary(accesses)}")
    immediate = [a.base for a in accesses if a.base is not None]
    if immediate:
        print(f"max base        : 0x{max(immediate):04X}")
    else:
        print("max base        : n/a")
    print(f"region errors   : {len(region_errors)}")
    for err in region_errors:
        print(f"  - {err}")
    print(f"binary errors   : {len(binary_errors)}")
    for err in binary_errors:
        print(f"  - {err}")
    print("formula checks  :")
    for name, got, expected in formula_checks():
        status = "OK" if got == expected else f"FAIL (expected 0x{expected:04X})"
        print(f"  {name:24s} = 0x{got:04X}  {status}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    here = Path(__file__).resolve()
    project = here.parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disasm", type=Path, required=True, help="H8/300H disassembly text")
    parser.add_argument("--bin", type=Path, default=project / "decoded" / "firmware" / "FTDX10_MAIN_V0115.bin")
    parser.add_argument("--out", type=Path, default=project / "fram" / "evidence" / "access_sites.csv")
    parser.add_argument("--map", type=Path, default=project / "fram" / "fram_map.csv")
    parser.add_argument("--verify", action="store_true", help="run consistency checks and fail on errors")
    args = parser.parse_args(argv)

    if not args.disasm.exists():
        print(f"error: disassembly not found: {args.disasm}", file=sys.stderr)
        return 2
    if not args.bin.exists():
        print(f"error: binary not found: {args.bin}", file=sys.stderr)
        return 2

    insns = parse_disasm(args.disasm)
    accesses = find_accesses(insns)
    write_access_csv(accesses, args.out)
    write_region_csv(REGIONS, args.map)

    region_errors = verify_regions()
    binary_errors = verify_binary(args.bin, insns, accesses) if args.verify else []
    print_summary(insns, accesses, args.bin, region_errors, binary_errors)
    print(f"wrote           : {args.out}")
    print(f"wrote           : {args.map}")

    if args.verify and (region_errors or binary_errors):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
