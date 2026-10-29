"""重要性水平计算（共享规则 R8，PRD §7/§15-5）——唯一实现点。

默认基准（按主体类型预置）：
- 盈利主体：营业收入 × 0.5%
- 成本费用化主体：费用总额 × 1%
- 资产基础型主体：总资产 × 0.5%
实际执行比例：50%–75%，默认 60%；明显微小：默认 5%。
项目经理可修改，修改记留痕，随审计计划一并报合伙人审批确认。
"""
from __future__ import annotations

DEFAULT_EXEC_RATIO = 0.60
EXEC_RATIO_RANGE = (0.50, 0.75)
DEFAULT_TRIVIAL_RATIO = 0.05

# 基准类型 -> (默认比例, 基准说明)
BASIS_DEFAULTS: dict[str, tuple[float, str]] = {
    "营业收入": (0.005, "盈利主体"),
    "费用总额": (0.010, "成本费用化主体"),
    "总资产": (0.005, "资产基础型主体"),
}


class MaterialityError(ValueError):
    pass


def compute_materiality(
    basis_type: str,
    basis_amount: float,
    exec_ratio: float = DEFAULT_EXEC_RATIO,
    trivial_ratio: float = DEFAULT_TRIVIAL_RATIO,
) -> dict[str, object]:
    """计算整体重要性、实际执行重要性、明显微小错报临界值。"""
    if basis_type not in BASIS_DEFAULTS:
        raise MaterialityError(
            f"非法基准类型：{basis_type}，合法值：{list(BASIS_DEFAULTS)}（修改默认比例需留痕并报合伙人审批）"
        )
    if basis_amount <= 0:
        raise MaterialityError("基准金额必须为正数")
    ratio, subject_hint = BASIS_DEFAULTS[basis_type]
    if not (EXEC_RATIO_RANGE[0] <= exec_ratio <= EXEC_RATIO_RANGE[1]):
        raise MaterialityError(
            f"实际执行比例须在 {EXEC_RATIO_RANGE[0]:.0%}–{EXEC_RATIO_RANGE[1]:.0%}，当前 {exec_ratio:.0%}"
        )
    if not (0 < trivial_ratio < 0.10):
        raise MaterialityError("明显微小比例默认 5%，超出合理区间（0–10%）需留痕说明")
    overall = basis_amount * ratio
    return {
        "basis_type": basis_type,
        "basis_amount": basis_amount,
        "subject_hint": subject_hint,
        "overall_materiality": round(overall, 2),
        "performance_materiality": round(overall * exec_ratio, 2),
        "exec_ratio": exec_ratio,
        "clearly_trivial_threshold": round(overall * trivial_ratio, 2),
        "trivial_ratio": trivial_ratio,
    }
