"""准则 RAG 问答（FR-07-01；六大 AI 模块之一，§2.2 ai/rag.py）。

检索问答而非生成：本地化准则库（准则锚点 + 已发布知识条目）上做纯 Python
中文 bigram 覆盖率检索，零新增依赖（TECH_DESIGN §1.1 核心算法口径）；
大模型通道默认关闭（硬约束 6/§15-15）：当前 model_version 固定本地检索器，
vLLM 生成式答案须三条件门禁满足后方可切换（llm_enabled + 数据一般级脱敏 + 保密评估留档）。

引用门禁（硬约束 6）单点：答案强制带准则锚点引用——命中分低于阈值即判「未覆盖」，
citations 恒为空、answer 明示未覆盖，不强行作答；命中则 answer 逐条内嵌
「准则号+条款+程序目的」引用。问答留痕：AIRun(module='rag') + append_trail 续链，
问答记录经 audit_trails(entity_type='rag_qa') 全程可追溯（PRD §5.7 结果列）。
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import pipeline
from ..core.audit_middleware import append_trail
from ..models.audit import AIRun as AIRunORM
from ..models.knowledge import KnowledgeItem, RuleAnchor
from ..models.user import User

# 本地检索器版本（大模型通道默认关闭；切 vLLM 须三条件门禁，§15-15/硬约束 6）
_MODEL_VERSION = "local-bigram-retrieval-v1"
_SKILL_VERSION = "rag-qa@1.0.0"

# 引用门禁阈值（硬约束 6）：问题 bigram 被语料覆盖率低于该值即判未覆盖
_COVER_THRESHOLD = 0.15
_TOP_K = 3
_UNCOVERED_ANSWER = "未覆盖：知识库暂无与该问题相关的准则依据，不强行作答；请咨询质控专员或补充准则库。"


def _bigrams(text: str) -> frozenset[str]:
    """中文 2-gram 集合（去空白）；单字符退化为自身。"""
    t = "".join(text.split())
    if len(t) < 2:
        return frozenset(t)
    return frozenset(t[i : i + 2] for i in range(len(t) - 1))


def _score(
    question: str, question_grams: frozenset[str], corpus_text: str, standard_no: str
) -> float:
    """覆盖率打分：问题 bigram 落入语料比例；问题直接提到准则号（如 1301）加成。"""
    hit = len(question_grams & _bigrams(corpus_text))
    score = hit / len(question_grams) if question_grams else 0.0
    if standard_no and standard_no in question:
        score = min(1.0, score + 0.5)
    return round(score, 4)


def _build_corpus(db: Session) -> list[tuple[str, dict]]:
    """检索语料：全部准则锚点 + 已发布知识条目（未过入库审批链的条目不可引用，§15-3）。"""
    corpus: list[tuple[str, dict]] = []
    for a in db.scalars(select(RuleAnchor)).all():
        text = f"{a.standard_no} {a.clause} {a.purpose}"
        corpus.append((text, {
            "source": "准则锚点", "ref": a.id,
            "standard_no": a.standard_no, "clause": a.clause, "purpose": a.purpose,
        }))
    for k in db.scalars(
        select(KnowledgeItem).where(KnowledgeItem.status == "已发布")
    ).all():
        text = f"{k.item_type} {k.content}"
        meta: dict = {"source": k.item_type, "ref": k.id, "content": k.content[:80]}
        if k.rule_anchor_id:
            anchor = db.get(RuleAnchor, k.rule_anchor_id)
            if anchor is not None:
                meta["standard_no"], meta["clause"], meta["purpose"] = (
                    anchor.standard_no, anchor.clause, anchor.purpose,
                )
        corpus.append((text, meta))
    return corpus


def answer_question(
    db: Session, operator: User, *, project_id: str, question: str
) -> dict:
    """准则问答（任一角色，PRD §5.7「任一角色在工作台发起」）。

    硬约束 6 引用门禁：citations 为空 ⇔ answer 为未覆盖明示，二者由本函数单点保证；
    每次问答落 AIRun(module='rag') 线索态留痕 + append_trail 续链（问答即时消费，
    不进业务表故不走第三段复核标记；留痕记录满足 §4.6 可追溯）。
    """
    grams = _bigrams(question)
    scored = sorted(
        ((_score(question, grams, text, meta.get("standard_no", "")), text, meta)
         for text, meta in _build_corpus(db)),
        key=lambda x: x[0], reverse=True,
    )[:_TOP_K]
    citations = [
        {**meta, "score": score}
        for score, _text, meta in scored if score >= _COVER_THRESHOLD
    ]

    if citations:
        answer = "依据：" + "；".join(
            f"【{c['source']} {c.get('standard_no', '')} {c.get('clause', '')}】"
            f"{c.get('purpose', c.get('content', ''))}"
            for c in citations
        )
    else:  # 未覆盖明示，不强行作答（FR-07-01/硬约束 6）
        answer = _UNCOVERED_ANSWER
        citations = []

    # 三段式前两段（红线 1）：产物为线索态留痕；问答即时消费不进业务表
    run = pipeline.AIRun(
        project_id=project_id, module="rag",
        skill_version=_SKILL_VERSION, model_version=_MODEL_VERSION,
        triggered_by=operator.id, input_material_ids=[],
    )
    summary = {"question": question, "answer": answer, "citations": citations,
               "covered": bool(citations)}
    pipeline.seal_output(run, summary, output_key=f"rag/{run.run_no}.json")
    row = AIRunORM(
        run_no=run.run_no, project_id=project_id, module=run.module,
        skill_version=run.skill_version, model_version=run.model_version,
        triggered_by=operator.id,
        input_material_ids=[], input_hashes={},
        output_digest=run.output_digest, output_key=run.output_key,
        status="线索", related_type="rag_qa",
    )
    db.add(row)
    db.flush()
    append_trail(  # 问答记录可追溯（PRD §5.7）：entity_id=项目，历史按实体反查
        db, tenant_id=operator.tenant_id, entity_type="rag_qa", entity_id=project_id,
        action="准则问答", operator=operator.name,
        after={"question": question, "answer": answer,
               "citations": citations, "run_no": run.run_no, "covered": bool(citations)},
    )
    return {**summary, "run_no": run.run_no}


def qa_history(db: Session, project_id: str) -> list[dict]:
    """问答历史（append-only 留痕反查）：按时间正序回放问答记录（可追溯）。"""
    from ..models.audit import AuditTrail

    trails = db.scalars(
        select(AuditTrail)
        .where(AuditTrail.entity_type == "rag_qa", AuditTrail.entity_id == project_id)
        .order_by(AuditTrail.created_at)
    ).all()
    return [
        {
            "question": t.after_snapshot.get("question"),
            "answer": t.after_snapshot.get("answer"),
            "citations": t.after_snapshot.get("citations"),
            "covered": t.after_snapshot.get("covered"),
            "run_no": t.after_snapshot.get("run_no"),
            "operator": t.operator,
            "asked_at": t.created_at,
        }
        for t in trails
        if t.after_snapshot and "question" in (t.after_snapshot or {})
    ]
