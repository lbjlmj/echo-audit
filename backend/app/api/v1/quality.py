"""质控域路由（§2.3 /quality）：FR-06-01 质控 AI 检查 / FR-06-03 阶段门 / FR-06-02+04 归档。

路由层薄：只做鉴权与出入参，规则在 services/quality（状态只走 engine，硬约束 2/3）；
错误码 QU-1xxx。质控 AI 检查的第三段复核标记复用 /worksheets/ai-runs/{run_no}/review。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ...core.deps import get_current_user
from ...db import get_db
from ...models.user import User
from ...schemas.quality import (
    ArchiveIn,
    ArchiveOut,
    ArchiveVerifyOut,
    CorrectionApproveIn,
    CorrectionArtifactOut,
    CorrectionOut,
    CorrectionQcReviewIn,
    CorrectionRegisterIn,
    CorrectionRevisionIn,
    CorrectionRevisionOut,
    GateOut,
    GatePassIn,
    GateSkipIn,
    InspectionPackIn,
    InspectionPackOut,
    PrecheckIn,
    PrecheckOut,
    ProjectOptionOut,
    QcCheckIn,
    QcCheckOut,
    QcRunOut,
    RearchiveOut,
)
from ...services import quality

router = APIRouter(prefix="/quality", tags=["quality"])


def _http_for(exc: Exception) -> HTTPException:
    """服务层领域异常 → QU-1xxx HTTP 映射（错误码按域分段）。"""
    if isinstance(exc, quality.NotFound):
        return HTTPException(404, f"QU-1002 {exc}")
    if isinstance(exc, quality.ArchiveBlocked):
        return HTTPException(409, f"{exc}：{exc.blockers}")
    if isinstance(exc, quality.TransitionRejected):
        return HTTPException(409, str(exc))
    if isinstance(exc, quality.ProjectFrozen):
        return HTTPException(409, f"QU-1005 {exc} 项目暂停期间阶段门冻结、禁止写操作")
    if isinstance(exc, quality.Locked):
        return HTTPException(409, str(exc))
    if isinstance(exc, quality.GateRejected):
        return HTTPException(422, str(exc))
    if isinstance(exc, ValueError):  # engine 参数拒绝（非法阶段/双批身份不符等）
        return HTTPException(422, str(exc))
    return HTTPException(500, "QU-1999 内部错误")


@router.post("/qc-checks", response_model=QcCheckOut, status_code=201,
             summary="发起质控 AI 检查（FR-06-01：底稿完整性/交叉索引/程序缺口/复核点清单，产物为线索态）")
def run_qc_check(
    body: QcCheckIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> QcCheckOut:
    try:
        orm, output = quality.run_qc_check(db, user, body.project_id)
    except (quality.NotFound, quality.Locked, quality.ProjectFrozen) as exc:
        raise _http_for(exc) from exc
    return QcCheckOut(
        run_no=orm.run_no, status=orm.status, output_digest=orm.output_digest,
        checks=output["checks"], failed_items=output["failed_items"], passed=output["passed"],
    )


@router.get("/qc-checks", response_model=list[QcRunOut],
            summary="质控检查留痕清单（线索态可见，第三段复核标记状态透明）")
def list_qc_checks(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[QcRunOut]:
    return [QcRunOut.model_validate(r) for r in quality.list_qc_checks(db, project_id)]


@router.post("/inspection-pack", response_model=InspectionPackOut, status_code=201,
             summary="检查应对材料包（FR-06-05：锚点反查「这条程序依据哪条准则」+ 质控报表，一键出包）")
def build_inspection_pack(
    body: InspectionPackIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> InspectionPackOut:
    try:
        data = quality.build_inspection_pack(db, user, body.project_id)
    except quality.NotFound as exc:
        raise _http_for(exc) from exc
    return InspectionPackOut(**data)


@router.post("/gates/{stage}/pass", response_model=GateOut,
             summary="阶段门通过（FR-06-03：engine 权限卡控 + 准入条件裁决，快照落库）")
def pass_stage_gate(
    stage: str,
    body: GatePassIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> GateOut:
    try:
        gate = quality.pass_stage_gate(db, user, body.project_id, stage)
    except (
        quality.NotFound, quality.GateRejected, quality.ProjectFrozen, ValueError,
    ) as exc:
        raise _http_for(exc) from exc
    return GateOut.model_validate(gate)


@router.post("/gates/{stage}/skip", response_model=GateOut,
             summary="阶段门跳过（FR-06-03 + R3：合伙人批准 + 质控双批，双批人落库）")
def skip_stage_gate(
    stage: str,
    body: GateSkipIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> GateOut:
    try:
        gate = quality.skip_stage_gate(
            db, user, body.project_id, stage, qc_reviewer_id=body.qc_reviewer_id
        )
    except (
        quality.NotFound, quality.GateRejected, quality.ProjectFrozen, ValueError,
    ) as exc:
        raise _http_for(exc) from exc
    return GateOut.model_validate(gate)


@router.get("/projects", response_model=list[ProjectOptionOut],
            summary="质控驾驶舱项目清单（archive.access ≥ VIEW，含已归档/终止——归档调阅场景）")
def list_archive_projects(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ProjectOptionOut]:
    return [ProjectOptionOut.model_validate(p) for p in quality.list_archive_projects(db, user)]


@router.get("/gates", response_model=list[GateOut],
            summary="八门状态清单（FR-06-03 验收口径：门状态与跳过记录全程可见）")
def list_gates(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[GateOut]:
    return [GateOut.model_validate(g) for g in quality.list_gates(db, project_id)]


@router.post("/archive/precheck", response_model=PrecheckOut,
             summary="归档前完整性校验（FR-06-02：缺复核标记/断链/未复核底稿/未签发报告 → 阻断清单）")
def precheck_archive(
    body: PrecheckIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PrecheckOut:
    try:
        return PrecheckOut(**quality.precheck_archive(db, body.project_id))
    except quality.NotFound as exc:
        raise _http_for(exc) from exc


@router.post("/archive", response_model=ArchiveOut,
             summary="一键归档（FR-06-04：校验→封包哈希→项目+底稿双状态机收口「已归档」）")
def archive_project(
    body: ArchiveIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ArchiveOut:
    try:
        result = quality.archive_project(db, user, body.project_id)
    except (
        quality.NotFound, quality.ArchiveBlocked, quality.TransitionRejected,
        quality.ProjectFrozen, quality.Locked, ValueError,
    ) as exc:
        raise _http_for(exc) from exc
    project = result["project"]
    return ArchiveOut(
        project_id=project.id, status=project.status, stage=project.current_stage,
        archive_package_hash=result["package_hash"],
        archive_object_key=result.get("archive_object_key"),
        archived_worksheets=result["archived_worksheets"], ai_runs=result["ai_runs"],
        knowledge_proposals=result["knowledge_proposals"],
    )


@router.get("/archive/{project_id}/package",
            summary="下载归档实包（FR-06-04：zip=底稿/报告产物+AI留痕+manifest+SHA256SUMS；未归档 409）")
def download_archive_package(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    try:
        object_key, data = quality.get_archive_package(db, user, project_id)
    except (quality.NotFound, quality.Locked) as exc:
        raise _http_for(exc) from exc
    filename = object_key.rsplit("/", 1)[-1]
    return Response(
        content=data, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/archive/{project_id}/verify", response_model=ArchiveVerifyOut,
             summary="归档包防篡改校验（FR-06-04：成员哈希逐项比对+包库自证；失败 422 QU-1009）")
def verify_archive_package(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ArchiveVerifyOut:
    try:
        data = quality.verify_archive_package(db, user, project_id)
    except (quality.NotFound, quality.Locked, ValueError) as exc:
        raise _http_for(exc) from exc
    return ArchiveVerifyOut(**data)


# ---------------------------------------------------------------------------
# §15-16 归档后差错处理链（登记→批准→副本修订→质控复核→重新归档 v(n+1)）
# ---------------------------------------------------------------------------
@router.post("/corrections", response_model=CorrectionOut, status_code=201,
             summary="差错登记（§15-16：质控专员对已归档项目登记；同项目仅一张在途单）")
def register_correction(
    body: CorrectionRegisterIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CorrectionOut:
    try:
        row = quality.register_correction(
            db, user, body.project_id,
            description=body.description, affected_worksheet_ids=body.affected_worksheet_ids,
        )
    except (quality.NotFound, quality.Locked, ValueError) as exc:
        raise _http_for(exc) from exc
    return CorrectionOut.model_validate(row)


@router.get("/corrections", response_model=list[CorrectionOut],
            summary="差错单清单（§15-16：新旧整包哈希随单留痕，双版本可查）")
def list_corrections(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[CorrectionOut]:
    try:
        rows = quality.list_corrections(db, user, project_id)
    except quality.NotFound as exc:
        raise _http_for(exc) from exc
    return [CorrectionOut.model_validate(r) for r in rows]


@router.post("/corrections/{correction_id}/approve", response_model=CorrectionOut,
             summary="差错批准/驳回（§15-16：合伙人专属；批准即解锁副本修订，驳回必附原因）")
def approve_correction(
    correction_id: str,
    body: CorrectionApproveIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CorrectionOut:
    try:
        row = quality.approve_correction(
            db, user, correction_id, approve=body.approve, reason=body.reason
        )
    except (quality.NotFound, quality.TransitionRejected, ValueError) as exc:
        raise _http_for(exc) from exc
    return CorrectionOut.model_validate(row)


@router.post("/corrections/{correction_id}/artifacts", response_model=CorrectionArtifactOut, status_code=201,
             summary="修订产物受控上传（§15-16：raw bytes 直传对象存储，内容寻址键；凭 file_key 登记版本链）")
def upload_correction_artifact(
    correction_id: str,
    worksheet_id: str,
    filename: str,
    content: bytes = Body(default=b"", media_type="application/octet-stream"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CorrectionArtifactOut:
    try:
        data = quality.upload_correction_artifact(
            db, user, correction_id,
            worksheet_id=worksheet_id, filename=filename, content=content,
        )
    except (quality.NotFound, quality.Locked, ValueError) as exc:
        raise _http_for(exc) from exc
    return CorrectionArtifactOut(**data)


@router.post("/corrections/{correction_id}/revisions", response_model=CorrectionRevisionOut, status_code=201,
             summary="副本修订登记（§15-16：版本链追加 v(n+1)，原稿版本与 v(n) 包锁定保留）")
def register_correction_revision(
    correction_id: str,
    body: CorrectionRevisionIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CorrectionRevisionOut:
    try:
        ver = quality.register_correction_revision(
            db, user, correction_id,
            worksheet_id=body.worksheet_id, file_key=body.file_key,
            change_summary=body.change_summary,
        )
    except (quality.NotFound, quality.Locked, ValueError) as exc:
        raise _http_for(exc) from exc
    return CorrectionRevisionOut.model_validate(ver)


@router.post("/corrections/{correction_id}/submit-review", response_model=CorrectionOut,
             summary="提交质控复核（§15-16：受影响底稿逐稿须已登记差错修订版本）")
def submit_correction_review(
    correction_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CorrectionOut:
    try:
        row = quality.submit_correction_review(db, user, correction_id)
    except (quality.NotFound, quality.TransitionRejected, ValueError) as exc:
        raise _http_for(exc) from exc
    return CorrectionOut.model_validate(row)


@router.post("/corrections/{correction_id}/qc-review", response_model=CorrectionOut,
             summary="差错质控复核（§15-16：质控专员专属；退回必附意见回修订中）")
def qc_review_correction(
    correction_id: str,
    body: CorrectionQcReviewIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CorrectionOut:
    try:
        row = quality.qc_review_correction(
            db, user, correction_id, conclusion=body.conclusion, opinion=body.opinion
        )
    except (quality.NotFound, quality.TransitionRejected, ValueError) as exc:
        raise _http_for(exc) from exc
    return CorrectionOut.model_validate(row)


@router.post("/corrections/{correction_id}/rearchive", response_model=RearchiveOut,
             summary="重新归档 v(n+1)（§15-16：manifest 含 supersedes；旧包 WORM 不动，双哈希留痕）")
def rearchive_project(
    correction_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RearchiveOut:
    try:
        result = quality.rearchive_project(db, user, correction_id)
    except (quality.NotFound, quality.Locked, quality.TransitionRejected, ValueError) as exc:
        raise _http_for(exc) from exc
    row = result["correction"]
    return RearchiveOut(
        correction_id=row.id, correction_code=row.code, status=row.status,
        project_id=row.project_id, previous_package_hash=result["previous_package_hash"],
        archive_package_hash=result["package_hash"],
        archive_object_key=result["archive_object_key"], zip_sha256=result["zip_sha256"],
        zip_members=result["zip_members"], missing_objects=result["missing_objects"],
    )
