"""函证超期裁决规则（§4.4、§15-7、FR-04-04）——纯函数单点，Beat 扫描与服务共用。

发出满 30 天未回函 → 裁决转「超期未回」；此后每 15 天提醒一次（上限 2 次）；
累计两次提醒后建议启动替代程序（仅建议留痕，不自动转态——替代程序由人工执行）。
提醒去重所需已提醒次数由调用方经 remind_count 传入（纯函数无副作用）。
"""
from __future__ import annotations

from datetime import date

OVERDUE_DAYS = 30
REMIND_INTERVAL_DAYS = 15
MAX_REMINDS = 2


def decide_overdue_status(
    status: str, issued_at: date, today: date, *, remind_count: int = 0
) -> dict[str, object]:
    """超期裁决：返回 {transition, remind_total, remind_due, suggest_alternative}。

    - transition：仅「已发出」满 30 天裁决转「超期未回」，其余状态一律 None；
    - remind_total：超期到期提醒总次数 = min(2, (超期天数 // 15))，30–44 天 0 次、
      45–59 天 1 次、60 天起 2 次；
    - remind_due：当前应补提醒（remind_total > remind_count）；
    - suggest_alternative：累计两次提醒后出具替代程序建议。
    """
    days = (today - issued_at).days
    if days < OVERDUE_DAYS:
        return {
            "transition": None, "remind_total": 0,
            "remind_due": False, "suggest_alternative": False,
        }
    remind_total = min(MAX_REMINDS, (days - OVERDUE_DAYS) // REMIND_INTERVAL_DAYS)
    remind_due = remind_total > remind_count
    suggest_alternative = remind_total >= MAX_REMINDS or remind_count >= MAX_REMINDS
    transition = "超期未回" if status == "已发出" else None
    return {
        "transition": transition,
        "remind_total": remind_total,
        "remind_due": remind_due,
        "suggest_alternative": suggest_alternative,
    }
