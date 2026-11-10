"""Evidence-first research responses built on the bounded knowledge search.

This is deliberately useful without a model: it returns the exact saved
revision hits a question matches and says when no synthesis was performed.
Generation is a separate, explicitly authorized operation and must not be
silently introduced by a read endpoint.
"""

from typing import Any

from app.modules.identity.access import ActorIdentity, SpaceAccess, recheck_identity
from app.modules.notes.search import SearchQuery, search_page


def research_page(access: SpaceAccess, params: SearchQuery) -> dict[str, Any]:
    result = search_page(access, params)
    rows = result["data"]
    recheck_identity(
        access.actor.session, ActorIdentity(access.actor.user.id, access.actor.token_hash)
    )
    return {
        "data": {
            "question": params.q,
            "mode": "evidence_retrieval",
            "answer": None,
            "answer_status": "evidence_ready" if rows else "insufficient_evidence",
            "sources": rows,
            "next_cursor": result["next_cursor"],
            "limitations": [
                "当前返回已保存内容的字面命中和固定修订，不生成未经授权的模型结论。",
                "命中片段是定位线索；请打开对应修订核对原文证据、版本和来源范围。",
                "没有命中不代表资料不存在；可调整关键词、内容范围或分类后重试。",
            ],
        }
    }
