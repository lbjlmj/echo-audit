"""函证超期裁决与阶段门/命名引擎测试。"""
import datetime as dt

import pytest

from app.engine.confirmation_rules import decide_overdue_status
from app.engine.naming import CATEGORY_CODES, NamingError, build_document_name
from app.engine.stage_gate import STAGES, pass_gate


class TestOverdue:
    def test_within_30_days_no_transition(self):
        issued = dt.date(2026, 10, 1)
        r = decide_overdue_status("已发出", issued, dt.date(2026, 10, 20))
        assert r == {"transition": None, "remind_total": 0, "remind_due": False, "suggest_alternative": False}

    def test_day30_transitions_without_remind(self):
        issued = dt.date(2026, 10, 1)
        r = decide_overdue_status("已发出", issued, dt.date(2026, 10, 31))
        assert r["transition"] == "超期未回"
        assert r["remind_total"] == 0 and not r["remind_due"]

    def test_first_remind_at_day45(self):
        issued = dt.date(2026, 10, 1)
        r = decide_overdue_status("已发出", issued, dt.date(2026, 11, 15))  # 30+15
        assert r["remind_total"] == 1
        assert r["remind_due"] and not r["suggest_alternative"]

    def test_second_remind_suggests_alternative(self):
        issued = dt.date(2026, 10, 1)
        r = decide_overdue_status("已发出", issued, dt.date(2026, 11, 30))  # 30+30
        assert r["remind_total"] == 2
        assert r["remind_due"] and r["suggest_alternative"]  # 累计两次提醒后出具替代程序建议

    def test_only_issued_status_affected(self):
        r = decide_overdue_status("台账已生成", dt.date(2026, 1, 1), dt.date(2026, 12, 1))
        assert r["transition"] is None


class TestStageGate:
    def test_all_8_stages(self):
        assert len(STAGES) == 8
        assert STAGES[0] == "承接" and STAGES[-1] == "归档"

    def test_gate_blocked_on_missing_conditions(self):
        with pytest.raises(ValueError, match="confirmations_closed"):
            pass_gate("实质性", {"programs_executed": True})

    def test_gate_passes_when_conditions_met(self):
        assert pass_gate("实质性", {"confirmations_closed": True, "programs_executed": True}) == "已通过"


class TestNaming:
    def test_build_name(self):
        assert build_document_name("P2026-001", "某某公司", 2026, "JH") == "P2026-001-某某公司-2026-JH"

    def test_category_enum_has_10_codes(self):
        assert len(CATEGORY_CODES) == 10
        with pytest.raises(NamingError):
            build_document_name("P1", "C", 2026, "XX")
