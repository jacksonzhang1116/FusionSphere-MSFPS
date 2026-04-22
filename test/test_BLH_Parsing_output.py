"""
用于测试GNSS模块的卫星信号接收功,可处理GGA、GSA、GSV
打印,UTC时间、经纬度、大地高、DOPs、SNR
    print("---- GNSS SUMMARY ----")
    print("UTC:", state["utc"], " FixQ:", state["fix_quality"])
    print("Lat/Lon:", lat, lon)
    print("Alt(m):", alt, " SV:", sv)
    print("DOP: PDOP=", pdop, " HDOP=", hdop, " VDOP=", vdop)
    print("SNR: count=", c, " avg=", avg, " min=", mn, " max=", mx)
    print("----------------------")

"""

from machine import UART, FPIOA
import time

# ====== UART2: TX2(IO11) / RX2(IO12) ======
fpioa = FPIOA()
fpioa.set_function(12, FPIOA.UART2_RXD)   # RX2 = IO12
fpioa.set_function(11, FPIOA.UART2_TXD)   # TX2 = IO11（可不接）

# 打开UART接口，注意只传入接口号和波特率
uart = UART(2, 115200)   # 若你的模块是 9600，就改成 9600

# ====== 全局状态（持续更新） ======
state = {
    "utc": None,
    "lat": None,
    "lon": None,
    "alt_m": None,
    "fix_quality": None,
    "sv_used": None,      # GGA 的卫星数（参与解算/跟踪数，依模块定义）
    "hdop": None,

    "pdop": None,         # GSA
    "vdop": None,         # GSA

    # SNR 汇总（来自 GSV）
    "snr_count": 0,
    "snr_avg": None,
    "snr_max": None,
    "snr_min": None,

    # 存每颗卫星的最新 SNR（key: "GP02", "BD45" 之类）
    "snr_map": {},
}

# ====== NMEA 工具函数 ======
def nmea_checksum_ok(sentence: str) -> bool:
    if not sentence or sentence[0] != '$' or '*' not in sentence:
        return False
    try:
        star = sentence.rfind('*')
        data = sentence[1:star]
        given = sentence[star+1:star+3]
        if len(given) != 2:
            return False
        cs = 0
        for ch in data:
            cs ^= ord(ch)
        return ("%02X" % cs) == given.upper()
    except:
        return False

