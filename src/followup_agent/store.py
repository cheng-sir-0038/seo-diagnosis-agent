from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.followup_agent.materials import (
    MAX_FILES,
    MAX_RECORDS,
    Material,
    MedicalRecord,
    RecordEdit,
    RecordKind,
    case_evidence,
    prepare_case,
    read_material,
    review_reports,
)
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.db.models import FollowupCaseRow

DEMO_SUBJECT = "DEMO-001"
_EXAMPLES = Path(__file__).with_name("examples")


class CaseConflictError(ValueError):
    pass


class CaseData(BaseModel):
    subject_id: str | None = None
    demo: bool = False
    revision: int = 0
    files: list[Material] = Field(default_factory=list)
    draft: dict | None = None
    saved: dict | None = None

    def view(self) -> dict:
        evidence = case_evidence(self.files)
        comparisons, issues = review_reports(evidence)
        return {
            **self.model_dump(mode="json", exclude={"files"}),
            "files": [
                {**file.model_dump(mode="json", exclude={"evidence"}), "rows": len(file.evidence)}
                for file in self.files
            ],
            "records": [entry.model_dump(mode="json") for entry in evidence],
            "report_review": {
                "comparable_pairs": sum(row["delta"] is not None for row in comparisons),
                "issues": issues,
            },
        }


def access_hash(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def demo_supplement() -> Material:
    data = (_EXAMPLES / "supplement.csv").read_bytes()
    return read_material("合成补充报告.csv", data, DEMO_SUBJECT)


class FollowupCaseStore:
    def __init__(self, database: SeoAgentDatabase) -> None:
        self.database = database

    async def create(self, run_id: str, key_hash: str, *, demo: bool) -> None:
        async with self.database.orm_session(write=True) as session:
            if await session.get(FollowupCaseRow, run_id) is not None:
                return
            case = CaseData(demo=demo)
            if demo:
                material = read_material(
                    "合成首诊与日常记录.csv",
                    (_EXAMPLES / "initial.csv").read_bytes(),
                    DEMO_SUBJECT,
                )
                if material.error:
                    raise RuntimeError("合成资料模板读取失败。")
                case.subject_id = DEMO_SUBJECT
                case.files = [material]
                case.revision = 1
            await session.execute(insert(FollowupCaseRow).values(
                run_id=run_id, access_hash=key_hash, state=case.model_dump(mode="json"),
            ).on_conflict_do_nothing())

    async def _row(
        self, session: AsyncSession, run_id: str, *, key: str | None = None, lock: bool = False,
    ) -> tuple[FollowupCaseRow, CaseData]:
        row = await session.get(FollowupCaseRow, run_id, with_for_update=lock)
        if row is None or (
            key is not None and not compare_digest(row.access_hash, access_hash(key))
        ):
            raise KeyError("复诊任务不存在或当前会话无访问权限。")
        return row, CaseData.model_validate(row.state)

    async def check_input_access(self, run_id: str, key: str | None) -> bool:
        async with self.database.orm_session() as session:
            row = await session.get(FollowupCaseRow, run_id)
            if row is None:
                return False
            if not key or not compare_digest(row.access_hash, access_hash(key)):
                raise KeyError("当前会话无复诊任务访问权限。")
            return True

    async def view(self, run_id: str, key: str) -> dict:
        async with self.database.orm_session() as session:
            _, case = await self._row(session, run_id, key=key)
            return case.view()

    async def add(self, run_id: str, key: str, subject: str, material: Material) -> dict:
        async with self.database.orm_session(write=True) as session:
            row, case = await self._row(session, run_id, key=key, lock=True)
            if case.subject_id and case.subject_id != subject:
                raise CaseConflictError("此任务已绑定其他对象编号。")
            existing = next(
                (index for index, file in enumerate(case.files) if file.digest == material.digest),
                None,
            )
            if existing is not None and (case.files[existing].evidence or not material.evidence):
                return case.view()
            if case.saved or (existing is None and len(case.files) >= MAX_FILES):
                raise CaseConflictError("资料包已保存或已达到 8 份资料的上限。")
            total_records = sum(len(file.evidence) for file in case.files) + len(material.evidence)
            if total_records > MAX_RECORDS:
                raise CaseConflictError("本次复诊资料最多保留 120 条记录。")
            if material.evidence:
                case.subject_id = subject
            if existing is None:
                case.files.append(material)
            else:
                case.files[existing] = material
            case.revision += 1
            case.draft = None
            row.state = case.model_dump(mode="json")
            return case.view()

    async def edit(
        self, run_id: str, key: str, record_id: str, revision: int, change: RecordEdit,
    ) -> dict:
        async with self.database.orm_session(write=True) as session:
            row, case = await self._row(session, run_id, key=key, lock=True)
            if case.saved or revision != case.revision:
                raise CaseConflictError("资料版本已变化，请刷新后重新核对。")
            evidence = next(
                (entry for entry in case_evidence(case.files) if entry.id == record_id),
                None,
            )
            if evidence is None:
                raise KeyError("记录不存在。")
            current = MedicalRecord.model_validate({
                **evidence.record.model_dump(), **change.model_dump(exclude_unset=True),
            })
            if current == evidence.record:
                return case.view()
            evidence.original = evidence.original or evidence.record
            evidence.record = current
            case.revision += 1
            case.draft = None
            row.state = case.model_dump(mode="json")
            return case.view()

    async def read_receipt(self, run_id: str) -> dict:
        async with self.database.orm_session() as session:
            _, case = await self._row(session, run_id)
            evidence = case_evidence(case.files)
            dates = {
                entry.record.record_date for entry in evidence
                if entry.record.kind == RecordKind.REPORT and entry.record.record_date
            }
            comparisons, _ = review_reports(evidence)
            return {
                "records": len(evidence),
                "report_dates": len(dates),
                "comparable_pairs": sum(row["delta"] is not None for row in comparisons),
                "rejected_files": sum(bool(file.error) for file in case.files),
                "revision": case.revision,
            }

    async def prepare(self, run_id: str) -> dict:
        async with self.database.orm_session(write=True) as session:
            row, case = await self._row(session, run_id, lock=True)
            if case.saved:
                return {"already_saved": True, "revision": case.revision}
            result = prepare_case(case_evidence(case.files))
            case.draft = {**result, "revision": case.revision}
            row.state = case.model_dump(mode="json")
            return {
                "revision": case.revision,
                "comparisons": len(result["comparisons"]),
                "pending_plans": len(result["pending_plans"]),
            }

    async def confirm(self, run_id: str, revision: int) -> dict:
        async with self.database.orm_session(write=True) as session:
            row, case = await self._row(session, run_id, lock=True)
            if case.draft is None or case.revision != revision:
                return {"saved": False, "reason": "revision_changed"}
            if case.saved is None:
                evidence = {entry.id: entry for entry in case_evidence(case.files)}
                tasks = []
                for record_id in case.draft["pending_plans"]:
                    entry = evidence[record_id]
                    due = entry.record.due_date
                    tasks.append({
                        "id": uuid5(NAMESPACE_URL, f"{run_id}:{record_id}").hex,
                        "title": entry.record.item,
                        "due_date": due.isoformat() if due else None,
                        "source": entry.source,
                    })
                case.saved = {
                    "revision": revision,
                    "saved_at": datetime.now(UTC).isoformat(),
                    "tasks": tasks,
                }
                row.state = case.model_dump(mode="json")
            return {"saved": True, "tasks": len(case.saved["tasks"])}
