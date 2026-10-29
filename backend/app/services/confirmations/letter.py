"""函证件出稿（FR-04-01，硬约束 8）——询证函模板保真写回，通用编排见 core/excel_render。

函证模板矩阵（数据即代码）随版本发布于 backend/templates/confirmation/（manifest.json
登记，键如 "bank/v2025.xlsx"）。台账项按类型默认模板：银行→bank、往来→ar；
白名单填充（致/函证编号），函证金额绝不出站（§4.2.7），公式区与下拉框零触碰，
落盘后重开校验结构指纹。CF-1010 指纹校验失败/模板未登记，沿 ValueError → 路由 422。
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

# 函证模板矩阵根（随版本发布；数据即代码）
TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "templates" / "confirmation"

_ERR_CODE = "CF-1010"
_DOMAIN = "函证"

# 台账类型 → 出稿域（FR-04-01：按往来款/银行账户生成函证件）。
# 模板键按项目业务类型准则路由，单点在 engine/industry_matrix.domain_template_key：
# 银行函为监管统一版式两准则共用；往来函小企业版为独立键 ar-sme。
TYPE_DOMAINS = {"银行": "bank", "往来": "ar"}

# 模板键 → 表头要素坐标（致/函证编号；索引号由底稿侧登记，此处不填）
_LETTER_COORDS: dict[str, dict[str, str]] = {
    "bank/v2025.xlsx": {"addr": "B4", "letter_no": "L4"},
    "ar/v2025.xlsx": {"addr": "B4", "letter_no": "F4"},
    "ar-sme/v2025.xlsx": {"addr": "B4", "letter_no": "F4"},
}

__all__ = [
    "FingerprintMismatch",
    "TEMPLATE_DIR",
    "TEMPLATE_LETTER_ERR_CODE",
    "TYPE_DOMAINS",
    "TemplateNotRegistered",
    "build_letter_fields",
    "render_letter",
]

TEMPLATE_LETTER_ERR_CODE = _ERR_CODE


def build_letter_fields(template_key: str, *, counterparty: str, letter_no: str) -> dict[str, str]:
    """函证要素 → 白名单坐标字段（越出坐标集的键由 render_fidelity 拒绝）。"""
    spec = _LETTER_COORDS.get(template_key)
    if spec is None:
        raise TemplateNotRegistered(f"{_ERR_CODE} {_DOMAIN}模板键未登记：{template_key}")
    return {spec["addr"]: counterparty, spec["letter_no"]: letter_no}


def render_letter(
    fields: dict[str, str],
    template_key: str,
    *,
    storage: ObjectStorage,
    object_key: str,
) -> str:
    """函证要素 → 询证函模板保真写回 → 重开校验结构指纹 → 返回对象键。"""
    template_path, entry = resolve_template(TEMPLATE_DIR, template_key, _ERR_CODE, _DOMAIN)
    return render_fidelity(
        template_path, entry, fields,
        storage=storage, object_key=object_key,
        err_code=_ERR_CODE, fp_err_code=_ERR_CODE, domain=_DOMAIN,
    )
