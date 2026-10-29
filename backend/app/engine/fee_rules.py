"""业务收费校验基准（FR-01-06；数据即代码，随版本发布）。

《业务收费管理办法》Phase 1 口径：年报审计按客户资产总额分档给最低收费基准，
报价低于基准即校验不通过（阻断立项 + 差异项提示）；档位表调整随版本发布留痕。
"""
from __future__ import annotations

from decimal import Decimal

# (资产总额上限（元），最低收费（元）)：升序匹配首个满足档位
FEE_TIERS: tuple[tuple[Decimal, Decimal, str], ...] = (
    (Decimal("5_000_000"), Decimal("20_000"), "资产 500 万以下"),
    (Decimal("10_000_000"), Decimal("30_000"), "资产 500–1000 万"),
    (Decimal("50_000_000"), Decimal("50_000"), "资产 1000–5000 万"),
    (Decimal("999_999_999_999"), Decimal("80_000"), "资产 5000 万以上"),
)


def check_fee(total_assets: Decimal, fee_amount: Decimal) -> dict[str, object]:
    """收费合规校验（FR-01-06）：返回通过结论 + 差异项（供阻断立项时提示）。"""
    assets = Decimal(total_assets)
    fee = Decimal(fee_amount)
    if assets < 0 or fee < 0:
        raise ValueError("资产总额与收费不可为负")
    cap, min_fee, tier_label = next(
        (c, m, label) for c, m, label in FEE_TIERS if assets <= c
    )
    passed = fee >= min_fee
    return {
        "passed": passed,
        "tier_label": tier_label,
        "min_fee": float(min_fee),
        "offered_fee": float(fee),
        "gap": float(max(Decimal("0"), min_fee - fee)),
    }
