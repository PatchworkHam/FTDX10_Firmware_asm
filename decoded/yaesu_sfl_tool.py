#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Yaesu SFL 编解码工具：CRC、BIN、HEX、Motorola S-record。"""
import argparse
import contextlib
import hashlib
import io
import re
import sys
import tempfile
import zlib
from pathlib import Path
HEADER_LEN = 576
KEY = bytes([
    30, 39, 27, 18, 17, 22, 25, 23, 36, 37,
    41, 28, 13, 11, 6, 4, 3, 1, 2, 5,
    7, 12, 14, 15, 24, 29, 32, 34, 42, 43,
    8, 9, 10, 16, 19, 20, 21, 26, 31, 33, 40, 35, 38,
])
CRC_TRAILER = bytes.fromhex("A6EC9E13")
CRC_FIELD_OFFSET = 544
CRC_FIELD_LEN = 8
SREC_ADDR_BYTES = {0: 2, 1: 2, 2: 3, 3: 4, 5: 2, 7: 4, 8: 3, 9: 2}
HEX_CHARS = set(b"0123456789abcdefABCDEF \t\r\n,:;_-")
class SFLFormatError(ValueError):
    """Invalid SFL or payload format."""
def read_bytes(path):
    return Path(path).read_bytes()
def decode_payload(cipher):
    return bytes((value - KEY[i % len(KEY)]) & 0xFF
                 for i, value in enumerate(cipher))
def encode_payload(plain):
    return bytes((value + KEY[i % len(KEY)]) & 0xFF
                 for i, value in enumerate(plain))
def compute_crc(cipher):
    return zlib.crc32(cipher + CRC_TRAILER) & 0xFFFFFFFF
def parse_meta(header):
    """Return (type, size, crc, crc_offset) from the metadata line."""
    offset = 0
    for line in header.split(b"\r\n"):
        parts = line.split()
        if len(parts) == 3 and re.fullmatch(rb"[0-9A-Fa-f]{8}", parts[2]):
            try:
                kind = parts[0].decode("ascii")
                size = int(parts[1], 10)
                crc = int(parts[2], 16)
                relative = line.rfind(parts[2])
                return kind, size, crc, offset + relative
            except (UnicodeDecodeError, ValueError):
                pass
        offset += len(line) + 2
    return None
def validate_sfl(raw, skip_crc=False):
    """Validate SFL structural fields and stored-payload CRC before decoding."""
    if len(raw) < HEADER_LEN:
        raise SFLFormatError(
            f"文件只有 {len(raw)} 字节，小于标头长度 {HEADER_LEN}"
        )
    header, cipher = raw[:HEADER_LEN], raw[HEADER_LEN:]
    meta = parse_meta(header)
    if meta is None:
        raise SFLFormatError("576 字节标头内未找到有效的 TYPE SIZE CRC32 元数据行")
    kind, declared_size, declared_crc, crc_offset = meta
    if declared_size != len(raw):
        raise SFLFormatError(
            f"标头 SIZE={declared_size}，实际文件长度={len(raw)}；"
            "如这是有意修改过的模板，请先修正标头 SIZE"
        )
    if crc_offset != CRC_FIELD_OFFSET:
        # Do not silently patch a different fixed offset; parsing still allows
        # validation of a valid metadata line at another offset.
        pass
    actual_crc = compute_crc(cipher)
    if not skip_crc and actual_crc != declared_crc:
        raise SFLFormatError(
            f"CRC32 错误：标头={declared_crc:08X}，计算={actual_crc:08X}。"
            "如需忽略 CRC 检查，请使用 --skip-crc"
        )
    return header, cipher, meta, actual_crc
