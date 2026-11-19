"""PNG 头部校验：设备层只确认截图是 PNG 并读出尺寸，不做解码。"""
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


def png_size(data: bytes) -> tuple[int, int] | None:
    """返回 PNG 的宽高；不是有效 PNG 时返回 None。"""
    if len(data) < 24 or not data.startswith(PNG_SIGNATURE):
        return None
    width = int.from_bytes(data[16:20], 'big')
    height = int.from_bytes(data[20:24], 'big')
    if width <= 0 or height <= 0:
        return None
    return width, height
