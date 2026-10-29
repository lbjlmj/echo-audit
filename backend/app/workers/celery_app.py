"""Celery 应用与 Beat 定时任务（TECH_DESIGN §2.1 workers/、§4.6 函证超期）。

Beat 每日扫描：
- 发出满 30 天未回函 → 自动转「超期未回」（§15-7）
- 此后每 15 天提醒一次，累计两次后出具替代程序建议（FR-04-04），全部动作留痕
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from ..core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "echo-audit",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)

celery_app.conf.beat_schedule = {
    "confirm-overdue-daily": {
        # 函证超期扫描：每日 08:30
        "task": "app.workers.tasks.confirm_overdue",
        "schedule": crontab(hour=8, minute=30),
    },
    "backup-daily": {
        # 备份触发（RPO≤24h）：每日 23:00（实际备份在 deploy/backup 脚本，此处登记调度位）
        "task": "app.workers.tasks.noop_backup_placeholder",
        "schedule": crontab(hour=23, minute=0),
    },
}
celery_app.conf.timezone = "Asia/Shanghai"
