"""报告模板出稿渲染（FR-05-04，硬约束 8）——通用编排见 core/excel_render。

本模块仅负责报告域模板矩阵解析与业务编排：
- 模板矩阵（数据即代码）随版本发布于 backend/templates/report/（manifest.json 登记）；
- RP-1007 未登记键 / RP-1010 指纹校验失败，均沿 ValueError → 路由 422。
"""
from __future__ import annotations

from pathlib import Path

from ...core.excel_render import (
    FingerprintMismatch,
    TemplateNotRegistered,
    load_manifest,
    render_fidelity,
    resolve_template,
)
from ...core.storage import ObjectStorage

# 报告模板矩阵根（随版本发布；数据即代码）
TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "templates" / "report"

# RP-1010 模板指纹校验失败（硬约束 8：不一致即失败回滚）
CODE_FINGERPRINT = "RP-1010"
_ERR_CODE = "RP-1007"
_DOMAIN = "报告"

__all__ = [
    "FingerprintMismatch",
    "TEMPLATE_DIR",
    "TemplateNotRegistered",
    "load_manifest",
    "render_report",
]


def resolve_template_path(template_key: str) -> tuple[Path, dict]:
    """登记键 → 模板文件路径；未登记键直接拒绝（RP-1007 前置）。"""
    return resolve_template(TEMPLATE_DIR, template_key, _ERR_CODE, _DOMAIN)


def render_report(
    template_key: str,
    fields: dict[str, str],
    *,
    storage: ObjectStorage,
    object_key: str,
) -> str:
    """模板 → 填充白名单单元格 → 落盘 → 重开校验结构指纹 → 返回对象键。"""
    template_path, entry = resolve_template_path(template_key)
    return render_fidelity(
        template_path, entry, fields,
        storage=storage, object_key=object_key,
        err_code=_ERR_CODE, fp_err_code=CODE_FINGERPRINT, domain=_DOMAIN,
    )
