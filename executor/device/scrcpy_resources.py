"""固定 scrcpy 服务端的开发与随包资源解析、完整性和缺失诊断。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from executor.device.base import DeviceError

DEFAULT_RESOURCE_DIR = Path(__file__).resolve().parents[2] / 'resources' / 'scrcpy'


def verify_scrcpy_server(resource_dir: str | Path | None = None) -> Path:
    root = Path(resource_dir) if resource_dir is not None else DEFAULT_RESOURCE_DIR
    manifest_path = root / 'manifest.json'
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        version = manifest['version']
        filename = manifest['server_file']
        expected = manifest['sha256'].lower()
    except Exception as error:
        raise DeviceError(f'scrcpy 资源清单缺失或无效：{manifest_path}：{error}；请重新安装或恢复固定资源') from error
    if Path(filename).name != filename:
        raise DeviceError(f'scrcpy 资源清单中的服务端文件名无效：{filename!r}（{manifest_path}）')
    server = root / filename
    if not server.is_file():
        raise DeviceError(f'scrcpy 服务端缺失：{server}；固定版本 {version}。开发版请恢复 resources/scrcpy，'
                          '安装版请重新安装 HugoFgo；不会下载未校验的替代文件')
    actual = hashlib.sha256(server.read_bytes()).hexdigest()
    if actual != expected:
        raise DeviceError(f'scrcpy 服务端 SHA-256 不匹配：{server}；固定版本 {version}，'
                          f'期望 {expected}，实际 {actual}；请重新安装或恢复官方固定资源')
    return server
