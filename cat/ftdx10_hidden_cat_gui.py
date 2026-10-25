#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 FTDX10 未公开(隐藏) CAT 命令 —— GUI 读写工具
================================================================================

功能
----
* 图形界面(tkinter)，支持读写固件中全部 11 条有效隐藏命令：
    VE  FD  KT  EK  EO  PE  SP(二进制子协议)  E0  E8  JP  ZZ
* 机型 / 魔术串：
    - 允许用户输入 VE / JP / ZZ 魔术串，也可“手动输入 / 新建”自定义机型；
    - 机型与魔术串统一保存在 rig_profiles.json；
    - 预制 FTDX10（RAH076H / JP0010 / PE0010）与
      FT-891（RAH065H / JP0891 / PE0891，来自 AH065_M_V0110 静态研究）；
    - 支持 ID / VE 读取和 JP 参数读写。
* 功率 / 频段：
    - 读写 FRAM[0x0007] 的 PWR 档（100/50/20/10 W）；
    - 读写 TS(bit0-2) / EXP(bit5)，显示对应频段范围；
    - 读写各频段 RF POWER(F) 0x0335–0x0338、MAX POWER(AC) 0x046A–0x046D；
    - 提供“临时宽频/服务模式”入口（ZZ<ZZ前缀>000），并提示不要用 ZZPE001001 退出。
* 外部 FRAM 与设置表读写：
    - SPW/SPR : 外部 FRAM 地址空间 0x0000–0x7FFF，按 2 字节读写；
    - SPw/SPr : 内部设置/校准表 0xFFA948，地址 0x000A–0x017C，按 1 字节读写；
    - 完整 FRAM 编辑器：区域说明、十六进制编辑、导入/导出、整片读写。
* 服务菜单 / 校准：
    - 294 项服务菜单中文/英文标签、频段、存储地址、应用目标；
    - 371 字节内部设置表整表读写；
    - 按服务菜单项编辑校准值，SPN 选择项目，SPAET 应用特殊项。
* PE 校准：27 组选择子的原始读取、中文说明和写入。
* 完全备份：把全部可读信息导出为 JSON(含设置表、外部 FRAM、E0/E8 转储、
  PE 全表、版本串、FD/KT/JP 等)，并支持从 JSON 恢复可写部分

依赖
----
    pip install pyserial        # 串口
    tkinter                     # Python 自带(部分 Linux 需 apt install python3-tk)

协议要点(详见 cat/隐藏CAT命令列表.md；取证见 cat/分析过程.md)
--------------------------------------
* 帧 = <2 字母命令><参数...>';'，ASCII 命令以 ';'(0x3B) 结束。
* 大小写：以 "SP"/"EM" 开头的帧不做大写化(所以 SPW≠SPw)。
* SP 是二进制子协议：SP 之后是原始字节，校验 = ('S'+'P'+其后所有字节) & 0xFF，
  作为单个原始字节发送；参数里应避免出现 0x3B。
* 成功 ACK = 'A;'，失败 = '?;'。部分命令成功时不回包(EO/EK/KT写/PE写/FD写/JP写)。

安全提示
--------
SPW/SPR 可改外部 FRAM（含配置/设置），SPw/SPA*/PE/JP 会写校准/启动参数，
ZZ 会立即复位。除非你能重刷固件，否则不要写。
写 FRAM/服务菜单/校准/功率前至少备份 FRAM[0x0000–0x0490]。
进入服务模式用 ZZ<ZZ前缀>000;（FTDX10: ZZPE001000;；FT-891: ZZPE089100;）；
退出用 RESET 菜单或整机断电，不要把 ZZPE001001;（ALL RESET）当作普通退出。
FT-891 目前只分离了 VE/JP/ZZ 魔术串；E0/E8 长度、服务表/功率地址仍未适配。
本工具为静态逆向结论，未在真机验证；使用风险自负。

命令行(无 GUI)
--------------
    python3 ftdx10_hidden_cat_gui.py -p /dev/ttyUSB0 -b 38400 --backup out.json
    python3 ftdx10_hidden_cat_gui.py -p /dev/ttyUSB0 -b 38400 --restore out.json --yes
