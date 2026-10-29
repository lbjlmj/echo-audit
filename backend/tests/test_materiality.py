"""重要性水平 R8 测试。"""
import pytest

from app.engine.materiality import compute_materiality


def test_profit_entity_revenue_basis():
    r = compute_materiality("营业收入", 10_000_000)
    assert r["overall_materiality"] == 50_000.0          # 0.5%
    assert r["performance_materiality"] == 30_000.0      # ×60%
    assert r["clearly_trivial_threshold"] == 2_500.0     # ×5%


def test_expense_basis():
    assert compute_materiality("费用总额", 1_000_000)["overall_materiality"] == 10_000.0  # 1%


def test_asset_basis():
    assert compute_materiality("总资产", 20_000_000)["overall_materiality"] == 100_000.0  # 0.5%


def test_exec_ratio_bounds():
    with pytest.raises(ValueError, match="50%–75%"):
        compute_materiality("营业收入", 100, exec_ratio=0.80)
    r = compute_materiality("营业收入", 1_000, exec_ratio=0.50)
    assert r["performance_materiality"] == 2.5


def test_invalid_basis_rejected():
    with pytest.raises(ValueError, match="非法基准类型"):
        compute_materiality("净资产", 100)
