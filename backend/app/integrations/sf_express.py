"""顺丰适配器骨架（首期唯一外部通道，PRD §15-9）。

启用前置：ECHO_SF_EXPRESS_ENABLED=true 且域名在出站白名单（egress.assert_egress_allowed）。
"""
from __future__ import annotations

from .egress import assert_egress_allowed
from .express import ExpressAdapter, TrackInfo, WaybillRequest

SF_API_HOST = "sf-api.example.invalid"  # 沙箱联调时替换为真实域名并加入白名单


class SFExpressAdapter(ExpressAdapter):
    name = "顺丰"

    def _guard(self) -> None:
        assert_egress_allowed(SF_API_HOST)

    def create_waybill(self, req: WaybillRequest) -> str:
        self._guard()
        raise NotImplementedError("沙箱联调阶段实现：下单接口（仅传收件人/地址/电话白名单字段）")

    def query_track(self, waybill_no: str) -> TrackInfo:
        self._guard()
        raise NotImplementedError("沙箱联调阶段实现：轨迹查询")

    def parse_callback(self, payload: dict) -> TrackInfo:
        # 回传解析不发起出站，可直接实现；骨架占位
        raise NotImplementedError("沙箱联调阶段实现：签收回传解析 → 回写 Confirmation.物流状态")
