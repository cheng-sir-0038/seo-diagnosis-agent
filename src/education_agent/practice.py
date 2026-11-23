from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from random import Random
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.agent.models import HumanInputRequest
from src.agent.tools.base import AgentTool, ToolResult, ToolRuntimeContext

TOPIC = "一元一次方程"
MAX_QUESTIONS = 5
_TAL_VERSION = "8a66d6e14c65ba002beda39747e6b7c43bd2d472"
_TAL_SOURCE = f"https://github.com/math-eval/TAL-SCQ5K/blob/{_TAL_VERSION}"
_BANK = Path(__file__).with_name("corpus") / "tal_equations.jsonl"
_OPTION = re.compile(
    r"(?:我(?:选(?:择)?|的答案是)?|答案[是为：:]?|选(?:择)?)?\s*([A-G])(?:选项)?[。.!！]?"
)
_PRACTICE_TOPICS = {TOPIC, "一元一次方程及应用", "一元一次方程与应用"}


class MathPracticeQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    topic: str | None = Field(default=None, max_length=60)
    count: int = Field(default=3, ge=1)
    review: bool = False


class Question(BaseModel):
    id: str
    split: Literal["train", "test"]
    line: int = Field(gt=0)
    original_id: str
    difficulty: int
    stem: str = Field(min_length=1)
    options: dict[str, str] = Field(min_length=2, max_length=7)
    answer: str
    analysis: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def check_options(self) -> Question:
        if self.answer not in self.options or any(
            not re.fullmatch(r"[A-G]", c) for c in self.options
        ):
            raise ValueError("TAL 答案或选项无效")
        return self

    @property
    def source_url(self) -> str:
        size = "3K" if self.split == "train" else "2K"
        filename = f"ch_single_choice_{self.split}_{size}.jsonl"
        return f"{_TAL_SOURCE}/ch_single_choice_constructed_5K/{filename}#L{self.line}"


