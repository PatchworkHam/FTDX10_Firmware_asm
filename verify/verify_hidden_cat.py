#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FTDX10 未公开 CAT 命令 —— 独立二次验证脚本
只依赖原始固件字节（不依赖任何反汇编文本），逐条核对报告结论。

用法：
    python3 verify/verify_hidden_cat.py          # 默认读取 decoded/firmware/FTDX10_MAIN_V0115.bin
    python3 verify/verify_hidden_cat.py <bin路径>

输出：每条断言 P/F（PASS/FAIL），末尾给出总结。
"""
import sys, struct, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_BIN = os.path.join(ROOT, "decoded", "firmware", "FTDX10_MAIN_V0115.bin")

# ---- 手册命令（对照 original/manuals/dx10-cat.pdf；UL=UNLOCK 也在手册摘要表内）----
DOC = ("AB AC AG AI AM AO AV BA BC BD BI BM BP BS BU BY CF CH CN CO CS CT DA DN DT "
       "ED EM EN EU EX FA FB FN FT GT ID IF IS KM KP KR KS KY LK LM MA MB MC MD MG ML "
       "MR MS MT MW MX NA NB NL NR OI OS PA PB PC PL PR PS QI QR QS RA RC RD RF RG RI RL "
       "RM RS RT RU SC SD SF SH SM SQ SS ST SV TS TX UL UP VD VG VM VS VX XT ZI").split()

results = []
def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))

def u16(b, a): return struct.unpack(">H", b[a:a+2])[0]
def u32(b, a): return struct.unpack(">I", b[a:a+4])[0]

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BIN
    b = open(path, "rb").read()
    print(f"# 目标: {path}  ({len(b)} 字节)\n")

    TAB, PTRB, PTRN = 0x27CF2, 0x27AFA, 126
    cmds = [b[TAB+i*2:TAB+i*2+2].decode("ascii", "replace") for i in range(PTRN)]
    ptrs = [u32(b, PTRB+i*4) for i in range(PTRN)]

    # CHK1 表长/唯一性
    check("CHK1 命令表有 126 条且互不相同", len(cmds) == 126 and len(set(cmds)) == 126,
          f"n={len(cmds)} uniq={len(set(cmds))}")

    # CHK2 两表相邻：指针表末尾正好接命令表
    check("CHK2 指针表 0x27AFA+126*4 == 命令表 0x27CF2", PTRB + PTRN*4 == TAB,
          hex(PTRB + PTRN*4))

    # CHK3 未公开集合
    extras = sorted(set(cmds) - set(DOC))
    expect = sorted("AN BG DP DS EK EO FD FK FR FS KC KT MK PE RO SY VF VT SP VE JP ZZ E0 E8".split())
    check("CHK3 未公开命令 = 预期的 24 条", extras == expect, ",".join(extras))

    # CHK4 桩判定（解 4 字节 bra 的 16 位位移，PC=addr+4）
    def bra_target(a):
        return (a + 4 + struct.unpack(">h", b[a+2:a+4])[0]) & 0xFFFFFF
    def is_stub(a):
        return b[a] == 0x58 and b[a+1] == 0x00 and bra_target(a) == 0x27A5E
    stubs = [cmds[i] for i in range(PTRN) if is_stub(ptrs[i])]
    stub_undoc = sorted(set(stubs) & set(extras))
    code_undoc = sorted(set(extras) - set(stubs))
    check("CHK4 桩总数=14（13 未公开 + 文档命令 UL）",
          len(stubs) == 14 and stub_undoc == sorted(
              "AN BG DP DS FK FR FS KC MK RO SY VF VT".split()) and stubs.count("UL") == 1,
          f"stubs={','.join(stubs)}")
    check("CHK5 未公开 = 13 桩 + 11 有效",
          code_undoc == sorted("EK EO FD KT PE SP VE JP ZZ E0 E8".split()),
          "有效=" + ",".join(code_undoc))

    # CHK6 两处解析器扫描计数
    check("CHK6 解析器1 计数=0x7e(126)", b[0x27A10] == 0xF0 and b[0x27A11] == 0x7E)
    check("CHK7 解析器2 计数=0xfc(252,越界 bug)", b[0x2E012] == 0xF0 and b[0x2E013] == 0xFC)

    # CHK8 派发器跳转表上限
    check("CHK8 派发器内联表上限(0x27AF8)=125", u16(b, 0x27AF8) == 125,
          f"0x{u16(b,0x27AF8):x}")

    # CHK9 VE 魔术串：逐条 cmp.b #imm,r0l 的立即数字节 = 'R','A','H','0','7','6','H'
    #       (0x2d4ee/4fa/506/512/51e/52a/536 是 a8 xx 指令，立即数在 +1)
    ve_ops = (0x2D4EE, 0x2D4FA, 0x2D506, 0x2D512, 0x2D51E, 0x2D52A, 0x2D536)
    ve_magic = bytes(b[a+1] for a in ve_ops)
    check("CHK9 VE 魔术串 == 'RAH076H'", ve_magic == b"RAH076H", repr(ve_magic))

    # CHK10 ZZ 前缀 'PE0010' + 长度 8
    #       每条 cmp.b #imm,@0xff8aNN 长 8 字节，立即数在末字节
    zz_ops = (0x27DEE, 0x27DFA, 0x27E06, 0x27E12, 0x27E1E, 0x27E2A)
    zz_pref = bytes(b[a+7] for a in zz_ops)
    check("CHK10 ZZ 前缀='PE0010'", zz_pref == b"PE0010", repr(zz_pref))
    check("CHK10b ZZ 长度=8", b[0x27E36:0x27E38] == b"\xab\x08")

    # CHK11 JP 前缀 '0010' + 长度 4/0xc
    jp_ops = (0x27E7A, 0x27E86, 0x27E92, 0x27E9E)
    jp_pref = bytes(b[a+7] for a in jp_ops)
    check("CHK11 JP 前缀='0010'", jp_pref == b"0010", repr(jp_pref))
    check("CHK11b JP 长度=4 或 0xc", b[0x27EAA:0x27EAC] == b"\xab\x04" and b[0x27EB0:0x27EB2] == b"\xab\x0c")

    # CHK12 E0/E8 长度与源/目标（mov.l #imm,erN 的立即数在指令 +2 处，4 字节）
    check("CHK12 E0 长度=0xe5(229)", b[0x2D7CA] == 0xF8 and b[0x2D7CB] == 0xE5)
    check("CHK12b E0 源=0xff36e2 目标=0xff6089",
          u32(b, 0x2D7D6) == 0x00FF36E2 and u32(b, 0x2D7DC) == 0x00FF6089)
    check("CHK13 E8 长度=0x294d(10573)", b[0x2D8DA] == 0x79 and u16(b, 0x2D8DC) == 0x294D)
    check("CHK13b E8 源=0xff36e2 目标=0xff6089",
          u32(b, 0x2D8E6) == 0x00FF36E2 and u32(b, 0x2D8EC) == 0x00FF6089)

    # CHK14 JP/ZZ 末尾 jmp 0x6ee（复位入口）；0x6ee 是复位初始化
    check("CHK14 ZZ 尾 jmp 0x6ee", b[0x27E76:0x27E7A] == b"\x5a\x00\x06\xee")
    check("CHK14c JP 处理域内不含 jmp 0x6ee（JP 不复位）",
          b"\x5a\x00\x06\xee" not in b[0x27E7A:0x28104])
    check("CHK14b 0x6ee 是复位初始化(栈=0xffc000)",
          u32(b, 0x6EE) == 0x7A0700FF and u16(b, 0x6F2) == 0xC000)

    # CHK15 接收 worker：0xff8a94 = 参数长度
    check("CHK15 0x27ab2 从 0xff8a94 取参数长度到 r3l",
          b[0x27AB2:0x27AB8] == b"\x6a\x2b\x00\xff\x8a\x94")

    # CHK16 SPW：长度 6 + 校验 7 字节 + 地址阈值 0x8000 + 写原语 0xa528
    check("CHK16 SPW 长度=6 / 校验 7 字节 / 阈值 0x8000 / 调 0xa528",
          b[0x2D016:0x2D018] == b"\xab\x06" and b[0x2D01E] == 0xF2 and b[0x2D01F] == 0x07
          and u16(b, 0x2D048) == 0x8000 and b[0x2D054:0x2D058] == b"\x5e\x00\xa5\x28")

    # 交叉：手册里出现的两字母词中，除噪声外没有“表外命令”
    print("\n# 附：未公开命令处理函数入口地址（按表）")
    idx = {c: i for i, c in enumerate(cmds)}
    for c in expect:
        print(f"   {c}: 0x{ptrs[idx[c]]:05X}{'  [桩]' if is_stub(ptrs[idx[c]]) else ''}")

    ok = sum(results)
    print(f"\n==== 结果：{ok}/{len(results)} 通过 ====")
    return 0 if ok == len(results) else 1

if __name__ == "__main__":
    sys.exit(main())
