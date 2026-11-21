"""画面感知的错误类型。"""


class PerceptionError(RuntimeError):
    """帧无法解码、模板缺失或识别器不可用。"""


class StaleFrameError(PerceptionError):
    """请求的帧已经不是当前画面；结果只能进缓存，不能驱动动作。"""
