"""权限矩阵测试（TECH_DESIGN §7：10 权限项 × 4 全局角色允许/拒绝断言，含 R1 职责分离）。"""
import pytest

from app.core.permissions import (
    AUDITOR,
    MANAGER,
    MATRIX,
    PARTNER,
    QC,
    ALL_ROLES,
    Level,
    PermissionDenied,
    can,
    check_preparer_reviewer_separation,
)


def test_matrix_covers_10_permissions_x_4_roles():
    assert len(ALL_ROLES) == 4
    for perm, row in MATRIX.items():
        assert set(row) == set(ALL_ROLES), f"{perm} 角色覆盖不全"


def test_partner_approves_project():
    assert can(PARTNER, "project.approve", required=Level.APPROVE)


def test_auditor_cannot_review():
    assert not can(AUDITOR, "worksheet.review_sign")


def test_manager_can_review_but_partner_final():
    assert can(MANAGER, "worksheet.review_sign")
    assert can(PARTNER, "worksheet.review_sign", required=Level.APPROVE)


def test_qc_can_write_knowledge_directly():
    # §15-3：质控专员直接写入（免自审）+ 审核他人提交
    assert can(QC, "knowledge.write", required=Level.OPERATE)


def test_preparer_reviewer_separation():
    with pytest.raises(PermissionDenied, match="R1"):
        check_preparer_reviewer_separation("u1", "u1")
    check_preparer_reviewer_separation("u1", "u2")  # 不抛即通过


def test_unknown_permission_or_role_raises():
    with pytest.raises(KeyError):
        can(PARTNER, "nonexistent.perm")
    with pytest.raises(KeyError):
        can("超级管理员", "project.approve")