def nmea_to_decimal(degmin: str, hemi: str):
    if not degmin:
        return None
    try:
        v = float(degmin)
        deg = int(v // 100)
        minutes = v - deg * 100
        dec = deg + minutes / 60.0
        if hemi in ('S', 'W'):
            dec = -dec
        return dec
    except:
        return None

def parse_hhmmss(ss: str):
    if not ss or len(ss) < 6:
        return None
    try:
        hh = int(ss[0:2])
        mm = int(ss[2:4])
        sec = float(ss[4:])
        return (hh, mm, sec)
    except:
        return None

def safe_float(x):
    try:
        return float(x) if x not in (None, "") else None
    except:
        return None

def safe_int(x):
    try:
        return int(x) if x not in (None, "") else None
    except:
        return None

def update_snr_stats():
    # 只统计有数值的 SNR
    snrs = []
    for k, v in state["snr_map"].items():
        if v is not None:
            snrs.append(v)
    if not snrs:
        state["snr_count"] = 0
        state["snr_avg"] = None
        state["snr_max"] = None
        state["snr_min"] = None
        return
    state["snr_count"] = len(snrs)
    s = 0
    mn = snrs[0]
    mx = snrs[0]
    for v in snrs:
        s += v
        if v < mn: mn = v
        if v > mx: mx = v
    state["snr_avg"] = s / len(snrs)
    state["snr_min"] = mn
    state["snr_max"] = mx

# ====== 语句解析 ======
def parse_gga(fields):
    # $GNGGA,time,lat,N,lon,E,quality,numSV,hdop,alt,M,sep,M,,*CS
    state["utc"] = parse_hhmmss(fields[1]) if len(fields) > 1 else state["utc"]
    state["lat"] = nmea_to_decimal(fields[2], fields[3]) if len(fields) > 3 else state["lat"]
    state["lon"] = nmea_to_decimal(fields[4], fields[5]) if len(fields) > 5 else state["lon"]

    state["fix_quality"] = safe_int(fields[6]) if len(fields) > 6 else state["fix_quality"]
    state["sv_used"] = safe_int(fields[7]) if len(fields) > 7 else state["sv_used"]
    state["hdop"] = safe_float(fields[8]) if len(fields) > 8 else state["hdop"]
    state["alt_m"] = safe_float(fields[9]) if len(fields) > 9 else state["alt_m"]

def parse_gsa(fields):
    # $GNGSA, A/M , fixType, sat1..sat12, PDOP, HDOP, VDOP, sysId*CS(有些模块有)
    # PDOP/HDOP/VDOP 通常是倒数第 3、2、1 个字段（去掉*CS后已拆好）
    # 但不同厂商可能带系统ID在末尾，导致最后一个是 sysId
    # 我们做一个“从后往前找连续三个可转 float 的值”的稳健解析
    floats = []
    for i in range(len(fields)-1, 0, -1):
        v = safe_float(fields[i])
        if v is not None:
            floats.append(v)
            if len(floats) == 3:
                break
        else:
            # 遇到非空但不可解析的，继续找
            continue

    if len(floats) == 3:
        # floats 顺序是从后往前采的： [last, prev, prevprev]
        vdop = floats[0]
        hdop = floats[1]
        pdop = floats[2]
        state["pdop"] = pdop
        state["vdop"] = vdop
        # state["hdop"]：以 GGA 为主；如你希望以 GSA 为主，可取消注释：
        # state["hdop"] = hdop

def parse_gsv(fields, talker):
    """
    GSV: 每颗卫星 4 字段：PRN, elev, az, SNR
    示例：$GPGSV,3,1,09,02,39,164,29,04,24,213,23,...
    """
    # talker 前两位用于构造 key（GP/BD/GN/GQ等）
    prefix = talker[0:2]  # "GP" / "BD" / "GN" / "GQ"
    # 从第4个字段开始（index=4）按 4 个一组
    i = 4
    while i + 3 < len(fields):
        prn = fields[i]
        snr = safe_int(fields[i+3])
        if prn:
            key = prefix + prn.zfill(2)  # 统一两位，方便查看
            # snr 可能为空（无信号），我们也记录为 None
            state["snr_map"][key] = snr
        i += 4

    update_snr_stats()

def parse_txt(fields):
    msg = fields[4] if len(fields) > 4 else ""
    # 如果你希望看到天线状态提示，打开下一行：
    print("[TXT]", msg)

def handle_sentence(line: str):
    line = line.strip()
    if not line.startswith('$'):
        return
    if not nmea_checksum_ok(line):
        return

    star = line.rfind('*')
    body = line[1:star]
    fields = body.split(',')
    talker = fields[0]  # 如 GNGGA / GPGSV / BDGSV / GNGSA ...

    if talker.endswith("GGA"):
        parse_gga(fields)
    elif talker.endswith("GSA"):
        parse_gsa(fields)
    elif talker.endswith("GSV"):
        parse_gsv(fields, talker)
    elif talker.endswith("TXT"):
        parse_txt(fields)
    else:
        pass

# ====== 串口行缓冲读取 ======
buf = b""
last_print = time.ticks_ms()

def print_summary():
    lat = state["lat"]
    lon = state["lon"]
    alt = state["alt_m"]
    sv = state["sv_used"]
    hdop = state["hdop"]
    pdop = state["pdop"]
    vdop = state["vdop"]

    # SNR
    c = state["snr_count"]
    avg = state["snr_avg"]
    mn = state["snr_min"]
    mx = state["snr_max"]

    # 统一输出：没有值就打印 None
    print("---- GNSS SUMMARY ----")
    print("UTC:", state["utc"], " FixQ:", state["fix_quality"])
    print("Lat/Lon:", lat, lon)
    print("Alt(m):", alt, " SV:", sv)
    print("DOP: PDOP=", pdop, " HDOP=", hdop, " VDOP=", vdop)
    print("SNR: count=", c, " avg=", avg, " min=", mn, " max=", mx)
    print("----------------------")

print("NMEA solver running...")

while True:
    data = uart.read()
    if data:
        buf += data
        while True:
            idx = buf.find(b'\n')
            if idx < 0:
                break
            line = buf[:idx+1]
            buf = buf[idx+1:]
            try:
                s = line.decode('utf-8', 'ignore')
                handle_sentence(s)
            except:
                pass

    # 每 1 秒打印一次汇总（你可改 500ms 或 2s）
    now = time.ticks_ms()
    if time.ticks_diff(now, last_print) > 1000:
        print_summary()
        last_print = now

    time.sleep_ms(20)
