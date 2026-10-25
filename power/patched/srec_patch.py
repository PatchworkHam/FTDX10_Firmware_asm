#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S-record 字节级补丁器

- 支持跨记录写入(一条指令落在两条 S-record 时自动拆分)
- 自动重算每条受影响记录的校验和 (sum(count+addr+data+cksum) & 0xFF == 0xFF)
- 字节级保留原文件(不改变 CRLF / 行数 / 其余内容)

用法:
    python3 power/patched/srec_patch.py SRC.srec DST.srec --set 0x8F1A:F218 --set 0x3BCA0:F21800000000
"""
import argparse

def parse(ln: str):
    t = ln[:2]
    b = bytes.fromhex(ln[2:])
    cnt = b[0]
    alen = 2 if t == 'S1' else 3
    addr = int.from_bytes(b[1:1 + alen], 'big')
    data = bytearray(b[1 + alen:1 + alen + cnt - alen - 1])
    return t, alen, addr, data

def rebuild(t: str, alen: int, addr: int, data: bytearray) -> str:
    body = bytes([len(data) + alen + 1]) + addr.to_bytes(alen, 'big') + bytes(data)
    ck = (0xFF - (sum(body) & 0xff)) & 0xff   # Motorola: sum(count+addr+data+ck) & 0xFF == 0xFF
    return t + body.hex().upper() + f"{ck:02X}"

def patch_file(src, dst, patches):
    raw = open(src, 'r', newline='').read()
    lines = raw.split('\n')                 # elements still carry trailing '\r'
    idx, parsed = {}, {}
    for i, ln in enumerate(lines):
        s = ln[:-1] if ln.endswith('\r') else ln
        if s[:2] in ('S1', 'S2'):
            t, alen, addr, data = parse(s)
            idx[addr] = i
            parsed[i] = (t, alen, addr, data)
    changed = {}
    for w, nb in patches:
        off = 0
        while off < len(nb):
            cand = [a for a in idx if a <= w + off < a + len(parsed[idx[a]][3])]
            assert cand, f"no record for 0x{w + off:05X}"
            a = max(cand)
            i = idx[a]
            t, alen, addr, data = parsed[i]
            o = w + off - addr
            n = min(len(nb) - off, len(data) - o)
            data[o:o + n] = nb[off:off + n]
            changed[i] = (t, alen, addr, data)
            off += n
    for i, (t, alen, addr, data) in changed.items():
        eol = '\r' if lines[i].endswith('\r') else ''
        lines[i] = rebuild(t, alen, addr, data) + eol
    open(dst, 'w', newline='').write('\n'.join(lines))
    return sorted(changed)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('dst')
    ap.add_argument('--set', action='append', required=True, help='ADDR:HEXBYTES')
    a = ap.parse_args()
    ps = []
    for s in a.set:
        ad, hx = s.split(':')
        ps.append((int(ad, 16), bytes.fromhex(hx)))
    ch = patch_file(a.src, a.dst, ps)
    print("changed lines:", [i + 1 for i in ch])
