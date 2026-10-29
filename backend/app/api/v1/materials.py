"""资料域路由（§2.3 /materials）：FR-03-01 材料登记/补齐/解析/缺口 + §15-6 字段映射 + 凭证行导入。

路由层薄：只做鉴权与出入参，规则在 services/materials；三层缺口拦截（不出半成品）
由 services/worksheets 出稿前校验（WS-1003），本域维护材料行与缺口状态。错误码 MA-1xxx。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...core.deps import get_current_user
from ...db import get_db
from ...models.user import User
from ...schemas.material import (
    GapsOut,
    BatchArriveIn,
    BatchArriveOut,
    ChecklistOut,
    ClassifyOut,
    ConfirmClassifyIn,
    MappingIn,
    MappingOut,
    MaterialIn,
    MaterialOut,
    ParseIn,
    VoucherLineIn,
    VoucherLineOut,
)
from ...services import materials

router = APIRouter(prefix="/materials", tags=["materials"])


def _http_for(exc: Exception) -> HTTPException:
    """服务层领域异常 → MA-1xxx HTTP 映射（错误码按域分段）。"""
    if isinstance(exc, materials.NotFound):
        return HTTPException(404, str(exc))
    if isinstance(exc, materials.ProjectFrozen):
        return HTTPException(409, f"MA-1005 {exc} 项目暂停期间禁止写操作")
    if isinstance(exc, materials.Locked):
        return HTTPException(409, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(422, str(exc))
    return HTTPException(500, "MA-1999 内部错误")


@router.post("", response_model=MaterialOut, status_code=201,
             summary="登记三层级材料（FR-03-01：必需未到位挂缺口标志，补齐后解除）")
def register_material(
    body: MaterialIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MaterialOut:
    try:
        row = materials.register_material(
            db, user, body.project_id,
            level=body.level, file_key=body.file_key, required=body.required,
        )
    except (materials.NotFound, materials.Locked, materials.ProjectFrozen, ValueError) as exc:
        raise _http_for(exc) from exc
    return MaterialOut.model_validate(row)


@router.get("", response_model=list[MaterialOut], summary="项目材料清单")
def list_materials(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[MaterialOut]:
    return [MaterialOut.model_validate(r) for r in materials.list_materials(db, project_id)]


# ---------------------------------------------------------------------------
# FR-01-05 资料清单前置（scope 快照 × 客户层材料实时匹配；按清单一次性补齐）
# ---------------------------------------------------------------------------
@router.get("/checklist", response_model=ChecklistOut,
            summary="客户资料清单（FR-01-05：立项 scope 快照生成，到料状态一一对应、缺口实时可见）")
def project_checklist(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChecklistOut:
    try:
        data = materials.project_checklist(db, user, project_id)
    except (materials.NotFound, ValueError) as exc:
        raise _http_for(exc) from exc
    return ChecklistOut.model_validate(data)


@router.post("/checklist/batch-arrive", response_model=BatchArriveOut, status_code=201,
             summary="按清单一次性补齐（FR-01-05：批量登记客户层材料，逐项留痕并回显到料进度）")
def batch_arrive_checklist(
    body: BatchArriveIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BatchArriveOut:
    try:
        data = materials.batch_arrive_checklist(
            db, user, body.project_id,
            entries=[e.model_dump() for e in body.entries],
        )
    except (materials.NotFound, materials.ProjectFrozen, materials.Locked, ValueError) as exc:
        raise _http_for(exc) from exc
    return BatchArriveOut.model_validate(data)


# ---------------------------------------------------------------------------
# FR-01-04 资料自动分类（线索 → 审计员确认采信 → R6 重命名归档，FR-01-03 接线）
# ---------------------------------------------------------------------------
@router.post("/classify", response_model=ClassifyOut, status_code=201,
             summary="资料自动分类（FR-01-04：关键词归入决策档案目录，产物为线索态）")
def classify_materials(
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ClassifyOut:
    try:
        data = materials.classify_materials(db, user, body["project_id"])
    except (materials.NotFound, materials.ProjectFrozen, ValueError) as exc:
        raise _http_for(exc) from exc
    return ClassifyOut.model_validate(data)


@router.post("/classify/confirm", response_model=dict,
             summary="分类确认（第三段标记：采信→R6 重命名归档；不采信必附原因）")
def confirm_classification(
    body: ConfirmClassifyIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    try:
        row = materials.confirm_classification(
            db, user, body.run_no,
            entries=[e.model_dump() for e in body.entries],
            conclusion=body.conclusion, rejection_reason=body.rejection_reason,
        )
    except (materials.NotFound, materials.ProjectFrozen, materials.Locked, ValueError) as exc:
        raise _http_for(exc) from exc
    return {"run_no": row.run_no, "status": row.status,
            "rejection_reason": row.rejection_reason}


@router.get("/gaps", response_model=GapsOut,
            summary="缺口清单（FR-03-01：资料不齐列出缺口待补；三层级缺层一并列出）")
def list_gaps(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> GapsOut:
    return GapsOut(**materials.list_gaps(db, project_id))


@router.post("/{material_id}/arrive", response_model=MaterialOut,
             summary="材料补齐（缺口 → 到位，待解析）")
def mark_arrived(
    material_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MaterialOut:
    try:
        row = materials.mark_arrived(db, user, material_id)
    except (materials.NotFound, materials.ProjectFrozen, materials.Locked, ValueError) as exc:
        raise _http_for(exc) from exc
    return MaterialOut.model_validate(row)


@router.post("/{material_id}/parse", response_model=MaterialOut,
             summary="解析结果回写（待解析/失败→已解析/失败；缺口登记不可解析）")
def parse_material(
    material_id: str,
    body: ParseIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MaterialOut:
    try:
        row = materials.parse_material(
            db, user, material_id, ok=body.ok, vector_ref=body.vector_ref
        )
    except (materials.NotFound, materials.ProjectFrozen, materials.Locked, ValueError) as exc:
        raise _http_for(exc) from exc
    return MaterialOut.model_validate(row)


@router.post("/import-mappings", response_model=MappingOut, status_code=201,
             summary="创建字段映射（§15-6：按类型跑最低字段校验，人工映射一次、同客户复用）")
def create_mapping(
    body: MappingIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MappingOut:
    try:
        row = materials.create_mapping(
            db, user, client_id=body.client_id, source_system=body.source_system,
            mapping_type=body.mapping_type, field_mapping=body.field_mapping,
            reuse_scope=body.reuse_scope,
        )
    except ValueError as exc:
        raise _http_for(exc) from exc
    return MappingOut.model_validate(row)


@router.get("/import-mappings", response_model=list[MappingOut],
            summary="同客户字段映射查询（§15-6 复用）")
def list_mappings(
    client_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[MappingOut]:
    return [MappingOut.model_validate(r) for r in materials.list_mappings(db, client_id)]


@router.post("/{material_id}/voucher-lines", response_model=list[VoucherLineOut], status_code=201,
             summary="导入明细账凭证行（已解析材料 → VoucherLine，供 FR-03-04 异常评分）")
def import_voucher_lines(
    material_id: str,
    body: list[VoucherLineIn],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[VoucherLineOut]:
    try:
        rows = materials.import_voucher_lines(
            db, user, material_id, lines=[l.model_dump() for l in body]
        )
    except (materials.NotFound, materials.ProjectFrozen, materials.Locked, ValueError) as exc:
        raise _http_for(exc) from exc
    return [VoucherLineOut.model_validate(r) for r in rows]
