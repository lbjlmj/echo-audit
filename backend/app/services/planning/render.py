"""计划域模板出稿（FR-02-01，硬约束 8）——审计计划保真写回，通用编排见 core/excel_render。

计划模板矩阵（数据即代码）随版本发布于 backend/templates/planning/（manifest.json
登记，键 "plan/v2025.xlsx"）。2600 主表填表头要素（被审计单位/编制/截止日/日期），
2610 重要性表头为跨表公式引用主表自动联动，公式区与下拉框零触碰；重要性数值表格区
行级取数留二期。PL-1010 指纹校验失败/模板未登记，沿 ValueError → 路由 422。
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

# 计划模板矩阵根（随版本发布；数据即代码）
TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "templates" / "planning"

_ERR_CODE = "PL-1010"
_DOMAIN = "计划"

__all__ = [
    "FingerprintMismatch",
    "TEMPLATE_DIR",
    "TemplateNotRegistered",
    "build_plan_fields",
    "render_plan",
]

# 2600 主表表头要素坐标（manifest fill_cells 白名单交集由 render_fidelity 校验）
_PLAN_COORDS = {"auditee": "B2", "preparer": "B4", "period": "G4", "prepared_at": "B5", "exported_at": "G5"}


def build_plan_fields(
    *, auditee: str, preparer: str, period: str, prepared_at: str, exported_at: str
) -> dict[str, str]:
    """计划要素 → 白名单坐标字段（2610 重要性表头由跨表公式自动联动，不直填）。"""
    return {
        _PLAN_COORDS["auditee"]: auditee,
        _PLAN_COORDS["preparer"]: preparer,
        _PLAN_COORDS["period"]: period,
        _PLAN_COORDS["prepared_at"]: prepared_at,
        _PLAN_COORDS["exported_at"]: exported_at,
    }


def render_plan(
    fields: dict[str, str],
    template_key: str,
    *,
    storage: ObjectStorage,
    object_key: str,
) -> str:
    """计划要素 → 审计计划模板保真写回 → 重开校验结构指纹 → 返回对象键。"""
    template_path, entry = resolve_template(TEMPLATE_DIR, template_key, _ERR_CODE, _DOMAIN)
    return render_fidelity(
        template_path, entry, fields,
        storage=storage, object_key=object_key,
        err_code=_ERR_CODE, fp_err_code=_ERR_CODE, domain=_DOMAIN,
    )
