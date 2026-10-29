"""全量异常 AI 路由（§2.3，挂 /projects 子资源）：FR-03-04 评分与抽凭清单。

路由层薄：只做鉴权与出入参，规则在 services/anomaly（算法在 engine/anomaly 单点）；
错误码沿用底稿域 WS-1xxx。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...core.deps import get_current_user
from ...db import get_db
from ...models.user import User
from ...schemas.anomaly import AdjustIn, ExecuteIn, SampleOut
from ...schemas.worksheet import AiDraftOut
from ...services import anomaly

router = APIRouter(prefix="/projects", tags=["ai"])


def _http_for(exc: Exception) -> HTTPException:
    if isinstance(exc, anomaly.NotFound):
        return HTTPException(404, f"WS-1002 {exc}")
    if isinstance(exc, anomaly.ProjectFrozen):
        return HTTPException(409, f"WS-1005 {exc} 项目暂停期间禁止写操作")
    if isinstance(exc, anomaly.Locked):
        return HTTPException(409, str(exc))
    if isinstance(exc, ValueError):  # 无数据/下限/回填必填等参数拒绝
        return HTTPException(422, str(exc))
    return HTTPException(500, "WS-1999 内部错误")


@router.post("/{project_id}/anomaly-scan", response_model=AiDraftOut,
             summary="发起全量异常评分（FR-03-04：100% 凭证孤立森林评分，产物为线索态）")
def scan_vouchers(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AiDraftOut:
    try:
        row = anomaly.scan_vouchers(db, user, project_id)
    except (anomaly.NotFound, anomaly.Locked, anomaly.ProjectFrozen, ValueError) as exc:
        raise _http_for(exc) from exc
    return AiDraftOut.model_validate(row)


@router.get("/{project_id}/samples", response_model=list[SampleOut],
            summary="抽凭清单（按风险降序生成的 AI 采信清单，含执行状态）")
def list_samples(
    project_id: str,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[SampleOut]:
    return [SampleOut.model_validate(r) for r in anomaly.list_samples(db, project_id)]


@router.post("/{project_id}/samples/adjust", response_model=list[SampleOut],
             summary="项目经理调整清单（下限 5%，调整留痕，FR-03-04）")
def adjust_samples(
    project_id: str,
    body: AdjustIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[SampleOut]:
    try:
        rows = anomaly.adjust_samples(
            db, user, project_id, add_ids=body.add_ids, remove_ids=body.remove_ids
        )
    except (anomaly.NotFound, anomaly.Locked, anomaly.ProjectFrozen, ValueError) as exc:
        raise _http_for(exc) from exc
    return [SampleOut.model_validate(r) for r in rows]


@router.post("/samples/{sample_id}/execute", response_model=SampleOut,
             summary="审计员回填抽凭执行结果（执行记录留痕）")
def execute_sample(
    sample_id: str,
    body: ExecuteIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SampleOut:
    try:
        row = anomaly.execute_sample(db, user, sample_id, exec_note=body.exec_note)
    except (anomaly.NotFound, anomaly.Locked, anomaly.ProjectFrozen, ValueError) as exc:
        raise _http_for(exc) from exc
    return SampleOut.model_validate(row)
