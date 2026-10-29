"""对象存储（Phase 1 本地目录实现；MinIO 接线点）。

NFS-04/FR-05-04 出稿产物落存：接口（save/load/delete/exists）与 MinIO 对齐，
Phase 2 切换仅替换本文件实现，调用方（services 层）零改动。
dev 口径：ECHO_STORAGE_DIR（默认 ./data/objects）下的普通文件，键即相对路径。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .config import get_settings

STORAGE_ROOT_DEFAULT = "data/objects"


class ObjectStorage:
    """键值式对象存取（键不可变覆盖写由调用方保证：报告键含 report_id+版本）。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        # 键白名单字符校验：拒绝路径穿越（..、绝对路径）
        if not key or ".." in key.replace("\\", "/").split("/") or Path(key).is_absolute():
            raise ValueError(f"非法对象键：{key!r}")
        return self.root / key

    def save_bytes(self, key: str, data: bytes) -> Path:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def load_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


@lru_cache
def get_storage() -> ObjectStorage:
    return ObjectStorage(get_settings().storage_dir or STORAGE_ROOT_DEFAULT)
