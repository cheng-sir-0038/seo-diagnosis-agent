from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.core.exceptions import InternalProviderRequestError, InternalProviderResponseError
from src.observability.client import ObservabilityClient
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.services.system import SeoAgentSystemService
from src.seo_agent.tasks.service import TaskDeliveryService
from src.seo_agent.workflows.task_delivery import TaskDeliveryWorkflows


class TaskPlanningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    site_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    task_type: str = Field(min_length=1, max_length=80)
    priority: Literal["LOW", "MEDIUM", "HIGH"]
    opportunity_ids: list[str] = Field(min_length=1, max_length=10)
    owner_actor_id: str = Field(min_length=1, max_length=200)
    created_by_actor_id: str = Field(min_length=1, max_length=200)
    deadline_at: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=128)


class TaskPlanningWorkflow:
    """已批准机会生成任务与 PRD；模型调用在写事务之外，业务结果原子提交。"""

    def __init__(
        self,
        database: SeoAgentDatabase,
        tasks: TaskDeliveryService,
        delivery: TaskDeliveryWorkflows,
        *,
        observability: ObservabilityClient | None = None,
    ) -> None:
        self.database, self.tasks, self.delivery = database, tasks, delivery
        self.observability = observability or delivery.observability
        self._lock = asyncio.Lock()

    async def execute(self, request: TaskPlanningRequest) -> dict:
        key = f"task-planning:{request.site_id}:{request.idempotency_key}"
        request_hash = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        async with self._lock:
            existing = await self._receipt(self.database, key, request_hash)
            if existing is not None:
                async with observed_operation(
                    self.observability,
                    "seo.task_planning.reused",
                    kind=SpanKind.INTERNAL,
                    input_data={"site_id": request.site_id},
                    metadata={"reused": True, "source_trace_id": existing.get("trace_id")},
                ) as replay:
                    if replay is not None:
                        replay.output_data = existing
                return existing
            async with observed_operation(
                self.observability,
                "seo.task_planning",
                kind=SpanKind.INTERNAL,
                input_data=request.model_dump(),
                trigger_type="api",
                metadata={"workflow_name": "task_planning", "site_id": request.site_id},
            ) as span:
                async with observed_operation(
                    self.observability,
                    "validate_approved_opportunities",
                    kind=SpanKind.INTERNAL,
                    input_data={"opportunity_ids": request.opportunity_ids},
                ) as validation:
                    opportunities = await self._context(self.database, request)
                    if validation is not None:
                        validation.output_data = {
                            "opportunities": [
                                {
                                    key: row[key]
                                    for key in ("id", "approval_action_id", "approval_status")
                                }
                                for row in opportunities
                            ]
                        }
                try:
                    invocation = await self.delivery.prepare_prd(
                        {
                            **request.model_dump(),
                            "id": None,
                            "opportunities": opportunities,
                        }
                    )
                except ValueError as exc:
                    raise InternalProviderResponseError("PRD 模型未返回有效结构化内容") from exc
                except Exception as exc:
                    raise InternalProviderRequestError("PRD 模型调用暂不可用") from exc
                task_id = None
                try:
                    async with observed_operation(
                        self.observability,
                        "persist_task_and_prd",
                        kind=SpanKind.INTERNAL,
                        input_data={
                            "site_id": request.site_id,
                            "opportunity_ids": request.opportunity_ids,
                        },
                    ) as write:
                        async with self.database.transaction() as transaction:
                            existing = await self._receipt(transaction, key, request_hash)
                            if existing is not None:
                                if write is not None:
                                    write.output_data = {"reused": True, **existing}
                                if span is not None:
                                    span.output_data = existing
                                return existing
                            await self._context(transaction, request)
                            tasks = TaskDeliveryService(
                                transaction,
                                artifact_root=self.tasks.artifact_root,
                                notifications=self.tasks.notifications,
                            )
                            tasks.markdown_renderer = self.tasks.markdown_renderer
                            tasks.pdf_renderer = self.tasks.pdf_renderer
                            task = await tasks.create_task(
                                **request.model_dump(
                                    include={
                                        "site_id",
                                        "title",
                                        "task_type",
                                        "priority",
                                        "opportunity_ids",
                                        "owner_actor_id",
                                        "deadline_at",
                                    }
                                ),
                                connection=transaction._require_connection(),
                            )
                            task_id = task["id"]
                            prd = await tasks.create_prd_version(
                                task_id=task_id,
                                title=f"{task['title']} PRD V1",
                                content=invocation.output,
                                created_by_actor_id=request.created_by_actor_id,
                                change_summary="已批准机会任务规划",
                                prompt_version_id=invocation.prompt_version_id,
                                source_trace_id=invocation.trace_id,
                            )
                            artifacts = await tasks.render_prd_artifacts(prd["id"])
                            result = {
                                "site_id": request.site_id,
                                "task_id": task_id,
                                "prd_version_id": prd["id"],
                                "artifact_ids": [row["id"] for row in artifacts],
                                "status": task["status"],
                                "workflow_status": "complete",
                                "trace_id": span.trace_id if span else invocation.trace_id,
                            }
                            await SeoAgentSystemService(transaction).append_work_record(
                                site_id=request.site_id,
                                title="完成已批准机会任务规划",
                                summary="已创建草稿任务与 PRD，尚未派发或发布。",
                                record_type="task_planning_completed",
                                actor_id=request.created_by_actor_id,
                                idempotency_key=key,
                                payload={"request_hash": request_hash, "result": result},
                            )
                        if write is not None:
                            write.output_data = result
                except BaseException:
                    if task_id and not await self.database.fetch_one(
                        "SELECT id FROM execution_tasks WHERE id=?", (task_id,)
                    ):
                        shutil.rmtree(self.tasks.artifact_root / task_id, ignore_errors=True)
                    raise
                if span is not None:
                    span.output_data = result
                return result

    async def _context(
        self, database: SeoAgentDatabase, request: TaskPlanningRequest
    ) -> list[dict]:
        site = await SeoAgentSystemService(database).get_site(request.site_id)
        if site["status"] != "ACTIVE" or site.get("archived_at") is not None:
            raise ValueError("站点当前不可创建任务")
        for actor_id in {request.owner_actor_id, request.created_by_actor_id}:
            actor = await database.fetch_one("SELECT * FROM actors WHERE id=?", (actor_id,))
            if actor is None:
                raise KeyError("任务负责人或操作人不存在")
            if actor["status"] != "ACTIVE" or actor.get("archived_at") is not None:
                raise ValueError("任务负责人或操作人当前不可用")
        return await self.tasks.validate_opportunities(
            site_id=request.site_id,
            opportunity_ids=request.opportunity_ids,
            connection=database._require_connection(),
        )

    @staticmethod
    async def _receipt(database: SeoAgentDatabase, key: str, request_hash: str) -> dict | None:
        record = await database.fetch_one(
            "SELECT payload_json FROM work_records WHERE idempotency_key=?", (key,)
        )
        if record is None:
            return None
        payload = json.loads(record["payload_json"])
        if payload.get("request_hash") != request_hash:
            raise ValueError("相同幂等键不能提交不同的任务规划请求")
        return payload["result"]
