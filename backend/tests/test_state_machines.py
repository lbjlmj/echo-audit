"""状态机测试（TECH_DESIGN §7：全流转与非法流转拦截）。"""
import pytest

from app.engine.state_machines import (
    CONFIRMATION,
    CORRECTION,
    FINDING,
    PROJECT,
    WORKSHEET,
    IllegalTransition,
)


class TestWorksheet:
    def test_full_happy_path(self):
        s = "草稿"
        s = WORKSHEET.transit(s, "待复核", submitted_by="u1", ai_review_marked=True)
        s = WORKSHEET.transit(s, "复核中", reviewer="u2")
        s = WORKSHEET.transit(s, "复核通过", reviewer="u2")
        s = WORKSHEET.transit(s, "终审通过", approver="u3", is_important=True)
        assert WORKSHEET.transit(s, "已归档", archived_with_project=True) == "已归档"

    def test_reject_return_requires_opinion(self):
        with pytest.raises(IllegalTransition, match="opinion"):
            WORKSHEET.transit("复核中", "草稿", reviewer="u2")

    def test_final_review_only_for_important(self):
        with pytest.raises(IllegalTransition, match="is_important"):
            WORKSHEET.transit("复核通过", "终审通过", approver="u3", is_important=False)

    def test_submit_blocked_without_ai_review_mark(self):
        # §7 R2：AI 内容未完成复核标记不得提交复核
        with pytest.raises(IllegalTransition, match="ai_review_marked"):
            WORKSHEET.transit("草稿", "待复核", submitted_by="u1", ai_review_marked=False)

    def test_illegal_jump_rejected(self):
        with pytest.raises(IllegalTransition):
            WORKSHEET.transit("草稿", "已归档")


class TestConfirmation:
    def test_overdue_flow(self):
        s = CONFIRMATION.transit("已发出", "超期未回", overdue_days=30)
        s = CONFIRMATION.transit(s, "替代程序", alt_test_doc="doc-key")
        assert CONFIRMATION.transit(s, "闭环", closed_by="u1") == "闭环"

    def test_difference_to_alternative(self):
        s = CONFIRMATION.transit("已回函", "差异处理", difference_record={"amount": 1})
        assert CONFIRMATION.can(s, "替代程序")

    def test_closed_is_terminal(self):
        assert not CONFIRMATION.can("闭环", "已回函")


class TestFinding:
    def test_exclude_requires_reason(self):
        with pytest.raises(IllegalTransition, match="reason"):
            FINDING.transit("已归集", "已排除", operator="u1")

    def test_close_blocked_on_escalation(self):
        # 涉及报告意见类型的分歧须先合伙人裁决（FR-05-05）
        with pytest.raises(IllegalTransition, match="escalation_cleared"):
            FINDING.transit("已解决", "已关闭", closer="u1", escalation_cleared=False)


class TestProject:
    def test_suspend_requires_partner(self):
        with pytest.raises(IllegalTransition, match="approved_by_partner"):
            PROJECT.transit("进行中", "暂停", approved_by_partner=None, reason="x")

    def test_terminate_requires_package_hash(self):
        with pytest.raises(IllegalTransition, match="termination_package_hash"):
            PROJECT.transit("进行中", "终止", approved_by_partner="p", termination_package_hash="")


class TestCorrection:
    """差错单六状态（§15-16：登记→批准→副本修订→质控复核→重新归档 v(n+1)）。"""

    def test_full_happy_path(self):
        s = CORRECTION.transit("待批准", "修订中", approved_by_partner="p1")
        s = CORRECTION.transit(s, "待质控复核", submitted_by="u1", revision_registered=True)
        s = CORRECTION.transit(s, "复核通过", qc_reviewer="qc1")
        assert CORRECTION.transit(s, "已重新归档", rearchive_package_hash="h" * 64) == "已重新归档"

    def test_reject_requires_reason(self):
        with pytest.raises(IllegalTransition, match="reason"):
            CORRECTION.transit("待批准", "已驳回", approved_by_partner="p1")

    def test_qc_return_requires_opinion(self):
        with pytest.raises(IllegalTransition, match="opinion"):
            CORRECTION.transit("待质控复核", "修订中", qc_reviewer="qc1")

    def test_rearchive_requires_package_hash(self):
        with pytest.raises(IllegalTransition, match="rearchive_package_hash"):
            CORRECTION.transit("复核通过", "已重新归档")

    def test_illegal_jump_rejected(self):
        with pytest.raises(IllegalTransition):
            CORRECTION.transit("待批准", "已重新归档")

    def test_terminal_states_closed(self):
        assert not CORRECTION.can("已重新归档", "修订中")
        assert not CORRECTION.can("已驳回", "修订中")
