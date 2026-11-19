"""用 Python 标准库读取 JSON Schema 子集；结构契约只有 schemas/job-v1.schema.json 一份。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from executor.domain.model import Diagnostic

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / 'schemas' / 'job-v1.schema.json'
JOB_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding='utf-8'))


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    return left == right


def _matches_type(value: Any, expected: str) -> bool:
    if expected == 'object':
        return isinstance(value, dict)
    if expected == 'array':
        return isinstance(value, list)
    if expected == 'string':
        return isinstance(value, str)
    if expected == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == 'integer':
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == 'boolean':
        return isinstance(value, bool)
    if expected == 'null':
        return value is None
    return True


def _resolve_ref(reference: str) -> dict[str, Any]:
    target: Any = JOB_SCHEMA
    for part in reference.removeprefix('#/').split('/'):
        target = target[part.replace('~1', '/').replace('~0', '~')]
    return target


def _location(path: str) -> tuple[int | None, int | None]:
    wave_match = re.search(r'^\$\.waves\[(\d+)\]', path)
    turn_match = re.search(r'\.turns\[(\d+)\]', path)
    return (
        int(wave_match.group(1)) + 1 if wave_match else None,
        int(turn_match.group(1)) + 1 if turn_match else None,
    )


def _error(diagnostics: list[Diagnostic], path: str, message: str) -> None:
    wave, turn = _location(path)
    diagnostics.append(Diagnostic(path=path, message=message, wave=wave, turn=turn))


def _constraint_error(diagnostics: list[Diagnostic], path: str, rule: str,
                      expected: Any, value: Any) -> None:
    if path == '$.version':
        message = f'不支持的作业版本：{value!r}；仅支持 1'
    elif path == '$.enemyCount':
        message = f'敌人数量必须是 3 或 6，收到 {value!r}'
    elif path.endswith('.kind'):
        message = f'不支持的动作：{value!r}'
    elif path.endswith('.secondarySelection.type'):
        message = f'不支持的二次选择类型：{value!r}'
    elif path.endswith(('.slot', '.memberSlot', '.playerTargetSlot', '.swapOutSlot', '.swapInSlot')):
        message = f'槽位越界：{value!r} 不在 0–5'
    else:
        message = f'不符合约束 {rule}={expected!r}，收到 {value!r}'
    _error(diagnostics, path, message)


def _matches(value: Any, schema: dict[str, Any], path: str) -> bool:
    diagnostics: list[Diagnostic] = []
    _validate(value, schema, path, diagnostics)
    return not diagnostics


def _validate(value: Any, schema: dict[str, Any], path: str,
              diagnostics: list[Diagnostic]) -> None:
    if not schema:
        return
    if '$ref' in schema:
        _validate(value, _resolve_ref(schema['$ref']), path, diagnostics)
        return

    expected_type = schema.get('type')
    if expected_type is not None:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_matches_type(value, item) for item in types):
            _error(diagnostics, path, f'类型错误：期望 {types}，收到 {type(value).__name__}')
            return
    if 'const' in schema and not _json_equal(value, schema['const']):
        _constraint_error(diagnostics, path, 'const', schema['const'], value)
    if 'enum' in schema and not any(_json_equal(value, item) for item in schema['enum']):
        _constraint_error(diagnostics, path, 'enum', schema['enum'], value)

    if 'allOf' in schema:
        for item in schema['allOf']:
            _validate(value, item, path, diagnostics)
    if 'anyOf' in schema and not any(_matches(value, item, path) for item in schema['anyOf']):
        _constraint_error(diagnostics, path, 'anyOf', '任一子模式', value)
    if 'oneOf' in schema:
        matches = sum(_matches(value, item, path) for item in schema['oneOf'])
        if matches != 1:
            _constraint_error(diagnostics, path, 'oneOf', '恰好一个子模式', value)
    if 'if' in schema and _matches(value, schema['if'], path) and 'then' in schema:
        _validate(value, schema['then'], path, diagnostics)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 'minimum' in schema and value < schema['minimum']:
            _constraint_error(diagnostics, path, 'minimum', schema['minimum'], value)
        if 'maximum' in schema and value > schema['maximum']:
            _constraint_error(diagnostics, path, 'maximum', schema['maximum'], value)
    if isinstance(value, str):
        if 'minLength' in schema and len(value) < schema['minLength']:
            _constraint_error(diagnostics, path, 'minLength', schema['minLength'], value)
        if 'pattern' in schema and re.search(schema['pattern'], value) is None:
            _constraint_error(diagnostics, path, 'pattern', schema['pattern'], value)

    if isinstance(value, dict):
        for key in schema.get('required', []):
            if key not in value:
                _error(diagnostics, f'{path}.{key}', f'缺少必填字段：{key}')
        property_names = schema.get('propertyNames')
        if property_names and 'pattern' in property_names:
            for key in value:
                if re.search(property_names['pattern'], key) is None:
                    _error(diagnostics, f'{path}.{key}', f'字段名不符合规则：{key}')
        properties = schema.get('properties', {})
        for key, subschema in properties.items():
            if key in value:
                _validate(value[key], subschema, f'{path}.{key}', diagnostics)
        additional = schema.get('additionalProperties')
        for key in value.keys() - properties.keys():
            if additional is False:
                _error(diagnostics, f'{path}.{key}', f'不支持的字段：{key}')
            elif isinstance(additional, dict):
                _validate(value[key], additional, f'{path}.{key}', diagnostics)

    if isinstance(value, list):
        if 'minItems' in schema and len(value) < schema['minItems']:
            _constraint_error(diagnostics, path, 'minItems', schema['minItems'], len(value))
        if 'items' in schema:
            for index, item in enumerate(value):
                _validate(item, schema['items'], f'{path}[{index}]', diagnostics)


def validate_job(job: Any) -> list[Diagnostic]:
    """按唯一 Schema 契约校验结构；业务语义由 compiler 另行检查。"""
    diagnostics: list[Diagnostic] = []
    _validate(job, JOB_SCHEMA, '$', diagnostics)
    return diagnostics