================================================================================
"""
from __future__ import annotations

import argparse
import copy
import csv
import datetime
import json
import queue
import re
import sys
import threading
import time
from pathlib import Path

try:
    import serial
    from serial.tools import list_ports
except Exception:  # pyserial 未安装
    serial = None
    list_ports = None

# ------------------------------------------------------------------ 常量
SETTINGS_TABLE_BASE = 0xFFA948      # 设置/校准表基址
TABLE_START = 0x000A                # SPw/SPr 地址下限
TABLE_END = 0x017C                  # SPw/SPr 地址上限(含)
TABLE_LEN = TABLE_END - TABLE_START + 1          # 371
FRAM_START = 0x0000                 # SPW/SPR 外部 FRAM 地址空间
FRAM_END = 0x7FFF
APP_TITLE = "FTDX10 隐藏 CAT 工具"
BACKUP_FORMAT = "ftdx10-hidden-cat-backup"
BACKUP_VERSION = 1
MANUAL_PROFILE_NAME = "自定义（手动输入）"

# 本文件所在目录、仓库根目录与外部数据文件
APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
PROFILES_PATH = APP_DIR / "rig_profiles.json"
FRAM_MAP_PATH = ROOT_DIR / "fram" / "fram_map.csv"
SERVICE_CSV_PATH = ROOT_DIR / "service_menu" / "服务菜单项目表.csv"

# FRAM[0x0007] 位定义（来自 power/TS 研究）
CONFIG_ADDR = 0x0006
TS_MASK = 0x07
PWR_MASK = 0x18
EXP_BIT = 0x20
AUX_MASK = 0xC0
PWR_BITS = {100: 0x18, 50: 0x00, 20: 0x08, 10: 0x10}
PWR_WATTS = {v: k for k, v in PWR_BITS.items()}

# 各频段 RF POWER(F) / MAX POWER(AC)，顺序：HF / 50M / 70M / AM
F_ADDR = 0x0335
AC_ADDR = 0x046A
POWER_BAND_NAMES = ("HF", "50M", "70M", "AM")

# TS 频段表（MHz，来源 MAIN V01-15；与 TS/yaesu_ts_cat.py 一致）
TS_TABLES = {
    0: [(1.8, 2.0), (3.5, 4.0), (7.0, 7.3), (10.1, 10.15),
        (14.0, 14.35), (18.068, 18.168), (21.0, 21.45),
        (24.89, 24.99), (28.0, 29.7), (50.0, 54.0)],
    1: [(1.8, 2.0), (3.5, 4.0), (7.0, 7.3), (10.1, 10.15),
        (14.0, 14.35), (18.068, 18.168), (21.0, 21.45),
        (24.89, 24.99), (28.0, 29.7), (50.0, 54.0)],
    2: [(1.81, 2.0), (3.5, 3.8), (5.25, 5.4065), (7.0, 7.2),
        (10.1, 10.15), (14.0, 14.35), (18.068, 18.168),
        (21.0, 21.45), (24.89, 24.99), (28.0, 29.7),
        (50.0, 52.0), (70.0, 70.5)],
    3: [(1.8, 2.0), (3.5, 3.8), (5.3515, 5.3665), (7.0, 7.2),
        (10.1, 10.15), (14.0, 14.35), (18.068, 18.168),
        (21.0, 21.45), (24.89, 24.99), (28.0, 29.7), (50.0, 52.0)],
    4: [(1.83, 1.85), (3.5, 3.8), (7.0, 7.1), (10.1, 10.15),
        (14.0, 14.35), (18.068, 18.168), (21.0, 21.45),
        (24.89, 24.99), (28.0, 29.7), (50.0, 52.0)],
    5: [(1.8, 2.0), (3.5, 4.0), (7.0, 7.3), (10.1, 10.15),
        (14.0, 14.35), (18.068, 18.168), (21.0, 21.45),
        (24.89, 24.99), (28.0, 29.7), (50.0, 54.0)],
    6: [(1.8, 2.0), (3.5, 4.0), (7.0, 7.3), (10.1, 10.15),
        (14.0, 14.35), (18.068, 18.168), (21.0, 21.45),
        (24.89, 24.99), (28.0, 29.7), (50.0, 54.0)],
    7: [(1.8, 2.0), (3.5, 4.0), (5.3515, 5.3665), (7.0, 7.5),
        (10.0, 10.5), (14.0, 14.5), (18.0, 18.5),
        (21.0, 21.5), (24.5, 25.0), (28.0, 30.0), (50.0, 54.0)],
}
EXP_TABLE = [(1.8, 30.0), (50.0, 54.0), (70.0, 70.5)]
SERVICE_TABLE = [(1.8, 75.0)]

# 服务菜单 CSV 的 apply_kind 中文说明（来自 0x1662 应用逻辑）
SM_KIND_ZH = {
    "item": "普通项（371B 表源值）",
    "special_11": "特殊 11B 表头",
    "shared_54": "共享 54B（每 3 项共享 1B）",
    "meter_vdd": "仪表 VDD 表头",
    "meter_alc": "仪表 ALC 表头",
    "meter_idd": "仪表 IDD 表头",
}

# PE 读数(0xFFABD1..0xFFABE2)选择子取值
PE_SEL = ("0", "1", "2")


def fmt_ranges(segments) -> str:
    """把 [(低, 高), ...] 格式化为 “低-高 / 低-高” 的 MHz 文本。"""
    return " / ".join(f"{lo:g}-{hi:g}" for lo, hi in segments)


def effective_ranges(ts: int, exp: bool):
    """返回 (频段段列表, 覆盖说明)。优先级：EXP > TS。"""
    if exp:
        return EXP_TABLE, "EXP=1（覆盖 TS）"
    return TS_TABLES.get(ts, []), f"TS={ts}"


def pwr_bits_from_text(text: str) -> int:
    """从 “100 W” / “0x18” 文本解析 PWR 位。"""
    t = text.strip().upper()
    for watts, bits in PWR_BITS.items():
        if t.startswith(str(watts)) or t == f"0X{bits:02X}":
            return bits
    raise ValueError(f"未知功率档: {text!r}")


def default_profiles() -> dict:
    """内置机型配置；rig_profiles.json 缺失时使用。"""
    return {
        "format": "ftdx10-rig-profiles",
        "version": 1,
        "profiles": [
            {
                "id": "ftdx10",
                "name": "Yaesu FTDX10 (MAIN V01-15)",
                "cat_id": "0761",
                "ve_magic": "RAH076H",
                "ve_full": "VERAH076H",
                "jp_prefix": "JP0010",
                "jp_magic": "1234EDCB",
                "zz_prefix": "PE0010",
                "status": "固件静态确认 / 真机未验证",
                "magic_status": "固件静态确认 / 真机未验证",
                "magic_evidence": (
                    "MAIN V01-15：VE 在 0x2D4E8 逐字节比较 RAH076H；"
                    "ZZ 在 0x27DEE 校验 PE0010；JP 在 0x27F3A 校验 0010。"),
                "notes": "本仓库静态逆向目标。",
            },
            {
                "id": "ft891",
                "name": "Yaesu FT-891",
                "cat_id": "0650",
                "ve_magic": "RAH065H",
                "ve_full": "VERAH065H",
                "jp_prefix": "JP0891",
                "jp_magic": "",
                "zz_prefix": "PE0891",
                "status": "魔术串静态确认 / 其它功能未验证",
                "magic_status": "固件静态确认 / 真机未验证（VE/JP/ZZ）",
                "magic_evidence": (
                    "AH065_M_V0110：VE 在 0x1D642 逐字节比较 RAH065H；"
                    "JP 在 0x19728 比较 0891；ZZ 在 0x1968A 比较 PE0891。"
                    "JP 写入值尚未解码，留空，需按实机/后续研究手动填写。"),
                "notes": ("魔术串来自 AH065_M_V0110（2021-09-27）静态分析。"
                          "其余 E0/E8 长度、服务表/功率地址与 FTDX10 不同，"
                          "本 GUI 尚未按 FT-891 全量适配。"),
            },
        ],
        "fram_regions_zh": {},
        "pe_labels": {},
    }


def load_rig_profiles() -> dict:
    """读取 rig_profiles.json；失败时返回内置配置。"""
    try:
        with open(PROFILES_PATH, encoding="utf-8") as fp:
            obj = json.load(fp)
        if not isinstance(obj.get("profiles"), list) or not obj["profiles"]:
            raise ValueError("profiles 为空")
        return obj
    except Exception:
        return copy.deepcopy(default_profiles())


def save_rig_profiles(obj: dict) -> None:
    """把机型配置（含魔术串）写回 rig_profiles.json。"""
    with open(PROFILES_PATH, "w", encoding="utf-8") as fp:
        json.dump(obj, fp, ensure_ascii=False, indent=2)


def load_fram_regions() -> list[dict]:
    """读取 fram/fram_map.csv，返回区域列表；文件缺失时返回空列表。"""
    if not FRAM_MAP_PATH.exists():
        return []
    rows = []
    with open(FRAM_MAP_PATH, encoding="utf-8", newline="") as fp:
        for row in csv.DictReader(fp):
            try:
                row["start_i"] = int(row["start"], 16)
                row["end_i"] = int(row["end"], 16)
                row["size_i"] = int(row["size"])
            except Exception:
                continue
            rows.append(row)
    return rows


def load_service_items() -> list[dict]:
    """读取 294 项服务菜单 CSV；文件缺失时返回空列表。"""
    if not SERVICE_CSV_PATH.exists():
        return []
    rows = []
    with open(SERVICE_CSV_PATH, encoding="utf-8", newline="") as fp:
        for row in csv.DictReader(fp):
            try:
                row["n"] = int(row["code_dec"])
                row["item_addr"] = int(row["item_fram"], 16)
                row["apply_addr"] = int(row["apply_fram"], 16)
                row["band_mhz_f"] = float(row["band_mhz"])
            except Exception:
                continue
            rows.append(row)
    return rows


def parse_hex_dump(text: str) -> list[tuple[int, bytes]]:
    """解析“AAAA: 00 11 22 ...”十六进制编辑框，返回 [(地址, 数据), ...]。

    允许行尾带 ASCII 注释；没有地址前缀的行会被拒绝，避免写错位置。
    """
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "//")):
            continue
        m = re.match(r"^(?:0x)?([0-9A-Fa-f]{1,4})\s*:\s*(.*)$", line)
        if not m:
            raise ValueError(f"无法解析行（需要 AAAA: 00 11 …）: {line}")
        addr = int(m.group(1), 16)
        body = m.group(2)
        # 截掉常见的行尾 ASCII 区（两个以上空格后接可打印文本）
        body = re.split(r"\s{2,}", body, maxsplit=1)[0]
        try:
            data = bytes.fromhex(body.replace(" ", ""))
        except ValueError as exc:
            raise ValueError(f"十六进制解析失败: {line}") from exc
        if not data:
            continue
        out.append((addr, data))
    return out

# ------------------------------------------------------------------ 异常
class CatError(Exception):
    pass

class CatNotOpen(CatError):
    pass

class CatTimeout(CatError):
    pass

# ------------------------------------------------------------------ 帧构造
def sp_frame(body: bytes) -> bytes:
    """构造 SP 二进制帧：body 不含 'S''P'，函数自动加校验与 ';'。

    0x3B 是 ASCII 帧结束符 ';'，出现在二进制参数/校验中会被设备截断，
    因此这里直接拒绝，避免发出半帧。
    """
    head = b"SP" + bytes(body)
    chk = sum(head) & 0xFF
    frame = head + bytes([chk]) + b";"
    if 0x3B in frame[:-1]:
        raise CatError("SP 帧参数或校验包含 0x3B (';')，已拒绝发送；请换地址/值")
    return frame


def hexs(b: bytes, sep: str = " ") -> str:
    return sep.join(f"{x:02X}" for x in b)


# ------------------------------------------------------- 按钮悬浮说明
# 键为按钮文字（唯一者）。重复文字（如多处“发送”）改为在创建处单独绑定。
BUTTON_TIPS = {
    # 全局
    "取消任务": "中断正在运行的长任务（范围导出 / 备份 / 恢复）。",
    # 连接
    "刷新": "重新枚举本机串口设备并刷新下拉列表。",
    "打开": "按所选端口与波特率打开串口；打开后按钮变为“关闭”，再按即断开。",
    "关闭": "断开当前串口连接。",
    # 快捷命令
    "VE 版本查询 (魔术串)": "发送 VE + 当前魔术串（默认 RAH076H）; 读取 DSP/ACPU/TFT/SDR 版本（48 字节）。只读。",
    "ID 查询": "发送 ID; 读取 7 字节机型 ID（如 ID0761;），用于核对机型预设。只读。",
    "FD 读": "发送 FD00<x>; 读取通道 x 的 32 位频率/数据寄存器（15 字节）。只读。",
    "FD 写 (8 hex)": "发送 FD0020<8hex>; 写入 32 位值。写操作。",
    "KT 读余量": "发送 KT; 读取 CW 文本（键控存储器）缓冲状态（6 字节）。只读。",
    "KT 写入文本": "先读 KT 剩余空间，再发送 KT1<文本>; 写入 CW 文本（固件允许 ASCII 0x20–0x5F 或 0x7D，字母转大写，≤30 字符）。写操作。",
    "KT 清空 (KT0)": "发送 KT0; 清空 CW 文本缓冲。写操作。",
    "EK 触发": "发送 EK; 无参触发（调用 0xDA00）。通常无回包。",
    "EO 微调 (a,sign,b,ddd)": "发送 EO<a><sign><b><ddd>;（如 0+0000）做 32 位偏移/微调。写操作。",
    "JP 读 (当前前缀)": "发送当前 JP 前缀（FTDX10: JP0010；FT-891: JP0891），读取启动/服务参数（25 字节）。只读。",
    "JP 写 (互补 8 hex)": "发送 JP 前缀+8hex；写入启动参数，前后两字须互补（v^w==0xFFFF）。写操作，风险较高。",
    "E0 取 229B": "发送 E0; 取 229 字节诊断帧。只读。",
    "E8 取 10573B": "发送 E8; 取 10573 字节大转储（低速需十余秒）。只读。",
    "SPARD 读 371B 表": "SP 帧 ARD：一次读出 371 字节设置表整帧（378 字节，需 0xFF2020 bit7）。只读。",
    "SPM TR": "SP 帧 MTR：读取仪表/温度类测量值（14 字节）。只读。",
    "SPM T2": "SP 帧 MT2：读取另一组测量值（16 字节）。只读。",
    "SPN 读 0xFFAB00": "SP 帧 N FF FF：读取 0xFFAB00 单元（7 字节，需门控）。只读。",
    "SPAWE 写使能": "SP 帧 AWE：设置表写使能（调用 0xDCBA）。写使能。",
    "SPACL 清除": "SP 帧 ACL：清除设置表（调用 0x7ED0）。破坏性。",
    "SPAET": "SP 帧 AET：设置表相关操作（调用 0x1662），成功回 A; 否则 ?;。",
    "ZZ 服务模式 (会复位)": "发送 ZZ<ZZ前缀>000;（FTDX10: ZZPE001000;；FT-891: ZZPE089100;），写 0xFF2002=0 并 soft reset，进入服务模式。会立即重启设备。",
    # FRAM / 设置表
    "读 2 字节": "用 SPR 读外部 FRAM 地址（0x0000-0x7FFF）的 2 字节。只读。",
    "写 2 字节": "用 SPW 写 2 字节到外部 FRAM 地址（有确认框）。写操作。",
    "读 1 字节": "用 SPr 读设置表地址（0x000A-0x017C）的 1 字节。只读。",
    "写 1 字节": "用 SPw 写 1 字节到设置表地址（有确认框）。写操作，影响校准。",
    "读 0xFF2001": "用 SPr 特殊地址 0xFFFF 读取 0xFF2001 单元。只读。",
    "导出到文件": "用 SPR 逐 2 字节把 [起,止] 范围导出为 .bin。只读。",
    "我已备份 0–0x0490": "写入 FRAM/服务菜单前的强制确认；请先用“备份 0–0x0490”保存 .bin。",
    # 机型 / 功率 / 服务菜单 / PE / FRAM 全表
    "应用魔术串": "把本页 VE / JP / ZZ 魔术串应用到当前串口会话。",
    "重新载入 JSON": "重新读取 cat/rig_profiles.json。",
    "保存当前配置到 JSON": "把本页机型与 VE/JP/ZZ 魔术串写回 rig_profiles.json；保留其它预设。",
    "手动输入 / 新建": "清空编辑框，手动输入任意机型魔术串；保存时新建自定义预设，不覆盖已有项。",
    "读取 ID": "发送 ID; 核对实机 CAT ID 是否与预设一致。",
    "读取 VE 版本": "发送 VE+魔术串，读取 48 字节版本串。",
    "JP 读取": "发送当前 JP 前缀（FTDX10: JP0010；FT-891: JP0891），读取 25 字节并解析互补字。",
    "JP 写入": "发送 JP 前缀+8hex；两个 16 位字必须互补，写启动参数风险高。",
    "读取配置": "读取 FRAM[0x0006..0x0007]，解析 TS/PWR/EXP/AUX。",
    "预览功率写入": "只计算 PWR 位目标值，不写设备。",
    "写入功率档": "只改 FRAM[0x0007] 的 bit3-4，保留 TS/EXP/AUX，写后回读。",
    "预览 TS/EXP": "只计算 TS/EXP 位目标值，不写设备。",
    "写入 TS/EXP": "改 FRAM[0x0007] 的 TS/EXP 位，写后需整机断电生效。",
    "读取 F/AC": "读取各频段 RF POWER(F) 0x0335–0x0338 与 MAX POWER(AC) 0x046A–0x046D。",
    "写入 F/AC": "写各频段 F/AC 字节；有效功率=min(F,AC)，有确认。",
    "进入服务模式 / 临时宽频（会复位）": "发送 ZZ<ZZ前缀>000; 进入服务模式（FTDX10 下可到 1.8–75 MHz），会立即软复位；不要用 ZZ<前缀>01（ALL RESET）退出。",
    "读取区间": "按起止地址用 SPR 读取外部 FRAM 并显示为十六进制。",
    "写入编辑框": "解析编辑框中的 AAAA: ... 行并逐段写回 FRAM。",
    "读取全部 32KB": "读取 0x0000–0x7FFF 全部外部 FRAM，耗时较长。",
    "备份 0–0x0490": "快速备份 FRAM[0x0000–0x0490]，写服务菜单/校准前至少做这一份。",
    "导出区间 .bin": "把编辑框数据解析后保存为 .bin，不访问设备。",
    "导入 .bin 到编辑框": "把 .bin 文件显示到编辑框，可用“写入编辑框”写回。",
    "读取整表 (SPr 371B)": "用 SPr 逐字节读取 371B 服务菜单/设置表，不依赖门控。",
    "SPARD 整表": "用 SPARD 一次读取 371B（需要服务模式门控）。",
    "读取选中项": "读取选中服务菜单 N 在 371B 表中的校准字节。",
    "写入选中项校准值": "用 SPw 写选中 N 的 371B 表字节；可选写后 SPAET。",
    "SPN 选择/应用 N": "发送 SPN N，选择/应用服务项目并切换当前波段/模式（需服务模式）。",
    "SPAET 应用特殊项": "发送 SPAET；只有特殊表头/r3l=1/2 项会成功，普通项回 ?;。",
    "进入服务模式": "发送 ZZ<ZZ前缀>000; 进入服务模式；会立即软复位。",
    "读取选中": "读取当前 PE 选择子的原始回包。",
    "写入选中": "按 PE<选择子><符号><2hex> 写 PE；会改校准。",
    "读取 N/S (0344/0346)": "只读 FRAM[0x0344–0x0345] 的 16 位大端服务菜单项目号 N 和 FRAM[0x0346] 的子码 S。",
    "SPN 读当前 N": "发送 SPN FF FF，读取 RAM 0xFFAB00 当前项目号；需服务模式门控。",
    "SPN 写": "快捷发送 SPN N；会切换波段/模式，需要服务模式。",
    "SPSDR 读": "发送 SPSDR，读取 9 字节 SDR 数据帧。",
    # 校准 / PE
    "读取设置表(371B)": "用 SPr 遍历 0x000A-0x017C 读取完整 371 字节设置表（不依赖门控，最稳妥）。只读。",
    "写入设置表(危险)": "把当前 371 字节逐字节用 SPw 写回；可回读校验。破坏性写操作。",
    "从备份文件载入…": "从 JSON 备份或裸 .bin 载入设置表到文本区。",
    "保存为 .bin…": "把当前设置表保存为裸 .bin（371 字节）。",
    "写回后校验": "写回设置表后自动回读逐字节比对，不一致则报错。",
    "显示十六进制": "开启时按“地址: 16 字节 hex”分行显示。",
    "读取全部 PE": "枚举 PE 的 27 组选择子逐个读取并列出回包。只读。",
    # 备份
    "① 开始完全备份 → JSON": "按勾选项采集数据并写入 JSON 备份文件。",
    "② 载入备份并预览": "打开 JSON 备份、校验并预览；含设置表时自动载入校准页。",
    "③ 恢复设置表(危险)": "从已载入备份取设置表逐字节 SPw 写回并校验。破坏性。",
    "④ 恢复外部 FRAM(极危险)": "从已载入备份取外部 FRAM 数据逐 2 字节 SPW 写回（两次确认）。可能导致无法开机。",
    # 终端
    "构造→原始框": "按 body 计算校验并补 ; 生成完整 SP 帧，填入“原始字节”框。",
    "纯读": "不发送数据，仅读取指定字节数（观察设备自发帧）。",
    "清空记录": "清空终端页显示记录。",
}

BACKUP_ITEM_TIPS = {
    "ve": "采集版本 / ID 串。",
    "fd": "采集 FD000/001/002 三个通道。",
    "kt": "采集 CW 文本缓冲状态。",
    "pe": "采集全部 27 组 PE 读数。",
    "table": "用 SPr 采集 371 字节设置表（备份/恢复核心）。",
    "spard": "采集 SPARD 378 字节整帧（需 0xFF2020 bit7）。",
    "e0": "采集 229 字节诊断帧。",
    "e8": "采集 10573 字节大转储。",
    "jp": "采集 25 字节 JP 参数。",
    "mem": "用 SPR 逐 2 字节采集外部 FRAM 地址空间（耗时，可缩小范围）。",
}


class ToolTip:
    """轻量级悬浮提示。"""

    def __init__(self, widget, text, delay=450):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tip = None
        self._id = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        try:
            self._id = self.widget.after(self.delay, self._show)
        except Exception:
            self._id = None

    def _cancel(self):
        if self._id is not None:
            try:
                self.widget.after_cancel(self._id)
            except Exception:
                pass
            self._id = None

    def _show(self):
        if self.tip is not None:
            return
        try:
            x = self.widget.winfo_rootx() + 18
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
            tw = __import__("tkinter").Toplevel(self.widget)
        except Exception:
            return
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        lbl = __import__("tkinter").Label(
            tw, text=self.text, justify="left", background="#ffffe0",
            relief="solid", borderwidth=1, font=("", 9))
        lbl.pack()
        self.tip = tw

    def _hide(self, _event=None):
        self._cancel()
        if self.tip is not None:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


# ============================================================ 串口链路
class SerialLink:
    def __init__(self):
        self.ser = None
        self.lock = threading.RLock()
        self.port = None
        self.baud = 38400

    @property
    def is_open(self) -> bool:
        return self.ser is not None and getattr(self.ser, "is_open", False)

    def open(self, port: str, baud: int = 38400):
        if serial is None:
            raise CatError("未安装 pyserial，请先 `pip install pyserial`")
        self.close()
        self.ser = serial.Serial(port=port, baudrate=baud, bytesize=8,
                                 parity="N", stopbits=1, timeout=0.05)
        self.port, self.baud = port, baud

    def close(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def write(self, data: bytes):
        if not self.is_open:
            raise CatNotOpen("串口未打开")
        with self.lock:
            self.ser.reset_input_buffer()
            self.ser.write(data)
            self.ser.flush()

    def _read_some(self, n: int) -> bytes:
        try:
            return self.ser.read(n)
        except Exception:
            return b""

    def read_exact(self, n: int, timeout: float) -> bytes:
        if not self.is_open:
            raise CatNotOpen("串口未打开")
        end = time.time() + timeout
        buf = bytearray()
        while len(buf) < n and time.time() < end:
            c = self._read_some(n - len(buf))
            if c:
                buf += c
        if len(buf) < n:
            raise CatTimeout(f"期望 {n} 字节，仅收到 {len(buf)}；已收: {hexs(bytes(buf))}")
        return bytes(buf)

    def read_until(self, term: bytes, timeout: float) -> bytes:
        end = time.time() + timeout
        buf = bytearray()
        while time.time() < end:
            c = self._read_some(1)
            if c:
                buf += c
                if c == term:
                    break
        return bytes(buf)

    def drain(self, seconds: float) -> bytes:
        end = time.time() + seconds
        buf = bytearray()
        while time.time() < end:
            c = self._read_some(256)
            if c:
                buf += c
        return bytes(buf)


# ============================================================ 协议封装
class HiddenCat:
    def __init__(self, link: SerialLink, log=None):
        self.link = link
        self.log = log or (lambda *a, **k: None)
        # 可由“机型 / 魔术串”页覆盖；默认值与 FTDX10 静态逆向一致。
        self.ve_magic = "RAH076H"
        self.jp_prefix = "JP0010"
        self.zz_prefix = "PE0010"

    def set_magic(self, ve_magic: str | None = None,
                  jp_prefix: str | None = None,
                  zz_prefix: str | None = None) -> None:
        """设置 VE / JP / ZZ 魔术串；长度/字符不合法时抛 ValueError。"""
        if ve_magic is not None:
            s = ve_magic.strip().upper()
            # 允许用户直接粘贴完整命令 VERAH076H / VERAH065H。
            if len(s) == 9 and s.startswith("VE"):
                s = s[2:]
            if len(s) != 7 or not s.isascii() or ";" in s:
                raise ValueError("VE 魔术串必须是 7 个 ASCII 字符（如 RAH076H / RAH065H；也可粘贴 VERAH...）")
            self.ve_magic = s
        if jp_prefix is not None:
            p = jp_prefix.strip().upper()
            if len(p) != 6 or not p.isascii() or ";" in p:
                raise ValueError("JP 前缀必须是 6 个 ASCII 字符（FTDX10: JP0010；FT-891: JP0891）")
            self.jp_prefix = p
        if zz_prefix is not None:
            z = zz_prefix.strip().upper()
            # 允许直接粘贴完整 ZZPE0010 / ZZPE0891，内部只保存 6 字符前缀。
            if len(z) == 8 and z.startswith("ZZ"):
                z = z[2:]
            if len(z) != 6 or not z.isascii() or ";" in z:
                raise ValueError("ZZ 前缀必须是 6 个 ASCII 字符（FTDX10: PE0010；FT-891: PE0891；也可粘贴 ZZPE...）")
            self.zz_prefix = z

    # ---- 底层 ----
    def _t_for(self, nbytes: int, base: float = 0.6) -> float:
        baud = self.link.baud or 38400
        return base + nbytes * 10.0 / baud

    def _ascii(self, cmd: str, expect=None, timeout=None):
        """expect=None→读到 ';'；expect=-1→读固定窗口；expect=int→读恰好 n 字节。"""
        self.link.write(cmd.encode("ascii") + b";")
        if expect == -1:
            return self.link.drain(timeout or 0.25)
        if expect is None:
            return self.link.read_until(b";", timeout or self._t_for(32, 0.5))
        return self.link.read_exact(expect, timeout or self._t_for(expect, 0.6))

    def _sp(self, body: bytes, expect, timeout=None) -> bytes:
        self.link.write(sp_frame(body))
        if expect == -1:
            return self.link.drain(timeout or 0.25)
        if expect is None:
            return self.link.read_until(b";", timeout or self._t_for(32, 0.5))
        return self.link.read_exact(expect, timeout or self._t_for(expect, 0.6))

    @staticmethod
    def _ok(r: bytes) -> bool:
        return r[:2] == b"A;"

    @staticmethod
    def _err(r: bytes) -> bool:
        return r[:2] == b"?;"

    # ---- ID / VE ----
    def id_read(self) -> bytes:
        """标准 CAT ID 查询：ID; 回 7 字节，例如 ID0761;。"""
        return self._ascii("ID", expect=7)

    def ve(self) -> bytes:
        """VE 版本查询：命令 = VE + 可编辑魔术串（默认 RAH076H），回 48 字节。"""
        r = self._ascii("VE" + self.ve_magic, expect=48,
                        timeout=self._t_for(48, 1.0))
        return r

    # ---- FD ----
    def fd_read(self, x: int) -> bytes:
        if x not in (0, 1, 2):
            raise ValueError("FD 通道必须为 0/1/2")
        return self._ascii(f"FD00{x}", expect=15)

    def fd_write(self, hex8: str) -> bytes:
        h = hex8.strip().upper()
        if len(h) != 8 or any(c not in "0123456789ABCDEF" for c in h):
            raise ValueError("FD 写需要 8 个十六进制字符")
        return self._ascii("FD0020" + h, expect=-1)

    # ---- KT ----
    def kt_read(self) -> bytes:
        return self._ascii("KT", expect=6)

    def kt_clear(self) -> bytes:
        return self._ascii("KT0", expect=-1)

    def kt_remaining(self) -> int:
        """读 KT 回包的“剩余空间”字段（2 位十六进制）。"""
        r = self.kt_read()
        if r[:2] != b"KT" or len(r) < 6:
            raise CatError(f"KT 回包异常: {hexs(r)}")
        try:
            return int(r[3:5].decode("ascii"), 16)
        except ValueError as exc:
            raise CatError(f"KT 剩余空间解析失败: {hexs(r)}") from exc

    def kt_write(self, text: str) -> bytes:
        if not text:
            raise ValueError("文本为空")
        if any(ord(ch) > 0x7F for ch in text):
            raise ValueError("KT 文本只接受 ASCII 字符")
        if ";" in text:
            raise ValueError("KT 文本不能包含 ';'（0x3B 会被当作帧尾）")
        text = text.upper()  # CAT 解析器默认把字母大写化
        if len(text) > 30:
            raise ValueError("文本超过 30 字符上限")
        for ch in text:
            code = ord(ch)
            if not (0x20 <= code <= 0x5F or code == 0x7D):
                raise ValueError(
                    f"含非法字符 {ch!r}（固件允许 0x20–0x5F 或 0x7D；字母会转大写）")
        remaining = self.kt_remaining()
        if len(text) > remaining:
            raise ValueError(
                f"文本 {len(text)} 字符，超过缓冲剩余 {remaining} 字符")
        return self._ascii("KT1" + text, expect=-1)

    # ---- EK ----
    def ek(self) -> bytes:
        return self._ascii("EK", expect=-1)

    # ---- EO ----
    def eo(self, a: int, sign: str, b: int, ddd: int) -> bytes:
        if a not in (0, 1, 2) or b not in (0, 1, 2):
            raise ValueError("a/b 必须为 0/1/2")
        if sign not in "+-":
            raise ValueError("符号必须为 + 或 -")
        if not (0 <= ddd <= 999):
            raise ValueError("数值得在 000–999")
        return self._ascii(f"EO{a}{sign}{b}{ddd:03d}", expect=-1)

    # ---- PE ----
    def pe_read(self, s0: int, s1: int, m: int):
        r = self._ascii(f"PE{s0}{s1}{m}", expect=9)
        if r[:2] == b"?;":
            return None
        return r

    def pe_write_raw(self, params: str) -> bytes:
        """直接发送 'PE'+params（params 为 6 个字符，语义见分析文档）。"""
        p = params.strip().upper()
        # 语法：PE<s0><s1><m><sym><2 hex>，选择子 0/1/2，符号 +/-。
        if (len(p) != 6 or any(c not in "012" for c in p[:3])
                or p[3] not in "+-" or any(c not in "0123456789ABCDEF" for c in p[4:])):
            raise ValueError("PE 写需要 6 字符：3 位 0/1/2 选择子 + 符号(+/-) + 2 位十六进制")
        return self._ascii("PE" + p, expect=-1)

    # ---- E0 / E8 ----
    def e0(self) -> bytes:
        return self._ascii("E0", expect=229, timeout=self._t_for(229, 1.5))

    def e8(self) -> bytes:
        return self._ascii("E8", expect=10573, timeout=self._t_for(10573, 3.0))

    # ---- JP ----
    def jp_read(self) -> bytes:
        """JP 读取：JP 前缀 + 空参数，回 25 字节配置/参数字。"""
        return self._ascii(self.jp_prefix, expect=25)

    def jp_write(self, hex8: str) -> bytes:
        """JP 写入：JP 前缀 + 8 hex，两个 16 位字必须互补（v ^ w == 0xFFFF）。"""
        h = hex8.strip().upper()
        if len(h) != 8 or any(c not in "0123456789ABCDEF" for c in h):
            raise ValueError("JP 写需要 8 个十六进制字符")
        v = int(h[:4], 16)
        w = int(h[4:], 16)
        if (v ^ w) != 0xFFFF:
            raise ValueError(f"两字必须互补：0x{v:04X} ^ 0x{w:04X} != 0xFFFF")
        return self._ascii(self.jp_prefix + h, expect=-1)

    # ---- ZZ ----
    def zz(self, value: int = 0) -> None:
        if value not in (0, 1):
            raise ValueError("ZZ 值必须为 0 或 1")
        self.link.write(f"ZZ{self.zz_prefix}{value:02X}".encode() + b";")
        time.sleep(0.3)

    # ---- SP 子协议 ----
    def spw(self, addr: int, d0: int, d1: int) -> bytes:
        if not (0 <= addr < 0x8000):
            raise ValueError("地址须 < 0x8000")
        return self._sp(bytes([0x57, addr >> 8, addr & 0xFF, d0 & 0xFF, d1 & 0xFF]), 2)

    def spr(self, addr: int) -> bytes:
        if not (0 <= addr < 0x8000):
            raise ValueError("地址须 < 0x8000")
        r = self._sp(bytes([0x52, addr >> 8, addr & 0xFF]), 9)
        if r[:3] != b"SPR":
            raise CatError(f"SPR 应答异常: {hexs(r)}")
        return r

    def spw_table(self, addr: int, data: int, pad: int = 0x00) -> bytes:
        if not (TABLE_START <= addr <= TABLE_END):
            raise ValueError(f"表地址须在 0x{TABLE_START:04X}–0x{TABLE_END:04X}")
        return self._sp(bytes([0x77, addr >> 8, addr & 0xFF, pad & 0xFF, data & 0xFF]), 2)

    def spr_table(self, addr: int) -> bytes:
        if addr != 0xFFFF and not (TABLE_START <= addr <= TABLE_END):
            raise ValueError(f"表地址须在 0x{TABLE_START:04X}–0x{TABLE_END:04X} 或 0xFFFF")
        r = self._sp(bytes([0x72, addr >> 8, addr & 0xFF]), 9)
        if r[:3] != b"SPr":
            raise CatError(f"SPr 应答异常: {hexs(r)}")
        return r

    def spm(self, which: str) -> bytes:
        w = which.strip().upper()
        if w == "TR":
            return self._sp(b"MTR", 14)
        if w == "T2" or w == "2":
            return self._sp(b"MT2", 16)
        raise ValueError("SPM 参数为 TR 或 T2")

    def spn_read(self) -> bytes:
        return self._sp(b"N\xff\xff", 7)

    def spn_write(self, value: int) -> bytes:
        if not (0 <= value < 0x126):
            raise ValueError("SPN 写值须 < 0x126")
        return self._sp(bytes([0x4E, value >> 8, value & 0xFF]), 2)

    def spa_we(self) -> bytes:
        return self._sp(b"AWE", 2)

    def spa_rd(self) -> bytes:
        # 378 字节：5 回显 + 371 表 + 校验 + ';'
        return self._sp(b"ARD", 378, timeout=self._t_for(378, 1.2))

    def spa_cl(self) -> bytes:
        return self._sp(b"ACL", 2)

    def spa_et(self) -> bytes:
        return self._sp(b"AET", 2)

    def spsdr(self, b0: int, b1: int) -> bytes:
        """SPSDR：读取 SDR 相关的 9 字节数据帧。"""
        return self._sp(bytes([0x53, 0x44, 0x52, b0 & 0xFF, b1 & 0xFF]), 9)

    # ---- 配置字节 / FRAM 批量 ----
    def read_config_pair(self) -> tuple[int, int, bytes]:
        """读取 FRAM[0x0006..0x0007] 配置对，返回 (b0, b1, 原始回包)。"""
        r = self.spr(CONFIG_ADDR)
        if r[:3] != b"SPR" or len(r) < 9:
            raise CatError(f"SPR 配置读取异常: {hexs(r)}")
        return r[5], r[6], r

    def write_config_pair(self, b0: int, b1: int) -> bytes:
        """写回配置对；若帧中含 0x3B，自动退到 0x0007+0x0008 并保留 FRAM[8]。"""
        if not (0 <= b0 <= 0xFF and 0 <= b1 <= 0xFF):
            raise ValueError("配置字节必须在 0x00–0xFF")
        try:
            return self.spw(CONFIG_ADDR, b0, b1)
        except CatError as exc:
            if "0x3B" not in str(exc):
                raise
            old = self.spr(CONFIG_ADDR + 1)
            return self.spw(CONFIG_ADDR + 1, b1, old[6])

    def write_fram(self, start: int, data: bytes, progress=None,
                   cancel=None, verify=False) -> int:
        """把 bytes 逐 2 字节写入外部 FRAM；奇数长度时保留最后一字节的原值。"""
        if not (0 <= start < 0x8000):
            raise ValueError("FRAM 起始地址必须 < 0x8000")
        if start + len(data) > 0x8000:
            raise ValueError("写入范围超过 0x7FFF")
        if not data:
            return 0
        if len(data) % 2 and start + len(data) - 1 == FRAM_END:
            raise ValueError("奇数长度且末尾为 0x7FFF：无法在不越界的情况下成对写入")
        total = (len(data) + 1) // 2
        for k in range(total):
            if cancel is not None and cancel.is_set():
                raise CatError("已取消")
            i = k * 2
            d0 = data[i]
            d1 = data[i + 1] if i + 1 < len(data) else self.spr(start + i + 1)[6]
            self.spw(start + i, d0, d1)
            if progress and (k % 32 == 0 or k == total - 1):
                progress(k + 1, total)
        if verify:
            back = self.read_fram(start, start + len(data) - 1, strict=True)
            if back != data:
                raise CatError("FRAM 写后回读不一致")
        return len(data)

    def spa_table(self) -> bytes:
        """SPARD 整表读取：从 378 字节回包中取出 371 字节设置表。"""
        r = self.spa_rd()
        if len(r) < 5 + TABLE_LEN + 2:
            raise CatError(f"SPARD 回包过短: {len(r)}")
        table = r[5:5 + TABLE_LEN]
        # 回包结构：SPARD + 371B + 校验 + ';'；最后两字节的倒数第二字节是校验。
        chk = sum(r[:5 + TABLE_LEN]) & 0xFF
        if r[5 + TABLE_LEN] != chk:
            raise CatError(
                f"SPARD 校验失败: 期望 {chk:02X}，收到 {r[5 + TABLE_LEN]:02X}")
        return table

    @staticmethod
    def parse_jp(r: bytes) -> dict:
        """解析 JP 25 字节回包，返回可读字段（失败时尽量保留原始 hex）。"""
        out = {"raw_hex": r.hex(), "length": len(r)}
        if len(r) >= 25:
            # JP 回包是 ASCII：JP0010 + 8 hex + 8 hex + 2 hex + ';'
            out["head"] = r[:6].decode("latin-1", "replace")
            vh = r[6:10].decode("ascii", "replace").upper()
            wh = r[10:14].decode("ascii", "replace").upper()
            out["v_hex"], out["w_hex"] = vh, wh
            try:
                out["v"] = int(vh, 16)
                out["w"] = int(wh, 16)
                out["complement"] = ((out["v"] ^ out["w"]) == 0xFFFF)
            except ValueError:
                out["complement"] = None
            out["extra_hex"] = r[14:24].decode("ascii", "replace").upper()
        return out

    # ---- 组合读取 ----
    def read_fram(self, start: int, end: int, progress=None, cancel=None,
                  quiet=True, strict=False):
        """用 SPR 读 [start,end]（含）的外部 FRAM 地址空间，返回 bytes。

        strict=True 时任何一帧读失败都会抛错；备份/编辑器应使用严格模式，
        避免把读失败静默填成 0xFF 后误当成真实数据。
        """
        if not (0 <= start <= end <= FRAM_END):
            raise ValueError(f"FRAM 读取范围须满足 0 <= 起 <= 止 <= 0x{FRAM_END:04X}")
        buf = bytearray()
        total = (end - start + 1 + 1) // 2
        n = 0
        for addr in range(start, end + 1, 2):
            if cancel is not None and cancel.is_set():
                raise CatError("已取消")
            try:
                r = self.spr(addr)
                buf += r[5:7]
            except CatError:
                if strict:
                    raise
                buf += b"\xff\xff"          # 非严格模式：读失败以 0xFF 填充
            n += 1
            if progress and (n % 32 == 0 or n == total):
                progress(n, total)
        return bytes(buf[: end - start + 1])

    def read_settings_table(self, progress=None, cancel=None) -> bytes:
        buf = bytearray()
        total = TABLE_LEN
        for i, addr in enumerate(range(TABLE_START, TABLE_END + 1)):
            if cancel is not None and cancel.is_set():
                raise CatError("已取消")
            r = self.spr_table(addr)
            buf.append(r[6])
            if progress and (i % 16 == 0 or i == total - 1):
                progress(i + 1, total)
        return bytes(buf)


# ============================================================ 备份 / 恢复
def _iso_now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def make_backup(cat: HiddenCat, opts: dict, progress=None, cancel=None) -> dict:
    """按 opts 采集数据并返回备份字典。opts 键见 GUI。"""
    data = {}
    steps = []
    if opts.get("ve"):
        steps.append("ve")
    if opts.get("fd"):
        steps.append("fd")
    if opts.get("kt"):
        steps.append("kt")
    if opts.get("pe"):
        steps.append("pe")
    if opts.get("table"):
        steps.append("table")
    if opts.get("e0"):
        steps.append("e0")
    if opts.get("e8"):
        steps.append("e8")
    if opts.get("jp"):
        steps.append("jp")
    if opts.get("mem"):
        steps.append("mem")
    if opts.get("spard"):
        steps.append("spard")

    total = len(steps) or 1
    for si, s in enumerate(steps):
        if cancel is not None and cancel.is_set():
            raise CatError("已取消")
        if progress:
            progress(si, total, f"采集 {s} …")

        if s == "ve":
            try:
                r = cat.ve()
                data["ve"] = {"raw_hex": r.hex(),
                              "text": r.decode("latin-1")}
            except CatError as e:
                data["ve"] = {"error": str(e)}

        elif s == "fd":
            fd = {}
            for x in (0, 1, 2):
                try:
                    r = cat.fd_read(x)
                    fd[str(x)] = {"raw_hex": r.hex()}
                except CatError as e:
                    fd[str(x)] = {"error": str(e)}
            data["fd"] = fd

        elif s == "kt":
            try:
                data["kt"] = {"raw_hex": cat.kt_read().hex()}
            except CatError as e:
                data["kt"] = {"error": str(e)}

        elif s == "pe":
            pe = {}
            for s0 in PE_SEL:
                for s1 in PE_SEL:
                    for m in PE_SEL:
                        key = f"{s0}{s1}{m}"
                        try:
                            r = cat.pe_read(int(s0), int(s1), int(m))
                            pe[key] = {"raw_hex": r.hex()} if r else {"ok": False}
                        except CatError as e:
                            pe[key] = {"error": str(e)}
            data["pe"] = pe

        elif s == "table":
            try:
                tb = cat.read_settings_table(
                    progress=(lambda a, b: progress(si, total, f"设置表 {a}/{b}", a, b)
                              if progress else None),
                    cancel=cancel)
                data["settings_table"] = {
                    "base": f"0x{SETTINGS_TABLE_BASE:04X}",
                    "start_addr": f"0x{TABLE_START:04X}",
                    "end_addr": f"0x{TABLE_END:04X}",
                    "bytes": len(tb),
                    "hex": tb.hex(),
                }
            except CatError as e:
                data["settings_table"] = {"error": str(e)}

        elif s == "spard":
            try:
                r = cat.spa_rd()
                data["spard"] = {"raw_hex": r.hex(), "len": len(r)}
            except CatError as e:
                data["spard"] = {"error": str(e)}

        elif s == "e0":
            try:
                r = cat.e0()
                data["e0"] = {"len": len(r), "hex": r.hex()}
            except CatError as e:
                data["e0"] = {"error": str(e)}

        elif s == "e8":
            try:
                r = cat.e8()
                data["e8"] = {"len": len(r), "hex": r.hex()}
            except CatError as e:
                data["e8"] = {"error": str(e)}

        elif s == "jp":
            try:
                data["jp"] = {"raw_hex": cat.jp_read().hex()}
            except CatError as e:
                data["jp"] = {"error": str(e)}

        elif s == "mem":
            start = opts.get("mem_start", FRAM_START)
            end = opts.get("mem_end", FRAM_END)
            try:
                mb = cat.read_fram(
                    start, end, cancel=cancel, strict=True,
                    progress=(lambda a, b: progress(si, total, f"外部 FRAM {a}/{b}", a, b)
                              if progress else None))
                data["mem"] = {
                    "space": "external_fram",
                    "start": f"0x{start:04X}", "end": f"0x{end:04X}",
                    "bytes": len(mb), "hex": mb.hex(),
                }
            except Exception as e:
                data["mem"] = {"error": str(e)}

    return {
        "format": BACKUP_FORMAT,
        "format_version": BACKUP_VERSION,
        "created": _iso_now(),
        "transport": {"port": cat.link.port, "baud": cat.link.baud},
        "data": data,
    }


def restore_table(cat: HiddenCat, table: bytes, progress=None, cancel=None,
                  verify=True) -> int:
    """把 371 字节设置表逐字节写回(SPw)，返回写入字节数。"""
    if len(table) != TABLE_LEN:
        raise ValueError(f"设置表长度必须为 {TABLE_LEN} 字节，收到 {len(table)}")
    total = len(table)
    for i, addr in enumerate(range(TABLE_START, TABLE_START + total)):
        if cancel is not None and cancel.is_set():
            raise CatError("已取消")
        cat.spw_table(addr, table[i])
        if progress and (i % 8 == 0 or i == total - 1):
            progress(i + 1, total)
    if verify:
        back = bytearray()
        for addr in range(TABLE_START, TABLE_START + total):
            back.append(cat.spr_table(addr)[6])
        if bytes(back) != table:
            bad = [i for i in range(total) if back[i] != table[i]]
            raise CatError(f"写回校验失败，{len(bad)} 处不一致，例如偏移 {bad[:8]}")
    return total


def restore_fram(cat: HiddenCat, start: int, mem: bytes, progress=None, cancel=None) -> int:
    """把外部 FRAM 数据逐 2 字节写回(SPW)，返回写入字节数。"""
    if not (0 <= start < 0x8000 and start + len(mem) <= 0x8000):
        raise ValueError("恢复范围必须在 0x0000–0x7FFF 内")
    if len(mem) % 2 and start + len(mem) - 1 == FRAM_END:
        raise ValueError("奇数长度且末尾为 0x7FFF：无法成对恢复")
    total = (len(mem) + 1) // 2
    for k in range(total):
        if cancel is not None and cancel.is_set():
            raise CatError("已取消")
        i = k * 2
        d0 = mem[i]
        # 奇数长度时保留 addr+1 原值，避免把相邻字节写成 0xFF。
        d1 = mem[i + 1] if i + 1 < len(mem) else cat.spr(start + i + 1)[6]
        cat.spw(start + i, d0, d1)
        if progress and (k % 32 == 0 or k == total - 1):
            progress(k + 1, total)
    return len(mem)


# ============================================================ GUI
def launch_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(APP_TITLE)
            self.geometry("1120x780")
            self.minsize(940, 640)

            self.link = SerialLink()
            self.cat = HiddenCat(self.link)
            self.logq = queue.Queue()
            self.uiq = queue.Queue()   # 后台线程 → 主线程的 UI 回调队列
            self.busy = False
            self.cancel = threading.Event()
            self.last_backup = None

            # 外部数据：机型魔术串、FRAM 区域中文名、294 项服务菜单。
            self.profile_data = load_rig_profiles()
            self.fram_regions = load_fram_regions()
            self.service_items = load_service_items()
            self.current_profile_id = (self.profile_data["profiles"][0].get("id")
                                       if self.profile_data.get("profiles") else "ftdx10")
            self.cal_table = b""       # 371B 设置表缓存（服务菜单 / 校准页共用）
            self.cfg_pair = None       # FRAM[0x0006..0x0007] 最近一次读取值
            self.var_backup_ack = tk.BooleanVar(value=False)  # 写前“已备份”确认

            self._build_menu()
            self._build()
            self.after(80, self._poll)
            if serial is None:
                self.log("⚠ 未检测到 pyserial，请先 `pip install pyserial`")

        # ---------------- 通用 ----------------
        def ui(self, fn):
            """把 UI 更新排到主线程执行；tkinter 不允许后台线程直接操作控件。"""
            self.uiq.put(fn)

        def _q_append_text(self, widget, text: str, see: bool = True):
            """后台线程安全的文本追加。"""
            def do():
                widget.insert("end", text)
                if see:
                    widget.see("end")
            self.ui(do)

        def log(self, msg: str):
            self.logq.put(("log", msg))

        def status(self, msg: str):
            self.logq.put(("status", msg))

        def _poll(self):
            try:
                while True:
                    kind, msg = self.logq.get_nowait()
                    if kind == "log":
                        self.logbox.configure(state="normal")
                        ts = datetime.datetime.now().strftime("%H:%M:%S")
                        self.logbox.insert("end", f"[{ts}] {msg}\n")
                        self.logbox.see("end")
                        self.logbox.configure(state="disabled")
                    elif kind == "status":
                        self.var_status.set(msg)
                    elif kind == "busy":
                        self.busy = msg
                        self.btn_cancel.configure(state=("normal" if msg else "disabled"))
            except queue.Empty:
                pass
            try:
                while True:
                    fn = self.uiq.get_nowait()
                    try:
                        fn()
                    except Exception as e:
                        self.log(f"UI 回调错误: {e}")
            except queue.Empty:
                pass
            self.after(80, self._poll)

        def _set_busy(self, b, text=""):
            self.logq.put(("busy", b))
            if text:
                self.status(text)

        def bg(self, fn, done_msgs=None):
            """后台线程执行 fn(cat)；fn 内可用 self.log/self.status。"""
            if self.busy:
                messagebox.showinfo(APP_TITLE, "有任务正在执行，请稍候或点击取消。")
                return
            self.cancel.clear()
            self._set_busy(True, "运行中…")

            def runner():
                try:
                    fn(self.cat)
                except Exception as e:  # noqa
                    self.log(f"✗ 错误: {e}")
                finally:
                    self._set_busy(False, "就绪")
            threading.Thread(target=runner, daemon=True).start()

        def require_open(self) -> bool:
            if not self.link.is_open:
                messagebox.showwarning(APP_TITLE, "请先在“连接”页打开串口。")
                return False
            return True

        def on_cancel(self):
            self.cancel.set()
            self.log("… 已请求取消")

        # ---------------- 菜单 ----------------
        def _build_menu(self):
            m = tk.Menu(self)
            hm = tk.Menu(m, tearoff=0)
            hm.add_command(label="使用说明", command=self._help)
            hm.add_command(label="协议/安全提示", command=self._help_safety)
            hm.add_separator()
            hm.add_command(label="退出", command=self.destroy)
            m.add_cascade(label="帮助", menu=hm)
            self.config(menu=m)

        def _help(self):
            messagebox.showinfo(APP_TITLE, __doc__[:1500])

        def _help_safety(self):
            messagebox.showwarning(
                "安全提示",
                "· SP / PE / JP 会写校准或启动参数，可能改变发射指标或导致无法开机。\n"
                "· ZZ 会立即复位整机。\n"
                "· 写操作前请先备份 FRAM[0x0000–0x0490]，并确保能重刷固件。\n"
                "· SP 是二进制子协议：参数/校验为原始字节，且不能含 0x3B(';')。\n"
                "· 进入服务模式用 ZZ<ZZ前缀>000;（FTDX10=ZZPE001000;，FT-891=ZZPE089100;）；\n"
                "  退出用 RESET 菜单或整机断电。ZZ<前缀>01（FTDX10: ZZPE001001;）是 ALL RESET，不是普通退出。\n"
                "· 本工具为静态逆向结论，未在真机验证；风险自负。")

        # ---------------- 布局 ----------------
        def _build(self):
            top = ttk.Frame(self, padding=(6, 4))
            top.pack(fill="x")
            self.var_status = tk.StringVar(value="就绪")
            ttk.Label(top, textvariable=self.var_status, foreground="#054").pack(side="left")
            self.btn_cancel = ttk.Button(top, text="取消任务", command=self.on_cancel,
                                         state="disabled")
            self.btn_cancel.pack(side="right")

            self.nb = ttk.Notebook(self)
            self.nb.pack(fill="both", expand=True, padx=6, pady=(0, 4))
            self._tab_connect()
            self._tab_profile()
            self._tab_quick()
            self._tab_power()
            self._tab_reg()
            self._tab_fram_full()
            self._tab_service()
            self._tab_cal()
            self._tab_pe()
            self._tab_backup()
            self._tab_term()

            lf = ttk.LabelFrame(self, text="日志")
            lf.pack(fill="both", padx=6, pady=(0, 6))
            self.logbox = scrolledtext.ScrolledText(lf, height=10, state="disabled",
                                                    font=("monospace", 9))
            self.logbox.pack(fill="both", expand=True)
            self._attach_tooltips()

        def _attach_tooltips(self):
            """给所有按钮/复选框绑定中文悬浮说明（重复文字单独绑定）。"""
            def walk(w):
                for c in w.winfo_children():
                    yield c
                    yield from walk(c)

            for w in walk(self):
                try:
                    txt = w.cget("text")
                except Exception:
                    continue
                if txt in BUTTON_TIPS:
                    ToolTip(w, BUTTON_TIPS[txt])
            for attr, tip in (
                ("btn_pe_send", "发送 PE<6字符>; 写 PE（有确认框）。写操作，改校准。"),
                ("btn_term_ascii", "发送 ASCII 命令（自动补 ';'）并显示回包。"),
                ("btn_term_hex", "按原始十六进制字节发送；发送 SP 帧请取消“自动补 ';'”。"),
            ):
                w = getattr(self, attr, None)
                if w is not None:
                    ToolTip(w, tip)

        def _mktext(self, parent, height=12):
            t = scrolledtext.ScrolledText(parent, height=height, font=("monospace", 9))
            return t

        # ---------------- Tab: 连接 ----------------
        def _tab_connect(self):
            f = ttk.Frame(self.nb, padding=10)
            self.nb.add(f, text="连接")
            row = ttk.Frame(f)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text="端口:").pack(side="left")
            self.cmb_port = ttk.Combobox(row, width=28)
            self.cmb_port.pack(side="left", padx=4)
            ttk.Button(row, text="刷新", command=self.refresh_ports).pack(side="left")
            ttk.Label(row, text="波特率:").pack(side="left", padx=(12, 2))
            self.cmb_baud = ttk.Combobox(row, width=10, values=("4800", "9600", "19200", "38400"),
                                         state="readonly")
            self.cmb_baud.set("38400")
            self.cmb_baud.pack(side="left")
            self.btn_open = ttk.Button(row, text="打开", command=self.toggle_open)
            self.btn_open.pack(side="left", padx=10)

            info = ttk.Label(f, foreground="#555", justify="left", text=(
                "说明：CAT 用 USB 虚拟串口的 Enhanced COM Port，或后板 RS-232C；"
                "8N1 无流控。\n"
                "菜单 08 CAT RATE 默认 9600；做 E8(10573B)/内存转储建议 38400。\n"
                "帧以 ';' 结束；设备最多缓存 30 帧。"))
            info.pack(anchor="w", pady=8)
            self.refresh_ports()

        def refresh_ports(self):
            if list_ports is None:
                self.cmb_port["values"] = ()
                return
            ports = [p.device for p in list_ports.comports()]
            self.cmb_port["values"] = ports
            if ports and not self.cmb_port.get():
                self.cmb_port.set(ports[0])

        def toggle_open(self):
            if self.link.is_open:
                self.link.close()
                self.btn_open.configure(text="打开")
                self.status("已关闭")
                self.log("串口已关闭")
            else:
                port = self.cmb_port.get().strip()
                if not port:
                    messagebox.showwarning(APP_TITLE, "请选择端口")
                    return
                try:
                    self.link.open(port, int(self.cmb_baud.get()))
                    self.btn_open.configure(text="关闭")
                    self.status(f"已打开 {port} @ {self.link.baud}")
                    self.log(f"串口已打开: {port} @ {self.link.baud} 8N1")
                except Exception as e:
                    messagebox.showerror(APP_TITLE, f"打开失败: {e}")

        # ---------------- Tab: 快捷命令 ----------------
        def _tab_quick(self):
            f = ttk.Frame(self.nb, padding=10)
            self.nb.add(f, text="快捷命令")
            grid = ttk.Frame(f)
            grid.pack(fill="x")

            def btn(text, cmd, row, col, width=22):
                b = ttk.Button(grid, text=text, command=cmd, width=width)
                b.grid(row=row, column=col, padx=4, pady=3, sticky="w")
                return b

            # VE / ID
            btn("VE 版本查询 (魔术串)", self.do_ve, 0, 0)
            btn("ID 查询", self.do_id_read, 0, 4, 10)
            # FD
            ttk.Label(grid, text="FD 通道:").grid(row=0, column=1, sticky="e")
            self.cmb_fd = ttk.Combobox(grid, width=4, values=("0", "1", "2"), state="readonly")
            self.cmb_fd.set("0")
            self.cmb_fd.grid(row=0, column=2, sticky="w")
            btn("FD 读", self.do_fd_read, 0, 3, 10)
            self.ent_fd = ttk.Entry(grid, width=12)
            self.ent_fd.grid(row=1, column=1, columnspan=2, sticky="w")
            btn("FD 写 (8 hex)", self.do_fd_write, 1, 3, 12)
            # KT
            btn("KT 读余量", self.do_kt_read, 2, 0, 22)
            self.ent_kt = ttk.Entry(grid, width=30)
            self.ent_kt.grid(row=3, column=0, columnspan=2, sticky="w")
            btn("KT 写入文本", self.do_kt_write, 3, 2, 14)
            btn("KT 清空 (KT0)", self.do_kt_clear, 3, 3, 14)
            # EK / EO
            btn("EK 触发", self.do_ek, 2, 2, 14)
            self.ent_eo = ttk.Entry(grid, width=10)
            self.ent_eo.insert(0, "0+0000")
            self.ent_eo.grid(row=4, column=0, sticky="w")
            btn("EO 微调 (a,sign,b,ddd)", self.do_eo, 4, 1, 20)
            ttk.Label(grid, text="如 0+0000").grid(row=4, column=2, sticky="w")
            # JP / ZZ / E0 / E8 / SPARD / SPM / SPN / SPS
            btn("JP 读 (当前前缀)", self.do_jp_read, 5, 0, 22)
            self.ent_jp = ttk.Entry(grid, width=12)
            self.ent_jp.insert(0, "1234EDCB")
            self.ent_jp.grid(row=6, column=0, sticky="w")
            btn("JP 写 (互补 8 hex)", self.do_jp_write, 6, 1, 18)
            btn("E0 取 229B", self.do_e0, 5, 2, 14)
            btn("E8 取 10573B", self.do_e8, 5, 3, 16)
            btn("SPARD 读 371B 表", self.do_spard, 6, 2, 18)
            btn("SPM TR", lambda: self.do_spm("TR"), 7, 0, 10)
            btn("SPM T2", lambda: self.do_spm("T2"), 7, 1, 10)
            btn("SPN 读 0xFFAB00", self.do_spn_read, 7, 2, 18)
            btn("SPAWE 写使能", self.do_spa_we, 8, 0, 14)
            btn("SPACL 清除", self.do_spa_cl, 8, 1, 12)
            btn("SPAET", self.do_spa_et, 8, 2, 10)
            # SPN 写项目号（服务菜单/临时宽频入口）
            ttk.Label(grid, text="SPN 写 N:").grid(row=9, column=0, sticky="e")
            self.ent_spn_write = ttk.Entry(grid, width=8)
            self.ent_spn_write.insert(0, "000")
            self.ent_spn_write.grid(row=9, column=1, sticky="w")
            btn("SPN 写", self.do_spn_write, 9, 2, 8)
            # SPSDR 读取
            ttk.Label(grid, text="SPSDR b0/b1:").grid(row=10, column=0, sticky="e")
            self.ent_spsdr0 = ttk.Entry(grid, width=5); self.ent_spsdr0.insert(0, "00")
            self.ent_spsdr0.grid(row=10, column=1, sticky="w")
            self.ent_spsdr1 = ttk.Entry(grid, width=5); self.ent_spsdr1.insert(0, "00")
            self.ent_spsdr1.grid(row=10, column=2, sticky="w")
            btn("SPSDR 读", self.do_spsdr, 10, 3, 12)
            btn("ZZ 服务模式 (会复位)", self.do_zz, 11, 0, 24)

            self.txt_quick = self._mktext(f, 12)
            self.txt_quick.pack(fill="both", expand=True, pady=(8, 0))

        # --- 快捷命令实现 ---
        def _q_show_quick(self, title, text):
            self._q_append_text(self.txt_quick, f"── {title} ──\n{text}\n")

        def _show_quick(self, title, text):
            self.txt_quick.insert("end", f"── {title} ──\n{text}\n")
            self.txt_quick.see("end")

        def do_ve(self):
            if not self.require_open(): return
            def job(cat):
                r = cat.ve()
                self.log(f"VE: {hexs(r)}")
                self.log(f"VE 文本: {r.decode('latin-1')}")
                self._q_show_quick("VE", f"{hexs(r)}\n{r.decode('latin-1')}")
            self.bg(job)

        def do_fd_read(self):
            if not self.require_open(): return
            x = int(self.cmb_fd.get())
            def job(cat):
                r = cat.fd_read(x)
                self._q_show_quick(f"FD 读 通道{x}", hexs(r))
            self.bg(job)

        def do_fd_write(self):
            if not self.require_open(): return
            h = self.ent_fd.get()
            def job(cat):
                r = cat.fd_write(h)
                self._q_show_quick("FD 写", f"({h}) 回包: {hexs(r) if r else '无'}")
            self.bg(job)

        def do_kt_read(self):
            if not self.require_open(): return
            def job(cat):
                r = cat.kt_read()
                self._q_show_quick("KT 读余量", hexs(r))
            self.bg(job)

        def do_kt_write(self):
            if not self.require_open(): return
            t = self.ent_kt.get()
            def job(cat):
                r = cat.kt_write(t)
                self._q_show_quick("KT 写", f"({t!r}) 回包: {hexs(r) if r else '无'}")
            self.bg(job)

        def do_kt_clear(self):
            if not self.require_open(): return
            def job(cat):
                r = cat.kt_clear()
                self._q_show_quick("KT 清空", f"回包: {hexs(r) if r else '无'}")
            self.bg(job)

        def do_ek(self):
            if not self.require_open(): return
            def job(cat):
                r = cat.ek()
                self._q_show_quick("EK", f"回包: {hexs(r) if r else '无'}")
            self.bg(job)

        def do_eo(self):
            if not self.require_open(): return
            s = self.ent_eo.get().strip()
            if len(s) != 6 or s[0] not in "012" or s[1] not in "+-" \
               or s[2] not in "012" or not s[3:].isdigit():
                messagebox.showwarning(APP_TITLE, "EO 参数形如 0+0123")
                return
            def job(cat):
                r = cat.eo(int(s[0]), s[1], int(s[2]), int(s[3:]))
                self._q_show_quick("EO", f"({s}) 回包: {hexs(r) if r else '无'}")
            self.bg(job)

        def do_jp_read(self):
            """读取 JP 参数（前缀由机型页决定），并在机型页显示解析结果。"""
            if not self.require_open(): return
            def job(cat):
                r = cat.jp_read()
                info = cat.parse_jp(r)
                self._q_show_quick("JP 读", hexs(r))
                self.log(f"JP 读: {hexs(r)}")
                if hasattr(self, "txt_prof_result"):
                    msg = (f"JP 读 ({cat.jp_prefix}) -> {hexs(r)}\n"
                           f"  v={info.get('v_hex', '?')} w={info.get('w_hex', '?')} "
                           f"互补={info.get('complement', '?')} extra={info.get('extra_hex', '?')}\n")
                    self._q_append_text(self.txt_prof_result, msg)
            self.bg(job)

        def do_jp_write(self):
            """写 JP 参数；只接受互补的 8 hex，并提示风险。"""
            if not self.require_open(): return
            h = self.ent_jp.get().strip().upper()
            if not h:
                messagebox.showwarning(
                    APP_TITLE,
                    "JP 写入值为空。FT-891 的 JP 写值尚未解码，请先读取/研究并手动填写 8 个 hex；"
                    "不要凭空猜测写入。")
                return
            if len(h) != 8 or any(c not in "0123456789ABCDEF" for c in h):
                messagebox.showwarning(APP_TITLE, "JP 写入值必须是 8 个十六进制字符（或留空）。")
                return
            v, w = int(h[:4], 16), int(h[4:], 16)
            if (v ^ w) != 0xFFFF:
                messagebox.showwarning(APP_TITLE, f"JP 两字不互补：0x{v:04X} ^ 0x{w:04X} != 0xFFFF")
                return
            if not messagebox.askyesno(
                    APP_TITLE,
                    f"确认写入 JP 启动参数 {h}？\n当前前缀: {self.cat.jp_prefix}\n风险较高，先备份。"):
                return
            def job(cat):
                r = cat.jp_write(h)
                self._q_show_quick("JP 写", f"({h}) 回包: {hexs(r) if r else '无'}")
                self.log(f"JP 写: {h} ({cat.jp_prefix})")
            self.bg(job)

        def do_e0(self):
            if not self.require_open(): return
            def job(cat):
                r = cat.e0()
                self._q_show_quick("E0", f"{len(r)} 字节:\n{hexs(r)}")
            self.bg(job)

        def do_e8(self):
            if not self.require_open(): return
            def job(cat):
                self.status("正在读取 E8(10573B)…")
                r = cat.e8()
                self._q_show_quick("E8", f"{len(r)} 字节(前 256):\n{hexs(r[:256])}")
            self.bg(job)

        def do_spard(self):
            if not self.require_open(): return
            def job(cat):
                r = cat.spa_rd()
                self._q_show_quick("SPARD", f"{len(r)} 字节(前 256):\n{hexs(r[:256])}")
            self.bg(job)

        def do_spm(self, which):
            if not self.require_open(): return
            def job(cat):
                r = cat.spm(which)
                self._q_show_quick(f"SPM {which}", hexs(r))
            self.bg(job)

        def do_spn_read(self):
            if not self.require_open(): return
            def job(cat):
                r = cat.spn_read()
                self._q_show_quick("SPN 读", hexs(r))
            self.bg(job)

        def do_spn_write(self):
            """快捷页 SPN 写项目号；会切换波段/模式，需服务模式。"""
            if not self.require_open(): return
            try:
                n = int(self.ent_spn_write.get())
                if not 0 <= n <= 0x125:
                    raise ValueError
            except Exception:
                messagebox.showwarning(APP_TITLE, "SPN N 必须是 0–293（0x000–0x125）"); return
            if not messagebox.askyesno(
                    APP_TITLE,
                    f"发送 SPN N={n:03d}？会切换当前波段/模式，需要服务模式门控。"):
                return
            def job(cat):
                r = cat.spn_write(n)
                self._q_show_quick("SPN 写", f"N={n:03d} 回包: {hexs(r) if r else '无'}")
            self.bg(job)

        def do_spsdr(self):
            """SPSDR：读取 b0/b1 对应的 9 字节数据帧。"""
            if not self.require_open(): return
            try:
                b0 = int(self.ent_spsdr0.get(), 16)
                b1 = int(self.ent_spsdr1.get(), 16)
                if not (0 <= b0 <= 0xFF and 0 <= b1 <= 0xFF):
                    raise ValueError
            except Exception:
                messagebox.showwarning(APP_TITLE, "SPSDR 的 b0/b1 必须是 0x00–0xFF"); return
            def job(cat):
                r = cat.spsdr(b0, b1)
                self._q_show_quick("SPSDR", f"b0={b0:02X} b1={b1:02X}\n{hexs(r)}")
            self.bg(job)

        def do_spa_we(self):
            if not self.require_open(): return
            def job(cat):
                self._q_show_quick("SPAWE", hexs(cat.spa_we()))
            self.bg(job)

        def do_spa_cl(self):
            if not self.require_open(): return
            if not self._require_backup_ack(): return
            if not messagebox.askyesno(
                    APP_TITLE,
                    "SPACL 会清除设置表（破坏性）。\n"
                    "请确认已备份 FRAM[0x0000–0x0490]。继续？"):
                return
            def job(cat):
                self._q_show_quick("SPACL", hexs(cat.spa_cl()))
            self.bg(job)

        def do_spa_et(self):
            if not self.require_open(): return
            if not messagebox.askyesno(
                    APP_TITLE,
                    "发送 SPAET 会触发设置表应用逻辑（0x1662），可能改变校准行为。继续？"):
                return
            def job(cat):
                self._q_show_quick("SPAET", hexs(cat.spa_et()))
            self.bg(job)

        def do_zz(self):
            """发送 ZZ<前缀>000;：进入服务模式（不是 ALL RESET）。"""
            if not self.require_open(): return
            zz = self.cat.zz_prefix
            if not messagebox.askyesno(
                    APP_TITLE,
                    f"将发送 ZZ{zz}000; 进入服务模式并立即软复位。\n"
                    "这不是 ALL RESET；退出请用 RESET 菜单或断电。继续？"):
                return
            def job(cat):
                cat.zz(0)
                self._q_show_quick("ZZ", f"已发送 ZZ{zz}000;，设备应复位进入服务模式")
            self.bg(job)

        # ---------------- Tab: 功率 / 频段 ----------------
        def _tab_power(self):
            """功率/频段页：PWR 档、TS/EXP、各频段 F/AC、服务模式入口。"""
            f = ttk.Frame(self.nb, padding=10)
            self.nb.add(f, text="功率 / 频段")

            # --- 配置字节 ---
            box = ttk.LabelFrame(f, text="配置字节 FRAM[0x0006..0x0007]（SPR/SPW）")
            box.pack(fill="x", pady=4)
            row = ttk.Frame(box); row.pack(fill="x", padx=6, pady=4)
            ttk.Button(row, text="读取配置", command=self.do_cfg_read).pack(side="left", padx=4)
            self.var_cfg_raw = tk.StringVar(value="未读取")
            ttk.Label(row, textvariable=self.var_cfg_raw, font=("monospace", 10)).pack(side="left", padx=10)
            self.var_cfg_ts = tk.StringVar(value="TS=--")
            self.var_cfg_pwr = tk.StringVar(value="PWR=--")
            self.var_cfg_exp = tk.StringVar(value="EXP=--")
            for v in (self.var_cfg_ts, self.var_cfg_pwr, self.var_cfg_exp):
                ttk.Label(row, textvariable=v, foreground="#054").pack(side="left", padx=8)

            row2 = ttk.Frame(box); row2.pack(fill="x", padx=6, pady=4)
            ttk.Label(row2, text="功率档 PWR:").pack(side="left")
            self.cmb_pwr = ttk.Combobox(
                row2, width=16, state="readonly",
                values=("100 W (0x18)", "50 W (0x00)", "20 W (0x08)", "10 W (0x10)"))
            self.cmb_pwr.set("100 W (0x18)")
            self.cmb_pwr.pack(side="left", padx=4)
            ttk.Button(row2, text="预览功率写入", command=self.do_pwr_preview).pack(side="left", padx=4)
            ttk.Button(row2, text="写入功率档", command=self.do_pwr_write).pack(side="left", padx=4)

            row3 = ttk.Frame(box); row3.pack(fill="x", padx=6, pady=4)
            ttk.Label(row3, text="TS 频段表 (bit0-2):").pack(side="left")
            self.cmb_ts = ttk.Combobox(row3, width=5, state="readonly",
                                       values=tuple(str(i) for i in range(8)))
            self.cmb_ts.set("0")
            self.cmb_ts.pack(side="left", padx=4)
            self.var_exp = tk.BooleanVar(value=False)
            ttk.Checkbutton(row3, text="EXP 扩展频段 (bit5，持久；写完需冷启动；临时宽频见下方)",
                            variable=self.var_exp).pack(side="left", padx=8)
            ttk.Button(row3, text="预览 TS/EXP", command=self.do_ts_preview).pack(side="left", padx=4)
            ttk.Button(row3, text="写入 TS/EXP", command=self.do_ts_write).pack(side="left", padx=4)

            self.var_range = tk.StringVar(value="频段范围: 未读取")
            ttk.Label(box, textvariable=self.var_range, foreground="#555",
                      wraplength=980, justify="left").pack(anchor="w", padx=8, pady=(0, 6))

            # --- 各频段功率 ---
            box2 = ttk.LabelFrame(f, text="各频段 RF POWER(F) / MAX POWER(AC)（FRAM 字节）")
            box2.pack(fill="x", pady=4)
            self.ent_f = {}
            self.ent_ac = {}
            for i, band in enumerate(POWER_BAND_NAMES):
                r = ttk.Frame(box2); r.pack(fill="x", padx=6, pady=2)
                ttk.Label(r, text=f"{band}:").pack(side="left")
                ttk.Label(r, text="F=", foreground="#666").pack(side="left", padx=(8, 0))
                e = ttk.Entry(r, width=6); e.pack(side="left")
                self.ent_f[band] = e
                ttk.Label(r, text="AC=", foreground="#666").pack(side="left", padx=(12, 0))
                e2 = ttk.Entry(r, width=6); e2.pack(side="left")
                self.ent_ac[band] = e2
            r = ttk.Frame(box2); r.pack(fill="x", padx=6, pady=4)
            ttk.Button(r, text="读取 F/AC", command=self.do_power_bands_read).pack(side="left", padx=4)
            ttk.Button(r, text="写入 F/AC", command=self.do_power_bands_write).pack(side="left", padx=4)
            ttk.Label(r, text="F=0x0335..0x0338，AC=0x046A..0x046D；顺序 HF/50M/70M/AM；"
                      "有效功率=min(F,AC)。", foreground="#555").pack(side="left", padx=8)

            # --- 临时宽频 / 服务模式 ---
            box3 = ttk.LabelFrame(f, text="临时宽频 / 服务模式（不写 EXP 位）")
            box3.pack(fill="x", pady=4)
            ttk.Label(box3, foreground="#a00", justify="left", text=(
                "取证：PWR=0xFF2007 bit3-4；TS 选择 0x32B2A 的 8 张表；EXP=0x32B54。\n"
                "ZZ<ZZ前缀>000;（FTDX10=ZZPE001000;，FT-891=ZZPE089100;）会写 0xFF2002=0 并立即软复位，\n"
                "进入服务模式；此模式下频段可到 1.8–75 MHz，但这是“临时”状态，不修改 FRAM[0x0007] 的 EXP 位。\n"
                "退出请用 RESET 菜单或整机断电，绝对不要把 ZZPE001001; 当作普通退出：它是 ALL RESET，\n"
                "会重写 400 字节用户设置。以上地址/效果为 FTDX10；FT-891 的功率/服务表地址不同，尚未适配。"),
                wraplength=1050).pack(anchor="w", padx=8, pady=4)
            ttk.Button(box3, text="进入服务模式 / 临时宽频（会复位）",
                       command=self.do_service_mode).pack(anchor="w", padx=8, pady=(0, 6))

            self.txt_power = self._mktext(f, 8)
            self.txt_power.pack(fill="both", expand=True, pady=(4, 0))

        def _render_cfg(self, b0: int, b1: int, raw: bytes | None = None):
            """刷新配置字节显示、功率/TS/EXP 控件和频段范围说明。"""
            if threading.current_thread() is not threading.main_thread():
                self.ui(lambda: self._render_cfg(b0, b1, raw))
                return
            self.cfg_pair = (b0, b1)
            ts = b1 & TS_MASK
            bits = b1 & PWR_MASK
            exp = bool(b1 & EXP_BIT)
            self.var_cfg_raw.set(f"00 06: {b0:02X} {b1:02X}"
                                 + (f"   {hexs(raw)}" if raw else ""))
            self.var_cfg_ts.set(f"TS={ts}")
            self.var_cfg_pwr.set(f"PWR=0x{bits:02X} ({PWR_WATTS.get(bits, '?')} W)")
            self.var_cfg_exp.set(f"EXP={'1' if exp else '0'}")
            self.cmb_ts.set(str(ts))
            self.var_exp.set(exp)
            for watts, pb in PWR_BITS.items():
                if pb == bits:
                    self.cmb_pwr.set(f"{watts} W (0x{pb:02X})")
                    break
            ranges, why = effective_ranges(ts, exp)
            self.var_range.set(f"频段范围: {fmt_ranges(ranges)} MHz   （{why}；AUX={(b1 & AUX_MASK) >> 6}）")

        def do_cfg_read(self):
            """读取 FRAM[0x0006..0x0007]，解析 PWR/TS/EXP。"""
            if not self.require_open(): return
            def job(cat):
                b0, b1, r = cat.read_config_pair()
                self.log(f"配置读取: FRAM[0006..0007]={b0:02X} {b1:02X}")
                self._render_cfg(b0, b1, r)
                self._q_append_text(self.txt_power, f"读取: {hexs(r)}\n")
            self.bg(job)

        def _need_cfg(self) -> bool:
            """预览/写入前要求先读取配置对，避免覆盖未知的 TS/EXP/AUX。"""
            if self.cfg_pair is None:
                messagebox.showwarning(APP_TITLE, "请先点“读取配置”，再做预览/写入。")
                return False
            return True

        def do_pwr_preview(self):
            """只计算 PWR 位目标值，不写设备。"""
            if not self._need_cfg(): return
            try:
                bits = pwr_bits_from_text(self.cmb_pwr.get())
                b0, old = self.cfg_pair
                new = (old & ~PWR_MASK) | bits
                self.txt_power.insert(
                    "end", f"预览 PWR: FRAM[0007] 0x{old:02X} -> 0x{new:02X} "
                           f"(保留 TS/EXP/AUX={old & ~PWR_MASK & 0xFF:02X})\n")
                self.txt_power.see("end")
            except Exception as e:
                messagebox.showwarning(APP_TITLE, str(e))

        def do_pwr_write(self):
            """写 PWR 位（只改 bit3-4），写前确认，写后回读。"""
            if not self.require_open() or not self._need_cfg(): return
            try:
                bits = pwr_bits_from_text(self.cmb_pwr.get())
                b0, old = self.cfg_pair
                new = (old & ~PWR_MASK) | bits
            except Exception as e:
                messagebox.showwarning(APP_TITLE, str(e)); return
            if not self._require_backup_ack():
                return
            if not messagebox.askyesno(
                    APP_TITLE,
                    f"确认写功率档？\nFRAM[0007]: 0x{old:02X} -> 0x{new:02X}\n"
                    "只改 bit3-4，保留 TS/EXP/AUX；写后需回读确认。"):
                return
            def job(cat):
                cat.write_config_pair(b0, new)
                b0b, b1b, r = cat.read_config_pair()
                self.log(f"功率档写入并回读: FRAM[0007]=0x{b1b:02X}")
                self._render_cfg(b0b, b1b, r)
                self._q_append_text(self.txt_power, f"功率写入回读: {b0b:02X} {b1b:02X} ({hexs(r)})\n")
            self.bg(job)

        def do_ts_preview(self):
            """只计算 TS/EXP 位目标值，不写设备。"""
            if not self._need_cfg(): return
            try:
                ts = int(self.cmb_ts.get())
                exp = bool(self.var_exp.get())
                b0, old = self.cfg_pair
                new = (old & ~(TS_MASK | EXP_BIT)) | (ts & TS_MASK) | (EXP_BIT if exp else 0)
                ranges, why = effective_ranges(ts, exp)
                self.txt_power.insert(
                    "end", f"预览 TS/EXP: FRAM[0007] 0x{old:02X} -> 0x{new:02X}  "
                           f"{fmt_ranges(ranges)} MHz ({why})\n")
                self.txt_power.see("end")
            except Exception as e:
                messagebox.showwarning(APP_TITLE, str(e))

        def do_ts_write(self):
            """写 TS/EXP 位（保留 PWR/AUX），写后回读；提示需整机断电。"""
            if not self.require_open() or not self._need_cfg(): return
            try:
                ts = int(self.cmb_ts.get())
                exp = bool(self.var_exp.get())
                b0, old = self.cfg_pair
                new = (old & ~(TS_MASK | EXP_BIT)) | (ts & TS_MASK) | (EXP_BIT if exp else 0)
                ranges, why = effective_ranges(ts, exp)
            except Exception as e:
                messagebox.showwarning(APP_TITLE, str(e)); return
            if not self._require_backup_ack():
                return
            if not messagebox.askyesno(
                    APP_TITLE,
                    f"确认写 TS/EXP？\nFRAM[0007]: 0x{old:02X} -> 0x{new:02X}\n"
                    f"目标范围: {fmt_ranges(ranges)} MHz ({why})\n"
                    "写后必须整机断电重启才会完全生效；不会进入服务模式。"):
                return
            def job(cat):
                cat.write_config_pair(b0, new)
                b0b, b1b, r = cat.read_config_pair()
                self.log(f"TS/EXP 写入并回读: FRAM[0007]=0x{b1b:02X}")
                self._render_cfg(b0b, b1b, r)
                self._q_append_text(self.txt_power, f"TS/EXP 写入回读: {b0b:02X} {b1b:02X} ({hexs(r)})\n")
            self.bg(job)

        def _parse_power_bytes(self) -> tuple[bytes, bytes]:
            """把 F/AC 输入框分别解析为 4 字节；允许十进制或 0xNN。"""
            def one(e, band):
                s = e.get().strip()
                v = int(s, 16) if s.lower().startswith("0x") else int(s, 10)
                if not 0 <= v <= 0xFF:
                    raise ValueError(f"{band} 值超出 0x00–0xFF")
                return v
            f = bytes(one(self.ent_f[b], b) for b in POWER_BAND_NAMES)
            ac = bytes(one(self.ent_ac[b], b) for b in POWER_BAND_NAMES)
            return f, ac

        def do_power_bands_read(self):
            """读取 F(0x0335..0x0338) 和 AC(0x046A..0x046D)。"""
            if not self.require_open(): return
            def job(cat):
                fb = cat.read_fram(F_ADDR, F_ADDR + 3, strict=True)
                ab = cat.read_fram(AC_ADDR, AC_ADDR + 3, strict=True)
                def fill():
                    for i, band in enumerate(POWER_BAND_NAMES):
                        self.ent_f[band].delete(0, "end")
                        self.ent_f[band].insert(0, f"0x{fb[i]:02X}")
                        self.ent_ac[band].delete(0, "end")
                        self.ent_ac[band].insert(0, f"0x{ab[i]:02X}")
                self.ui(fill)
                self._q_append_text(
                    self.txt_power,
                    "F: " + " ".join(f"{b}={fb[i]:02X}" for i, b in enumerate(POWER_BAND_NAMES))
                    + "\nAC: " + " ".join(f"{b}={ab[i]:02X}" for i, b in enumerate(POWER_BAND_NAMES))
                    + "\n")
                self.log("已读取 F/AC 各频段功率字节")
            self.bg(job)

        def do_power_bands_write(self):
            """写入 F/AC 各 4 字节；带确认和回读。"""
            if not self.require_open(): return
            try:
                fbytes, abytes = self._parse_power_bytes()
            except Exception as e:
                messagebox.showwarning(APP_TITLE, f"参数错误: {e}"); return
            if not self._require_backup_ack():
                return
            if not messagebox.askyesno(
                    APP_TITLE,
                    f"确认写各频段功率字节？\nF  0x{F_ADDR:04X}: {fbytes.hex(' ')}\n"
                    f"AC 0x{AC_ADDR:04X}: {abytes.hex(' ')}\n"
                    "顺序 HF/50M/70M/AM；有效功率=min(F,AC)。"):
                return
            def job(cat):
                cat.write_fram(F_ADDR, fbytes)
                cat.write_fram(AC_ADDR, abytes)
                fb = cat.read_fram(F_ADDR, F_ADDR + 3, strict=True)
                ab = cat.read_fram(AC_ADDR, AC_ADDR + 3, strict=True)
                self._q_append_text(self.txt_power, f"F 回读: {fb.hex(' ')}\nAC 回读: {ab.hex(' ')}\n")
                self.log("F/AC 写入并回读完成")
            self.bg(job)

        def do_service_mode(self):
            """发送 ZZ<前缀>000; 进入服务模式/临时宽频（会立即软复位）。"""
            if not self.require_open(): return
            zz = self.cat.zz_prefix
            if not messagebox.askyesno(
                    APP_TITLE,
                    f"将发送 ZZ{zz}000; 进入服务模式并立即软复位。\n"
                    "这是临时宽频入口，不写 EXP 位。\n"
                    f"退出请用 RESET 菜单或断电；不要用 ZZ{zz}01（ALL RESET）。\n继续？"):
                return
            def job(cat):
                cat.zz(0)
                self.log(f"已发送 ZZ{zz}000;，设备应软复位进入服务模式")
                self._q_append_text(self.txt_power, f"已发送 ZZ{zz}000;（服务模式 / 临时宽频）\n")
            self.bg(job)

        # ---------------- Tab: 机型 / 魔术串 ----------------
        def _tab_profile(self):
            """机型配置页：选择 FTDX10/FT-891 预设，编辑并应用 VE/JP/ZZ 魔术串。"""
            f = ttk.Frame(self.nb, padding=10)
            self.nb.add(f, text="机型 / 魔术串")

            top = ttk.Frame(f); top.pack(fill="x", pady=4)
            ttk.Label(top, text="机型预设:").pack(side="left")
            self.cmb_profile = ttk.Combobox(
                top, width=40, state="readonly",
                values=self._profile_names())
            self.cmb_profile.pack(side="left", padx=6)
            if self.cmb_profile["values"]:
                self.cmb_profile.set(self.cmb_profile["values"][0])
            self.cmb_profile.bind("<<ComboboxSelected>>", lambda _e: self.load_profile_form())
            self.var_prof_status = tk.StringVar(value="")
            ttk.Label(top, textvariable=self.var_prof_status, foreground="#a60").pack(side="left", padx=8)
            ttk.Button(top, text="重新载入 JSON", command=self.reload_profiles).pack(side="left", padx=4)
            ttk.Button(top, text="保存当前配置到 JSON", command=self.save_profile_form).pack(side="left", padx=4)
            ttk.Button(top, text="手动输入 / 新建", command=self.new_manual_profile).pack(side="left", padx=4)

            box = ttk.LabelFrame(f, text="魔术串 / 机型参数（可直接覆盖预设并保存）")
            box.pack(fill="x", pady=6)
            row = ttk.Frame(box); row.pack(fill="x", padx=6, pady=4)
            ttk.Label(row, text="显示名称:").grid(row=0, column=0, sticky="e")
            self.ent_prof_name = ttk.Entry(row, width=34)
            self.ent_prof_name.grid(row=0, column=1, sticky="w", padx=4)
            ttk.Label(row, text="CAT ID:").grid(row=0, column=2, sticky="e", padx=(12, 0))
            self.ent_prof_id = ttk.Entry(row, width=10)
            self.ent_prof_id.grid(row=0, column=3, sticky="w", padx=4)

            ttk.Label(row, text="VE 魔术串 (7 ASCII；可粘贴 VERAH...):").grid(
                row=1, column=0, sticky="e", pady=4)
            self.ent_prof_ve = ttk.Entry(row, width=18)
            self.ent_prof_ve.grid(row=1, column=1, sticky="w", padx=4)
            ttk.Label(row, text="JP 前缀 (6 ASCII):").grid(row=1, column=2, sticky="e", padx=(12, 0))
            self.ent_prof_jp_prefix = ttk.Entry(row, width=12)
            self.ent_prof_jp_prefix.grid(row=1, column=3, sticky="w", padx=4)

            ttk.Label(row, text="JP 写入值 (8 hex，可留空):").grid(
                row=2, column=0, sticky="e", pady=4)
            self.ent_prof_jp_magic = ttk.Entry(row, width=18)
            self.ent_prof_jp_magic.grid(row=2, column=1, sticky="w", padx=4)
            ttk.Label(row, text="ZZ 前缀 (6 ASCII；可粘贴 ZZPE...):").grid(row=2, column=2, sticky="e", padx=(12, 0))
            self.ent_prof_zz_prefix = ttk.Entry(row, width=12)
            self.ent_prof_zz_prefix.grid(row=2, column=3, sticky="w", padx=4)
            ttk.Label(row, text="JP 写入时两个 16 位字必须互补 (v^w=FFFF)；留空表示尚未确定。",
                      foreground="#a00").grid(row=3, column=0, columnspan=4, sticky="w", pady=(0, 2))

            note = ttk.Label(f, foreground="#555", justify="left", text=(
                "说明：\n"
                "· 机型与 VE/JP/ZZ 魔术串统一保存在 cat/rig_profiles.json；本页字段可手工覆盖并保存。\n"
                "· VE 完整帧 = “VE” + 7 字符魔术串：FTDX10=VERAH076H，FT-891=VERAH065H。\n"
                "· JP 读取帧 = JP 前缀：FTDX10=JP0010，FT-891=JP0891；写入帧 = 前缀 + 8 位十六进制。\n"
                "· ZZ 前缀：FTDX10=PE0010，FT-891=PE0891；ZZ 帧 = “ZZ” + 该前缀 + 2 位操作值。\n"
                "· FT-891 魔术串来自 AH065_M_V0110 静态比较：VE 0x1D642、JP 0x19728、ZZ 0x1968A；\n"
                "  JP 写入值尚未解码，预设留空，需实机/后续研究后手动填写并保存。\n"
                "· 修改后先点“应用魔术串”，再使用快捷命令/服务菜单。FT-891 的 E0/E8 长度和服务表地址\n"
                "  与 FTDX10 不同，本 GUI 目前仅魔术串按机型分离，其它页面仍按 FTDX10 实现。"))
            note.pack(anchor="w", pady=(0, 4))

            btns = ttk.Frame(f); btns.pack(fill="x", pady=4)
            ttk.Button(btns, text="应用魔术串", command=self.apply_profile_magic).pack(side="left", padx=4)
            ttk.Button(btns, text="读取 ID", command=self.do_id_read).pack(side="left", padx=4)
            ttk.Button(btns, text="读取 VE 版本", command=self.do_ve).pack(side="left", padx=4)
            ttk.Button(btns, text="JP 读取", command=self.do_jp_read).pack(side="left", padx=4)
            ttk.Button(btns, text="JP 写入", command=self.do_jp_write).pack(side="left", padx=4)
            self.lbl_magic_state = ttk.Label(
                btns,
                text=f"当前: VE={self.cat.ve_magic} / JP={self.cat.jp_prefix} / ZZ={self.cat.zz_prefix}",
                foreground="#054")
            self.lbl_magic_state.pack(side="left", padx=12)

            ttk.Label(f, text="备注 / 实机观察:").pack(anchor="w", pady=(6, 0))
            self.txt_prof_notes = self._mktext(f, 5)
            self.txt_prof_notes.pack(fill="x", pady=(2, 4))
            ttk.Label(f, text="回包 / 解析:").pack(anchor="w")
            self.txt_prof_result = self._mktext(f, 8)
            self.txt_prof_result.pack(fill="both", expand=True, pady=(2, 0))

            self.load_profile_form()

        def _profile_names(self):
            """下拉框名称；最后一项是手动输入模式。"""
            names = [p.get("name", p.get("id", "?"))
                     for p in self.profile_data.get("profiles", [])]
            return names + [MANUAL_PROFILE_NAME]

        def _profile_by_id(self, pid):
            """按 id 查预设。"""
            for p in self.profile_data.get("profiles", []):
                if p.get("id") == pid:
                    return p
            return None

        def _selected_profile(self):
            """按下拉菜单当前显示名查预设；手动输入模式返回 None。"""
            name = self.cmb_profile.get()
            if name == MANUAL_PROFILE_NAME:
                return None
            for p in self.profile_data.get("profiles", []):
                if p.get("name", p.get("id")) == name:
                    return p
            return None

        def _new_custom_id(self):
            """生成不会与现有预设冲突的手动配置 id。"""
            stamp = time.strftime("%Y%m%d_%H%M%S")
            base = f"custom_{stamp}"
            ids = {p.get("id") for p in self.profile_data.get("profiles", [])}
            cid, n = base, 2
            while cid in ids:
                cid = f"{base}_{n}"
                n += 1
            return cid

        def _clear_profile_form(self):
            """清空编辑框，进入手动输入模式。"""
            for ent in (self.ent_prof_name, self.ent_prof_id, self.ent_prof_ve,
                        self.ent_prof_jp_prefix, self.ent_prof_jp_magic,
                        self.ent_prof_zz_prefix):
                ent.delete(0, "end")
            self.ent_prof_name.insert(0, "自定义 / 手动输入")
            self.txt_prof_notes.delete("1.0", "end")
            self.txt_prof_notes.insert(
                "1.0", "手动输入模式：填写魔术串后点“应用魔术串”；点“保存当前配置到 JSON”会新建预设。")
            self.var_prof_status.set("手动输入 / 未验证")

        def new_manual_profile(self):
            """新建手动输入配置；不覆盖已有预设。"""
            self.current_profile_id = self._new_custom_id()
            self.cmb_profile.set(MANUAL_PROFILE_NAME)
            self._clear_profile_form()
            self.log(f"已切换到手动输入模式（保存后 id={self.current_profile_id}）")

        def load_profile_form(self):
            """把当前下拉预设填入编辑框；选中“自定义”时清空。"""
            if self.cmb_profile.get() == MANUAL_PROFILE_NAME:
                self.new_manual_profile()
                return
            p = self._selected_profile()
            if not p:
                return
            self.current_profile_id = p.get("id", self.current_profile_id)
            for ent, key, default in (
                (self.ent_prof_name, "name", ""),
                (self.ent_prof_id, "cat_id", ""),
                (self.ent_prof_ve, "ve_magic", "RAH076H"),
                (self.ent_prof_jp_prefix, "jp_prefix", "JP0010"),
                (self.ent_prof_jp_magic, "jp_magic", ""),
                (self.ent_prof_zz_prefix, "zz_prefix", "PE0010"),
            ):
                ent.delete(0, "end")
                ent.insert(0, str(p.get(key, default)))
            self.txt_prof_notes.delete("1.0", "end")
            self.txt_prof_notes.insert("1.0", p.get("notes", ""))
            status = p.get("status", "未标注证据状态")
            ms = p.get("magic_status", "")
            if ms and ms not in status:
                status = f"{status}；魔术串： {ms}"
            self.var_prof_status.set(status)

        def reload_profiles(self):
            """从 rig_profiles.json 重新载入。"""
            self.profile_data = load_rig_profiles()
            names = self._profile_names()
            self.cmb_profile["values"] = names
            if names:
                self.cmb_profile.set(names[0])
            self.load_profile_form()
            self.log(f"已重新载入 {PROFILES_PATH.name}")

        def _form_profile(self) -> dict:
            """收集编辑框内容并做基本校验，返回可写 JSON 的预设字典。"""
            name = self.ent_prof_name.get().strip()
            cat_id = self.ent_prof_id.get().strip().upper()
            ve = self.ent_prof_ve.get().strip().upper()
            if len(ve) == 9 and ve.startswith("VE"):
                ve = ve[2:]
            jp_prefix = self.ent_prof_jp_prefix.get().strip().upper()
            jp_magic = self.ent_prof_jp_magic.get().strip().upper()
            zz_prefix = self.ent_prof_zz_prefix.get().strip().upper()
            if len(zz_prefix) == 8 and zz_prefix.startswith("ZZ"):
                zz_prefix = zz_prefix[2:]
            if not name:
                raise ValueError("显示名称不能为空")
            if len(ve) != 7 or not ve.isascii() or ";" in ve:
                raise ValueError("VE 魔术串必须是 7 个 ASCII 字符")
            if len(jp_prefix) != 6 or not jp_prefix.isascii() or ";" in jp_prefix:
                raise ValueError("JP 前缀必须是 6 个 ASCII 字符")
            if len(zz_prefix) != 6 or not zz_prefix.isascii() or ";" in zz_prefix:
                raise ValueError("ZZ 前缀必须是 6 个 ASCII 字符")
            if jp_magic:
                if len(jp_magic) != 8 or any(c not in "0123456789ABCDEF" for c in jp_magic):
                    raise ValueError("JP 写入值必须是 8 个十六进制字符（或留空）")
                v, w = int(jp_magic[:4], 16), int(jp_magic[4:], 16)
                if (v ^ w) != 0xFFFF:
                    raise ValueError(f"JP 两字不互补：0x{v:04X} ^ 0x{w:04X} != 0xFFFF")
            orig = self._selected_profile() or {}
            pid = self.current_profile_id or re.sub(r"\W+", "_", name.lower())
            magic_status = orig.get("magic_status") or "手动输入 / 未验证"
            magic_evidence = orig.get("magic_evidence") or "用户手动输入；未在固件/真机验证。"
            return {
                "id": pid,
                "name": name,
                "cat_id": cat_id,
                "ve_magic": ve,
                "ve_full": "VE" + ve,
                "jp_prefix": jp_prefix,
                "jp_magic": jp_magic,
                "zz_prefix": zz_prefix,
                "status": self.var_prof_status.get().strip() or "未标注证据状态",
                "notes": self.txt_prof_notes.get("1.0", "end").strip(),
                "magic_status": magic_status,
                "magic_evidence": magic_evidence,
            }

        def save_profile_form(self):
            """把机型与魔术串一起写回 rig_profiles.json。"""
            try:
                p = self._form_profile()
                profiles = self.profile_data.setdefault("profiles", [])
                for i, old in enumerate(profiles):
                    if old.get("id") == p["id"]:
                        profiles[i] = p
                        break
                else:
                    profiles.append(p)
                save_rig_profiles(self.profile_data)
                self.cmb_profile["values"] = self._profile_names()
                self.cmb_profile.set(p["name"])
                self.current_profile_id = p["id"]
                self.log(f"已保存机型/魔术串 -> {PROFILES_PATH.name}: {p['name']}")
            except Exception as e:
                messagebox.showerror(APP_TITLE, f"保存失败: {e}")

        def apply_profile_magic(self):
            """校验并把 VE/JP/ZZ 魔术串应用到当前 HiddenCat 实例。"""
            try:
                ve = self.ent_prof_ve.get().strip()
                jp = self.ent_prof_jp_prefix.get().strip()
                zz = self.ent_prof_zz_prefix.get().strip()
                self.cat.set_magic(ve, jp, zz)
                self.lbl_magic_state.configure(
                    text=f"当前: VE={self.cat.ve_magic} / JP={self.cat.jp_prefix} / ZZ={self.cat.zz_prefix}")
                # 快捷页的 JP 写值同步为当前预设；留空时清空，避免误用旧值。
                if hasattr(self, "ent_jp"):
                    self.ent_jp.delete(0, "end")
                    self.ent_jp.insert(0, self.ent_prof_jp_magic.get().strip().upper())
                self.log(f"已应用魔术串: VE={self.cat.ve_magic} / JP={self.cat.jp_prefix} / ZZ={self.cat.zz_prefix}")
            except Exception as e:
                messagebox.showwarning(APP_TITLE, str(e))

        def do_id_read(self):
            """标准 ID; 查询，用于确认下拉预设是否匹配实机。"""
            if not self.require_open(): return
            want = self.ent_prof_id.get().strip().upper()
            def job(cat):
                r = cat.id_read()
                txt = r.decode("latin-1", "replace")
                self.log(f"ID: {hexs(r)} / {txt}")
                warn = ""
                if want and want not in txt.upper():
                    warn = f"  ⚠ 与预设 CAT ID {want} 不一致，请确认机型/魔术串"
                    self.log(warn)
                self._q_append_text(self.txt_prof_result, f"ID -> {hexs(r)}  {txt}{warn}\n")
            self.bg(job)

        # ---------------- Tab: 外部 FRAM / 设置表 ----------------
        def _tab_reg(self):
            f = ttk.Frame(self.nb, padding=10)
            self.nb.add(f, text="FRAM / 表")
            g = ttk.Frame(f); g.pack(fill="x")

            ttk.Label(g, text="外部 FRAM 地址 (SPW/SPR, 0x0000-0x7FFF):").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))
            ttk.Label(g, text="地址(hex):").grid(row=1, column=0, sticky="e")
            self.ent_addr = ttk.Entry(g, width=10); self.ent_addr.insert(0, "0100")
            self.ent_addr.grid(row=1, column=1, sticky="w")
            ttk.Label(g, text="数据(2字节 hex):").grid(row=1, column=2, sticky="e")
            self.ent_data = ttk.Entry(g, width=12); self.ent_data.insert(0, "0000")
            self.ent_data.grid(row=1, column=3, sticky="w")
            ttk.Button(g, text="读 2 字节", command=self.do_reg_read).grid(row=1, column=4, padx=6)
            ttk.Button(g, text="写 2 字节", command=self.do_reg_write).grid(row=1, column=5, padx=6)

            ttk.Separator(f, orient="horizontal").pack(fill="x", pady=8)

            ttk.Label(f, text="设置/校准表 (SPw/SPr, 0xFFA948, 地址 0x000A-0x017C):").pack(anchor="w")
            g2 = ttk.Frame(f); g2.pack(fill="x", pady=4)
            ttk.Label(g2, text="表地址(hex):").pack(side="left")
            self.ent_taddr = ttk.Entry(g2, width=10); self.ent_taddr.insert(0, "000A")
            self.ent_taddr.pack(side="left", padx=4)
            ttk.Label(g2, text="数据(1字节 hex):").pack(side="left")
            self.ent_tdata = ttk.Entry(g2, width=8); self.ent_tdata.insert(0, "00")
            self.ent_tdata.pack(side="left", padx=4)
            ttk.Button(g2, text="读 1 字节", command=self.do_tab_read).pack(side="left", padx=6)
            ttk.Button(g2, text="写 1 字节", command=self.do_tab_write).pack(side="left", padx=6)
            ttk.Button(g2, text="读 0xFF2001", command=self.do_2001).pack(side="left", padx=6)

            ttk.Separator(f, orient="horizontal").pack(fill="x", pady=8)

            ttk.Label(f, text="范围导出 (SPR 逐 2 字节):").pack(anchor="w")
            g3 = ttk.Frame(f); g3.pack(fill="x", pady=4)
            ttk.Label(g3, text="起(hex):").pack(side="left")
            self.ent_rng_s = ttk.Entry(g3, width=8); self.ent_rng_s.insert(0, "0000"); self.ent_rng_s.pack(side="left", padx=4)
            ttk.Label(g3, text="止(hex):").pack(side="left")
            self.ent_rng_e = ttk.Entry(g3, width=8); self.ent_rng_e.insert(0, "00FF"); self.ent_rng_e.pack(side="left", padx=4)
            ttk.Button(g3, text="导出到文件", command=self.do_range_export).pack(side="left", padx=6)

            self.txt_reg = self._mktext(f, 16)
            self.txt_reg.pack(fill="both", expand=True, pady=(8, 0))

        def _pv(self, s, name):
            try:
                return int(s.strip(), 16)
            except ValueError:
                raise ValueError(f"{name} 不是合法十六进制: {s!r}")

        def do_reg_read(self):
            if not self.require_open(): return
            try: addr = self._pv(self.ent_addr.get(), "地址")
            except ValueError as e:
                messagebox.showwarning(APP_TITLE, str(e)); return
            def job(cat):
                r = cat.spr(addr)
                self._q_append_text(self.txt_reg, f"SPR {addr:04X} -> {r[5]:02X} {r[6]:02X}   ({hexs(r)})\n")
            self.bg(job)

        def do_reg_write(self):
            if not self.require_open(): return
            try:
                addr = self._pv(self.ent_addr.get(), "地址")
                d = bytes.fromhex(self.ent_data.get().replace(" ", ""))
                assert len(d) == 2
            except Exception as e:
                messagebox.showwarning(APP_TITLE, f"参数错误: {e}"); return
            if not self._require_backup_ack():
                return
            if not messagebox.askyesno(APP_TITLE, f"确认写外部 FRAM 地址 0x{addr:04X} = {d.hex()}？"):
                return
            def job(cat):
                r = cat.spw(addr, d[0], d[1])
                self._q_append_text(self.txt_reg, f"SPW {addr:04X} <- {d.hex()}  回包 {hexs(r)}\n")
            self.bg(job)

        def do_tab_read(self):
            if not self.require_open(): return
            try: addr = self._pv(self.ent_taddr.get(), "表地址")
            except ValueError as e:
                messagebox.showwarning(APP_TITLE, str(e)); return
            def job(cat):
                r = cat.spr_table(addr)
                self._q_append_text(self.txt_reg, f"SPr {addr:04X} -> {r[6]:02X}   ({hexs(r)})\n")
            self.bg(job)

        def do_tab_write(self):
            if not self.require_open(): return
            try:
                addr = self._pv(self.ent_taddr.get(), "表地址")
                d = int(self.ent_tdata.get().strip(), 16)
                assert 0 <= d <= 0xFF
            except Exception as e:
                messagebox.showwarning(APP_TITLE, f"参数错误: {e}"); return
            if not self._require_backup_ack():
                return
            if not messagebox.askyesno(APP_TITLE,
                                       f"确认写设置表 0x{addr:04X} = 0x{d:02X}？可能影响校准。"):
                return
            def job(cat):
                r = cat.spw_table(addr, d)
                self._q_append_text(self.txt_reg, f"SPw {addr:04X} <- {d:02X}  回包 {hexs(r)}\n")
            self.bg(job)

        def do_2001(self):
            if not self.require_open(): return
            def job(cat):
                r = cat.spr_table(0xFFFF)
                self._q_append_text(self.txt_reg, f"SPr FFFF -> {r[6]:02X}   ({hexs(r)})\n")
            self.bg(job)

        def do_range_export(self):
            if not self.require_open(): return
            try:
                s = self._pv(self.ent_rng_s.get(), "起始")
                e = self._pv(self.ent_rng_e.get(), "结束")
            except ValueError as ex:
                messagebox.showwarning(APP_TITLE, str(ex)); return
            if e < s:
                messagebox.showwarning(APP_TITLE, "结束地址小于起始"); return
            path = filedialog.asksaveasfilename(defaultextension=".bin",
                                                filetypes=[("二进制", "*.bin"), ("全部", "*.*")])
            if not path:
                return
            def job(cat):
                total = (e - s + 1 + 1) // 2
                def prog(a, b):
                    self.status(f"导出 {a}/{b}  …")
                data = cat.read_fram(s, e, progress=prog, cancel=self.cancel, strict=True)
                with open(path, "wb") as fp:
                    fp.write(data)
                self._q_append_text(self.txt_reg, f"已导出 0x{s:04X}-0x{e:04X} ({len(data)}B) -> {path}\n")
                self.log(f"范围导出完成: {path}")
            self.bg(job)

        # ---------------- Tab: 完整 FRAM 编辑器 ----------------
        def _tab_fram_full(self):
            """FRAM 全表页：区域说明 + 十六进制编辑 + 整片/区间读写。"""
            f = ttk.Frame(self.nb, padding=10)
            self.nb.add(f, text="FRAM 全表")

            top = ttk.Frame(f); top.pack(fill="x", pady=3)
            ttk.Button(top, text="读取区间", command=self.do_fram_read_range).pack(side="left", padx=3)
            ttk.Button(top, text="写入编辑框", command=self.do_fram_write_dump).pack(side="left", padx=3)
            ttk.Button(top, text="读取全部 32KB", command=self.do_fram_read_all).pack(side="left", padx=3)
            ttk.Button(top, text="备份 0–0x0490", command=self.do_quick_backup).pack(side="left", padx=3)
            ttk.Button(top, text="导出区间 .bin", command=self.do_fram_export_bin).pack(side="left", padx=3)
            ttk.Checkbutton(top, text="我已备份 0–0x0490", variable=self.var_backup_ack).pack(side="left", padx=8)
            ttk.Button(top, text="导入 .bin 到编辑框", command=self.do_fram_import_bin).pack(side="left", padx=3)

            r = ttk.Frame(f); r.pack(fill="x", pady=3)
            ttk.Label(r, text="起(hex):").pack(side="left")
            self.ent_fram_s = ttk.Entry(r, width=8); self.ent_fram_s.insert(0, "0000")
            self.ent_fram_s.pack(side="left", padx=4)
            ttk.Label(r, text="止(hex):").pack(side="left")
            self.ent_fram_e = ttk.Entry(r, width=8); self.ent_fram_e.insert(0, "00FF")
            self.ent_fram_e.pack(side="left", padx=4)
            ttk.Label(r, text="编辑格式: AAAA: 00 11 22 …（行尾 ASCII 会被忽略）",
                      foreground="#666").pack(side="left", padx=10)

            paned = ttk.PanedWindow(f, orient="horizontal")
            paned.pack(fill="both", expand=True, pady=4)

            left = ttk.Frame(paned)
            ttk.Label(left, text="FRAM 区域（中文说明）").pack(anchor="w")
            cols = ("start", "end", "size", "name", "conf")
            self.fram_tree = ttk.Treeview(left, columns=cols, show="headings", height=16)
            for c, txt, width in (
                    ("start", "起", 62), ("end", "止", 62), ("size", "大小", 52),
                    ("name", "区域 / 中文说明", 260), ("conf", "置信", 56)):
                self.fram_tree.heading(c, text=txt)
                self.fram_tree.column(c, width=width, anchor="w")
            self.fram_tree.pack(fill="both", expand=True)
            self.fram_tree.bind("<<TreeviewSelect>>", self.on_fram_region_select)
            zh = self.profile_data.get("fram_regions_zh", {})
            for row in self.fram_regions:
                self.fram_tree.insert(
                    "", "end", iid=f"r{row['start_i']:04X}",
                    values=(row["start"], row["end"], row["size"],
                            zh.get(row["name"], row["name"]), row.get("confidence", "")))
            ttk.Label(left, text="选中区域会填入起止地址；写前请先读取/备份。",
                      foreground="#a00").pack(anchor="w", pady=(3, 0))
            paned.add(left, weight=1)

            right = ttk.Frame(paned)
            ttk.Label(right, text="十六进制编辑（按行地址写回；不会自动保存）").pack(anchor="w")
            self.txt_fram = self._mktext(right, 20)
            self.txt_fram.pack(fill="both", expand=True)
            self.txt_fram.insert("1.0", "# 点“读取区间”或“读取全部 32KB”后在此编辑。\n"
                                        "# 写回只使用形如 0000: 12 34 的行。\n")
            paned.add(right, weight=3)

        def on_fram_region_select(self, _event=None):
            """把区域树选中行填入起止地址。"""
            sel = self.fram_tree.selection()
            if not sel:
                return
            iid = sel[0]
            for row in self.fram_regions:
                if f"r{row['start_i']:04X}" == iid:
                    self.ent_fram_s.delete(0, "end"); self.ent_fram_s.insert(0, row["start"][2:])
                    self.ent_fram_e.delete(0, "end"); self.ent_fram_e.insert(0, row["end"][2:])
                    return

        def _require_backup_ack(self) -> bool:
            """高风险 FRAM/校准写入前，要求用户勾选“已备份 0–0x0490”。"""
            if not self.var_backup_ack.get():
                messagebox.showwarning(
                    APP_TITLE,
                    "请先在「FRAM 全表」页用“备份 0–0x0490”保存 .bin，\n"
                    "并勾选该页或「服务菜单」页的“我已备份 0–0x0490”后再写入。")
                return False
            return True

        def _fram_range(self) -> tuple[int, int]:
            s = int(self.ent_fram_s.get().strip(), 16)
            e = int(self.ent_fram_e.get().strip(), 16)
            if not 0 <= s <= e <= 0x7FFF:
                raise ValueError("要求 0x0000 <= 起 <= 止 <= 0x7FFF")
            return s, e

        def _render_fram(self, data: bytes, start: int):
            """把 FRAM 数据渲染为 16 字节/行的带地址十六进制。"""
            if threading.current_thread() is not threading.main_thread():
                self.ui(lambda: self._render_fram(data, start))
                return
            self.txt_fram.delete("1.0", "end")
            for off in range(0, len(data), 16):
                chunk = data[off:off + 16]
                asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                self.txt_fram.insert("end", f"{start + off:04X}: {hexs(chunk):<47}  {asc}\n")

        def do_fram_read_range(self):
            """按起止地址用 SPR 读取并显示。"""
            if not self.require_open(): return
            try:
                s, e = self._fram_range()
            except Exception as ex:
                messagebox.showwarning(APP_TITLE, str(ex)); return
            def job(cat):
                def prog(a, b): self.status(f"读取 FRAM {a}/{b} …")
                data = cat.read_fram(s, e, progress=prog, cancel=self.cancel)
                self._render_fram(data, s)
                self.log(f"FRAM 读取完成: 0x{s:04X}-0x{e:04X} ({len(data)}B)")
            self.bg(job)

        def do_fram_read_all(self):
            """读取完整 0x0000–0x7FFF（16384 次 SPR，耗时较长）。"""
            if not self.require_open(): return
            if not messagebox.askyesno(APP_TITLE,
                                       "将逐 2 字节读取 32KB FRAM，可能耗时数分钟。继续？"):
                return
            self.ent_fram_s.delete(0, "end"); self.ent_fram_s.insert(0, "0000")
            self.ent_fram_e.delete(0, "end"); self.ent_fram_e.insert(0, "7FFF")
            self.do_fram_read_range()

        def do_fram_write_dump(self):
            """解析编辑框中的每一行，并按行地址写回 FRAM。"""
            if not self.require_open(): return
            try:
                chunks = parse_hex_dump(self.txt_fram.get("1.0", "end"))
            except Exception as ex:
                messagebox.showwarning(APP_TITLE, f"解析失败: {ex}"); return
            if not chunks:
                messagebox.showwarning(APP_TITLE, "编辑框里没有可写数据"); return
            if not self._require_backup_ack():
                return
            total = sum(len(d) for _, d in chunks)
            if not messagebox.askyesno(
                    APP_TITLE,
                    f"确认写回 {len(chunks)} 行、共 {total} 字节？\n"
                    "这是外部 FRAM 写操作；写错可能导致配置/校准异常。\n"
                    "建议先“读取全部 32KB”并保存备份。"):
                return
            def job(cat):
                done = 0
                for addr, data in chunks:
                    cat.write_fram(addr, data)
                    done += len(data)
                self.log(f"FRAM 编辑框写回完成: {done} 字节")
            self.bg(job)

        def do_quick_backup(self):
            """快速备份 FRAM[0x0000–0x0490]，写服务菜单/校准前的最低备份。"""
            if not self.require_open(): return
            path = filedialog.asksaveasfilename(
                defaultextension=".bin",
                initialfile="fram_0000_0490.bin",
                filetypes=[("二进制", "*.bin"), ("全部", "*.*")])
            if not path:
                return
            def job(cat):
                def prog(a, b): self.status(f"备份 FRAM {a}/{b} …")
                data = cat.read_fram(0x0000, 0x0490, progress=prog, cancel=self.cancel, strict=True)
                with open(path, "wb") as fp:
                    fp.write(data)
                self.log(f"已备份 FRAM[0000–0490] ({len(data)}B) -> {path}")
            self.bg(job)

        def do_fram_export_bin(self):
            """把编辑框内容按 16 进制解析后保存为 .bin（不访问设备）。"""
            try:
                chunks = parse_hex_dump(self.txt_fram.get("1.0", "end"))
            except Exception as ex:
                messagebox.showwarning(APP_TITLE, f"解析失败: {ex}"); return
            if not chunks:
                messagebox.showwarning(APP_TITLE, "没有可导出的数据"); return
            base = min(a for a, _ in chunks)
            end = max(a + len(d) for a, d in chunks)
            buf = bytearray([0xFF] * (end - base))
            for a, d in chunks:
                buf[a - base:a - base + len(d)] = d
            path = filedialog.asksaveasfilename(defaultextension=".bin")
            if not path:
                return
            with open(path, "wb") as fp:
                fp.write(buf)
            self.log(f"已导出编辑框数据: {path}（{len(buf)}B，基址 0x{base:04X}）")

        def do_fram_import_bin(self):
            """把 .bin 载入编辑框，起始地址取当前“起”。"""
            path = filedialog.askopenfilename(filetypes=[("二进制", "*.bin"), ("全部", "*.*")])
            if not path:
                return
            try:
                data = open(path, "rb").read()
                start = int(self.ent_fram_s.get().strip(), 16)
                if start + len(data) > 0x8000:
                    raise ValueError("文件超出 0x7FFF")
                self._render_fram(data, start)
                self.ent_fram_e.delete(0, "end")
                self.ent_fram_e.insert(0, f"{start + len(data) - 1:04X}")
                self.log(f"已导入 {path} 到编辑框（{len(data)}B @ 0x{start:04X}）")
            except Exception as ex:
                messagebox.showerror(APP_TITLE, f"导入失败: {ex}")

        # ---------------- Tab: 服务菜单 ----------------
        def _tab_service(self):
            """294 项服务菜单：读取 371B 表、按项改校准值、SPN 选择/应用。"""
            f = ttk.Frame(self.nb, padding=10)
            self.nb.add(f, text="服务菜单")

            top = ttk.Frame(f); top.pack(fill="x", pady=3)
            ttk.Button(top, text="读取整表 (SPr 371B)", command=self.do_sm_read_all).pack(side="left", padx=3)
            ttk.Button(top, text="SPARD 整表", command=self.do_sm_spard).pack(side="left", padx=3)
            ttk.Button(top, text="读取选中项", command=self.do_sm_read_selected).pack(side="left", padx=3)
            ttk.Button(top, text="写入选中项校准值", command=self.do_sm_write_selected).pack(side="left", padx=3)
            ttk.Button(top, text="SPN 选择/应用 N", command=self.do_sm_spn).pack(side="left", padx=3)
            ttk.Button(top, text="SPAET 应用特殊项", command=self.do_sm_spaet).pack(side="left", padx=3)
            ttk.Button(top, text="SPAWE 写使能", command=self.do_sm_spawe).pack(side="left", padx=3)
            ttk.Button(top, text="进入服务模式", command=self.do_service_mode).pack(side="left", padx=3)
            ttk.Button(top, text="读取 N/S (0344/0346)", command=self.do_sm_read_ns).pack(side="left", padx=3)
            ttk.Button(top, text="SPN 读当前 N", command=self.do_sm_spn_read).pack(side="left", padx=3)
            self.var_sm_ns = tk.StringVar(value="N/S: 未读取")
            ttk.Label(top, textvariable=self.var_sm_ns, foreground="#054").pack(side="left", padx=8)
            ttk.Checkbutton(top, text="我已备份 0–0x0490",
                            variable=self.var_backup_ack).pack(side="left", padx=8)

            # 294 项列表：N / 标签 / 频段 / 存储地址 / 应用目标 / 当前值 / 新值
            cols = ("n", "label", "band", "item", "apply", "cur", "new")
            self.sm_tree = ttk.Treeview(f, columns=cols, show="headings", height=17)
            for c, txt, width in (
                    ("n", "N", 55), ("label", "标签（固件 ASCII）", 330),
                    ("band", "频段 MHz", 82), ("item", "存储 FRAM", 90),
                    ("apply", "应用目标", 90), ("cur", "当前", 55), ("new", "新值", 55)):
                self.sm_tree.heading(c, text=txt)
                self.sm_tree.column(c, width=width, anchor="w")
            self.sm_tree.pack(fill="both", expand=True, pady=3)
            self.sm_tree.bind("<<TreeviewSelect>>", self.on_sm_select)
            for it in self.service_items:
                self.sm_tree.insert(
                    "", "end", iid=f"sm{it['n']}",
                    values=(f"{it['n']:03d} {it.get('code_hex', '')}",
                            it.get("label", ""), f"{it.get('band_mhz_f', 0):g}",
                            it.get("item_fram", ""), it.get("apply_fram", ""), "", ""))

            sel = ttk.LabelFrame(f, text="选中项 / 编辑（校准值就是 371B 表中该 N 的字节）")
            sel.pack(fill="x", pady=3)
            r1 = ttk.Frame(sel); r1.pack(fill="x", padx=6, pady=3)
            ttk.Label(r1, text="N:").pack(side="left")
            self.ent_sm_n = ttk.Entry(r1, width=6); self.ent_sm_n.pack(side="left", padx=4)
            ttk.Label(r1, text="标签:").pack(side="left")
            self.ent_sm_label = ttk.Entry(r1, width=32); self.ent_sm_label.pack(side="left", padx=4)
            ttk.Label(r1, text="频段:").pack(side="left")
            self.ent_sm_band = ttk.Entry(r1, width=8); self.ent_sm_band.pack(side="left", padx=4)
            ttk.Label(r1, text="存储:").pack(side="left")
            self.ent_sm_addr = ttk.Entry(r1, width=8); self.ent_sm_addr.pack(side="left", padx=4)
            ttk.Label(r1, text="应用:").pack(side="left")
            self.ent_sm_apply = ttk.Entry(r1, width=8); self.ent_sm_apply.pack(side="left", padx=4)
            ttk.Label(r1, text="类型:").pack(side="left")
            self.ent_sm_kind = ttk.Entry(r1, width=14); self.ent_sm_kind.pack(side="left", padx=4)

            r2 = ttk.Frame(sel); r2.pack(fill="x", padx=6, pady=3)
            ttk.Label(r2, text="当前值(hex/dec):").pack(side="left")
            self.ent_sm_cur = ttk.Entry(r2, width=10); self.ent_sm_cur.pack(side="left", padx=4)
            ttk.Label(r2, text="新值(0-255):").pack(side="left")
            self.ent_sm_new = ttk.Entry(r2, width=10); self.ent_sm_new.pack(side="left", padx=4)
            self.var_sm_apply = tk.BooleanVar(value=False)
            ttk.Checkbutton(r2, text="写后 SPAET 应用（仅特殊项）",
                            variable=self.var_sm_apply).pack(side="left", padx=8)

            note = ttk.Label(f, foreground="#a00", justify="left", wraplength=1050, text=(
                "取证：项目表 0x27A8 + N*10；标签表 0x3324 + N*25；N 在 0xFFAB00 / FRAM[0x0344–0x0345]（u16 大端）。\n"
                "要点：SPw/SPr 直接读写 371B 内部设置表；SPN 只“选择/应用”项目 N，会切换当前波段和模式，"
                "不是只读命令。\n"
                "SPN/SPA* 需要 0xFF2020.7=1（进入服务模式）；SPAET 调 0x1662，普通项目返回 ?;。"
                "r3l=3 的项目每 3 项共享 1 个应用目标，写源值不等于直接改共享目标。\n"
                "退出服务模式请用 RESET 菜单或整机断电；不要用 ZZ<ZZ前缀>01（ALL RESET）。"
                "写前至少备份 FRAM[0x0000–0x0490]。"))
            note.pack(anchor="w", pady=4)

        def on_sm_select(self, _event=None):
            """把服务菜单树选中行填入编辑框。"""
            sel = self.sm_tree.selection()
            if not sel:
                return
            for it in self.service_items:
                if f"sm{it['n']}" != sel[0]:
                    continue
                self.ent_sm_n.delete(0, "end"); self.ent_sm_n.insert(0, str(it["n"]))
                self.ent_sm_label.delete(0, "end"); self.ent_sm_label.insert(0, it.get("label", ""))
                self.ent_sm_band.delete(0, "end"); self.ent_sm_band.insert(0, f"{it.get('band_mhz_f', 0):g}")
                self.ent_sm_addr.delete(0, "end"); self.ent_sm_addr.insert(0, it.get("item_fram", ""))
                self.ent_sm_apply.delete(0, "end"); self.ent_sm_apply.insert(0, it.get("apply_fram", ""))
                kind = it.get("apply_kind", "")
                self.ent_sm_kind.delete(0, "end")
                self.ent_sm_kind.insert(0, f"{kind}（{SM_KIND_ZH.get(kind, '未知')}）")
                idx = it["item_addr"] - TABLE_START
                self.ent_sm_cur.delete(0, "end")
                self.ent_sm_new.delete(0, "end")
                if 0 <= idx < len(self.cal_table):
                    v = self.cal_table[idx]
                    self.ent_sm_cur.insert(0, f"{v:02X} ({v})")
                    self.ent_sm_new.insert(0, f"{v:02X}")
                return

        def _sm_parse_value(self, text: str) -> int:
            """接受十进制、0xNN、NNh 或含 A–F 的纯 hex 的 0–255 值。"""
            t = text.strip().lower()
            if not t:
                raise ValueError("值不能为空；请输入 0–255、0xNN 或 NNh")
            if t.startswith("0x"):
                v = int(t, 16)
            elif t.endswith("h"):
                v = int(t[:-1], 16)
            else:
                try:
                    v = int(t, 10)
                except ValueError:
                    # 树里显示的是 "0A"/"7F" 这类裸 hex；无字母时仍按十进制。
                    v = int(t, 16)
            if not 0 <= v <= 0xFF:
                raise ValueError("值必须 0–255")
            return v

        def _sm_refresh_row(self, n: int, value: int | None = None):
            """刷新树中某个 N 的当前值/新值。"""
            if threading.current_thread() is not threading.main_thread():
                self.ui(lambda: self._sm_refresh_row(n, value))
                return
            iid = f"sm{n}"
            if not self.sm_tree.exists(iid):
                return
            vals = list(self.sm_tree.item(iid, "values"))
            if value is not None:
                vals[5] = f"{value:02X}"
                vals[6] = f"{value:02X}"
            self.sm_tree.item(iid, values=vals)

        def _sm_render_table(self, tb: bytes):
            """把 371B 表填入 294 行服务菜单的“当前值”列。"""
            if threading.current_thread() is not threading.main_thread():
                self.ui(lambda: self._sm_render_table(tb))
                return
            self.cal_table = tb
            for it in self.service_items:
                idx = it["item_addr"] - TABLE_START
                if 0 <= idx < len(tb):
                    self._sm_refresh_row(it["n"], tb[idx])
            self.log(f"服务菜单表已更新: {len(tb)}B")

        def do_sm_read_all(self):
            """用 SPr 逐字节读取 371B 设置表并填入服务菜单。"""
            if not self.require_open(): return
            def job(cat):
                def prog(a, b): self.status(f"读取服务菜单表 {a}/{b} …")
                tb = cat.read_settings_table(progress=prog, cancel=self.cancel)
                self._sm_render_table(tb)
                self._render_cal(tb)
            self.bg(job)

        def do_sm_spard(self):
            """用 SPARD 一次读取 371B（需服务模式门控）。"""
            if not self.require_open(): return
            def job(cat):
                tb = cat.spa_table()
                self._sm_render_table(tb)
                self._render_cal(tb)
                self.log("SPARD 整表读取成功（371B）")
            self.bg(job)

        def do_sm_read_selected(self):
            """读取当前选中 N 的校准字节。"""
            if not self.require_open(): return
            try:
                n = int(self.ent_sm_n.get())
                it = next(x for x in self.service_items if x["n"] == n)
            except Exception:
                messagebox.showwarning(APP_TITLE, "请先选择一个有效的服务菜单项"); return
            def job(cat):
                r = cat.spr_table(it["item_addr"])
                def show():
                    self.ent_sm_cur.delete(0, "end")
                    self.ent_sm_cur.insert(0, f"{r[6]:02X} ({r[6]})")
                self.ui(show)
                self._sm_refresh_row(n, r[6])
                self.log(f"服务菜单 N={n:03d} 读取: {r[6]:02X}")
            self.bg(job)

        def do_sm_write_selected(self):
            """写当前 N 的 371B 表字节；可选写后 SPAET。"""
            if not self.require_open(): return
            try:
                n = int(self.ent_sm_n.get())
                it = next(x for x in self.service_items if x["n"] == n)
                v = self._sm_parse_value(self.ent_sm_new.get())
            except Exception as e:
                messagebox.showwarning(APP_TITLE, f"参数错误: {e}"); return
            if not self._require_backup_ack():
                return
            apply_after = bool(self.var_sm_apply.get())
            if not messagebox.askyesno(
                    APP_TITLE,
                    f"确认写服务菜单 N={n:03d}（{it.get('label', '')}）？\n"
                    f"371B 表地址 0x{it['item_addr']:04X} = 0x{v:02X}\n"
                    "这会改变校准源值；特殊项还需 SPAET/SPN 才会应用。继续？"):
                return
            def job(cat):
                cat.spw_table(it["item_addr"], v)
                idx = it["item_addr"] - TABLE_START
                if 0 <= idx < len(self.cal_table):
                    self.cal_table = self.cal_table[:idx] + bytes([v]) + self.cal_table[idx + 1:]
                self._sm_refresh_row(n, v)
                self.log(f"服务菜单 N={n:03d} 已写 0x{v:02X}")
                if apply_after:
                    r = cat.spa_et()
                    self.log(f"SPAET: {hexs(r) if r else '无回包'}")
            self.bg(job)

        def do_sm_read_ns(self):
            """只读 FRAM[0x0344–0x0345] 的 16 位项目号 N 与 FRAM[0x0346] 子码 S。"""
            if not self.require_open(): return
            def job(cat):
                # FRAM[0x0344..0x0345] 是 16 位大端项目号 N（0x000–0x125），
                # FRAM[0x0346] 是子码 S。不能只读低字节，否则 N>0xFF 会显示错。
                rlo = cat.spr(0x0344)
                rhi = cat.spr(0x0345)
                rs = cat.spr(0x0346)
                n = (rlo[5] << 8) | rhi[5]
                sub = rs[5]
                self.ui(lambda: self.var_sm_ns.set(f"N=0x{n:03X} ({n}) / S=0x{sub:02X} ({sub})"))
                self.log(f"服务菜单 N/S: N=0x{n:03X} S=0x{sub:02X}")
            self.bg(job)

        def do_sm_spn_read(self):
            """SPN FF FF：读取 RAM 0xFFAB00 当前项目号（需服务模式门控）。"""
            if not self.require_open(): return
            def job(cat):
                r = cat.spn_read()
                self.log(f"SPN 读: {hexs(r)}")
                if len(r) >= 5:
                    self._q_append_text(self.txt_power, f"SPN 读: {hexs(r)}\n")
            self.bg(job)

        def do_sm_spn(self):
            """发送 SPN 选择/应用项目 N（会切换波段/模式）。"""
            if not self.require_open(): return
            try:
                n = int(self.ent_sm_n.get())
                if not 0 <= n <= 0x125:
                    raise ValueError
            except Exception:
                messagebox.showwarning(APP_TITLE, "SPN 的 N 必须是 0–293（0x000–0x125）"); return
            if not messagebox.askyesno(
                    APP_TITLE,
                    f"发送 SPN N={n:03d}？\n"
                    "它会选择/应用服务项目，并切换当前波段/模式；需要服务模式门控。\n"
                    "这不是只读命令。"):
                return
            def job(cat):
                r = cat.spn_write(n)
                self.log(f"SPN {n:03d}: {hexs(r) if r else '无回包'}")
            self.bg(job)

        def do_sm_spaet(self):
            """对当前特殊服务项发送 SPAET；普通项会回 ?;。"""
            if not self.require_open(): return
            if not messagebox.askyesno(APP_TITLE, "确认发送 SPAET？会触发设置表应用逻辑。"):
                return
            def job(cat):
                r = cat.spa_et()
                self.log(f"SPAET: {hexs(r) if r else '无回包'}")
            self.bg(job)

        def do_sm_spawe(self):
            """发送 SPAWE 写使能。"""
            if not self.require_open(): return
            def job(cat):
                r = cat.spa_we()
                self.log(f"SPAWE: {hexs(r) if r else '无回包'}")
            self.bg(job)

        # ---------------- Tab: 校准 ----------------
        def _tab_cal(self):
            f = ttk.Frame(self.nb, padding=10)
            self.nb.add(f, text="校准 / PE")
            top = ttk.Frame(f); top.pack(fill="x")
            ttk.Button(top, text="读取设置表(371B)", command=self.do_cal_read).pack(side="left", padx=4)
            ttk.Button(top, text="写入设置表(危险)", command=self.do_cal_write).pack(side="left", padx=4)
            ttk.Button(top, text="从备份文件载入…", command=self.do_cal_load).pack(side="left", padx=4)
            ttk.Button(top, text="保存为 .bin…", command=self.do_cal_save_bin).pack(side="left", padx=4)

            mid = ttk.Frame(f); mid.pack(fill="x", pady=6)
            self.var_cal_verify = tk.BooleanVar(value=True)
            ttk.Checkbutton(mid, text="写回后校验", variable=self.var_cal_verify).pack(side="left")
            self.var_cal_show_hex = tk.BooleanVar(value=True)
            ttk.Checkbutton(mid, text="显示十六进制", variable=self.var_cal_show_hex).pack(side="left", padx=10)
            ttk.Checkbutton(mid, text="我已备份 0–0x0490",
                            variable=self.var_backup_ack).pack(side="left", padx=10)
            ttk.Label(mid, text="（表基址 0xFFA948，SPw 地址 0x000A–0x017C）",
                      foreground="#666").pack(side="left")

            ttk.Label(f, text="设置/校准表 (371 字节):").pack(anchor="w")
            self.txt_cal = self._mktext(f, 14)
            self.txt_cal.pack(fill="both", expand=True)

            ttk.Separator(f, orient="horizontal").pack(fill="x", pady=6)
            ttk.Label(f, text="PE 参数 (0xFFABD1–0xFFABE2, 27 组选择子) + 原始写:").pack(anchor="w")
            g = ttk.Frame(f); g.pack(fill="x", pady=4)
            ttk.Button(g, text="读取全部 PE", command=self.do_pe_all).pack(side="left", padx=4)
            ttk.Label(g, text="原始写 PE(6字符):").pack(side="left", padx=(12, 2))
            self.ent_pe_raw = ttk.Entry(g, width=10)
            self.ent_pe_raw.pack(side="left")
            self.btn_pe_send = ttk.Button(g, text="发送", command=self.do_pe_write_raw)
            self.btn_pe_send.pack(side="left", padx=6)
            self.txt_pe = self._mktext(f, 10)
            self.txt_pe.pack(fill="both", expand=True, pady=(4, 0))

        def do_cal_read(self):
            if not self.require_open(): return
            def job(cat):
                def prog(a, b):
                    self.status(f"读取设置表 {a}/{b} …")
                tb = cat.read_settings_table(progress=prog, cancel=self.cancel)
                self.cal_table = tb
                self._render_cal(tb)
                self.log(f"设置表已读取: {len(tb)} 字节")
            self.bg(job)

        def _render_cal(self, tb: bytes):
            if threading.current_thread() is not threading.main_thread():
                self.ui(lambda: self._render_cal(tb))
                return
            self.txt_cal.delete("1.0", "end")
            if self.var_cal_show_hex.get():
                for off in range(0, len(tb), 16):
                    chunk = tb[off:off + 16]
                    addr = TABLE_START + off
                    self.txt_cal.insert("end", f"0x{addr:04X}: {hexs(chunk)}\n")
            else:
                self.txt_cal.insert("end", hexs(tb, sep=""))

        def do_cal_write(self):
            if not self.require_open(): return
            if not hasattr(self, "cal_table") or not self.cal_table:
                messagebox.showwarning(APP_TITLE, "请先读取或载入设置表")
                return
            if len(self.cal_table) != TABLE_LEN:
                messagebox.showerror(APP_TITLE,
                                     f"设置表长度 {len(self.cal_table)} != {TABLE_LEN}，拒绝写入")
                return
            if not self._require_backup_ack():
                return
            if not messagebox.askyesno(APP_TITLE,
                                       "确认把当前 371 字节设置表写回设备？\n这会改校准数据，风险极高。"):
                return
            verify = bool(self.var_cal_verify.get())
            def job(cat):
                def prog(a, b):
                    self.status(f"写回设置表 {a}/{b} …")
                n = restore_table(cat, self.cal_table, progress=prog,
                                  cancel=self.cancel, verify=verify)
                self.log(f"设置表写回完成: {n} 字节，校验={'通过' if verify else '跳过'}")
            self.bg(job)

        def do_cal_load(self):
            path = filedialog.askopenfilename(filetypes=[("JSON/二进制", "*.json *.bin"), ("全部", "*.*")])
            if not path:
                return
            try:
                if path.lower().endswith(".json"):
                    obj = json.load(open(path, encoding="utf-8"))
                    tb = bytes.fromhex(obj["data"]["settings_table"]["hex"])
                else:
                    tb = open(path, "rb").read()
                if len(tb) != TABLE_LEN:
                    if not messagebox.askyesno(APP_TITLE,
                                               f"长度 {len(tb)} != {TABLE_LEN}，仍要载入？"):
                        return
                self.cal_table = tb
                self._render_cal(tb)
                self.log(f"已载入设置表: {path}（{len(tb)}B）")
            except Exception as e:
                messagebox.showerror(APP_TITLE, f"载入失败: {e}")

        def do_cal_save_bin(self):
            if not hasattr(self, "cal_table") or not self.cal_table:
                messagebox.showwarning(APP_TITLE, "请先读取设置表"); return
            path = filedialog.asksaveasfilename(defaultextension=".bin")
            if not path:
                return
            open(path, "wb").write(self.cal_table)
            self.log(f"已保存设置表: {path}")

        def do_pe_all(self):
            if not self.require_open(): return
            def job(cat):
                lines = []
                ok = 0
                for s0 in PE_SEL:
                    for s1 in PE_SEL:
                        for m in PE_SEL:
                            try:
                                r = cat.pe_read(int(s0), int(s1), int(m))
                            except CatError as e:
                                lines.append(f"PE{s0}{s1}{m}: 错误 {e}\n")
                                continue
                            if r is None:
                                lines.append(f"PE{s0}{s1}{m}: ?;\n")
                            else:
                                ok += 1
                                lines.append(f"PE{s0}{s1}{m}: {r.decode('latin-1')}   ({hexs(r)})\n")
                lines.append(f"\n有效 {ok}/27\n")
                def show():
                    self.txt_pe.delete("1.0", "end")
                    self.txt_pe.insert("end", "".join(lines))
                    self.txt_pe.see("end")
                self.ui(show)
                self.log(f"PE 读取完成: 有效 {ok}/27")
            self.bg(job)

        def do_pe_write_raw(self):
            if not self.require_open(): return
            if not self._require_backup_ack(): return
            p = self.ent_pe_raw.get().strip().upper()
            if (len(p) != 6 or any(c not in "012" for c in p[:3])
                    or p[3] not in "+-" or any(c not in "0123456789ABCDEF" for c in p[4:])):
                messagebox.showwarning(APP_TITLE,
                                       "PE 写格式：PE<3位0/1/2><+或-><2位十六进制>，例如 PE000+0A")
                return
            if not messagebox.askyesno(APP_TITLE, f"确认发送 PE{p} ？会写校准。"):
                return
            def job(cat):
                r = cat.pe_write_raw(p)
                self._q_append_text(self.txt_pe, f"PE{p} -> {hexs(r) if r else '无回包'}\n")
            self.bg(job)

        # ---------------- Tab: PE 校准 ----------------
        def _tab_pe(self):
            """PE 校准页：27 组选择子，读取原始回包并按“符号+2hex”写入。"""
            f = ttk.Frame(self.nb, padding=10)
            self.nb.add(f, text="PE 校准")

            top = ttk.Frame(f); top.pack(fill="x", pady=3)
            ttk.Button(top, text="读取全部 PE", command=self.do_pe_read_all2).pack(side="left", padx=3)
            ttk.Button(top, text="读取选中", command=self.do_pe_read_one).pack(side="left", padx=3)
            ttk.Button(top, text="写入选中", command=self.do_pe_write_one).pack(side="left", padx=3)
            ttk.Checkbutton(top, text="我已备份 0–0x0490",
                            variable=self.var_backup_ack).pack(side="left", padx=8)

            cols = ("sel", "desc", "resp", "cur")
            self.pe_tree = ttk.Treeview(f, columns=cols, show="headings", height=15)
            for c, txt, width in (("sel", "选择子", 70), ("desc", "中文说明", 300),
                                  ("resp", "原始回包", 330), ("cur", "当前值", 100)):
                self.pe_tree.heading(c, text=txt)
                self.pe_tree.column(c, width=width, anchor="w")
            self.pe_tree.pack(fill="both", expand=True, pady=3)
            self.pe_tree.bind("<<TreeviewSelect>>", self.on_pe_select)
            for s0 in PE_SEL:
                for s1 in PE_SEL:
                    for m in PE_SEL:
                        sel = f"{s0}{s1}{m}"
                        self.pe_tree.insert("", "end", iid=f"pe{sel}", values=(
                            sel, self._pe_desc(sel), "", ""))

            box = ttk.LabelFrame(f, text="PE 写入（格式：PE<选择子><符号><2 位十六进制>）")
            box.pack(fill="x", pady=3)
            r = ttk.Frame(box); r.pack(fill="x", padx=6, pady=4)
            ttk.Label(r, text="选择子:").pack(side="left")
            self.ent_pe_sel = ttk.Entry(r, width=6); self.ent_pe_sel.pack(side="left", padx=4)
            ttk.Label(r, text="符号:").pack(side="left")
            self.cmb_pe_sym = ttk.Combobox(r, width=4, state="readonly", values=("+", "-"))
            self.cmb_pe_sym.set("+"); self.cmb_pe_sym.pack(side="left", padx=4)
            ttk.Label(r, text="值(2 hex):").pack(side="left")
            self.ent_pe_val = ttk.Entry(r, width=6); self.ent_pe_val.pack(side="left", padx=4)
            ttk.Label(r, text="当前回包:").pack(side="left")
            self.ent_pe_cur2 = ttk.Entry(r, width=34); self.ent_pe_cur2.pack(side="left", padx=4)

            note = ttk.Label(f, foreground="#a00", justify="left", wraplength=1050, text=(
                "PE 的 27 组选择子对应 0xFFABD1–0xFFABE2 参数区；选择子/位域尚未完全解码，"
                "本页只做原始读写。\n"
                "写入前必须记录当前回包；没有真机验证时不要批量改。写入格式为 6 字符，例如选择子 000、"
                "符号 +、值 0A → PE000+0A;。\n"
                "PE 写会直接改校准并可能写总线，风险高；请先备份 FRAM[0x0000–0x0490]。"))
            note.pack(anchor="w", pady=4)

        @staticmethod
        def _pe_desc(sel: str) -> str:
            """把 3 位选择子翻译成中文说明（不臆造未解码语义）。"""
            s0, s1, m = sel[0], sel[1], sel[2]
            return f"参数组 {s0} / 子项 {s1} / 模式 {m}（原始 PE；位域未完全解码）"

        def on_pe_select(self, _event=None):
            """选中 PE 行时填入选择子和当前回包。"""
            sel = self.pe_tree.selection()
            if not sel:
                return
            selstr = sel[0][2:]
            self.ent_pe_sel.delete(0, "end"); self.ent_pe_sel.insert(0, selstr)
            vals = self.pe_tree.item(sel[0], "values")
            self.ent_pe_cur2.delete(0, "end")
            self.ent_pe_cur2.insert(0, vals[2] if len(vals) > 2 else "")

        def _pe_fill_row(self, sel: str, text: str):
            """刷新某组 PE 的回包/当前值列。"""
            if threading.current_thread() is not threading.main_thread():
                self.ui(lambda: self._pe_fill_row(sel, text))
                return
            iid = f"pe{sel}"
            if not self.pe_tree.exists(iid):
                return
            desc = self._pe_desc(sel)
            vals = list(self.pe_tree.item(iid, "values"))
            vals[2] = text
            # PE 读回包格式：PE<3选><sign><2hex>; 例如 PE000+0A;
            cur = ""
            m = re.search(r"PE[012]{3}([0+-])([0-9A-Fa-f]{2});", text)
            if m:
                cur = f"{m.group(1)}{m.group(2).upper()}"
            vals[3] = cur
            self.pe_tree.item(iid, values=vals)

        def do_pe_read_all2(self):
            """逐个读取 27 组 PE，填表。"""
            if not self.require_open(): return
            def job(cat):
                ok = 0
                for s0 in PE_SEL:
                    for s1 in PE_SEL:
                        for m in PE_SEL:
                            sel = f"{s0}{s1}{m}"
                            try:
                                r = cat.pe_read(int(s0), int(s1), int(m))
                            except CatError as e:
                                self._pe_fill_row(sel, f"错误: {e}")
                                continue
                            if r is None:
                                self._pe_fill_row(sel, "?;")
                            else:
                                ok += 1
                                self._pe_fill_row(sel, r.decode("latin-1", "replace")
                                                  + "  " + hexs(r))
                self.log(f"PE 读取完成: 有效 {ok}/27")
            self.bg(job)

        def do_pe_read_one(self):
            """读取当前选择子的 PE。"""
            if not self.require_open(): return
            sel = self.ent_pe_sel.get().strip()
            if len(sel) != 3 or any(c not in "012" for c in sel):
                messagebox.showwarning(APP_TITLE, "选择子必须是 3 位 0/1/2"); return
            def job(cat):
                r = cat.pe_read(int(sel[0]), int(sel[1]), int(sel[2]))
                text = "?;" if r is None else r.decode("latin-1", "replace") + "  " + hexs(r)
                self._pe_fill_row(sel, text)
                def show():
                    self.ent_pe_cur2.delete(0, "end"); self.ent_pe_cur2.insert(0, text)
                self.ui(show)
                self.log(f"PE{sel}: {text}")
            self.bg(job)

        def do_pe_write_one(self):
            """按选择子+符号+2hex 写 PE。"""
            if not self.require_open(): return
            sel = self.ent_pe_sel.get().strip()
            sym = self.cmb_pe_sym.get().strip()
            val = self.ent_pe_val.get().strip().upper()
            if len(sel) != 3 or any(c not in "012" for c in sel):
                messagebox.showwarning(APP_TITLE, "选择子必须是 3 位 0/1/2"); return
            if sym not in ("+", "-"):
                messagebox.showwarning(APP_TITLE, "符号必须是 + 或 -"); return
            if len(val) != 2 or any(c not in "0123456789ABCDEF" for c in val):
                messagebox.showwarning(APP_TITLE, "值必须是 2 位十六进制"); return
            payload = sel + sym + val
            if not self._require_backup_ack(): return
            if not messagebox.askyesno(APP_TITLE, f"确认发送 PE{payload}; ？会写校准。"):
                return
            def job(cat):
                r = cat.pe_write_raw(payload)
                self.log(f"PE{payload}: {hexs(r) if r else '无回包'}")
            self.bg(job)

        # ---------------- Tab: 备份 ----------------
        def _tab_backup(self):
            f = ttk.Frame(self.nb, padding=10)
            self.nb.add(f, text="完全备份")
            ttk.Label(f, text="勾选要采集的项（完全备份含全部项）:",
                      font=("", 10, "bold")).pack(anchor="w")

            g = ttk.Frame(f); g.pack(fill="x", pady=6)
            self.bk = {}
            def ck(key, text, row, col, default=True):
                v = tk.BooleanVar(value=default)
                cb = ttk.Checkbutton(g, text=text, variable=v)
                cb.grid(row=row, column=col, sticky="w", padx=6)
                if key in BACKUP_ITEM_TIPS:
                    ToolTip(cb, BACKUP_ITEM_TIPS[key])
                self.bk[key] = v
            ck("ve", "VE 版本", 0, 0);            ck("fd", "FD 三通道", 0, 1)
            ck("kt", "KT 状态", 0, 2);            ck("pe", "PE 全表(27)", 0, 3)
            ck("table", "设置/校准表(371B)", 1, 0); ck("spard", "SPARD 原始", 1, 1)
            ck("e0", "E0 (229B)", 1, 2);          ck("e8", "E8 (10573B)", 1, 3)
            ck("jp", "JP 寄存器", 2, 0);          ck("mem", "外部 FRAM (SPR)", 2, 1)

            rng = ttk.Frame(f); rng.pack(fill="x", pady=(0, 4))
            ttk.Label(rng, text="外部 FRAM 范围:").pack(side="left")
            self.ent_mem_s = ttk.Entry(rng, width=8); self.ent_mem_s.insert(0, "0000"); self.ent_mem_s.pack(side="left", padx=4)
            ttk.Label(rng, text="–").pack(side="left")
            self.ent_mem_e = ttk.Entry(rng, width=8); self.ent_mem_e.insert(0, "7FFF"); self.ent_mem_e.pack(side="left", padx=4)
            ttk.Label(rng, text="（0x0000–0x7FFF ≈ 16384 次读，低速下需数分钟）",
                      foreground="#a60").pack(side="left", padx=8)

            btns = ttk.Frame(f); btns.pack(fill="x", pady=6)
            ttk.Button(btns, text="① 开始完全备份 → JSON", command=self.do_backup).pack(side="left", padx=4)
            ttk.Button(btns, text="② 载入备份并预览", command=self.do_backup_load).pack(side="left", padx=4)
            ttk.Button(btns, text="③ 恢复设置表(危险)", command=self.do_restore_table).pack(side="left", padx=4)
            ttk.Button(btns, text="④ 恢复外部 FRAM(极危险)", command=self.do_restore_fram).pack(side="left", padx=4)

            self.txt_bk = self._mktext(f, 18)
            self.txt_bk.pack(fill="both", expand=True)

        def do_backup(self):
            if not self.require_open(): return
            path = filedialog.asksaveasfilename(defaultextension=".json",
                                                initialfile="ftdx10_backup.json",
                                                filetypes=[("JSON", "*.json")])
            if not path:
                return
            opts = {k: v.get() for k, v in self.bk.items()}
            try:
                opts["mem_start"] = int(self.ent_mem_s.get().strip(), 16)
                opts["mem_end"] = int(self.ent_mem_e.get().strip(), 16)
            except ValueError:
                messagebox.showwarning(APP_TITLE, "外部 FRAM 范围需为十六进制"); return

            def job(cat):
                def prog(i, n, msg, *a):
                    tail = f" {a[0]}/{a[1]}" if a else ""
                    self.status(f"{msg}{tail}  [{i + 1}/{n}]")
                obj = make_backup(cat, opts, progress=prog, cancel=self.cancel)
                with open(path, "w", encoding="utf-8") as fp:
                    json.dump(obj, fp, ensure_ascii=False, indent=1)
                self.last_backup = obj
                self._show_backup(obj)
                self.log(f"备份完成: {path}")
            self.bg(job)

        def _show_backup(self, obj):
            if threading.current_thread() is not threading.main_thread():
                self.ui(lambda: self._show_backup(obj))
                return
            self.txt_bk.delete("1.0", "end")
            d = obj.get("data", {})
            self.txt_bk.insert("end", json.dumps(obj.get("transport", {}), ensure_ascii=False) + "\n")
            self.txt_bk.insert("end", f"created: {obj.get('created')}\n\n")
            for k, v in d.items():
                if isinstance(v, dict) and "hex" in v:
                    self.txt_bk.insert("end", f"{k}: {v.get('bytes', v.get('len', len(v['hex']) // 2))} 字节\n")
                elif isinstance(v, dict) and "raw_hex" in v:
                    self.txt_bk.insert("end", f"{k}: {v['raw_hex']}\n")
                elif isinstance(v, dict) and "error" in v:
                    self.txt_bk.insert("end", f"{k}: 错误 {v['error']}\n")
                else:
                    self.txt_bk.insert("end", f"{k}: {type(v).__name__}\n")
            self.txt_bk.see("end")

        def do_backup_load(self):
            path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
            if not path:
                return
            try:
                obj = json.load(open(path, encoding="utf-8"))
                if obj.get("format") != BACKUP_FORMAT:
                    if not messagebox.askyesno(APP_TITLE, "格式标记不匹配，仍要载入？"):
                        return
                self.last_backup = obj
                self._show_backup(obj)
                st = obj.get("data", {}).get("settings_table", {})
                if "hex" in st:
                    self.cal_table = bytes.fromhex(st["hex"])
                    self._render_cal(self.cal_table)
                self.log(f"已载入备份: {path}")
            except Exception as e:
                messagebox.showerror(APP_TITLE, f"载入失败: {e}")

        def do_restore_table(self):
            if not self.require_open(): return
            if not self.last_backup:
                messagebox.showwarning(APP_TITLE, "请先载入备份文件"); return
            st = self.last_backup.get("data", {}).get("settings_table", {})
            if "hex" not in st:
                messagebox.showwarning(APP_TITLE, "备份中没有设置表"); return
            try:
                tb = bytes.fromhex(st["hex"])
            except ValueError as e:
                messagebox.showerror(APP_TITLE, f"备份设置表 hex 无效: {e}"); return
            if len(tb) != TABLE_LEN:
                messagebox.showerror(APP_TITLE, f"备份设置表长度 {len(tb)} != {TABLE_LEN}，拒绝恢复")
                return
            if not self._require_backup_ack():
                return
            if not messagebox.askyesno(APP_TITLE,
                                       "将把备份中的 371 字节设置表写回设备。\n这会改校准数据，可能导致异常。继续？"):
                return
            def job(cat):
                def prog(a, b): self.status(f"恢复设置表 {a}/{b} …")
                n = restore_table(cat, tb, progress=prog, cancel=self.cancel, verify=True)
                self.log(f"设置表恢复完成: {n} 字节，已校验")
            self.bg(job)

        def do_restore_fram(self):
            if not self.require_open(): return
            if not self.last_backup:
                messagebox.showwarning(APP_TITLE, "请先载入备份文件"); return
            mm = self.last_backup.get("data", {}).get("mem", {})
            if "hex" not in mm:
                messagebox.showwarning(APP_TITLE, "备份中没有外部 FRAM 数据"); return
            try:
                start = int(mm["start"], 16)
                mem = bytes.fromhex(mm["hex"])
            except (KeyError, ValueError) as e:
                messagebox.showerror(APP_TITLE, f"备份 FRAM 数据无效: {e}"); return
            if not (0 <= start <= FRAM_END and start + len(mem) <= 0x8000):
                messagebox.showerror(APP_TITLE, "备份 FRAM 地址/长度越界，拒绝恢复")
                return
            if not self._require_backup_ack():
                return
            if not messagebox.askyesno(APP_TITLE,
                                       "将把外部 FRAM 数据写回设备！\n这极其危险，可能导致无法开机。\n仍要继续？"):
                return
            if not messagebox.askyesno(APP_TITLE, "再次确认：写入外部 FRAM？"):
                return
            def job(cat):
                def prog(a, b): self.status(f"恢复外部 FRAM {a}/{b} …")
                n = restore_fram(cat, start, mem, progress=prog, cancel=self.cancel)
                self.log(f"外部 FRAM 恢复完成: {n} 字节")
            self.bg(job)

        # ---------------- Tab: 终端 ----------------
        def _tab_term(self):
            f = ttk.Frame(self.nb, padding=10)
            self.nb.add(f, text="终端")
            r1 = ttk.Frame(f); r1.pack(fill="x", pady=3)
            ttk.Label(r1, text="ASCII 命令:").pack(side="left")
            self.ent_ascii = ttk.Entry(r1, width=44)
            self.ent_ascii.pack(side="left", padx=4)
            self.btn_term_ascii = ttk.Button(r1, text="发送", command=self.do_ascii)
            self.btn_term_ascii.pack(side="left")
            ttk.Label(r1, text="(不含结尾 ; )").pack(side="left", padx=4)

            r2 = ttk.Frame(f); r2.pack(fill="x", pady=3)
            ttk.Label(r2, text="原始字节(hex):").pack(side="left")
            self.ent_hex = ttk.Entry(r2, width=44)
            self.ent_hex.pack(side="left", padx=4)
            self.btn_term_hex = ttk.Button(r2, text="发送", command=self.do_hex)
            self.btn_term_hex.pack(side="left")
            self.var_auto_semi = tk.BooleanVar(value=True)
            ttk.Checkbutton(r2, text="自动补 ';'", variable=self.var_auto_semi).pack(side="left", padx=6)

            r3 = ttk.Frame(f); r3.pack(fill="x", pady=3)
            ttk.Label(r3, text="读取:").pack(side="left")
            self.ent_readn = ttk.Entry(r3, width=8); self.ent_readn.insert(0, "256"); self.ent_readn.pack(side="left", padx=4)
            ttk.Label(r3, text="字节 / 或读到分号").pack(side="left")
            ttk.Button(r3, text="纯读", command=self.do_pureread).pack(side="left", padx=6)
            ttk.Button(r3, text="清空记录", command=lambda: self.txt_term.delete("1.0", "end")).pack(side="left")

            ttk.Label(f, text="帧构造助手(SP 子协议):").pack(anchor="w", pady=(6, 0))
            r4 = ttk.Frame(f); r4.pack(fill="x", pady=3)
            ttk.Label(r4, text="SP body(hex):").pack(side="left")
            self.ent_spbody = ttk.Entry(r4, width=30)
            self.ent_spbody.pack(side="left", padx=4)
            ttk.Button(r4, text="构造→原始框", command=self.do_build_sp).pack(side="left")
            ttk.Label(r4, text="例: 57 0123 4567 ⟶ SPW 0x0123=0x45,0x67",
                      foreground="#666").pack(side="left", padx=6)

            self.txt_term = self._mktext(f, 20)
            self.txt_term.pack(fill="both", expand=True, pady=(6, 0))

        def do_ascii(self):
            if not self.require_open(): return
            cmd = self.ent_ascii.get()
            def job(cat):
                r = cat._ascii(cmd, expect=None, timeout=1.0)
                self._q_append_text(self.txt_term, f"> {cmd};\n< {hexs(r)}\n")
            self.bg(job)

        def do_hex(self):
            if not self.require_open(): return
            try:
                data = bytes.fromhex(self.ent_hex.get().replace(" ", ""))
            except ValueError as e:
                messagebox.showwarning(APP_TITLE, f"hex 解析失败: {e}"); return
            if self.var_auto_semi.get() and not data.endswith(b";"):
                data += b";"
            def job(cat):
                cat.link.write(data)
                r = cat.link.drain(1.0)
                self._q_append_text(self.txt_term, f"> {data.hex()}\n< {hexs(r)}\n")
            self.bg(job)

        def do_pureread(self):
            if not self.require_open(): return
            try:
                n = int(self.ent_readn.get())
            except ValueError:
                n = 256
            def job(cat):
                r = cat.link.read_exact(n, timeout=cat._t_for(n, 1.0)) if n > 0 else cat.link.drain(1.0)
                self._q_append_text(self.txt_term, f"< ({len(r)}B) {hexs(r)}\n")
            self.bg(job)

        def do_build_sp(self):
            try:
                body = bytes.fromhex(self.ent_spbody.get().replace(" ", ""))
            except ValueError as e:
                messagebox.showwarning(APP_TITLE, f"hex 解析失败: {e}"); return
            try:
                frame = sp_frame(body)
            except CatError as e:
                messagebox.showwarning(APP_TITLE, str(e)); return
            self.ent_hex.delete(0, "end")
            self.ent_hex.insert(0, frame.hex())
            self.txt_term.insert("end", f"SP frame = {frame.hex()}   (校验 {frame[-2]:02X})\n")
            self.txt_term.see("end")

    app = App()
    app.mainloop()


# ============================================================ CLI
def build_argparser():
    p = argparse.ArgumentParser(
        description="FTDX10 隐藏 CAT 命令工具（GUI + 无界面备份/恢复）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-p", "--port", help="串口，例如 /dev/ttyUSB0")
    p.add_argument("-b", "--baud", type=int, default=38400)
    p.add_argument("--backup", metavar="JSON", help="无界面：完全备份到 JSON")
    p.add_argument("--no-mem", "--no-fram", dest="no_mem", action="store_true",
                   help="备份时跳过外部 FRAM")
    p.add_argument("--mem-start", "--fram-start", dest="mem_start", default="0000",
                   help="外部 FRAM 起始地址（hex）")
    p.add_argument("--mem-end", "--fram-end", dest="mem_end", default="7FFF",
                   help="外部 FRAM 结束地址（hex）")
    p.add_argument("--restore", metavar="JSON", help="无界面：从 JSON 恢复(默认只恢复设置表)")
    p.add_argument("--restore-mem", "--restore-fram", dest="restore_fram",
                   action="store_true", help="恢复时同时写回外部 FRAM(极危险)")
    p.add_argument("--yes", action="store_true", help="跳过交互确认")
    return p


def cli_main(argv=None) -> int:
    args = build_argparser().parse_args(argv)
    if args.backup or args.restore:
        if not args.port:
            print("错误: 需要 -p/--port", file=sys.stderr)
            return 2
        if serial is None:
            print("错误: 未安装 pyserial", file=sys.stderr)
            return 2
        link = SerialLink()
        link.open(args.port, args.baud)
        cat = HiddenCat(link, log=lambda m: print(m))
        try:
            if args.backup:
                opts = dict(ve=True, fd=True, kt=True, pe=True, table=True,
                            spard=True, e0=True, e8=True, jp=True,
                            mem=not args.no_mem,
                            mem_start=int(args.mem_start, 16),
                            mem_end=int(args.mem_end, 16))
                obj = make_backup(cat, opts,
                                  progress=lambda i, n, m, *a: print(f"  [{i+1}/{n}] {m}"))
                with open(args.backup, "w", encoding="utf-8") as fp:
                    json.dump(obj, fp, ensure_ascii=False, indent=1)
                print(f"备份完成: {args.backup}")
            if args.restore:
                obj = json.load(open(args.restore, encoding="utf-8"))
                tb = obj["data"].get("settings_table", {})
                if "hex" not in tb:
                    print("备份中没有设置表", file=sys.stderr)
                    return 3
                if not args.yes:
                    ans = input("将写回 371 字节设置表，输入 yes 继续: ")
                    if ans.strip().lower() != "yes":
                        print("已取消"); return 1
                table = bytes.fromhex(tb["hex"])
                n = restore_table(cat, table, verify=True,
                                  progress=lambda a, b: print(f"  写 {a}/{b}"))
                print(f"设置表恢复完成: {n} 字节")
                if args.restore_fram:
                    mm = obj["data"].get("mem", {})
                    if "hex" in mm:
                        if not args.yes:
                            ans = input("将写回外部 FRAM(极危险)，输入 yes 继续: ")
                            if ans.strip().lower() != "yes":
                                print("已取消"); return 1
                        restore_fram(cat, int(mm["start"], 16), bytes.fromhex(mm["hex"]),
                                    progress=lambda a, b: print(f"  写 {a}/{b}"))
                        print("外部 FRAM 恢复完成")
        finally:
            link.close()
        return 0

    # 默认启动 GUI
    try:
        launch_gui()
    except Exception as e:
        print(f"无法启动 GUI: {e}", file=sys.stderr)
        print("可改用无界面模式：--backup / --restore", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli_main())
