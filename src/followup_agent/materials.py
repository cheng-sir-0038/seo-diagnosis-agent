from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from io import BytesIO, StringIO
from itertools import chain
from pathlib import PurePosixPath
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)
from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_FILE_BYTES = 192 * 1024
MAX_FILES = 8
MAX_RECORDS = 120
MAX_PDF_PAGES = 6
SubjectCode = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,40}$")]


class RecordKind(StrEnum):
    REPORT = "report"
    VISIT_PLAN = "visit_plan"
    VISIT_SUMMARY = "visit_summary"
    SELF_REPORT = "self_report"
    PURCHASE = "purchase"
    MEDICATION_NOTE = "medication_note"


class PlanStatus(StrEnum):
    UNKNOWN = "unknown"
    SELF_REPORTED_DONE = "self_reported_done"
    DOCUMENTED_DONE = "documented_done"


class RecordEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, coerce_numbers_to_str=True)

    record_date: date | None = None
    value: str | None = Field(default=None, max_length=50)
    unit: str | None = Field(default=None, max_length=32)
    specimen: str | None = Field(default=None, max_length=64)
    method: str | None = Field(default=None, max_length=64)

    @field_validator("*", mode="before")
    @classmethod
    def blank_as_unknown(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value


class MedicalRecord(RecordEdit):
    subject_id: SubjectCode
    kind: RecordKind
    item: str = Field(min_length=1, max_length=90)
    reference_range: str | None = Field(default=None, max_length=90)
    flag: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=500)
    due_date: date | None = None
    plan_status: PlanStatus | None = None


class Evidence(BaseModel):
    id: str
    source: str
    record: MedicalRecord
    original: MedicalRecord | None = None


class Material(BaseModel):
    name: str
    digest: str
    uploaded_at: datetime
    evidence: list[Evidence] = Field(default_factory=list)
    error: str | None = None


class ChecklistSource(BaseModel):
    record_id: str
    source: str


class ChecklistItem(BaseModel):
    id: str
    title: str
    sources: list[ChecklistSource]
    detail: str | None = None
    gaps: list[str] | None = None


class ExistingArrangement(ChecklistItem):
    due_date: date | None = None
    plan_status: PlanStatus | None = None


class PreparationChecklist(BaseModel):
    materials_to_bring: list[ChecklistItem]
    existing_arrangements: list[ExistingArrangement]
    questions_for_doctor: list[ChecklistItem]


def _delimited(text: str, filename: str, *, delimiter: str, page: int | None = None) -> list:
    reader = csv.DictReader(StringIO(text), delimiter=delimiter, strict=True)
    columns = reader.fieldnames or []
    if len(set(columns)) != len(columns) or not {"subject_id", "kind", "item"} <= set(columns):
        raise ValueError("缺少资料模板的列名。")
    rows = []
    while True:
        start = reader.line_num + 1
        entry = next(reader, None)
        if entry is None:
            break
        location = f"第 {page} 页第 {start} 行" if page else f"第 {start} 行"
        rows.append((f"{filename} · {location}", entry))
        if len(rows) > MAX_RECORDS:
            raise ValueError("单份资料的记录数超过上限。")
    return rows


def _rows(filename: str, data: bytes) -> list:
    suffix = PurePosixPath(filename).suffix.casefold()
    if suffix == ".csv":
        return _delimited(data.decode("utf-8-sig"), filename, delimiter=",")
    if suffix == ".json":
        values = json.loads(data)
        if not isinstance(values, list) or len(values) > MAX_RECORDS:
            raise ValueError("JSON 需要使用资料记录数组。")
        return [(f"{filename} · 第 {index} 条", row) for index, row in enumerate(values, 1)]
    if suffix == ".pdf":
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= MAX_PDF_PAGES:
            raise ValueError("请提供最多 6 页的未加密文字 PDF。")
        rows = []
        for page_number, page in enumerate(reader.pages, 1):
            rows.extend(_delimited(
                page.extract_text(), filename, delimiter="|", page=page_number,
            ))
            if len(rows) > MAX_RECORDS:
                raise ValueError("单份资料的记录数超过上限。")
        return rows
    raise ValueError("资料格式为 CSV、JSON 或模板文字 PDF。")


