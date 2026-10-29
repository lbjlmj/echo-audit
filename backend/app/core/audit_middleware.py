"""统一留痕切面骨架（红线 1/3、§4.3.1）。

设计：core/audit_middleware 在服务层统一拦截写操作自动生成 AuditTrail——
业务开发者无法绕过（想不留痕得改框架代码，代码评审红线）。
实现形态：FastAPI middleware 记录请求级写操作 + services 显式登记实体变更；
哈希链 append-only：每条含前条哈希，链尾每日锚定，库端无 UPDATE/DELETE 权限。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session


@dataclass
class AuditTrailEntry:
    tenant_id: str
    entity_type: str
    entity_id: str
    action: str
    operator: str
    before_snapshot: dict[str, Any] | None
    after_snapshot: dict[str, Any] | None
    prev_hash: str
    ts: float = field(default_factory=time.time)
    entry_hash: str = ""

    def compute_hash(self) -> str:
        raw = json.dumps(
            {
                "tid": self.tenant_id,
                "entity": f"{self.entity_type}:{self.entity_id}",
                "action": self.action,
                "operator": self.operator,
                "before": self.before_snapshot,
                "after": self.after_snapshot,
                "prev": self.prev_hash,
                "ts": self.ts,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def seal(self) -> "AuditTrailEntry":
        """计算本条哈希（append-only，落库后禁止更新删除）。"""
        self.entry_hash = self.compute_hash()
        return self


class HashChain:
    """内存态哈希链骨架；生产实现落 AuditTrail 表并按日锚定快照。"""

    def __init__(self, genesis: str = "0" * 64) -> None:
        self.tail_hash: str = genesis
        self.entries: list[AuditTrailEntry] = []

    def append(
        self,
        *,
        tenant_id: str,
        entity_type: str,
        entity_id: str,
        action: str,
        operator: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> AuditTrailEntry:
        entry = AuditTrailEntry(
            tenant_id=tenant_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            operator=operator,
            before_snapshot=before,
            after_snapshot=after,
            prev_hash=self.tail_hash,
        ).seal()
        self.entries.append(entry)
        self.tail_hash = entry.entry_hash
        return entry

    def verify(self) -> bool:
        """整链验证：任何历史篡改都会断链（§4.3.2 出证依据）。"""
        prev = "0" * 64
        for e in self.entries:
            if e.prev_hash != prev or e.entry_hash != e.compute_hash():
                return False
            prev = e.entry_hash
        return True


# ---------------------------------------------------------------------------
# 落库版哈希链（共享规则单点，硬约束 4/9）：services 一律经 append_trail 续链，
# 禁止自行写 AuditTrail 表（append-only，库端回收 UPDATE/DELETE 权限）。
# ---------------------------------------------------------------------------
def append_trail(
    db: Session,
    *,
    tenant_id: str,
    entity_type: str,
    entity_id: str,
    action: str,
    operator: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> "AuditTrail":
    """查当前链尾 → seal → append-only 落库。"""
    from ..models.audit import AuditTrail  # 延迟导入避免环

    tail = db.scalar(select(AuditTrail.entry_hash).order_by(AuditTrail.ts.desc()).limit(1))
    entry = AuditTrailEntry(
        tenant_id=tenant_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        operator=operator,
        before_snapshot=before,
        after_snapshot=after,
        prev_hash=tail or "0" * 64,
    ).seal()
    row = AuditTrail(
        tenant_id=entry.tenant_id,
        entity_type=entry.entity_type,
        entity_id=entry.entity_id,
        action=entry.action,
        operator=entry.operator,
        before_snapshot=entry.before_snapshot,
        after_snapshot=entry.after_snapshot,
        prev_hash=entry.prev_hash,
        entry_hash=entry.entry_hash,
        ts=entry.ts,
    )
    db.add(row)
    db.flush()
    return row


def verify_chain(db: Session) -> bool:
    """整链验证：逐条复算哈希并比对前条指针（质控抽查/出证依据 §4.3.2）。"""
    from ..models.audit import AuditTrail

    prev = "0" * 64
    rows = db.scalars(select(AuditTrail).order_by(AuditTrail.ts)).all()
    for r in rows:
        entry = AuditTrailEntry(
            tenant_id=r.tenant_id,
            entity_type=r.entity_type,
            entity_id=r.entity_id,
            action=r.action,
            operator=r.operator,
            before_snapshot=r.before_snapshot,
            after_snapshot=r.after_snapshot,
            prev_hash=r.prev_hash,
            ts=r.ts,
        )
        if r.prev_hash != prev or r.entry_hash != entry.compute_hash():
            return False
        prev = r.entry_hash
    return True
