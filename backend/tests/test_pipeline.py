"""AI 管道门禁测试（红线 1、§7 R2、§15-16）。"""
import pytest

from app.ai.pipeline import AIRun, PipelineViolation, assert_can_enter_business, mark_review, record_input, seal_output


def make_run() -> AIRun:
    run = AIRun(project_id="p1", module="ai_risk", skill_version="1.0.0",
                model_version="local-v1", triggered_by="u1", input_material_ids=[])
    record_input(run, "m1", b"balance sheet")
    seal_output(run, {"risks": 3}, "minio://ai/run1.json")
    return run


def test_lead_cannot_enter_business():
    run = make_run()
    assert run.is_lead_only
    with pytest.raises(PipelineViolation, match="复核标记"):
        assert_can_enter_business(run, "WorksheetVersion")


def test_mark_review_unlocks():
    run = make_run()
    mark_review(run, reviewer="u2", conclusion="采信")
    assert not run.is_lead_only
    assert_can_enter_business(run, "WorksheetVersion")


def test_rejection_requires_reason_category():
    run = make_run()
    with pytest.raises(PipelineViolation, match="原因分类"):
        mark_review(run, reviewer="u2", conclusion="不采信")
    mark_review(run, reviewer="u2", conclusion="不采信", rejection_reason="幻觉")
    assert run.rejection_reason == "幻觉"


def test_run_no_globally_unique():
    a, b = make_run(), make_run()
    assert a.run_no != b.run_no


def test_risk_checklist_blocks_incomplete():
    from app.ai.risk_checklist import check_risk_checklist
    from app.ai.pipeline import RISK_CHECKLIST
    with pytest.raises(ValueError, match="核对不完整"):
        check_risk_checklist("ai_risk", {"保密泄露": True})
    assert check_risk_checklist("ai_risk", {k: True for k in RISK_CHECKLIST}) == []
