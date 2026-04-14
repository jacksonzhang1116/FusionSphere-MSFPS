'''
实验名称：GNSS 串口数据输出（UART2）
实验平台：01Studio CanMV K230 mini
'''

from machine import UART, FPIOA
import time

# ================= IO 映射 =================
fpioa = FPIOA()

# TX2 / RX2 接口：TX2(IO11) RX2(IO12)
fpioa.set_function(12, FPIOA.UART2_RXD)   # RX2
fpioa.set_function(11, FPIOA.UART2_TXD)   # TX2（可选）

# ================= UART 初始化 =================
# ⚠️ 关键：只传固件一定支持的参数
uart = UART(2, 115200)

print("UART2 GNSS ready, waiting for data...")

# ================= 主循环 =================
while True:
    data = uart.read()
    if data:
        # GNSS 多为 ASCII
        try:
            print(data.decode('utf-8'), end='')
        except:
            print(data)
    time.sleep_ms(100)
