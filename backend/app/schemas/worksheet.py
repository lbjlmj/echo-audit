"""底稿域出入参 DTO（FR-03：AI 初稿、复核流转；FR-05-02 复核结论）。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WorksheetCreateIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    template_id: str = Field(min_length=1, description="行业模板（模板矩阵）")
    rule_anchor_id: str = Field(min_length=1, description="准则锚点（FR-03-03，红线 2）")
    is_important: bool = Field(default=False, description="重要底稿（§15-11，触发终审）")


class AnchorOut(BaseModel):
    """准则锚点下拉数据源（红线 2：标准号+条款+程序目的）。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    standard_no: str
    clause: str
    purpose: str


class TemplateOptionOut(BaseModel):
    """底稿模板下拉数据源（Template 行 id，非行业矩阵 key）。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    type: str
    version: str


class ProjectOptionOut(BaseModel):
    """底稿工作台可选项目（编制人无 project.approve 权限时的项目数据源）。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_no: str
    name: str
    status: str


class WorksheetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    code: str
    title: str
    status: str
    current_version: str
    file_key: str | None = None
    is_important: bool | None = None
    template_id: str | None = None
    rule_anchor_id: str


class AiDraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_no: str
    module: str
    model_version: str
    output_digest: str | None = None
    output_key: str | None = None
    status: str


class AiReviewIn(BaseModel):
    conclusion: Literal["采信", "部分采信", "不采信"]
    rejection_reason: Literal["事实错误", "口径错误", "幻觉", "其他"] | None = Field(
        default=None, description="不采信必附原因分类（§15-16）"
    )


class ReviewIn(BaseModel):
    conclusion: Literal["通过", "退回"]
    opinion: str | None = Field(default=None, max_length=500, description="退回必附（FR-05-02）")
    revision_requirement: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------
# FR-03-05 交叉索引（底稿保存时写入并自动校验断链，R7；与质控检查器同口径）
# ---------------------------------------------------------------------------
class CrossRefIn(BaseModel):
    code: str = Field(min_length=1, max_length=32, description="被引用底稿编号")
    note: str = Field(default="", max_length=200, description="引用说明")


class CrossRefsIn(BaseModel):
    refs: list[CrossRefIn] = Field(default=[], max_length=50)


class CrossRefsOut(BaseModel):
    worksheet_id: str
    cross_refs: list[dict]
    broken_refs: list[str] = Field(description="断链清单（引用编号在本项目不存在，R7）")
    ok: bool


class ReviewSheetOut(BaseModel):
    """复核核对表出稿结果（FR-05-02；6700 保真写回产物）。"""

    worksheet_id: str
    worksheet_status: str
    template_key: str
    file_key: str
