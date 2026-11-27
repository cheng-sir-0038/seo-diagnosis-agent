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
    is_open_plan,
    prepare_case,
    read_material,
    review_reports,
)
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.db.models import FollowupCaseRow

DEMO_SUBJECT = "DEMO-001"
_EXAMPLES = Path(__file__).with_name("examples")
_DEMO_FILES = (
    ("合成门诊小结.csv", "initial.csv"),
    ("合成预约单.csv", "appointment.csv"),
    ("合成日常记录.csv", "daily-notes.csv"),
)


class CaseConflictError(ValueError):
    pass


class CaseData(BaseModel):
    """Persist case data while retaining the shape of drafts and saves from earlier runs."""

    subject_id: str | None = None
    demo: bool = False
    revision: int = 0
    files: list[Material] = Field(default_factory=list)
    draft: dict | None = None
    saved: dict | None = None

    def view(self) -> dict:
        """Expose every imported source row without reshaping historical draft or save payloads."""
        records = case_evidence(self.files, deduplicate=False)
        comparisons, issues = review_reports(case_evidence(self.files))
        return {
            **self.model_dump(mode="json", exclude={"files"}),
            "files": [
                {**file.model_dump(mode="json", exclude={"evidence"}), "rows": len(file.evidence)}
                for file in self.files
            ],
            "records": [entry.model_dump(mode="json") for entry in records],
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
        """Seed demo files only on first creation so a resumed case keeps its stored inputs."""
        async with self.database.orm_session(write=True) as session:
            if await session.get(FollowupCaseRow, run_id) is not None:
                return
            case = self._demo_case() if demo else CaseData()
            await session.execute(insert(FollowupCaseRow).values(
                run_id=run_id, access_hash=key_hash, state=case.model_dump(mode="json"),
            ).on_conflict_do_nothing())

    @staticmethod
    def _demo_case() -> CaseData:
        files = [
            read_material(name, (_EXAMPLES / path).read_bytes(), DEMO_SUBJECT)
            for name, path in _DEMO_FILES
        ]
        if any(file.error for file in files):
            raise RuntimeError("合成资料模板读取失败。")
        return CaseData(demo=True, subject_id=DEMO_SUBJECT, files=files, revision=1)

    async def load_demo(self, run_id: str, key: str) -> dict:
        """Load a repeatable demo into an empty case without mixing material sources."""
        async with self.database.orm_session(write=True) as session:
            row, case = await self._row(session, run_id, key=key, lock=True)
            if case.demo:
                return case.view()
            if case.files or case.subject_id or case.saved:
                raise CaseConflictError("请在空白复诊任务中载入合成样例。")
            demo = self._demo_case()
            demo.revision = case.revision + 1
            row.state = demo.model_dump(mode="json")
            return demo.view()

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
            records = case_evidence(case.files, deduplicate=False)
            evidence = next((entry for entry in records if entry.id == record_id), None)
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
        """Persist a checklist for the current inputs and return counts to the run trace."""
        async with self.database.orm_session(write=True) as session:
            row, case = await self._row(session, run_id, lock=True)
            if case.saved:
                return {"already_saved": True, "revision": case.revision}
            if not any(file.evidence for file in case.files):
                return {"no_materials": True, "revision": case.revision}
            result = prepare_case(case.files)
            case.draft = {**result, "revision": case.revision}
            row.state = case.model_dump(mode="json")
            return {
                "revision": case.revision,
                "comparisons": len(result["comparisons"]),
                "pending_plans": len(result["pending_plans"]),
                **{section: len(items) for section, items in result["checklist"].items()},
            }

    async def confirm(self, run_id: str, revision: int) -> dict:
        """Freeze the approved draft and its eligible explicit todos under the case lock."""
        async with self.database.orm_session(write=True) as session:
            row, case = await self._row(session, run_id, lock=True)
            changed = {"saved": False, "reason": "revision_changed"}
            if case.saved is not None:
                if case.saved.get("revision", case.revision) != revision:
                    return changed
                return {"saved": True, "tasks": len(case.saved["tasks"])}
            if (
                case.draft is None or case.revision != revision
                or case.draft.get("revision") != revision
            ):
                return changed

            evidence = {entry.id: entry for entry in case_evidence(case.files, deduplicate=False)}
            tasks = []
            for record_id in dict.fromkeys(case.draft.get("pending_plans", [])):
                entry = evidence.get(record_id)
                if entry is None or not is_open_plan(entry.record):
                    continue
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
            return {"saved": True, "tasks": len(tasks)}
