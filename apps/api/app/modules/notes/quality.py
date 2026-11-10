"""Conservative, deterministic checks; never claim semantic fact verification."""

import re
from decimal import Decimal

from app.modules.notes.content import Evidence, _ModelSummary

# Processing counts belong exclusively to the server. Reject even a correct
# model count: this also prevents a local batch count leaking into synthesis.
_COUNT = re.compile(
    r"(?:\d+|[零〇一二三四五六七八九十百两]+)\s*(?:个\s*)?(?:快照)?(?:片段|段快照|段落|段)"
    r"|(?:snapshot|segment|chunk|batch|paragraph)s?\s*(?:count\s*)?[:=]?\s*\d+"
    r"|\d+\s*(?:snapshot|segment|chunk|batch|paragraph)s?\b",
    re.IGNORECASE,
)
_DIGIT = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])|\d+(?:\.\d+)?")


def validate_quality(summary: _ModelSummary, evidence: list[Evidence]) -> None:
    """Fail closed on processing-count claims and unsupported Arabic numbers.

    This checks numeric presence in cited quotes, not entailment, conversion,
    Chinese numerals, dates, causal claims, units or general factual accuracy.
    Source-authored segment counts are also rejected, rather than silently edited.
    """
    texts = [summary.title, *summary.limitations]
    items = [summary.overview, *(i for s in summary.sections for i in s.items)]
    texts.extend(item.text for item in items)
    if any(_COUNT.search(value) for value in texts):
        raise ValueError("SUMMARY_PROCESSING_COUNT_CLAIM")
    quotes = {e.key: e.text for e in evidence}
    for item in items:
        available = {
            Decimal(number.group())
            for key in item.evidence_keys
            for number in _DIGIT.finditer(quotes.get(key, ""))
        }
        if any(Decimal(number.group()) not in available for number in _DIGIT.finditer(item.text)):
            raise ValueError("SUMMARY_NUMBER_UNSUPPORTED")


QUALITY_INSTRUCTION = (
    "处理范围、分段数量和片段编号由服务端独立显示，禁止在title/overview/sections/limitations"
    "中描述或复述处理了多少段、片段、快照或批次，不论来源是否提及。"
    "观点中的阿拉伯数字必须直接存在于该观点引用的原文中；不得换算或计算新数字。"
)