def read_material(filename: str, data: bytes, subject_id: str) -> Material:
    """Parse a local import, retaining row locations and rejecting mixed subjects."""
    filename = PurePosixPath(filename.replace("\\", "/")).name.strip()[:100] or "未命名资料"
    digest = hashlib.sha256(data).hexdigest()
    material = Material(name=filename, digest=digest, uploaded_at=datetime.now(UTC))
    try:
        rows = _rows(filename, data)
        if not rows:
            raise ValueError("资料里没有记录。")
        evidence = []
        for index, (source, raw) in enumerate(rows, 1):
            try:
                record = MedicalRecord.model_validate(raw)
            except ValidationError:
                raise ValueError(f"第 {index} 条记录的字段或格式有误。") from None
            evidence.append(Evidence(id=f"{digest}:{index}", source=source, record=record))
    except (UnicodeError, csv.Error, json.JSONDecodeError, PdfReadError, RecursionError):
        material.error = "无法读取资料，请核对格式并重新提供。"
    except ValueError as exc:
        material.error = str(exc)
    else:
        if any(entry.record.subject_id != subject_id for entry in evidence):
            material.error = "对象编号不一致，整份资料未合并。"
        else:
            material.evidence = evidence
    return material


def case_evidence(files: list[Material], *, deduplicate: bool = True) -> list[Evidence]:
    """Return dated evidence in order, optionally retaining every imported source row."""
    seen = set()
    result = []
    for entry in chain.from_iterable(file.evidence for file in files):
        if not deduplicate:
            result.append(entry)
            continue
        key = entry.record.model_dump_json()
        if key not in seen:
            result.append(entry)
            seen.add(key)
    return sorted(result, key=lambda entry: entry.record.record_date or date.max)


def _decimal(text: str | None) -> Decimal | None:
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if not value.is_finite() or not -12 <= value.as_tuple().exponent <= 12:
        return None
    return value


def _comparison(earlier: Evidence, later: Evidence, date_counts: Counter[date]) -> dict:
    a, b = earlier.record, later.record
    reasons = []
    if a.subject_id != b.subject_id:
        reasons.append("对象编号不一致")
    if not a.record_date or not b.record_date or a.record_date == b.record_date:
        reasons.append("缺少两个明确且不同的检查日期")
    elif any(date_counts[day] > 1 for day in (a.record_date, b.record_date)):
        reasons.append("同一天有多条不同记录，需要确认对应报告")
    for field, name in (("unit", "单位"), ("specimen", "标本"), ("method", "方法")):
        x, y = getattr(a, field), getattr(b, field)
        if not x or not y or x != y:
            reasons.append(f"{name}缺失或不同")
    values = _decimal(a.value), _decimal(b.value)
    if None in values:
        reasons.append("原值无法直接做数值运算")
    delta = None if reasons else format(values[1] - values[0], "f")
    return {
        "item": b.item,
        "earlier": earlier.id,
        "later": later.id,
        "delta": delta,
        "unit": b.unit if delta is not None else None,
        "reasons": reasons,
    }


def _select_comparison(reports: list[Evidence]) -> tuple[dict, list[dict]]:
    dated = sorted(
        (entry for entry in reports if entry.record.record_date is not None),
        key=lambda entry: entry.record.record_date,
    )
    date_counts = Counter(entry.record.record_date for entry in dated)
    previous_by_conditions: dict[tuple[str, str, str, str], Evidence] = {}
    comparable = None
    for later in dated:
        row = later.record
        if (
            date_counts[row.record_date] != 1
            or not (row.unit and row.specimen and row.method)
            or _decimal(row.value) is None
        ):
            continue
        conditions = (row.subject_id, row.unit, row.specimen, row.method)
        if earlier := previous_by_conditions.get(conditions):
            comparable = (earlier, later)
        previous_by_conditions[conditions] = later
    if comparable:
        earlier, later = comparable
        issues = []
        for entry in dated:
            if entry.record.record_date <= later.record.record_date:
                continue
            reasons = "、".join(_comparison(later, entry, date_counts)["reasons"])
            issues.append({
                "item": entry.record.item, "source": entry.source,
                "text": f"更新的记录未纳入这组对照，请核对：{reasons}",
            })
        return _comparison(earlier, later, date_counts), issues

    # Keep source rows visible for verification even when no delta can be calculated.
    distinct_dates = list({entry.record.record_date: entry for entry in dated}.values())
    if len(distinct_dates) >= 2:
        earlier, later = distinct_dates[-2:]
    elif dated and len(dated) < len(reports):
        earlier = dated[-1]
        later = next(entry for entry in reports if entry.record.record_date is None)
    else:
        earlier, later = reports[-2:]
    comparison = _comparison(earlier, later, date_counts)
    return comparison, [{
        "item": later.record.item, "source": f"{earlier.source} / {later.source}",
        "text": f"请核对两条原记录：{'、'.join(comparison['reasons'])}",
    }]