def patch_metadata(header, total_size, crc):
    if len(header) != HEADER_LEN:
        raise SFLFormatError(f"标头必须恰好为 {HEADER_LEN} 字节")
    meta = parse_meta(header)
    if meta is None:
        raise SFLFormatError("标头中未找到有效的元数据行")
    _, old_size, _, crc_offset = meta
    # Replace fields in-place, preserving the fixed 576-byte header.
    line_start = header.rfind(b"\r\n", 0, crc_offset)
    line_start = 0 if line_start < 0 else line_start + 2
    line_end = header.find(b"\r\n", crc_offset)
    if line_end < 0:
        line_end = len(header)
    line = header[line_start:line_end]
    parts = list(re.finditer(rb"\S+", line))
    if len(parts) != 3:
        raise SFLFormatError("元数据行格式无效")
    size_match, crc_match = parts[1], parts[2]
    width = len(size_match.group())
    new_size_text = str(total_size)
    if len(new_size_text) > width:
        raise SFLFormatError(
            f"SIZE 字段宽度为 {width} 位，无法表示总长度 {total_size}"
        )
    new_size = new_size_text.zfill(width).encode("ascii")
    patched = bytearray(header)
    patched[line_start + size_match.start():line_start + size_match.end()] = new_size
    # Size has unchanged width, so CRC offsets remain unchanged.
    patched[line_start + crc_match.start():line_start + crc_match.end()] = f"{crc:08X}".encode()
    return bytes(patched)
