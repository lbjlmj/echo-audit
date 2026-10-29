"""函证域出入参 DTO（FR-04：台账 / 发出 / 回函核对 / 替代程序）。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CfType = Literal["银行", "往来"]


class LedgerItem(BaseModel):
    type: CfType
    counterparty: str = Field(min_length=2, max_length=200, description="对方单位/账户行")
    amount: Decimal = Field(gt=0, description="函证金额（不出站，§4.2.7）")


class LedgerIn(BaseModel):
    project_id: str = Field(min_length=1)
    items: list[LedgerItem] = Field(min_length=1, description="台账项（按账户/往来方生成）")


class IssueIn(BaseModel):
    waybill_no: str = Field(min_length=4, max_length=50, description="快递单号（适配器回写或人工）")


class ReplyIn(BaseModel):
    reply_amount: Decimal = Field(ge=0, description="回函确认金额")
    reply_doc: str = Field(min_length=2, max_length=200, description="回函文件引用（扫描件对象键）")


class AltIn(BaseModel):
    alt_test_doc: str = Field(min_length=2, max_length=500, description="替代测试记录/凭据")


class LetterRenderIn(BaseModel):
    template_key: str | None = Field(
        default=None, max_length=100,
        description="询证函模板键（空则按台账类型默认：银行→bank、往来→ar）",
    )


class LetterOut(BaseModel):
    """函证件出稿结果（对象键经 /storage 下载；金额不出站）。"""

    confirmation_id: str
    template_key: str
    letter_no: str
    file_key: str


class ConfirmationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    type: CfType
    counterparty: str
    amount: Decimal
    status: str
    issued_at: datetime | None = None
    planned_reply_date: date | None = None
    waybill_no: str | None = None
    logistics_status: str | None = None
    difference: dict[str, Any] | None = None
    alt_test_record: str | None = None
    remind_count: int = 0


class TrailOut(BaseModel):
    """函证留痕时间线（所见即所查 §12；哈希链字段不出站，只出业务快照）。"""

    model_config = ConfigDict(from_attributes=True)

    action: str
    operator: str
    before_snapshot: dict[str, Any] | None = None
    after_snapshot: dict[str, Any] | None = None
    created_at: datetime
