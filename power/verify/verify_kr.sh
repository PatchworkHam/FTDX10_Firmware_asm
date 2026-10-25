#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT"
H8="hardware/files/scripts/h8.py"
BIN="decoded/firmware/FTDX10_MAIN_V0115.bin"
DISASM="hardware/files/disasm/main_disasm.txt"

echo "== 1. 读 PORTK 的指令（应 4 条）=="; python3 "$H8" find "0xfffba3"
echo "== 2. 读 0xFF2007 的 39 个读者 =="
python3 "$H8" find "mov\.b\s+@0xff2007" 9999 | tail -1
echo "== 3. and.b #0x18 全部站点（应 12 处，精确地址 + 字节）=="
python3 "$H8" find "and\.b\s+#0x18" 9999
python3 - "$BIN" <<'PY'
import pathlib
import sys

data = pathlib.Path(sys.argv[1]).read_bytes()
expected = {
    0x0150C: "e0 18", 0x01544: "e0 18", 0x08112: "e8 18",
    0x12E18: "e8 18", 0x1595C: "e0 18", 0x159D4: "e0 18",
    0x20548: "e8 18", 0x2055C: "e8 18", 0x2CB2A: "e0 18",
    0x38A5E: "e0 18", 0x3BB8A: "ee 18", 0x3BCA6: "ea 18",
}
bad = False
for addr, want in sorted(expected.items()):
    got = data[addr:addr + 2].hex(" ")
    ok = got == want
    print(f"  0x{addr:05X}: {got} 期望 {want} {'OK' if ok else '★错误'}")
    bad = bad or not ok
if bad:
    sys.exit(1)
print(f"  共 {len(expected)} 处，精确匹配")
PY
echo "== 4. 排除第二位读取路径（应 0 + 0）=="
python3 "$H8" find "cmp\.b\s+#.*,@0xff2007" 9999 | tail -1
python3 "$H8" find "btst\s+#0x[34],@0xff2007" 9999 | tail -1
echo "== 5. EXP 消费点（应 1 + 1）=="
python3 "$H8" find "btst\s+#0x5,@0xff2007" 9999 | tail -1
python3 "$H8" find "and\.b\s+#0x20,r0l" 9999 | tail -1
echo "== 6. 死代码 0x7D2E / 活路径 0xA38+0xA58（Python 单一实现）=="
python3 "$ROOT/verify/verify_short_jumper.py" --bin "$BIN" --disasm "$DISASM"
echo "== 7. 功率档→菜单区间表 =="
python3 -c "d=open('$BIN','rb').read(); print(d[0x159f6:0x159f6+32].hex(' '))"
echo "== 8. 补丁成品精确校验（5 个补丁点：地址 + 字节）=="
python3 - <<'PY'
import pathlib
import sys


def load_srec(path: str) -> dict[int, int]:
    image: dict[int, int] = {}
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if len(line) < 4 or line[0] != "S" or line[1] not in "123":
            continue
        raw = bytes.fromhex(line[2:])
        addr_len = {"1": 2, "2": 3, "3": 4}[line[1]]
        addr = int.from_bytes(raw[1:1 + addr_len], "big")
        for offset, value in enumerate(raw[1 + addr_len:-1]):
            image[addr + offset] = value
    return image


expected = {
    0x08112: "f8 18", 0x1595C: "f0 18", 0x159D4: "f0 18",
    0x3BB8A: "fe 18", 0x3BCA6: "fa 18",
}
image = load_srec("power/patched/MAIN_V0115_M_mask100W.srec")
bad = False
for addr, want in sorted(expected.items()):
    got = bytes(image.get(addr + i, 0xFF) for i in range(2)).hex(" ")
    ok = got == want
    print(f"  0x{addr:05X}: {got} 期望 {want} {'OK' if ok else '★错误'}")
    bad = bad or not ok
if bad:
    sys.exit(1)
print(f"  共 {len(expected)} 个补丁点，精确匹配")
PY

echo "== 9. SFL 解码 + 逐字节对比 + CRC32 =="
DEC_DIR="$(mktemp -d)"
trap 'rm -rf "$DEC_DIR"' EXIT
python3 decoded/yaesu_sfl_tool.py decode power/patched/MAIN_V0115_M_mask100W.SFL -o "$DEC_DIR"
cmp "$DEC_DIR/MAIN_V0115_M_mask100W.srec" power/patched/MAIN_V0115_M_mask100W.srec
echo "  解码 .srec 与成品 .srec 逐字节一致"

echo "== 10. 成品指纹 =="
md5sum power/patched/MAIN_V0115_M_mask100W.*
