"""
fake machine package.
machine包只存在于嵌入式Micropython中,这个包只是为了让代码不报错。
"""
class UART:
    def __init__(self, *args, **kwargs):
        pass

class FPIOA:
    UART2_RXD = 0
    UART2_TXD = 1

    def set_function(self, *args, **kwargs):
        pass