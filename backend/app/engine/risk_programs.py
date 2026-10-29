"""风险分级程序包选择（共享规则 R5，FR-02-03）——唯一实现点。

规则：高/中风险 → 高风险全程序；低风险 → 低风险简化程序包，
且简化包必带准则最低底线标注（R5），缺失即拒绝。
"""
from __future__ import annotations

HIGH_FULL = "高风险全程序"
LOW_SIMPLIFIED = "低风险简化程序包"


class RiskProgramError(ValueError):
    pass


def select_package(risk_level: str, *, min_standard_note: str | None = None) -> tuple[str, str | None]:
    """按风险等级裁决程序包类型；返回 (程序包类型, 准则最低底线标注)。"""
    if risk_level in ("高", "中"):
        return HIGH_FULL, None
    if risk_level == "低":
        if not min_standard_note:
            raise RiskProgramError("低风险简化程序包必带准则最低底线标注（R5）")
        return LOW_SIMPLIFIED, min_standard_note
    raise RiskProgramError(f"非法风险等级：{risk_level}，合法值：高/中/低")
