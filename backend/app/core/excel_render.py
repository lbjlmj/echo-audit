"""Excel 模板保真渲染通用编排（硬约束 8；报告出稿与底稿初稿写回共用单点）。

保真写回口径（TECH_DESIGN §4.4 / §7 模板指纹回归）：
1. 发布指纹比对：模板文件当前结构指纹必须等于 manifest 登记的发布指纹，否则视为
   模板被篡改/脱钩，拒绝写回（第一道闸）；
2. 白名单填充：仅写 manifest fill_cells 登记坐标（sheet → 坐标），公式区、数据验证
   （下拉框）、样式零触碰（第二道闸，"不自创格式"机器校验）；
3. 写后校验：落盘后重新加载产物比对写回前后结构指纹（engine/fingerprint 单点），
   不一致即删除落盘对象并失败回滚（第三道闸）。

各域模板矩阵（数据即代码，随版本发布）于 backend/templates/{report|worksheet}/
manifest.json 登记：键、file、fill_cells、structure_fingerprint（逐表 SHA256）。
"""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook

from ..engine.fingerprint import compute_fingerprint, verify_structure
from .storage import ObjectStorage


class TemplateNotRegistered(ValueError):
    """模板键未登记或文件缺失（防任意键注入：写回键必须命中 manifest）。"""


class FingerprintMismatch(ValueError):
    """模板与发布指纹不符，或写回后结构指纹不一致（失败回滚）。"""


def load_manifest(root: Path) -> dict[str, dict]:
    """已登记模板清单（key → 元数据）；manifest 缺失视为空矩阵。"""
    path = root / "manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_template(root: Path, template_key: str, err_code: str, domain: str) -> tuple[Path, dict]:
    """登记键 → 模板文件路径与登记项；未登记/文件缺失直接拒绝。"""
    entry = load_manifest(root).get(template_key)
    if entry is None:
        raise TemplateNotRegistered(
            f"{err_code} {domain}模板键未登记：{template_key}（可用：{sorted(load_manifest(root))}）"
        )
    path = root / entry["file"]
    if not path.exists():
        raise TemplateNotRegistered(f"{err_code} {domain}模板文件缺失：{entry['file']}（随版本发布）")
    return path, entry


def render_fidelity(
    template_path: Path,
    entry: dict,
    fields: dict[str, str],
    *,
    storage: ObjectStorage,
    object_key: str,
    err_code: str,
    fp_err_code: str,
    domain: str,
) -> str:
    """模板 → 发布指纹比对 → 白名单填充 → 落盘 → 写后重开校验 → 返回对象键。

    fields：单元格坐标 → 文本值（越出白名单一律拒绝）。
    err_code：登记/白名单类拒绝；fp_err_code：指纹类拒绝（各域分段，如 RP-1007/RP-1010）。
    任一道闸失败：抛 TemplateNotRegistered / FingerprintMismatch（ValueError 子类，
    路由层映射 422）；已落盘对象删除，调用方不得写库——失败回滚。
    """
    wb = load_workbook(template_path)
    baseline = compute_fingerprint(wb)  # 写前基线：模板当前结构

    registered = entry.get("structure_fingerprint")
    if not registered:
        raise TemplateNotRegistered(
            f"{err_code} {domain}模板 {entry.get('file', '')} 未登记结构指纹"
            f"（结构指纹须随版本发布登记）"
        )
    if registered != baseline:
        raise FingerprintMismatch(
            f"{fp_err_code} {domain}模板文件与发布登记结构指纹不一致（疑似被篡改），拒绝写回"
        )

    allowed = {c for cells in entry["fill_cells"].values() for c in cells}
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise ValueError(f"{err_code} 写回字段越出模板白名单：{unknown}")
    for sheet, cells in entry["fill_cells"].items():
        ws = wb[sheet]
        for coord in cells:
            if coord in fields:
                ws[coord] = fields[coord]

    buf = BytesIO()
    wb.save(buf)
    storage.save_bytes(object_key, buf.getvalue())  # 写回落盘（Phase 1 本地，MinIO 接线点）

    # 写后校验：重新加载**落盘产物**比对结构指纹（比校验内存更严格）
    reloaded = load_workbook(BytesIO(storage.load_bytes(object_key)))
    try:
        verify_structure(baseline, compute_fingerprint(reloaded))
    except Exception as exc:  # 指纹不一致：删除落盘对象，失败回滚（硬约束 8）
        storage.delete(object_key)
        raise FingerprintMismatch(
            f"{fp_err_code} 写回后模板结构指纹不一致，已回滚：{exc}"
        ) from exc
    return object_key
