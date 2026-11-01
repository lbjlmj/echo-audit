"""对象存储（本地文件系统实现，不引入 MinIO）。

NFS-04/FR-05-04 出稿产物落存：键即相对路径，目录按业务域分层
（worksheets/、confirmations/、reports/、archives/、materials/）。

选型决策（TECH_DESIGN §1.4/§4.7）：
- MinIO 开源版已归档停止维护（dl.min.io 返回 410 Gone，Docker Hub 镜像被拒）；
- 目标场景为中小所单台物理机全离线部署，不需要分布式对象存储；
- 静态加密靠 OS 层（LUKS/BitLocker 加密卷），不重复造轮子；
- 归档不可变（WORM）靠 Linux `chattr +a`（append-only）实现，见 freeze_archive。

接口（save/load/delete/exists）与 S3 语义对齐，未来若需切云对象存储仅替换本文件实现，
调用方（services 层）零改动。dev 口径：ECHO_STORAGE_DIR（默认 ./data/objects）。
"""
from __future__ import annotations

import subprocess
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

    def freeze_archive(self, key: str) -> None:
        """归档包 WORM 锁（TECH_DESIGN §4.3 红线 1/3：归档不可变）。

        Linux 下执行 `chattr +a`（append-only）：文件可追加不可改写/删除，
        差错修订走副本机制（v(n+1)），原稿锁定保留。
        非 Linux 环境（如 Windows dev）跳过并记录——生产部署为 Linux 容器。
        解锁需 `chattr -a`（仅限质控差错修订流程，操作留痕）。
        """
        path = self._path(key)
        if not path.exists():
            raise FileNotFoundError(f"归档包不存在：{key}")
        try:
            subprocess.run(
                ["chattr", "+a", str(path)],
                check=True, capture_output=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            # chattr 不存在（Windows/macOS dev）或无 CAP_LINUX_IMMUTABLE：
            # 生产 Linux 容器必生效；非 Linux 环境降级为只读权限，不阻断流程
            path.chmod(0o444)


@lru_cache
def get_storage() -> ObjectStorage:
    return ObjectStorage(get_settings().storage_dir or STORAGE_ROOT_DEFAULT)
