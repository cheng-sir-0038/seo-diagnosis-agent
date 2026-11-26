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


class PracticeDifficulty(StrEnum):
    BASIC = "basic"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


_DIFFICULTY = {
    PracticeDifficulty.BASIC: ("基础", {0, 1}),
    PracticeDifficulty.INTERMEDIATE: ("中等", {2}),
    PracticeDifficulty.ADVANCED: ("进阶", {3}),
}


class MathPracticeQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    topic: str | None = Field(default=None, max_length=60)
    count: int = Field(default=3, ge=1)
    review: bool = False
    difficulty: PracticeDifficulty | None = Field(
        default=None,
        description="仅在用户指定难度时填写：基础或简单 basic，中等 intermediate，进阶或较难 advanced。",
    )


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
    invalid_answers: int = Field(default=0, ge=0)


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
        next_step = (
            "在这段对话说「重练错题」。先遮住解析重新列式，再把求出的值代回原方程，"
            "核对等式两边是否相等。"
        )
    elif skipped or unanswered:
        next_step = f"想继续时，可以说「再出 1 道{TOPIC}题」，先写出一行等式再选答案。"
    else:
        next_step = "任选一道刚答对的题，遮住选项算一遍，再用代入法验算。"
    return ToolResult(success=True, data={
        "answer": "\n\n".join(filter(None, (feedback, summary, f"**下一步练习**　{next_step}"))),
        "progress": progress.model_dump(mode="json"),
        "counts": {
            "correct": correct, "incorrect": incorrect,
            "skipped": skipped, "unanswered": unanswered,
        },
    })


def _ask(
    progress: PracticeProgress, question: Question, feedback: str = "", *, start_note: str = "",
) -> ToolResult:
    options = "\n".join(f"- **{letter}**　{option}" for letter, option in question.options.items())
    heading = f"### {TOPIC} · 第 {len(progress.attempts) + 1} / {len(progress.question_ids)} 题"
    choices = " / ".join(question.options)
    instruction = f"回复 {choices} 中的一个字母，也可以回复「跳过」或「结束练习」。"
    prompt = "\n\n".join(
        filter(None, (feedback, heading, start_note, question.stem, options, instruction))
    )
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
            notes = ["题目选自 TAL-SCQ5K，作答后可查看题库原解析。"]
            if request.difficulty is not None:
                label, levels = _DIFFICULTY[request.difficulty]
                candidates = [key for key in candidates if bank[key].difficulty in levels]
                if not candidates:
                    scope = "这段对话的错题中" if request.review else "当前题库中"
                    return ToolResult(success=True, data={
                        "answer": f"{scope}没有{label}难度的题。可以换一个难度，或不限定难度。",
                    })
                notes.append(f"所选难度为{label}（按题库标记）。")
            Random(runtime.run_id if runtime else None).shuffle(candidates)
            count = min(request.count, MAX_QUESTIONS, self.limit)
            selected = candidates[:count]
            if len(selected) < request.count:
                reason = "符合条件的题量有限" if len(candidates) < count else "达到单次练习上限"
                notes.append(f"你想练 {request.count} 道。{reason}，本次先练 {len(selected)} 道。")
            progress = PracticeProgress(question_ids=selected)
            return _ask(progress, bank[selected[0]], start_note="".join(notes))

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
                progress.invalid_answers += 1
                if self._can_accept_answer(progress):
                    return _ask(progress, question, "请从当前题的选项中选一个。")
                return _finish(progress, "已用完本次练习的作答次数，无效选项不计分。")
            correct = choice[1] == question.answer
            outcome = AnswerOutcome.CORRECT if correct else AnswerOutcome.INCORRECT
            attempt = Attempt(question_id=question.id, outcome=outcome, option=choice[1])
        progress.attempts.append(attempt)
        feedback = _feedback(question, attempt)
        if len(progress.attempts) == len(progress.question_ids):
            return _finish(progress, feedback)
        if not self._can_accept_answer(progress):
            return _finish(progress, f"{feedback}\n\n已用完本次练习的作答次数。")
        return _ask(progress, bank[progress.question_ids[index + 1]], feedback)

    def _can_accept_answer(self, progress: PracticeProgress) -> bool:
        # The workspace provides a conservative number of tool calls after the initial question.
        return len(progress.attempts) + progress.invalid_answers < self.limit


def update_review_ids(previous: list[str], progress: dict) -> list[str]:
    review = set(previous)
    for attempt in PracticeProgress.model_validate(progress).attempts:
        if attempt.outcome == AnswerOutcome.INCORRECT:
            review.add(attempt.question_id)
        elif attempt.outcome == AnswerOutcome.CORRECT:
            review.discard(attempt.question_id)
    return sorted(review)
