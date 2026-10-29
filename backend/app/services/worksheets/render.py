"""底稿模板保真写回（FR-03-02 采信落链，硬约束 8）——通用编排见 core/excel_render。

底稿模板矩阵（数据即代码）随版本发布于 backend/templates/worksheet/（manifest.json
登记，键 = Template.skeleton_key，如 "templates/trade/v2025.xlsx"）。
AI 初稿采信时打开 skeleton_key 对应模板，白名单填充（编制人/项目编号/底稿编号/
准则锚点/AI 置空提示），公式区与下拉框零触碰，落盘后重开校验结构指纹。
WS-1010 指纹校验失败 / 模板未登记，沿 ValueError → 路由 422，版本链不落。
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

# 底稿模板矩阵根（随版本发布；数据即代码）
TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "templates" / "worksheet"

_ERR_CODE = "WS-1010"
_DOMAIN = "底稿"

__all__ = [
    "FingerprintMismatch",
    "TEMPLATE_DIR",
    "TemplateNotRegistered",
    "load_manifest",
    "render_ai_draft",
]


def resolve_template_path(skeleton_key: str) -> tuple[Path, dict]:
    """skeleton_key → 模板文件路径；未登记键直接拒绝（WS-1010）。"""
    return resolve_template(TEMPLATE_DIR, skeleton_key, _ERR_CODE, _DOMAIN)


def render_ai_draft(
    fields: dict[str, str],
    skeleton_key: str,
    *,
    storage: ObjectStorage,
    object_key: str,
) -> str:
    """AI 初稿要素 → 底稿模板保真写回 → 重开校验结构指纹 → 返回对象键。"""
    template_path, entry = resolve_template_path(skeleton_key)
    return render_fidelity(
        template_path, entry, fields,
        storage=storage, object_key=object_key,
        err_code=_ERR_CODE, fp_err_code=_ERR_CODE, domain=_DOMAIN,
    )
