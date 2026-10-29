"""质控域模板出稿（FR-06-01，硬约束 8）——6600 完成情况核对表保真写回。

质控模板矩阵（数据即代码）随版本发布于 backend/templates/quality/（manifest.json
登记，键 "checklist/v2025.xlsx"）。仅填核对表表头要素（被审计单位/编制/日期），
检查结论勾选区留人工核对；公式区零触碰。
QU-1020 指纹校验失败/模板未登记，沿 ValueError → 路由 422。
"""
from __future__ import annotations

from pathlib import Path

from ...core.excel_render import (
    FingerprintMismatch,
    TemplateNotRegistered,
    render_fidelity,
    resolve_template,
)
from ...core.storage import ObjectStorage

# 质控模板矩阵根（随版本发布；数据即代码）
TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "templates" / "quality"

_ERR_CODE = "QU-1020"
_DOMAIN = "完成核对表"

__all__ = [
    "FingerprintMismatch",
    "TEMPLATE_DIR",
    "TemplateNotRegistered",
    "build_checklist_fields",
    "render_checklist",
]

# 「审计工作完成情况核对表6600」表头要素坐标：值为标签右侧值区合并锚点
# （A2:B2=「被审计单位：」标签合并、C2:D2=值区，真值位在 C2——写 B2 会
# 落到标签锚点覆写标签）
_CHECKLIST_COORDS = {"auditee": "C2", "preparer": "C4", "prepared_at": "C5"}


def build_checklist_fields(*, auditee: str, preparer: str, prepared_at: str) -> dict[str, str]:
    """核对表要素 → 白名单坐标字段（检查结论勾选区留人工填写）。"""
    return {
        _CHECKLIST_COORDS["auditee"]: auditee,
        _CHECKLIST_COORDS["preparer"]: preparer,
        _CHECKLIST_COORDS["prepared_at"]: prepared_at,
    }


def render_checklist(
    fields: dict[str, str],
    template_key: str,
    *,
    storage: ObjectStorage,
    object_key: str,
) -> str:
    """核对表要素 → 6600 模板保真写回 → 重开校验结构指纹 → 返回对象键。"""
    template_path, entry = resolve_template(TEMPLATE_DIR, template_key, _ERR_CODE, _DOMAIN)
    return render_fidelity(
        template_path, entry, fields,
        storage=storage, object_key=object_key,
        err_code=_ERR_CODE, fp_err_code=_ERR_CODE, domain=_DOMAIN,
    )
