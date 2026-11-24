from __future__ import annotations

import re

_QUERY_REFRESH = (
    r"刷新(?!率|频率|速度)|重查|(?:重新|再)(?:查(?:询|找)?|搜(?:索)?)"
    r"|最新(?!款|版|机型|型号|一代|系列)"
)
_CLEAR_CACHE = r"清(?:理|除)?(?:一下|下)?缓存"
_CACHE_REFRESH = rf"(?:{_CLEAR_CACHE}|(?:跳过|绕过|不用|不使用|不要用|不走)缓存)"
_REFRESH = re.compile(rf"(?:{_QUERY_REFRESH}|{_CACHE_REFRESH})")
_LOOKUP = r"(?:查(?:询|找|看)?|搜(?:索)?|看看|看|用|按)"
_NEGATED = re.compile(
    rf"(?:不要|不用|不必|不需要|无需|别|不)(?:再)?(?:{_LOOKUP}(?:一下)?)?"
    rf"(?:{_QUERY_REFRESH}|{_CLEAR_CACHE})"
)
_RECENT = r"(?:刚才|上次|上一轮|这次|这轮|这批|之前)的?"
_RESULT = r"(?:商品(?:价格)?|结果|价格|参考价|报价|缓存)"
_FOLLOWUP = re.compile(
    r"(?:请帮我|帮我|给我|麻烦|请|我要)?"
    rf"(?:把{_RECENT}{_RESULT})?"
    rf"(?:{_LOOKUP}(?:一下)?)?"
    rf"(?:{_QUERY_REFRESH}|{_CACHE_REFRESH})"
    rf"(?:一下|一次|一遍|下)?(?:{_RECENT})?的?(?:{_RESULT})?(?:吧|看看)?"
)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text).strip("，,。！？!?")


def requests_catalog_refresh(text: str) -> bool:
    text = _compact(text)
    return bool(_REFRESH.search(text) and not _NEGATED.search(text))


def is_catalog_refresh_followup(text: str) -> bool:
    """A refresh command that leaves the product and filters unspecified."""
    return bool(_FOLLOWUP.fullmatch(_compact(text)))