def review_reports(evidence: list[Evidence]) -> tuple[list[dict], list[dict]]:
    reports = defaultdict(list)
    issues = []
    for entry in evidence:
        if entry.record.kind != RecordKind.REPORT:
            continue
        reports[entry.record.item].append(entry)
        if entry.record.record_date is None:
            issues.append({"item": entry.record.item, "source": entry.source, "text": "记录日期待核对"})

    comparisons = []
    for item, group in reports.items():
        if len(group) == 1:
            issues.append({
                "item": item, "source": group[0].source,
                "text": "仅有一条报告记录。如需数值对照，请补充同一项目、日期不同且单位、标本、方法一致的报告",
            })
            continue
        comparison, comparison_issues = _select_comparison(group)
        comparisons.append(comparison)
        issues.extend(comparison_issues)
    return comparisons, issues


def is_open_plan(record: MedicalRecord) -> bool:
    """Only explicitly imported arrangements without recorded completion can become todos."""
    return record.kind == RecordKind.VISIT_PLAN and record.plan_status in {None, PlanStatus.UNKNOWN}


def _sources(entries: list[Evidence]) -> list[ChecklistSource]:
    return [ChecklistSource(record_id=entry.id, source=entry.source) for entry in entries]


def _arrangement(entry: Evidence) -> ExistingArrangement:
    row = entry.record
    appointment = any(word in row.item.casefold() for word in ("预约", "挂号", "appointment"))
    gaps = None
    if appointment and row.due_date is None and is_open_plan(row):
        gaps = ["资料未注明预约日期，如已有完整的预约单，可以补充。"]
    return ExistingArrangement(
        id=f"arrangement:{entry.id}", title=row.item, sources=_sources([entry]),
        detail=row.note, due_date=row.due_date, plan_status=row.plan_status, gaps=gaps,
    )


_QUESTION_TEMPLATES = {
    RecordKind.REPORT: "关于「{item}」的报告，复诊时需要一起核对哪些原始信息？",
    RecordKind.VISIT_SUMMARY: "「{item}」中的哪些内容需要在这次复诊时向医生确认？",
    RecordKind.SELF_REPORT: "这些「{item}」里，哪些记录需要在复诊时向医生说明？",
    RecordKind.MEDICATION_NOTE: "记录里提到的「{item}」，复诊时如何核对这份用药记录？",
    RecordKind.VISIT_PLAN: "就「{item}」这项已有安排，需要和医生核对哪些资料？",
}


def _questions(evidence: list[Evidence]) -> list[ChecklistItem]:
    topics: dict[tuple[RecordKind, str], list[Evidence]] = defaultdict(list)
    for entry in evidence:
        row = entry.record
        if row.kind in _QUESTION_TEMPLATES and (
            row.kind != RecordKind.VISIT_PLAN or is_open_plan(row)
        ):
            topics[row.kind, row.item].append(entry)

    questions = []
    for (kind, item), entries in topics.items():
        gaps = None
        if kind == RecordKind.SELF_REPORT and any(
            entry.record.record_date is None for entry in entries
        ):
            gaps = ["部分日常记录未注明日期，如需按时间说明，可补充带日期的记录。"]
        questions.append(ChecklistItem(
            id=f"question:{entries[0].id}", title=_QUESTION_TEMPLATES[kind].format(item=item),
            sources=_sources(entries), gaps=gaps,
        ))
    return questions


def prepare_case(files: list[Material]) -> dict:
    """Build a local checklist from imported evidence and keep report comparisons as detail."""
    evidence = case_evidence(files)
    comparisons, issues = review_reports(evidence)
    plans = [entry for entry in evidence if entry.record.kind == RecordKind.VISIT_PLAN]
    checklist = PreparationChecklist(
        materials_to_bring=[
            ChecklistItem(
                id=f"material:{file.digest}", title=file.name, sources=_sources(file.evidence),
            )
            for file in files if file.evidence
        ],
        existing_arrangements=[_arrangement(entry) for entry in plans],
        questions_for_doctor=_questions(evidence),
    )
    return {
        "checklist": checklist.model_dump(mode="json", exclude_none=True),
        "comparisons": comparisons,
        "issues": issues,
        "plans": [entry.id for entry in plans],
        "pending_plans": [entry.id for entry in plans if is_open_plan(entry.record)],
    }
