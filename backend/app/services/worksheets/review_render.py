"""复核核对表出稿（FR-05-02，硬约束 8）——6700 复核底稿保真写回。

复核模板矩阵（数据即代码）随版本发布于 backend/templates/review/（manifest.json
登记，键 "review/v2025.xlsx"）。仅填「业务复核核对表-准则稿」表头要素（被审计单位/
编制/复核/日期），复核意见栏与三级复核表勾选留人工；公式区零触碰。
WS-1020 指纹校验失败/模板未登记，沿 ValueError → 路由 422。
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

# 复核模板矩阵根（随版本发布；数据即代码）
TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "templates" / "review"

_ERR_CODE = "WS-1020"
_DOMAIN = "复核核对表"

__all__ = [
    "FingerprintMismatch",
    "TEMPLATE_DIR",
    "TemplateNotRegistered",
    "build_review_sheet_fields",
    "render_review_sheet",
]

# 「业务复核核对表-准则稿」表头要素坐标：值为标签右侧值区合并锚点
# （A2:B2=「被审计单位：」标签合并、C2:D2=值区，真值位在 C2——写 B2 会
# 落到标签锚点覆写标签）
_REVIEW_COORDS = {"auditee": "C2", "preparer": "C4", "reviewer": "H4", "prepared_at": "C5", "reviewed_at": "H5"}


def build_review_sheet_fields(
    *, auditee: str, preparer: str, reviewer: str, prepared_at: str, reviewed_at: str
) -> dict[str, str]:
    """复核要素 → 白名单坐标字段（意见栏/三级复核勾选留人工填写）。"""
    return {
        _REVIEW_COORDS["auditee"]: auditee,
        _REVIEW_COORDS["preparer"]: preparer,
        _REVIEW_COORDS["reviewer"]: reviewer,
        _REVIEW_COORDS["prepared_at"]: prepared_at,
        _REVIEW_COORDS["reviewed_at"]: reviewed_at,
    }


def render_review_sheet(
    fields: dict[str, str],
    template_key: str,
    *,
    storage: ObjectStorage,
    object_key: str,
) -> str:
    """复核要素 → 6700 复核底稿模板保真写回 → 重开校验结构指纹 → 返回对象键。"""
    template_path, entry = resolve_template(TEMPLATE_DIR, template_key, _ERR_CODE, _DOMAIN)
    return render_fidelity(
        template_path, entry, fields,
        storage=storage, object_key=object_key,
        err_code=_ERR_CODE, fp_err_code=_ERR_CODE, domain=_DOMAIN,
    )
