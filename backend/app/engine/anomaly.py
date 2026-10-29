"""全量异常 AI 评分引擎（FR-03-04，§15-8）——唯一实现点。

- 孤立森林 + 本福特定律：纯 Python 零依赖（硬约束 10），固定随机种子（SEED）保证
  同批数据评分完全可复现——评分版本号 SCORE_VERSION 记入 AI 留痕（§15-8 可出证复算）；
- 抽凭清单默认规则单点：评分 Top 10% ∪ 单科目评分最高 5 笔，合计不少于 20 笔；
  项目经理可调整，下限 5%（调整动作与留痕由服务层执行，下限计算在本单点）；
- 本福特定律为科目级佐证指标（首数字分布偏离），不直接改变单笔排序。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

SEED = 2025
TREE_COUNT = 50
SUBSAMPLE = 256  # 子采样规模 ψ
TOP_RATIO = 0.10  # 默认抽取：评分 Top 10%
PER_ACCOUNT_TOP = 5  # 单科目评分最高 5 笔
MIN_TOTAL = 20  # 合计不少于 20 笔
MIN_ADJUST_RATIO = 0.05  # 项目经理调整下限 5%
BENFORD_MAX_DEV = 0.08  # 首数字分布最大偏差阈值（科目级佐证）

SCORE_VERSION = f"iforest-{TREE_COUNT}trees-seed{SEED}"

SRC_TOP = "评分Top10%"
SRC_ACCOUNT = "科目Top5"
SRC_MIN = "保底20笔"
SRC_MANUAL = "人工调整"


@dataclass(frozen=True)
class VoucherRow:
    voucher_id: str
    account_code: str
    amount: float


@dataclass(frozen=True)
class ScoredVoucher:
    voucher_id: str
    account_code: str
    score: float
    features: tuple[float, float, float]


class AnomalyError(ValueError):
    pass


# ---------------------------------------------------------------------------
# 孤立森林（iForest，Liu et al. 2008 口径）：路径长度越短越异常
# ---------------------------------------------------------------------------
def _c(n: int) -> float:
    """BST 平均搜索路径长度常数 c(n)（欧拉常数近似）。"""
    if n <= 1:
        return 0.0
    return 2.0 * (math.log(n - 1) + 0.5772156649) - 2.0 * (n - 1) / n


def _features(rows: list[VoucherRow]) -> list[tuple[float, float, float]]:
    """三维特征：金额对数、科目内 z 偏离、占科目发生额比。"""
    by_acct: dict[str, list[float]] = {}
    for r in rows:
        by_acct.setdefault(r.account_code, []).append(r.amount)
    stats: dict[str, tuple[float, float, float]] = {}
    for code, amts in by_acct.items():
        mean = sum(amts) / len(amts)
        var = sum((a - mean) ** 2 for a in amts) / len(amts)
        std = math.sqrt(var) or 1.0
        stats[code] = (mean, std, sum(amts) or 1.0)
    return [
        (
            math.log1p(max(r.amount, 0.0)),
            abs(r.amount - stats[r.account_code][0]) / stats[r.account_code][1],
            r.amount / stats[r.account_code][2],
        )
        for r in rows
    ]


class _Node:
    __slots__ = ("size", "attr", "split", "left", "right")

    def __init__(
        self,
        size: int,
        attr: int | None = None,
        split: float | None = None,
        left: "_Node | None" = None,
        right: "_Node | None" = None,
    ) -> None:
        self.size = size
        self.attr = attr
        self.split = split
        self.left = left
        self.right = right


def _build_tree(
    data: list[tuple[float, float, float]], depth: int, rng: random.Random, max_depth: int
) -> _Node:
    if len(data) <= 1 or depth >= max_depth:
        return _Node(len(data))
    attr = rng.randrange(len(data[0]))
    lo = min(d[attr] for d in data)
    hi = max(d[attr] for d in data)
    if lo == hi:  # 该特征无区分度 → 叶
        return _Node(len(data))
    split = rng.uniform(lo, hi)
    left = [d for d in data if d[attr] < split]
    right = [d for d in data if d[attr] >= split]
    return _Node(
        len(data), attr, split,
        _build_tree(left, depth + 1, rng, max_depth),
        _build_tree(right, depth + 1, rng, max_depth),
    )


def _path_length(x: tuple[float, float, float], root: _Node) -> float:
    node, depth = root, 0
    while node.split is not None:
        node = node.left if x[node.attr] < node.split else node.right
        depth += 1
    return depth + _c(node.size)


def score_vouchers(rows: list[VoucherRow]) -> list[ScoredVoucher]:
    """100% 逐笔异常评分：s(x, ψ) = 2^(-E(h)/c(ψ))，得分降序；固定种子可复现。"""
    if not rows:
        raise AnomalyError("凭证数据为空，无法评分")
    feats = _features(rows)
    n = len(rows)
    psi = min(SUBSAMPLE, n)
    max_depth = math.ceil(math.log2(max(psi, 2)))
    rng = random.Random(SEED)
    trees = [
        _build_tree([feats[i] for i in rng.sample(range(n), psi)], 0, rng, max_depth)
        for _ in range(TREE_COUNT)
    ]
    norm = _c(psi) or 1.0
    scored = [
        ScoredVoucher(
            r.voucher_id, r.account_code,
            round(2 ** (-(sum(_path_length(feats[i], t) for t in trees) / TREE_COUNT) / norm), 6),
            feats[i],
        )
        for i, r in enumerate(rows)
    ]
    scored.sort(key=lambda v: (-v.score, v.voucher_id))
    return scored


# ---------------------------------------------------------------------------
# 本福特定律（科目级佐证）：首数字 d 出现频率应趋近 log10(1 + 1/d)
# ---------------------------------------------------------------------------
def benford_check(rows: list[VoucherRow]) -> dict[str, dict[str, object]]:
    """按科目计算首数字分布最大偏差；≥10 笔且偏差超阈值标记存疑。"""
    by_acct: dict[str, list[float]] = {}
    for r in rows:
        if r.amount > 0:
            by_acct.setdefault(r.account_code, []).append(r.amount)
    expected = {d: math.log10(1 + 1 / d) for d in range(1, 10)}

    def _dev(amts: list[float]) -> float:
        counts = {d: 0 for d in range(1, 10)}
        for a in amts:
            first = int(str(f"{a:.6f}").lstrip("0.")[0]) if a > 0 else 0
            if first in counts:
                counts[first] += 1
        total = sum(counts.values()) or 1
        return max(abs(counts[d] / total - expected[d]) for d in range(1, 10))

    result: dict[str, dict[str, object]] = {}
    for code, amts in sorted(by_acct.items()):
        dev = round(_dev(amts), 4)
        result[code] = {
            "count": len(amts),
            "max_dev": dev,
            "suspicious": bool(len(amts) >= 10 and dev > BENFORD_MAX_DEV),
        }
    if rows:
        all_amts = [r.amount for r in rows if r.amount > 0]
        dev = round(_dev(all_amts), 4)
        result["ALL"] = {
            "count": len(all_amts), "max_dev": dev,
            "suspicious": bool(len(all_amts) >= 10 and dev > BENFORD_MAX_DEV),
        }
    return result


# ---------------------------------------------------------------------------
# 抽凭清单规则（FR-03-04 默认口径，单点）
# ---------------------------------------------------------------------------
def build_sampling_list(
    scored: list[ScoredVoucher],
    *,
    top_ratio: float = TOP_RATIO,
    per_account_top: int = PER_ACCOUNT_TOP,
    min_total: int = MIN_TOTAL,
) -> list[dict[str, str]]:
    """评分 Top 10% ∪ 单科 Top 5；不足 min_total 按评分降序补足（来源标记供留痕区分）。"""
    if not scored:
        raise AnomalyError("评分结果为空，无法生成抽凭清单")
    top_n = max(1, math.ceil(len(scored) * top_ratio))
    selected: dict[str, str] = {}
    for v in scored[:top_n]:
        selected[v.voucher_id] = SRC_TOP
    by_acct: dict[str, list[ScoredVoucher]] = {}
    for v in scored:
        by_acct.setdefault(v.account_code, []).append(v)
    for vs in by_acct.values():
        for v in vs[:per_account_top]:
            selected.setdefault(v.voucher_id, SRC_ACCOUNT)
    if len(selected) < min_total:
        for v in scored:  # scored 已按评分降序
            if len(selected) >= min_total:
                break
            selected.setdefault(v.voucher_id, SRC_MIN)
    return [{"voucher_id": vid, "source": src} for vid, src in selected.items()]


def min_sample_size(total_vouchers: int) -> int:
    """项目经理调整下限：凭证总数的 5%（向上取整，至少 1 笔）。"""
    return max(1, math.ceil(total_vouchers * MIN_ADJUST_RATIO))
