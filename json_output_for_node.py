"""
Python:用于烧录至K320mini的GNSS模块NMEA信息解算与JSON文件包的输出
"""

from machine import UART, FPIOA
import time

# ===== UART2: TX2(IO11) / RX2(IO12) =====
fpioa = FPIOA()
fpioa.set_function(12, FPIOA.UART2_RXD)
fpioa.set_function(11, FPIOA.UART2_TXD)

uart = UART(2, 115200)  # 按你的 GNSS 波特率设置

state = {
    "utc": None,
    "lat": None,
    "lon": None,
    "alt_m": None,
    "fix_quality": None,
    "sv_used": None,
    "hdop": None,
    "pdop": None,
    "vdop": None,
    "snr_map": {},       # key: "GP02"/"BD45"/...
    "snr_count": 0,
    "snr_avg": None,
    "snr_min": None,
    "snr_max": None,
}

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
    snrs = []
    for _, v in state["snr_map"].items():
        if v is not None:
            snrs.append(v)
    if not snrs:
        state["snr_count"] = 0
        state["snr_avg"] = None
        state["snr_min"] = None
        state["snr_max"] = None
        return
    s = 0
    mn = snrs[0]
    mx = snrs[0]
    for v in snrs:
        s += v
        if v < mn: mn = v
        if v > mx: mx = v
    state["snr_count"] = len(snrs)
    state["snr_avg"] = s / len(snrs)
    state["snr_min"] = mn
    state["snr_max"] = mx

def parse_gga(fields):
    # $GNGGA,time,lat,N,lon,E,quality,numSV,hdop,alt,M,...
    state["lat"] = nmea_to_decimal(fields[2], fields[3]) if len(fields) > 3 else state["lat"]
    state["lon"] = nmea_to_decimal(fields[4], fields[5]) if len(fields) > 5 else state["lon"]
    state["fix_quality"] = safe_int(fields[6]) if len(fields) > 6 else state["fix_quality"]
    state["sv_used"] = safe_int(fields[7]) if len(fields) > 7 else state["sv_used"]
    state["hdop"] = safe_float(fields[8]) if len(fields) > 8 else state["hdop"]
    state["alt_m"] = safe_float(fields[9]) if len(fields) > 9 else state["alt_m"]

def parse_gsa(fields):
    # 稳健地从末尾找 PDOP/HDOP/VDOP（防止末尾带 systemId）
    floats = []
    for i in range(len(fields)-1, 0, -1):
        v = safe_float(fields[i])
        if v is not None:
            floats.append(v)
            if len(floats) == 3:
                break
    if len(floats) == 3:
        state["vdop"] = floats[0]
        # floats[1] 是 HDOP（如要以 GSA 为准可覆盖）
        state["pdop"] = floats[2]

def parse_gsv(fields, talker):
    prefix = talker[0:2]  # GP/BD/GN/GQ
    i = 4
    while i + 3 < len(fields):
        prn = fields[i]
        snr = safe_int(fields[i+3])  # 可能为空
        if prn:
            key = prefix + prn.zfill(2)
            state["snr_map"][key] = snr
        i += 4
    update_snr_stats()

def handle_sentence(line: str):
    line = line.strip()
    if not line.startswith('$'):
        return
    if not nmea_checksum_ok(line):
        return
    star = line.rfind('*')
    body = line[1:star]
    fields = body.split(',')
    talker = fields[0]

    if talker.endswith("GGA"):
        parse_gga(fields)
    elif talker.endswith("GSA"):
        parse_gsa(fields)
    elif talker.endswith("GSV"):
        parse_gsv(fields, talker)


def emit_json_line():
    # 只有经纬度有效才输出
    if state["lat"] is None or state["lon"] is None:
        return

    # 用 null 表示 None，保证 JSON 合法
    def jnum(v, fmt=None):
        if v is None:
            return "null"
        if fmt:
            return (fmt % v)
        return str(v)

    # 你要的字段：lat/lon/alt/sv/snr/dop
    # snr：这里给 avg/min/max/count；你如需每颗卫星SNR也可扩展输出
    line = (
        '{"lat":%0.8f,"lon":%0.8f,"alt":%s,"sv":%s,'
        '"snr":{"count":%s,"avg":%s,"min":%s,"max":%s},'
        '"dop":{"pdop":%s,"hdop":%s,"vdop":%s}}'
        % (
            state["lat"], state["lon"],
            jnum(state["alt_m"], "%0.2f"),
            jnum(state["sv_used"]),
            jnum(state["snr_count"]),
            jnum(state["snr_avg"], "%0.2f"),
            jnum(state["snr_min"]),
            jnum(state["snr_max"]),
            jnum(state["pdop"], "%0.2f"),
            jnum(state["hdop"], "%0.2f"),
            jnum(state["vdop"], "%0.2f"),
        )
    )
    print(line)

# ===== 串口行缓冲读取 =====
buf = b""
last_emit = time.ticks_ms()

print("NMEA -> JSON running...")

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

    # 每 1 秒输出一行 JSON（你可改成 200ms）
    now = time.ticks_ms()
    if time.ticks_diff(now, last_emit) > 1000:
        emit_json_line()
        last_emit = now

    time.sleep_ms(20)