class AnswerOutcome(StrEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    SKIPPED = "skipped"


class Attempt(BaseModel):
    question_id: str
    outcome: AnswerOutcome
    option: str | None = None


class PracticeProgress(BaseModel):
    question_ids: list[str] = Field(min_length=1, max_length=MAX_QUESTIONS)
    attempts: list[Attempt] = Field(default_factory=list)


@lru_cache(maxsize=1)
def question_bank() -> dict[str, Question]:
    with _BANK.open(encoding="utf-8") as file:
        questions = [Question.model_validate_json(line) for line in file if line.strip()]
    return {question.id: question for question in questions}


def _feedback(question: Question, attempt: Attempt) -> str:
    if attempt.outcome == AnswerOutcome.SKIPPED:
        return "上一题已跳过，计入本组跳过次数。"
    result = "答对了。" if attempt.outcome == AnswerOutcome.CORRECT else "这题答错了，已记入错题。"
    explanation = "\n\n".join(question.analysis)
    return (
        f"{result} 标准答案 **{question.answer}**\n\n"
        f"**题库原解析**\n\n{explanation}\n\n"
        f"[TAL-SCQ5K 原题 {question.original_id}]({question.source_url})"
    )


def _finish(progress: PracticeProgress, feedback: str = "") -> ToolResult:
    correct = sum(row.outcome == AnswerOutcome.CORRECT for row in progress.attempts)
    incorrect = sum(row.outcome == AnswerOutcome.INCORRECT for row in progress.attempts)
    skipped = len(progress.attempts) - correct - incorrect
    unanswered = len(progress.question_ids) - len(progress.attempts)
    summary = (
        f"**练习小结**　答对 {correct} 道 · 答错 {incorrect} 道 · "
        f"跳过 {skipped} 道 · 未答 {unanswered} 道。"
    )
    if incorrect:
        summary += "\n\n在这段对话说「重练错题」，可以再做答错的题。"
    return ToolResult(success=True, data={
        "answer": "\n\n".join(filter(None, (feedback, summary))),
        "progress": progress.model_dump(mode="json"),
        "counts": {
            "correct": correct, "incorrect": incorrect,
            "skipped": skipped, "unanswered": unanswered,
        },
    })


def _ask(progress: PracticeProgress, question: Question, feedback: str = "") -> ToolResult:
    options = "\n".join(f"- **{letter}**　{option}" for letter, option in question.options.items())
    heading = f"### {TOPIC} · 第 {len(progress.attempts) + 1} / {len(progress.question_ids)} 题"
    choices = " / ".join(question.options)
    instruction = f"回复 {choices} 中的一个字母，也可以回复「跳过」或「结束练习」。"
    prompt = "\n\n".join(filter(None, (feedback, heading, question.stem, options, instruction)))
    request = HumanInputRequest(questions=[instruction], message=prompt)
    return ToolResult(success=True, data={
        "status": "needs_input",
        "progress": progress.model_dump(mode="json"),
        "source_id": question.original_id,
        "source_url": question.source_url,
    }, metadata={"human_input": request.model_dump(mode="json", exclude_none=True)})


class MathPractice(AgentTool):
    name = "tal_math_practice"
    description = "从 TAL-SCQ5K 出一元一次方程单选题，按原答案判分并给出解析。"
    parameters_schema = MathPracticeQuery.model_json_schema()

    def __init__(self, review_ids: list[str] | None = None, *, limit: int = MAX_QUESTIONS) -> None:
        self.review_ids = review_ids or []
        self.limit = limit

    async def execute(
        self, arguments: dict[str, Any], runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        request = MathPracticeQuery.model_validate(arguments)
        if self.limit < 1:
            return ToolResult(success=True, data={"answer": "本次的工具预算不足，无法开始练习。"})
        if request.topic and request.topic not in _PRACTICE_TOPICS:
            return ToolResult(success=True, data={"answer": f"题库目前收录「{TOPIC}」及应用题。"})
        bank = question_bank()
        resume = runtime.metadata.get("human_input_resume") if runtime else None
        if not resume:
            candidates = list(bank)
            if request.review:
                candidates = [key for key in self.review_ids if key in bank]
                if not candidates:
                    return ToolResult(success=True, data={"answer": "这段对话暂无可复练的错题。"})
            Random(runtime.run_id if runtime else None).shuffle(candidates)
            progress = PracticeProgress(question_ids=candidates[:min(request.count, self.limit)])
            return _ask(progress, bank[progress.question_ids[0]])

        progress = PracticeProgress.model_validate(resume["tool_result"]["data"]["progress"])
        index = len(progress.attempts)
        question = bank[progress.question_ids[index]]
        answer = unicodedata.normalize("NFKC", resume["answer"]).strip()
        if answer in {"结束", "结束练习"}:
            return _finish(progress)
        if answer in {"跳过", "跳过本题"}:
            attempt = Attempt(question_id=question.id, outcome=AnswerOutcome.SKIPPED)
        else:
            choice = _OPTION.fullmatch(answer.upper())
            if not choice or choice[1] not in question.options:
                return _ask(progress, question, "请从当前题的选项中选一个。")
            correct = choice[1] == question.answer
            outcome = AnswerOutcome.CORRECT if correct else AnswerOutcome.INCORRECT
            attempt = Attempt(question_id=question.id, outcome=outcome, option=choice[1])
        progress.attempts.append(attempt)
        feedback = _feedback(question, attempt)
        if len(progress.attempts) == len(progress.question_ids):
            return _finish(progress, feedback)
        return _ask(progress, bank[progress.question_ids[index + 1]], feedback)


def update_review_ids(previous: list[str], progress: dict) -> list[str]:
    review = set(previous)
    for attempt in PracticeProgress.model_validate(progress).attempts:
        if attempt.outcome == AnswerOutcome.INCORRECT:
            review.add(attempt.question_id)
        elif attempt.outcome == AnswerOutcome.CORRECT:
            review.discard(attempt.question_id)
    return sorted(review)
