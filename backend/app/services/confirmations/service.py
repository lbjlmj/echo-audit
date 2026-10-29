"""函证域业务逻辑（FR-04-01/03/04 + §4.4 八状态 + §15-7 超期驱动）。

规则落点：
- 八状态流转一律经 engine.CONFIRMATION（硬约束 2），禁止 service 自创状态；
- 回函录入后自动核对（FR-04-03）：金额一致（容差 0.01 元）→「核对一致」，
  否则「差异处理」并落差异 JSON（一次录入连转两态，均为 engine 裁决）；
- 快递对接预留 ExpressAdapter（§15-9），出站必经白名单（硬约束 7）；
  P0 运单号人工登记，适配器沙箱联调后接入自动回写；
- 超期裁决经 engine/confirmation_rules 单点，Beat 与手动扫描共用；建议替代程序只留痕不转态；
- 全部函证「闭环」为实质性阶段门准入条件（§4.2 门 5），门快照由归集服务统一刷新。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.audit_middleware import append_trail
from ...core.permissions import Level, assert_allowed
from ...core.storage import get_storage
from ...engine.confirmation_rules import decide_overdue_status
from ...engine.industry_matrix import domain_template_key
from ...engine.state_machines import CONFIRMATION, IllegalTransition
from ...models.audit import AuditTrail
from ...models.confirmation import Confirmation
from ...models.project import AuditProject
from ...models.user import User
from . import letter

# CF-1xxx 函证域错误码
CODE_TRANSITION = "CF-1001"
CODE_NOT_FOUND = "CF-1002"
CODE_FROZEN = "CF-1005"
CODE_LOCKED = "CF-1006"
CODE_PARAM = "CF-1007"

REPLY_TOLERANCE = Decimal("0.01")  # 回函核对金额容差（元）


class NotFound(Exception):
    pass


class Locked(Exception):
    pass


class ProjectFrozen(Exception):
    pass


class TransitionRejected(Exception):
    pass


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _get_cf(db: Session, confirmation_id: str) -> Confirmation:
    row = db.get(Confirmation, confirmation_id)
    if row is None:
        raise NotFound(CODE_NOT_FOUND)
    return row


def _assert_writable(project: AuditProject) -> None:
    if project.status == "暂停":
        raise ProjectFrozen(CODE_FROZEN)
    if project.status in ("已归档", "终止"):  # 终结态：禁止一切写操作（§15-17，与 quality 同口径）
        raise Locked(f"{CODE_LOCKED} 项目已终结（{project.status}），禁止写操作")


def _transit(row: Confirmation, target: str, **ctx: object) -> str:
    """八状态流转只走 engine（硬约束 2）：裁决通过才赋回状态字段。"""
    try:
        row.status = CONFIRMATION.transit(row.status, target, **ctx)
    except IllegalTransition as exc:
        raise TransitionRejected(f"{CODE_TRANSITION} {exc}") from exc
    return row.status


def list_confirmations(db: Session, operator: User, project_id: str) -> list[Confirmation]:
    """函证台账列表（worksheet.edit ≥ VIEW）：按创建时间升序；质控/合伙人只读可见。"""
    assert_allowed(operator.global_role, "worksheet.edit", Level.VIEW)
    return list(
        db.scalars(
            select(Confirmation)
            .where(Confirmation.project_id == project_id)
            .order_by(Confirmation.created_at)
        ).all()
    )


def list_trails(db: Session, operator: User, confirmation_id: str) -> list[AuditTrail]:
    """单张函证的留痕时间线（worksheet.edit ≥ VIEW，所见即所查 §12 交互原则）。"""
    assert_allowed(operator.global_role, "worksheet.edit", Level.VIEW)
    _get_cf(db, confirmation_id)
    return list(
        db.scalars(
            select(AuditTrail)
            .where(AuditTrail.entity_type == "confirmation", AuditTrail.entity_id == confirmation_id)
            .order_by(AuditTrail.created_at)
        ).all()
    )


def generate_ledger(
    db: Session, operator: User, project_id: str, *, items: list[dict]
) -> list[Confirmation]:
    """生成函证台账（FR-04-01，worksheet.edit ≥ OPERATE）：台账项进入「台账已生成」。"""
    assert_allowed(operator.global_role, "worksheet.edit", Level.OPERATE)
    project = db.get(AuditProject, project_id)
    if project is None:
        raise NotFound(CODE_NOT_FOUND)
    _assert_writable(project)

    rows = [
        Confirmation(
            project_id=project_id, type=item["type"],
            counterparty=item["counterparty"], amount=item["amount"],
        )
        for item in items
    ]
    db.add_all(rows)
    db.flush()
    for row in rows:
        append_trail(
            db, tenant_id=operator.tenant_id, entity_type="confirmation", entity_id=row.id,
            action="函证台账生成", operator=operator.name,
            after={"type": row.type, "counterparty": row.counterparty, "amount": float(row.amount)},
        )
    return rows


def issue_confirmation(
    db: Session, operator: User, confirmation_id: str, *, waybill_no: str
) -> Confirmation:
    """发出函证（worksheet.edit ≥ OPERATE）：登记运单号（适配器回写或人工）→「已发出」。

    P0 口径：顺丰适配器沙箱未联调，运单号人工登记；适配器接线后经 ExpressAdapter
    下单回写，出站字段白名单不含函证金额（§4.2.7）。
    """
    assert_allowed(operator.global_role, "worksheet.edit", Level.OPERATE)
    row = _get_cf(db, confirmation_id)
    _assert_writable(db.get(AuditProject, row.project_id))
    if not (waybill_no and waybill_no.strip()):
        raise ValueError(f"{CODE_PARAM} 快递单号必填（适配器回写或人工登记，FR-04-02）")
    _transit(row, "已发出", issued_by=operator.id, waybill_no=waybill_no.strip())
    row.issued_at = _now()
    row.waybill_no = waybill_no.strip()
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="confirmation", entity_id=row.id,
        action="函证发出", operator=operator.name,
        before={"status": "台账已生成"}, after={"status": "已发出", "waybill_no": row.waybill_no},
    )
    return row


def register_reply(
    db: Session, operator: User, confirmation_id: str, *,
    reply_amount: Decimal, reply_doc: str,
) -> Confirmation:
    """回函录入 + 自动核对（FR-04-03）：一致→「核对一致」；差异→「差异处理」并落差异 JSON。"""
    assert_allowed(operator.global_role, "worksheet.edit", Level.OPERATE)
    row = _get_cf(db, confirmation_id)
    _assert_writable(db.get(AuditProject, row.project_id))
    if not (reply_doc and reply_doc.strip()):
        raise ValueError(f"{CODE_PARAM} 回函文件引用必填")
    _transit(row, "已回函", reply_doc=reply_doc.strip())  # 「已发出/超期未回」→「已回函」

    diff = reply_amount - row.amount
    if abs(diff) <= REPLY_TOLERANCE:
        _transit(row, "核对一致", matched_amount=str(reply_amount))
    else:
        row.difference = {
            "回函金额": float(reply_amount), "台账金额": float(row.amount), "差异": float(diff),
        }
        _transit(row, "差异处理", difference_record=row.difference)
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="confirmation", entity_id=row.id,
        action="回函录入并自动核对", operator=operator.name,
        after={"status": row.status, "reply_amount": float(reply_amount),
               "difference": row.difference},
    )
    return row


def propose_alt_test(
    db: Session, operator: User, confirmation_id: str, *, alt_test_doc: str
) -> Confirmation:
    """执行替代程序（FR-04-04）：超期未回/差异处理 →「替代程序」，记录替代测试凭据。"""
    assert_allowed(operator.global_role, "worksheet.edit", Level.OPERATE)
    row = _get_cf(db, confirmation_id)
    _assert_writable(db.get(AuditProject, row.project_id))
    if not (alt_test_doc and alt_test_doc.strip()):
        raise ValueError(f"{CODE_PARAM} 替代测试记录必填")
    _transit(row, "替代程序", alt_test_doc=alt_test_doc.strip())
    row.alt_test_record = alt_test_doc.strip()
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="confirmation", entity_id=row.id,
        action="启动替代程序", operator=operator.name,
        after={"status": "替代程序"},
    )
    return row


def close_confirmation(db: Session, operator: User, confirmation_id: str) -> Confirmation:
    """函证闭环（worksheet.edit ≥ OPERATE）：核对一致/替代程序 →「闭环」（门 5 准入计数）。"""
    assert_allowed(operator.global_role, "worksheet.edit", Level.OPERATE)
    row = _get_cf(db, confirmation_id)
    _assert_writable(db.get(AuditProject, row.project_id))
    _transit(row, "闭环", closed_by=operator.id)
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="confirmation", entity_id=row.id,
        action="函证闭环", operator=operator.name,
        before={"status": row.status}, after={"status": "闭环"},
    )
    return row


def render_confirmation_letter(
    db: Session, operator: User, confirmation_id: str, *, template_key: str | None = None
) -> tuple[Confirmation, str, str, str]:
    """函证件出稿（FR-04-01，worksheet.edit ≥ OPERATE）：询证函模板保真写回（硬约束 8）。

    默认模板键按台账类型映射出稿域（银行→bank、往来→ar）并按项目业务类型准则
    路由（engine 单点：银行函监管统一版式两准则共用；往来函小企业版独立键）；
    显式入参 template_key 优先。只填致/函证编号白名单格，函证金额绝不出站
    （§4.2.7）。出稿不改八状态——函证件生成后仍按发出流程走。
    CF-1010 指纹/登记类拒绝（ValueError 子类）向上 → 路由 422，落盘对象已删，
    库字段未写，天然回滚。返回 (台账行, 对象键, 模板键, 函证编号)。
    """
    assert_allowed(operator.global_role, "worksheet.edit", Level.OPERATE)
    row = _get_cf(db, confirmation_id)
    project = db.get(AuditProject, row.project_id)
    if project is None:
        raise NotFound(CODE_NOT_FOUND)
    _assert_writable(project)
    domain = letter.TYPE_DOMAINS.get(row.type)
    if domain is None:
        raise ValueError(f"{CODE_PARAM} 台账类型无默认模板：{row.type}")
    key = (template_key or "").strip() or domain_template_key(domain, project.business_type)

    letter_no = f"CF-{row.id[:8].upper()}"
    fields = letter.build_letter_fields(key, counterparty=row.counterparty, letter_no=letter_no)
    object_key = f"confirmations/{row.project_id}/{row.id}/letter.xlsx"
    letter.render_letter(fields, key, storage=get_storage(), object_key=object_key)

    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="confirmation", entity_id=row.id,
        action="函证件出稿", operator=operator.name,
        after={"template_key": key, "letter_no": letter_no, "file_key": object_key},
    )
    return row, object_key, key, letter_no


def run_overdue_scan(db: Session, *, today: date) -> dict:
    """超期扫描（Beat 每日驱动，系统身份 beat-scheduler）：转态/提醒/建议替代，逐条留痕。"""
    rows = db.scalars(
        select(Confirmation).where(Confirmation.status.in_(["已发出", "超期未回"]))
    ).all()
    actions: list[dict] = []
    for row in rows:
        if row.issued_at is None:
            continue
        days = (today - row.issued_at.date()).days
        verdict = decide_overdue_status(
            row.status, row.issued_at.date(), today, remind_count=row.remind_count
        )
        transition = verdict["transition"]
        if transition == "超期未回":
            _transit(row, "超期未回", overdue_days=days)
            row.remind_count = int(verdict["remind_total"])  # type: ignore[arg-type]
            act = "转超期未回"
        elif verdict["remind_due"]:
            row.remind_count = int(verdict["remind_total"])  # type: ignore[arg-type]
            act = "超期提醒"
        elif verdict["suggest_alternative"]:
            act = "建议替代程序"  # 仅留痕建议，不自动转态（FR-04-04：替代程序由人工执行后闭环）
        else:
            continue
        append_trail(
            db, tenant_id=row.tenant_id, entity_type="confirmation", entity_id=row.id,
            action=f"超期扫描-{act}", operator="beat-scheduler",
            after={"days": days, "remind_count": row.remind_count,
                   "suggest_alt": verdict["suggest_alternative"], "status": row.status},
        )
        actions.append({"confirmation_id": row.id, "action": act})
    db.flush()
    return {"scanned": len(rows), "actions": actions}
