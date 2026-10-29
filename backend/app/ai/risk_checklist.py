"""AI 模块风险前置校验（红线 6、§15-10）。

每个 ai_* 模块运行前逐条核对 10 项风险清单，清单未过不推理；
清单为配置资产：质控专员每年至少修订一次，ITC 评估触发即时修订，
修订记录随版本发布留档。
"""
from __future__ import annotations

from .pipeline import RISK_CHECKLIST


def check_risk_checklist(module: str, answers: dict[str, bool]) -> list[str]:
    """逐条核对 10 项；返回未通过项（非空则拒绝推理）。"""
    missing = [item for item in RISK_CHECKLIST if item not in answers]
    if missing:
        raise ValueError(f"模块 {module} 风险清单核对不完整，缺少：{missing}")
    failed = [item for item, ok in answers.items() if not ok]
    return failed
