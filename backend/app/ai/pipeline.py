"""AI 统一管道（红线 1、§7 R2、§4.3.3 AI 留痕三段式）——架构级强制。

AI 模块只产出「线索」（lead），不直接写业务表：
  第一段 输入：材料清单 + 输入哈希
  第二段 推理：模块与 Skill/模型版本 + 输出摘要与对象键
  第三段 人工复核标记：复核人 + 日期 + 采信结论（采信/部分采信/不采信+原因分类）
第三段未落库，输出永远停在「线索」态——无法进入 WorksheetVersion 版本链，无法归档。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

LeadStatus = Literal["线索", "已复核", "已采信", "部分采信", "不采信"]
# 不采信必附原因分类（§15-16）
RejectionReason = Literal["事实错误", "口径错误", "幻觉", "其他"]

# AI 风险清单 10 项（红线 6、§15-10）：每个 ai_* 模块运行前逐条核对，清单未过不推理
RISK_CHECKLIST: tuple[str, ...] = (
    "保密泄露", "数据篡改", "幻觉输出", "训练偏差", "不可追溯",
    "版本失控", "过度依赖", "权限越界", "外部通道泄密", "法规漂移",
)


class PipelineViolation(Exception):
    pass


@dataclass
class AIRun:
    """AI 使用留痕（字段对齐 PRD §4.6）。"""

    project_id: str
    module: str                       # ai_risk / ai_anomaly / ai_confirm / ai_finding / ai_notes / ai_qc / rag
    skill_version: str
    model_version: str
    triggered_by: str
    input_material_ids: list[str]
    input_hashes: dict[str, str] = field(default_factory=dict)
    run_no: str = ""                  # 留痕编号（全局唯一）
    output_digest: str = ""           # 输出摘要
    output_key: str = ""              # 输出对象键（MinIO）
    status: LeadStatus = "线索"
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    rejection_reason: RejectionReason | None = None
    related_object: tuple[str, str] | None = None  # （类型, id）：底稿/函证/问题

    def __post_init__(self) -> None:
        if not self.run_no:
            self.run_no = f"AI-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:12].upper()}"

    @property
    def is_lead_only(self) -> bool:
        """未过第三段（人工复核标记）的输出永远是线索态。"""
        return self.reviewed_by is None or self.reviewed_at is None


def record_input(run: AIRun, material_id: str, content: bytes) -> None:
    """第一段：登记输入材料与哈希。"""
    run.input_material_ids.append(material_id)
    run.input_hashes[material_id] = hashlib.sha256(content).hexdigest()


def seal_output(run: AIRun, summary: dict[str, Any], output_key: str) -> str:
    """第二段：封存输出摘要与对象键（此时仍为线索态，禁止写业务表）。"""
    run.output_digest = hashlib.sha256(
        json.dumps(summary, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    run.output_key = output_key
    return run.output_digest


# 复核结论（人工口径）→ 留痕状态（库端 ck_airun_status 口径，§4.6）
STATUS_BY_CONCLUSION: dict[str, LeadStatus] = {
    "采信": "已采信",
    "部分采信": "部分采信",
    "不采信": "不采信",
}


def mark_review(
    run: AIRun,
    reviewer: str,
    conclusion: Literal["采信", "部分采信", "不采信"],
    rejection_reason: RejectionReason | None = None,
) -> AIRun:
    """第三段：人工复核标记。不采信必附原因分类；标记完成后才可进入业务表。"""
    run.reviewed_by = reviewer
    run.reviewed_at = datetime.now(timezone.utc).isoformat()
    if conclusion == "不采信":
        if not rejection_reason:
            raise PipelineViolation("不采信结论必须附带原因分类（事实错误/口径错误/幻觉/其他，§15-16）")
        run.rejection_reason = rejection_reason
    run.status = STATUS_BY_CONCLUSION[conclusion]
    return run


def assert_can_enter_business(run: AIRun, target: str) -> None:
    """进入业务表（底稿版本链/问题/函证）前的门禁：无复核标记一律拒绝。"""
    if run.is_lead_only:
        raise PipelineViolation(
            f"AI 输出 {run.run_no} 尚未人工复核标记，禁止进入 {target}（红线 1、§7 R2）"
        )
