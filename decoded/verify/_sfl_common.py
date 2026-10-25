#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""decoded/verify/ 共用的小工具：加载 decoded/yaesu_sfl_tool.py 的编解码函数。

只依赖标准库；不修改任何固件文件。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
DECODED = HERE.parent
ROOT = DECODED.parent
TOOL_PATH = DECODED / "yaesu_sfl_tool.py"
DEFAULT_REPO = ROOT / "original" / "firmware"
DEFAULT_PLAIN_DIR = DECODED / "firmware"


def load_tool():
    """以模块方式加载 decoded/yaesu_sfl_tool.py。"""
    spec = importlib.util.spec_from_file_location("yaesu_sfl_tool", TOOL_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载工具: {TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOOL = load_tool()
HEADER_LEN = TOOL.HEADER_LEN

# 原始 .SFL -> 可回打包的明文文件（相对 decoded/firmware/）
PAIRS = [
    ("FTDX10_AFCPU_V0100.SFL",   "FTDX10_AFCPU_V0100.srec"),
    ("FTDX10_MAIN_V0115.SFL",    "FTDX10_MAIN_V0115.srec"),
    ("FTDX10_DISPLAY_V0106.SFL", "FTDX10_DISPLAY_V0106.bin"),
    ("FTDX10_IFDSP_V0120.SFL",   "FTDX10_IFDSP_V0120.bin"),
    ("FTDX10_SDR_V0100.SFL",     "FTDX10_SDR_V0100.rawhex"),
]


def build_sfl(header: bytes, plain: bytes, fix_crc: bool = True) -> bytes:
    """用 576 字节标头 + 明文数据段重建 .SFL。

    fix_crc=True 时按 yaesu_sfl_tool 的算法重算并写回元数据 CRC32。
    """
    if len(header) != HEADER_LEN:
        raise ValueError(f"标头必须恰好 {HEADER_LEN} 字节，实际 {len(header)}")
    cipher = TOOL.encode_payload(plain)
    if fix_crc:
        header = TOOL.patch_metadata(
            header, HEADER_LEN + len(cipher), TOOL.compute_crc(cipher)
        )
    return header + cipher


def decode_sfl(raw: bytes) -> bytes:
    """解出 .SFL 的明文数据段。"""
    if len(raw) < HEADER_LEN:
        raise ValueError("文件短于标头")
    return TOOL.decode_payload(raw[HEADER_LEN:])


def read_declared_crc(raw: bytes) -> int:
    """读取 576 字节标头里声明的 CRC32。"""
    meta = TOOL.parse_meta(raw[:HEADER_LEN])
    if meta is None:
        raise ValueError("标头中没有有效的 TYPE SIZE CRC32 元数据行")
    return meta[2]