def parse_srec(data, strict=True):
    """Parse Motorola S-records into (memory map, start address records).
    S1/S2/S3 data records are assembled by address. Records are checked for
    byte count and checksum. Gaps are not filled until image export.
    """
    memory = {}
    starts = []
    seen = 0
    for line_no, raw_line in enumerate(data.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        if not re.fullmatch(rb"S[0-9][0-9A-Fa-f]+", line):
            if strict:
                raise SFLFormatError(f"S-record 第 {line_no} 行格式无效")
            continue
        typ = int(chr(line[1]))
        if typ not in SREC_ADDR_BYTES:
            if strict:
                raise SFLFormatError(f"S-record 第 {line_no} 行类型无效")
            continue
        try:
            record = bytes.fromhex(line[2:].decode("ascii"))
        except ValueError as exc:
            raise SFLFormatError(f"S-record 第 {line_no} 行不是有效十六进制") from exc
        if len(record) < 2 or record[0] != len(record) - 1:
            raise SFLFormatError(f"S-record 第 {line_no} 行 byte count 不匹配")
        if (sum(record) & 0xFF) != 0xFF:
            raise SFLFormatError(f"S-record 第 {line_no} 行 checksum 错误")
        address_len = SREC_ADDR_BYTES[typ]
        if len(record) < 1 + address_len + 1:
            raise SFLFormatError(f"S-record 第 {line_no} 行长度不足")
        address = int.from_bytes(record[1:1 + address_len], "big")
        payload = record[1 + address_len:-1]
        if typ in (1, 2, 3):
            seen += 1
            for i, byte in enumerate(payload):
                addr = address + i
                if addr in memory and memory[addr] != byte:
                    raise SFLFormatError(
                        f"S-record 地址冲突：0x{addr:X} 处出现不同数据"
                    )
                memory[addr] = byte
        elif typ in (7, 8, 9):
            starts.append(address)
    if not seen or not memory:
        raise SFLFormatError("S-record 中没有 S1/S2/S3 数据记录")
    return memory, starts
def memory_to_bin(memory, fill=0xFF, max_span=64 * 1024 * 1024):
    """Build a contiguous view; refuse pathological address gaps."""
    if not memory:
        return b"", 0, 0
    low, high = min(memory), max(memory)
    if high - low + 1 > max_span:
        raise SFLFormatError(
            f"地址跨度 {high - low + 1} 字节过大，拒绝生成填充 BIN；请使用地址型 S-record/Intel HEX"
        )
    return bytes(memory.get(addr, fill) for addr in range(low, high + 1)), low, high
def srec_to_bin(data):
    memory, _ = parse_srec(data)
    return memory_to_bin(memory)
def looks_like_srec(data):
    lines = [line.strip() for line in data.splitlines() if line.strip()]
    return bool(lines) and all(
        re.fullmatch(rb"S[0-9][0-9A-Fa-f]+", line) for line in lines
    ) and any(line.startswith((b"S1", b"S2", b"S3")) for line in lines)
def parse_intel_hex(data):
    """严格解析 Intel HEX，返回 (地址->字节, 起始地址)。"""
    memory, upper, start_address = {}, 0, None
    lines = [line.strip() for line in data.splitlines() if line.strip()]
    if not lines:
        raise SFLFormatError("Intel HEX 文件为空")
    saw_eof = False
    for line_no, line in enumerate(lines, 1):
        if saw_eof:
            raise SFLFormatError(f"Intel HEX 第 {line_no} 行位于 EOF 记录之后")
        if not re.fullmatch(rb":[0-9A-Fa-f]+", line) or (len(line) - 1) % 2:
            raise SFLFormatError(f"Intel HEX 第 {line_no} 行格式错误")
        try:
            record = bytes.fromhex(line[1:].decode("ascii"))
        except ValueError as exc:
            raise SFLFormatError(f"Intel HEX 第 {line_no} 行十六进制格式错误") from exc
        if len(record) < 5 or record[0] != len(record) - 5:
            raise SFLFormatError(f"Intel HEX 第 {line_no} 行长度错误")
        if sum(record) & 0xFF:
            raise SFLFormatError(f"Intel HEX 第 {line_no} 行 checksum 错误")
        count, address, rectype = record[0], int.from_bytes(record[1:3], "big"), record[3]
        payload = record[4:-1]
        expected = {0: None, 1: 0, 2: 2, 3: 4, 4: 2, 5: 4}
        if rectype not in expected:
            raise SFLFormatError(f"Intel HEX 第 {line_no} 行记录类型 {rectype} 不支持")
        if expected[rectype] is not None and count != expected[rectype]:
            raise SFLFormatError(f"Intel HEX 第 {line_no} 行类型 {rectype} 的数据长度必须为 {expected[rectype]}")
        if rectype != 0 and address != 0:
            raise SFLFormatError(f"Intel HEX 第 {line_no} 行类型 {rectype} 的地址字段必须为 0000")
        if rectype == 0:
            if address + count > 0x10000:
                raise SFLFormatError(f"Intel HEX 第 {line_no} 行数据跨越 64 KiB 边界")
            absolute = upper + address
            for i, value in enumerate(payload):
                addr = absolute + i
                if addr in memory and memory[addr] != value:
                    raise SFLFormatError(f"Intel HEX 地址冲突：0x{addr:X}")
                memory[addr] = value
        elif rectype == 1:
            saw_eof = True
        elif rectype == 2:
            upper = int.from_bytes(payload, "big") << 4
        elif rectype == 4:
            upper = int.from_bytes(payload, "big") << 16
        elif rectype == 3:
            start_address = (int.from_bytes(payload[:2], "big") << 4) + int.from_bytes(payload[2:], "big")
        elif rectype == 5:
            start_address = int.from_bytes(payload, "big")
    if not saw_eof:
        raise SFLFormatError("Intel HEX 缺少 EOF 记录")
    if not memory:
        raise SFLFormatError("Intel HEX 没有数据记录")
    return memory, start_address

def memory_to_ihex(memory, start_address=None, record_size=16):
    """Serialize an absolute-address memory map as valid Intel HEX (type 04)."""
    if not memory:
        raise SFLFormatError("没有可写入 Intel HEX 的数据")
    if not 1 <= record_size <= 255:
        raise SFLFormatError("Intel HEX record_size 必须在 1..255 之间")

    lines = []
    current_upper = None
    addresses = sorted(memory)
    index = 0
    while index < len(addresses):
        address = addresses[index]
        upper = address >> 16
        if upper > 0xFFFF:
            raise SFLFormatError(f"地址 0x{address:X} 超出 Intel HEX 32 位地址范围")
        if upper != current_upper:
            payload = upper.to_bytes(2, "big")
            record = bytes([2, 0, 0, 4]) + payload
            checksum = (-sum(record)) & 0xFF
            lines.append(":" + (record + bytes([checksum])).hex().upper())
            current_upper = upper

        # Emit contiguous bytes only; do not silently fill address gaps.
        chunk = bytearray([memory[address]])
        index += 1
        while (index < len(addresses) and len(chunk) < record_size
               and addresses[index] == address + len(chunk)
               and (addresses[index] >> 16) == upper):
            chunk.append(memory[addresses[index]])
            index += 1
        low = address & 0xFFFF
        record = bytes([len(chunk), (low >> 8) & 0xFF, low & 0xFF, 0]) + bytes(chunk)
        checksum = (-sum(record)) & 0xFF
        lines.append(":" + (record + bytes([checksum])).hex().upper())

    if start_address is not None:
        if not 0 <= start_address <= 0xFFFFFFFF:
            raise SFLFormatError("Intel HEX start address 超出 32 位范围")
        record = bytes([4, 0, 0, 5]) + start_address.to_bytes(4, "big")
        checksum = (-sum(record)) & 0xFF
        lines.append(":" + (record + bytes([checksum])).hex().upper())
    lines.append(":00000001FF")
    return ("\r\n".join(lines) + "\r\n").encode("ascii")


def contiguous_memory(data, base_address=0):
    """Map binary bytes to consecutive absolute addresses."""
    if base_address < 0 or base_address + len(data) > 0x100000000:
        raise SFLFormatError("BIN 起始地址或长度超出 Intel HEX 32 位地址范围")
    return {base_address + i: value for i, value in enumerate(data)}


def raw_hex_to_bin(data):
    """Parse a plain hex dump (not Intel HEX), allowing whitespace/punctuation."""
    stripped = data.strip()
    if not stripped:
        raise SFLFormatError("HEX 数据为空")
    if stripped.startswith(b":"):
        memory, _ = parse_intel_hex(stripped)
        image, low, high = memory_to_bin(memory)
        return image, low, high, "ihex"
    # Permit common hex dump separators; reject all other characters.
    if any(byte not in HEX_CHARS for byte in stripped):
        raise SFLFormatError("纯 HEX 文本含有非十六进制字符")
    compact = re.sub(rb"[\s,:;_\-]", b"", stripped)
    if not compact or not re.fullmatch(rb"[0-9A-Fa-f]+", compact):
        raise SFLFormatError("不是有效的纯 HEX 文本")
    if len(compact) % 2:
        raise SFLFormatError("纯 HEX 字符数为奇数")
    return bytes.fromhex(compact.decode("ascii")), 0, 0, "rawhex"


HEX_TEXT_MIN = 4096


def looks_like_hex_text(data):
    """Detect a plain hex dump (no addresses), such as the SDR payload.

    A firmware-sized byte stream consisting only of hex digits/separators is
    not a realistic binary image, so this is safe with a size floor. Auto
    encode deliberately does *not* use this to convert to binary: a plain hex
    payload must be preserved as text to reproduce the original SFL.
    """
    if len(data) < HEX_TEXT_MIN:
        return False
    if any(byte not in HEX_CHARS for byte in data):
        return False
    compact = re.sub(rb"[\s,:;_-]", b"", data)
    return bool(compact) and len(compact) % 2 == 0


def detect_payload(data, requested="auto"):
    """Return (kind, original/normalized bytes, optional binary image).

    Auto mode intentionally recognizes structured S-record/Intel HEX only;
    everything else is binary. Plain hex dumps are ambiguous with arbitrary
    binary bytes and therefore require --format hex.
    """
    if requested == "bin":
        return "bin", data, None
    if requested == "srec":
        memory, _ = parse_srec(data)
        image, low, high = memory_to_bin(memory)
        return "srec", data, (image, low, high)
    if requested == "hex":
        image, low, high, subtype = raw_hex_to_bin(data)
        return subtype, data, (image, low, high)

    if looks_like_srec(data):
        try:
            memory, _ = parse_srec(data)
        except SFLFormatError:
            # A binary payload can coincidentally contain printable S-record-like
            # lines; only classify it as SREC after full checksum/length validation.
            pass
        else:
            image, low, high = memory_to_bin(memory)
            return "srec", data, (image, low, high)
    stripped = data.lstrip()
    if stripped.startswith(b":"):
        try:
            image, low, high, subtype = raw_hex_to_bin(data)
        except SFLFormatError:
            # Do not misclassify arbitrary binary just because its first byte is ':'.
            pass
        else:
            return subtype, data, (image, low, high)
    return "bin", data, None


def classify_for_decode(data):
    """Classify decrypted payload; separate from encode's detect_payload."""
    kind, content, image_info = detect_payload(data, "auto")
    if kind == "bin" and looks_like_hex_text(data):
        image, _, _, _ = raw_hex_to_bin(data)
        return "hextext", content, (image, 0, max(len(image) - 1, 0))
    return kind, content, image_info


def decode_file(path, outdir, skip_crc=False, hex_base=None):
    raw = read_bytes(path)
    header, cipher, meta, actual_crc = validate_sfl(raw, skip_crc=skip_crc)
    plain = decode_payload(cipher)
    kind, _, image_info = classify_for_decode(plain)
    outdir.mkdir(parents=True, exist_ok=True)
    base = Path(path).stem

    def save(suffix, content):
        target = outdir / (base + suffix)
        target.write_bytes(content)
        print(f"    -> {target.name} ({len(content)} bytes)")

    label, declared_size, declared_crc, _ = meta
    print(f"[解码] {Path(path).name}: TYPE={label}, CRC32={actual_crc:08X}"
          + (" (已跳过比较)" if skip_crc else " OK"))
    if kind == "srec":
        # S-record text carries its own absolute addresses; .bin is a derived
        # view. Preserve the text verbatim as the re-encodable payload.
        save(".srec", plain)
        memory, starts = parse_srec(plain)
        low, high = min(memory), max(memory)
        save(".ihex", memory_to_ihex(memory, starts[-1] if starts else None))
        try:
            image, _, _ = memory_to_bin(memory)
        except SFLFormatError as exc:
            print(f"    跳过连续 BIN 视图：{exc}")
        else:
            save(".bin", image)
        print(f"    S-record 地址范围: 0x{low:X}..0x{high:X}")
        print("    .srec 是原始回打包输入；.ihex 保留绝对地址；.bin（若生成）是连续视图")
    elif kind == "ihex":
        # The payload itself is Intel HEX text; keep it byte-exact.
        save(".ihex", plain)
        memory, start = parse_intel_hex(plain)
        low, high = min(memory), max(memory)
        try:
            image, _, _ = memory_to_bin(memory)
        except SFLFormatError as exc:
            print(f"    跳过连续 BIN 视图：{exc}")
        else:
            save(".bin", image)
        print(f"    Intel HEX 地址范围: 0x{low:X}..0x{high:X}")
        print("    .ihex 是原始回打包输入；.bin（若生成）是连续视图")
    elif kind == "hextext":
        # Plain hex text (SDR). The text is the actual payload; the binary is
        # a decoded view and is not by itself a faithful re-encode input.
        save(".rawhex", plain)
        image = image_info[0]
        save(".bin", image)
        print("    HEX 文本载荷（无地址信息）；.bin 为解码视图")
        print("    注意: 回打包请用 .rawhex 原文，.bin 仅用于查看/分析")
        if hex_base is not None:
            save(".ihex", memory_to_ihex(contiguous_memory(image, hex_base)))
            print(f"    Intel HEX 起始地址: 0x{hex_base:X}（显式指定）")
    else:
        save(".bin", plain)
        if hex_base is not None:
            save(".hex", memory_to_ihex(contiguous_memory(plain, hex_base)))
            print(f"    Intel HEX 起始地址: 0x{hex_base:X}（显式指定）")
        else:
            print("    按 BIN 处理；SFL 未记录地址，未生成 Intel HEX"
                  "（如需请显式指定 --hex-base）")


def collect_inputs(files, directory):
    paths = [Path(item) for item in files]
    if directory:
        paths.extend(sorted(
            p for p in Path(directory).iterdir()
            if p.is_file() and p.suffix.upper() == ".SFL"
        ))
    # Preserve order while deduplicating.
    return list(dict.fromkeys(paths))
def cmd_decode(args):
    paths = collect_inputs(args.inputs, args.all)
    if not paths:
        raise SFLFormatError("请指定输入文件，或使用 decode --all DIR")
    args.outdir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for path in paths:
        try:
            decode_file(path, args.outdir, args.skip_crc, args.hex_base)
        except (OSError, ValueError) as exc:
            failures += 1
            print(f"[错误] {path}: {exc}", file=sys.stderr)
    print(f"[完成] {len(paths) - failures} 成功，{failures} 失败")
    return 2 if failures else 0
def cmd_verify(args):
    """Decode -> pick canonical payload file -> re-encode; compare SHA-256."""
    paths = collect_inputs(args.inputs, args.all)
    if not paths:
        raise SFLFormatError("请指定输入文件，或使用 verify --all DIR")
    print(f"{'结果':<5} {'文件':<34} {'大小':>9}  SHA-256(原始 = 重建)")
    print("-" * 104)
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        for path in paths:
            try:
                raw = read_bytes(path)
                header, _, _, _ = validate_sfl(raw)
                case_dir = tmpdir / Path(path).stem
                # Decode silently: the report below is the verification table.
                with contextlib.redirect_stdout(io.StringIO()):
                    decode_file(path, case_dir, skip_crc=False, hex_base=None)
                canonical = None
                # .srec / .ihex / .rawhex preserve text payloads; .bin is the
                # fallback for address-less binary payloads.
                for suffix in (".srec", ".ihex", ".rawhex", ".bin"):
                    candidate = case_dir / (Path(path).stem + suffix)
                    if candidate.exists():
                        canonical = candidate
                        break
                if canonical is None:
                    raise SFLFormatError("解码未产生可回打包的文件")
                plain, _ = load_plain(canonical, "auto")
                cipher = encode_payload(plain)
                new_header = patch_metadata(
                    header, HEADER_LEN + len(cipher), compute_crc(cipher)
                )
                rebuilt = new_header + cipher
            except (OSError, ValueError) as exc:
                failures += 1
                print(f"FAIL  {Path(path).name:<34} {Path(path).stat().st_size:>9}  {exc}",
                      file=sys.stderr)
                continue
            sha_orig = hashlib.sha256(raw).hexdigest()
            sha_new = hashlib.sha256(rebuilt).hexdigest()
            ok = raw == rebuilt and sha_orig == sha_new
            failures += 0 if ok else 1
            print(f"{'OK' if ok else 'FAIL':<5} {Path(path).name:<34} "
                  f"{len(raw):>9}  {sha_orig}")
            if not ok:
                print(f"      rebuilt {len(rebuilt)} bytes, SHA-256 {sha_new}")
    print("-" * 104)
    print(f"[完成] {len(paths) - failures} 成功，{failures} 失败")
    return 2 if failures else 0
def load_plain(path, fmt):
    data = read_bytes(path)
    kind, content, image_info = detect_payload(data, fmt)
    if kind == "srec":
        # Preserve S-record text as the payload: its addresses and record
        # checksums are part of the original firmware representation.
        return content, kind
    if kind == "ihex":
        # Preserve Intel HEX records if explicitly supplied; their addresses
        # remain meaningful. A plain merged .hex dump becomes raw bytes.
        return content, kind
    if kind == "rawhex":
        image, _, _ = image_info
        return image, kind
    return data, "bin"
def cmd_encode(args):
    if args.template:
        template_data = read_bytes(args.template)
        if len(template_data) < HEADER_LEN:
            raise SFLFormatError("模板文件短于 576 字节")
        header = template_data[:HEADER_LEN]
    else:
        header = read_bytes(args.header)
        if len(header) != HEADER_LEN:
            raise SFLFormatError("独立标头必须恰好为 576 字节")
    plain, kind = load_plain(args.plain, args.format)
    cipher = encode_payload(plain)
    crc = compute_crc(cipher)
    total_size = HEADER_LEN + len(cipher)
    if args.patch_size:
        header = patch_metadata(header, total_size, crc)
    else:
        # Always patch CRC. SIZE must already match unless user explicitly
        # requests --patch-size; otherwise output would fail strict decoding.
        meta = parse_meta(header)
        if meta is None:
            raise SFLFormatError("标头中没有有效元数据")
        if meta[1] != total_size:
            raise SFLFormatError(
                f"模板 SIZE={meta[1]}，新文件长度={total_size}。"
                "请使用 --patch-size 更新 SIZE"
            )
        header = patch_metadata(header, total_size, crc)
    result = header + cipher
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(result)
    print(f"[编码] 输入格式={kind}, 输出={args.out}, 总长度={len(result)}")
    print(f"       数据={len(plain)} 字节, CRC32={crc:08X}")
def build_parser():
    parser = argparse.ArgumentParser(description="Yaesu SFL 固件编解码工具")
    sub = parser.add_subparsers(dest="command", required=True)
    dec = sub.add_parser("decode", help="解码 SFL，默认校验 CRC32")
    dec.add_argument("inputs", nargs="*", help="SFL 文件")
    dec.add_argument("--all", metavar="DIR", help="批量处理目录（非递归）")
    dec.add_argument("-o", "--outdir", type=Path,
                     default=Path(__file__).resolve().parent / "firmware",
                     help="解码输出目录；默认为本工具同级的 firmware/")
    dec.add_argument("--skip-crc", action="store_true",
                     help="跳过 CRC32 比较；仍检查标头和 SIZE")
    dec.add_argument("--hex-base", "--base-address", dest="hex_base",
                     type=lambda x: int(x, 0), default=None,
                     help="仅当需要 Intel HEX 时显式指定起始地址；"
                          "SFL 本身不记录地址，默认不生成 .hex")
    enc = sub.add_parser("encode", help="将 BIN/HEX/S-record 打包为 SFL")
    source = enc.add_mutually_exclusive_group(required=True)
    source.add_argument("--template", help="原始 SFL 模板")
    source.add_argument("--header", help="恰好 576 字节的标头文件")
    enc.add_argument("--plain", required=True, type=Path,
                     help="输入数据：BIN、纯 HEX、Intel HEX 或 Motorola S-record；"
                          "auto 保留 S-record/Intel HEX/纯 HEX 文本原文（保证往返），"
                          "其它按 BIN")
    enc.add_argument("-o", "--out", required=True, type=Path)
    enc.add_argument("--format", choices=("auto", "bin", "hex", "srec"),
                     default="auto",
                     help="输入格式；auto 保留文本载荷原文；"
                          "--format hex 才会把纯 HEX 文本解码为二进制后打包")
    enc.add_argument("--patch-size", action="store_true",
                     help="按输出长度更新元数据 SIZE")
    ver = sub.add_parser("verify", help="解码->回打包，逐文件比对 SHA-256")
    ver.add_argument("inputs", nargs="*", help="SFL 文件")
    ver.add_argument("--all", metavar="DIR", help="批量处理目录（非递归）")
    return parser
def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "decode":
            return cmd_decode(args)
        if args.command == "encode":
            cmd_encode(args)
            return 0
        if args.command == "verify":
            return cmd_verify(args)
    except (OSError, ValueError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2
    return 0
if __name__ == "__main__":
    sys.exit(main())
