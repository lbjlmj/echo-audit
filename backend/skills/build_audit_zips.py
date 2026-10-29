"""Skill 资产打包与发布链（FR-07-03、§15-13、NFS-05）。

Skill 家族 21 个（存量 16 = 2 底座 + 5 阶段 + 9 循环，新增 5），命名 audit-* / ai-*。
- 打包：zip 版本化，内容静态扫描（禁含 IP/路径/凭证/外部文档 ID，违规构建期拦截）
- 发布：待审核 → 质控审核 → 合伙人批准 → 已发布；回滚以 zip 版本为单位
- 两端密钥：Skill 包无数据、无局域网路径，可上云端资产库（§4.2.5）
"""
from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path

# 静态扫描黑名单（Skill 内容禁止出现的模式）
FORBIDDEN_PATTERNS = [
    (r"\b\d{1,3}(\.\d{1,3}){3}\b", "IP 地址"),
    (r"[A-Za-z]:\\[^\s\"]+", "局域网/本机路径"),
    (r"(?i)(api[_-]?key|secret|password|token)\s*[:=]", "凭证字样"),
    (r"(?i)pw[_-]?c|普华永道", "品牌内容（去品牌化红线）"),
    (r"(?i)\bdoc\s*[:：]\s*[A-Za-z0-9]{10,}", "外部文档 ID"),
]


class SkillScanError(Exception):
    pass


def scan_skill_dir(skill_dir: Path) -> list[str]:
    """静态扫描：返回违规清单（空=通过）。"""
    violations: list[str] = []
    for f in skill_dir.rglob("*"):
        if f.is_file() and f.suffix in {".md", ".txt", ".py", ".json", ".yaml", ".yml"}:
            text = f.read_text(encoding="utf-8", errors="ignore")
            for pattern, label in FORBIDDEN_PATTERNS:
                if re.search(pattern, text):
                    violations.append(f"{f.name}: 含{label}")
    return violations


def build_zip(skill_dir: Path, out_dir: Path, version: str) -> Path:
    """扫描通过后打包为带版本 zip，返回对象路径与哈希登记。"""
    violations = scan_skill_dir(skill_dir)
    if violations:
        raise SkillScanError(f"Skill {skill_dir.name} 静态扫描未通过：{violations}")
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{skill_dir.name}-{version}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(skill_dir.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(skill_dir))
    return zip_path


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    import sys

    root = Path(__file__).parent / "src"
    out = Path(__file__).parent / "dist"
    if not root.exists():
        print("无 skills/src 目录，待 Skill 资产落位")
        sys.exit(0)
    for d in sorted(root.iterdir()):
        if d.is_dir():
            print(f"{d.name}: {build_zip(d, out, '1.0.0')} sha256={file_sha256(out / (d.name + '-1.0.0.zip'))[:16]}…")
