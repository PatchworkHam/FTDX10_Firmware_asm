#!/usr/bin/env python3
"""Regression tests for fram_scan.py."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fram_scan


def _insn(addr: int, raw: str, text: str) -> fram_scan.Instruction:
    return fram_scan.Instruction(addr, raw, text)


class RegisterWriteTests(unittest.TestCase):
    def test_cmp_is_not_a_write(self) -> None:
        self.assertFalse(fram_scan.writes_reg("cmp.w #0x8000,r1", "r1"))

    def test_btst_is_not_a_write(self) -> None:
        self.assertFalse(fram_scan.writes_reg("btst #0x0,@0xff2012:32", "r1"))

    def test_mov_immediate_is_a_write(self) -> None:
        self.assertTrue(fram_scan.writes_reg("mov.w #0x36f,r1", "r1"))

    def test_post_increment_writes_pointer(self) -> None:
        self.assertTrue(fram_scan.writes_reg("mov.b @er3+,r0l", "er3"))

    def test_memory_destination_does_not_write_pointer(self) -> None:
        self.assertFalse(fram_scan.writes_reg("mov.b r0l,@er3", "er3"))


class ResolutionTests(unittest.TestCase):
    def test_immediate_base(self) -> None:
        insns = [
            _insn(0x1000, "79 01 00 36", "mov.w #0x36f,r1"),
            _insn(0x1004, "5e 00 a5 28", "jsr @0xa528:24"),
        ]
        accesses = fram_scan.find_accesses(insns)
        self.assertEqual(len(accesses), 1)
        self.assertEqual(accesses[0].base, 0x36F)
        self.assertEqual(accesses[0].base_resolution, "immediate")

    def test_cmp_does_not_create_false_base(self) -> None:
        insns = [
            _insn(0x1000, "79 20 80 00", "cmp.w #0x8000,r1"),
            _insn(0x1004, "5e 00 a5 28", "jsr @0xa528:24"),
        ]
        accesses = fram_scan.find_accesses(insns)
        self.assertEqual(len(accesses), 1)
        self.assertIsNone(accesses[0].base)

    def test_primitive_increment_is_propagated(self) -> None:
        insns = [
            _insn(0x1000, "79 01 27 7e", "mov.w #0x277e,r1"),
            _insn(0x1004, "f8 00", "mov.b #0x0,r0l"),
            _insn(0x1006, "5e 00 a5 28", "jsr @0xa528:24"),
            _insn(0x100a, "f8 00", "mov.b #0x0,r0l"),
            _insn(0x100c, "5e 00 a5 28", "jsr @0xa528:24"),
        ]
        accesses = fram_scan.find_accesses(insns)
        self.assertEqual([a.base for a in accesses], [0x277E, 0x277F])
        self.assertEqual(accesses[1].base_resolution, "immediate")


class MapTests(unittest.TestCase):
    def test_regions_are_contiguous_and_cover_fram(self) -> None:
        self.assertEqual(fram_scan.verify_regions(), [])

    def test_formula_boundaries(self) -> None:
        for name, got, expected in fram_scan.formula_checks():
            with self.subTest(name=name):
                self.assertEqual(got, expected)

    def test_real_disassembly_has_246_calls(self) -> None:
        path = Path(os.environ.get("FTDX10_DISASM", ""))
        if not path.is_file():
            candidate = Path(__file__).resolve().parents[2] / "hardware" / "files" / "disasm" / "main_disasm.txt"
            path = candidate if candidate.is_file() else None
        if path is None:
            self.skipTest("set FTDX10_DISASM to the real disassembly to run this test")
        insns = fram_scan.parse_disasm(path)
        accesses = fram_scan.find_accesses(insns)
        self.assertEqual(len(accesses), 246)
        counts = fram_scan.access_summary(accesses)
        self.assertEqual(counts, {"read": 21, "write": 162, "block_read": 25, "block_write": 38})
        self.assertLessEqual(max(a.base for a in accesses if a.base is not None), 0x5ADB)


if __name__ == "__main__":
    unittest.main()
