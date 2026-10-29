"""快递适配器标准接口（§4.6.3、§15-9 顺丰为主）。

覆盖下单、轨迹、签收回传三个能力，回写 Confirmation 物流状态；
出站字段白名单：仅函证收件人、单号、物流状态等必要字段，
函证金额等审计数据不出站（§4.2.7）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class WaybillRequest:
    """下单出站字段白名单：不含函证金额与审计数据。"""

    project_no: str
    recipient_name: str
    recipient_phone: str
    recipient_address: str


@dataclass(frozen=True)
class TrackInfo:
    waybill_no: str
    status: str          # 在途/签收/异常
    signed_at: str | None = None


class ExpressAdapter(ABC):
    """标准接口：预留扩展 EMS 等其他快递（§15-9）。"""

    name: str

    @abstractmethod
    def create_waybill(self, req: WaybillRequest) -> str:
        """下单，返回快递单号。"""

    @abstractmethod
    def query_track(self, waybill_no: str) -> TrackInfo:
        """轨迹查询。"""

    @abstractmethod
    def parse_callback(self, payload: dict) -> TrackInfo:
        """签收回传解析（Webhook 回写 Confirmation.物流状态）。"""
