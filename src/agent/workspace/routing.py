"""Build the registered supervisor prompt and resolve user-supplied page targets."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit

from src.agent.workspace.registry import COURSE_REVIEW, RouteDecision, SpecialistRegistry
from src.core.exceptions import InternalAdapterError
from src.providers.page_metadata.client import normalize_site_url

ASK_PAGE = "请发要检查的公开网页链接或域名。"

_SUPERVISOR_PROMPT = (
    "你是业务工作台的主 Agent。根据最近对话和当前问题选择一个已登记的专员。\n"
    "pending_intent 和 awaiting_page 表示未完成的任务与等待的页面。最新用户消息可以同时补链接和限定交付物，"
    "也可以纠正、取消或改换任务。先按最新消息判断还要不要做原任务，再按需选择页面。"
    "只取消且没提出新任务时选 clarify，简短确认并请用户说明下一项任务。"
    "用户要求检查或更换具体网页时，target_index 必填，即使只有一个也选 1。"
    "它引用候选页序号。参考文献链接不选，沿用上轮页面时不选。"
    "多个链接且无法分清目标时不选。不从助手答案或作为依据的引用中推测网址。"
    "用户沿用上文的问题时结合前一轮业务与当前页面。问题里有足够信息就直接分派。"
    "没有明确的业务意图或超出已登记的业务时，选择 clarify 并给一个简短的追问。"
)
_COURSE_REVIEW_PROMPT = (
    "course_review 专用于拿医学人文考纲核对招生或课程页的公开说法，需要页面。"
    "用户要求按考纲核对课程页、或刚问过医学人文修订又要求对照网页，使用 course_review。"
    "明确取消课程核对并改问医考时选择公开医药查询，needs_page=false，参考网址不选为页面。"
    "仍要核对原课程但只要适用范围与出处等交付限制时保留 course_review。"
)


def routing_prompt(registry: SpecialistRegistry) -> tuple[str, dict[str, Any]]:
    """Derive a PromptOps key and initial version from the compiled contract."""
    paragraphs = [_SUPERVISOR_PROMPT, registry.routing_instructions()]
    if COURSE_REVIEW in registry.handoffs:
        paragraphs.append(_COURSE_REVIEW_PROMPT)
    definition = {
        "name": "工作台业务分派",
        "purpose": "根据业务意图、对话上下文和用户提供的目标选择专员",
        "system_template": "\n\n".join(paragraphs),
        "user_template": "{{input}}",
        "output_schema": registry.route_model.model_json_schema(),
    }
    contract = json.dumps(definition, sort_keys=True, ensure_ascii=False).encode()
    key = f"workspace.route.v9.{hashlib.sha256(contract).hexdigest()[:12]}"
    return key, definition


# Only user-authored URL tokens can introduce a target. The selected item is
# subsequently validated by the same public URL rules as the page provider.
_TOKEN = r"""[^\s<>"'，。；！？、（）【】《》「」\]\[()]+"""
_TARGET = re.compile(
    rf"(?<![A-Za-z0-9_@./-])(?:[a-z][a-z0-9+.-]*://{_TOKEN}|"
    rf"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:[a-z]{{2,63}}|[0-9]{{1,3}})"
    rf"(?::[0-9]+)?(?:[/?#](?:{_TOKEN})?)?)", re.I | re.ASCII,
)


def page_candidates(text: str) -> tuple[list[str], bool]:
    targets: list[str] = []
    invalid = False
    for match in _TARGET.finditer(text):
        value = match.group().rstrip(".,;:!?")
        if "://" not in value and value.lower().endswith(
            (".txt", ".xml", ".html", ".htm", ".json", ".js", ".css", ".pdf", ".md")
        ) and "/" not in value:
            continue
        try:
            target = normalize_site_url(value)
            host = urlsplit(target).hostname or ""
            if not host.isascii():
                host.encode("idna")
        except (InternalAdapterError, UnicodeError, ValueError):
            invalid = True
            continue
        if target not in targets:
            targets.append(target)
    return targets, invalid


def is_bare_page_reply(text: str) -> bool:
    value = text.strip().rstrip(".,;:!?，。；！？").strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
    return _TARGET.fullmatch(value) is not None


def select_page(
    current: str | None, candidates: list[str], invalid: bool, decision: RouteDecision,
) -> tuple[str | None, str | None]:
    index = decision.target_index
    if index and index <= len(candidates):
        return candidates[index - 1], None
    if candidates and (index or len(candidates) > 1 or not current):
        return None, "请指出要检查的是哪个页面，或直接贴出目标网址。"
    target = None if invalid else current
    if not target and (decision.needs_page or decision.route == COURSE_REVIEW):
        return None, ASK_PAGE
    return target, None
