"""画面感知适配器：同步快路径 + 按需后台 OCR。"""
from executor.perception.errors import PerceptionError, StaleFrameError
from executor.perception.observation import (
    BasePage,
    Control,
    ControlKind,
    Field,
    FieldStatus,
    Observation,
    Overlay,
    Variant,
)
from executor.perception.recognizer import Perception

__all__ = [
    'BasePage',
    'Control',
    'ControlKind',
    'Field',
    'FieldStatus',
    'Observation',
    'Overlay',
    'Perception',
    'PerceptionError',
    'StaleFrameError',
    'Variant',
]
