"""留痕哈希链测试（红线 1/3：append-only、篡改断链可检出）。"""
from app.core.audit_middleware import HashChain


def test_chain_append_and_verify():
    chain = HashChain()
    for i in range(3):
        chain.append(
            tenant_id="default", entity_type="Worksheet", entity_id=f"w{i}",
            action="create", operator="u1", after={"v": i},
        )
    assert chain.verify()


def test_tampering_breaks_chain():
    chain = HashChain()
    chain.append(tenant_id="t", entity_type="Worksheet", entity_id="w1", action="create", operator="u1")
    chain.append(tenant_id="t", entity_type="Worksheet", entity_id="w2", action="update", operator="u2")
    # 模拟历史篡改：直接改快照内容
    chain.entries[0].after_snapshot = {"v": 999}
    assert not chain.verify()
