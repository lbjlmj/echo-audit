"""Celery 任务：parse_large_file / anomaly_scoring / confirm_overdue(Beat)。

confirm_overdue 已实装：超期裁决规则在 engine/confirmation_rules（纯函数单点），
服务在 services/confirmations.run_overdue_scan；其余任务按 FR 域逐步落位。
"""
from __future__ import annotations

from .celery_app import celery_app


@celery_app.task(name="app.workers.tasks.parse_large_file")
def parse_large_file(material_id: str) -> dict:
    """大文件解析（NFS-02）：骨架占位。"""
    return {"material_id": material_id, "status": "pending_impl"}


@celery_app.task(name="app.workers.tasks.anomaly_scoring")
def anomaly_scoring(project_id: str) -> dict:
    """异常评分（孤立森林/本福特定律，纯 Python 零依赖）：骨架占位。"""
    return {"project_id": project_id, "status": "pending_impl"}


@celery_app.task(name="app.workers.tasks.confirm_overdue")
def confirm_overdue() -> dict:
    """函证超期扫描（Beat 每日 08:30，§15-7）：

    遍历「已发出/超期未回」函证，经 engine.confirmation_rules.decide_overdue_status
    裁决——满 30 天转「超期未回」，此后每 15 天提醒一次（上限 2 次），
    累计两次后建议替代程序（只留痕不转态），全部动作续哈希链留痕。
    """
    from datetime import datetime, timezone

    from ..db import SessionLocal
    from ..services.confirmations import run_overdue_scan

    with SessionLocal() as db:
        result = run_overdue_scan(db, today=datetime.now(tz=timezone.utc).date())
        db.commit()
    return result


@celery_app.task(name="app.workers.tasks.noop_backup_placeholder")
def noop_backup_placeholder() -> dict:
    return {"status": "backup-delegated-to-deploy-scripts"}
