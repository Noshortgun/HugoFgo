"""画面感知适配器：所有字段都来自当前帧的图像证据。"""
from executor.perception.errors import PerceptionError, StaleFrameError
from executor.perception.observation import (
    BasePage,
    CommandCardIdentity,
    Control,
    ControlKind,
    Field,
    FieldStatus,
    Observation,
    Overlay,
    SupportCandidate,
    Variant,
)
from executor.perception.recognizer import Perception

__all__ = [
    'BasePage',
    'CommandCardIdentity',
    'Control',
    'ControlKind',
    'Field',
    'FieldStatus',
    'Observation',
    'Overlay',
    'Perception',
    'PerceptionError',
    'StaleFrameError',
    'SupportCandidate',
    'Variant',
]
