from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Literal

from sqlalchemy import and_, case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.observability.storage import ObservabilityDatabase
from src.promptops.models import VersionStage
from src.promptops.schema import (
    PROMPTOPS_SCHEMA_VERSION,
    DatasetCase,
    PromotionHistory,
    PromptDataset,
    PromptDefinition,
    PromptDeployment,
    PromptSchemaVersion,
    PromptVersion,
    now,
    record,
)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class PromptRegistry:
    def __init__(self, database: ObservabilityDatabase) -> None:
        self.database = database

    async def startup(self) -> None:
        await self.database.startup()
        async with self.database.orm_session(write=True) as session:
            await session.execute(
                insert(PromptSchemaVersion)
                .values(version=PROMPTOPS_SCHEMA_VERSION)
                .on_conflict_do_nothing(index_elements=[PromptSchemaVersion.version])
            )

    async def create_prompt(self, *, prompt_key: str, name: str, purpose: str) -> dict[str, Any]:
        async with self.database.orm_session(write=True) as session:
            await session.execute(
                insert(PromptDefinition)
                .values(
                    id=_id("prompt"),
                    prompt_key=prompt_key,
                    name=name,
                    purpose=purpose,
                )
                .on_conflict_do_nothing(index_elements=[PromptDefinition.prompt_key])
            )
            row = (
                await session.scalars(
                    select(PromptDefinition).where(PromptDefinition.prompt_key == prompt_key)
                )
            ).one()
            return record(row)

    async def list_prompts(self) -> list[dict[str, Any]]:
        async with self.database.orm_session() as session:
            rows = await session.scalars(
                select(PromptDefinition).order_by(PromptDefinition.prompt_key)
            )
            return [record(row) for row in rows]

    async def get_prompt(self, prompt_id: str) -> dict[str, Any]:
        async with self.database.orm_session() as session:
            row = await session.get(PromptDefinition, prompt_id)
            if row is None:
                raise KeyError(f"Prompt 不存在: {prompt_id}")
            return record(row)

    async def get_prompt_by_key(self, prompt_key: str) -> dict[str, Any]:
        async with self.database.orm_session() as session:
            row = await session.scalar(
                select(PromptDefinition).where(PromptDefinition.prompt_key == prompt_key)
            )
            if row is None:
                raise KeyError(f"Prompt 不存在: {prompt_key}")
            return record(row)

    async def create_version(
        self,
        *,
        prompt_id: str,
        system_template: str,
        user_template: str,
        output_schema: dict[str, Any],
        stage: VersionStage = VersionStage.DRAFT,
        config: dict[str, Any] | None = None,
        parent_version_id: str | None = None,
        change_summary: str | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        content = json.dumps(
            {
                "system_template": system_template,
                "user_template": user_template,
                "output_schema": output_schema,
                "config": config or {},
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        async with self.database.orm_session(write=True) as session:
            # Serialize numbering per prompt, including two different edits arriving together.
            await self._lock_prompt(session, prompt_id)
            existing = await session.scalar(
                select(PromptVersion).where(
                    PromptVersion.prompt_id == prompt_id,
                    PromptVersion.content_hash == content_hash,
                )
            )
            if existing is not None:
                return existing.public()
            last = await session.scalar(
                select(func.max(PromptVersion.version_no)).where(
                    PromptVersion.prompt_id == prompt_id
                )
            )
            number = (last or 0) + 1
            version = PromptVersion(
                id=_id("promptv"),
                prompt_id=prompt_id,
                version_no=number,
                version_label=f"v{number}",
                stage=stage.value,
                content_hash=content_hash,
                system_template=system_template,
                user_template=user_template,
                output_schema_json=output_schema,
                config_json=config or {},
                parent_version_id=parent_version_id,
                change_summary=change_summary,
                created_by=created_by,
            )
            session.add(version)
            await session.flush()
            return version.public()

    async def get_version(self, version_id: str) -> dict[str, Any]:
        async with self.database.orm_session() as session:
            row = await session.get(PromptVersion, version_id)
            if row is None:
                raise KeyError(f"Prompt 版本不存在: {version_id}")
            return row.public()

    async def list_versions(self, prompt_id: str) -> list[dict[str, Any]]:
        async with self.database.orm_session() as session:
            rows = await session.scalars(
                select(PromptVersion)
                .where(PromptVersion.prompt_id == prompt_id)
                .order_by(PromptVersion.version_no.desc())
            )
            return [row.public() for row in rows]

    async def list_datasets(self, prompt_id: str | None = None) -> list[dict[str, Any]]:
        query = select(PromptDataset).order_by(PromptDataset.created_at.desc())
        if prompt_id:
            query = query.where(PromptDataset.prompt_id == prompt_id)
        async with self.database.orm_session() as session:
            return [record(row) for row in await session.scalars(query)]

    async def list_deployments(self, prompt_id: str | None = None) -> list[dict[str, Any]]:
        query = select(PromptDeployment)
        if prompt_id:
            query = query.where(PromptDeployment.prompt_id == prompt_id).order_by(
                PromptDeployment.environment
            )
        else:
            query = query.order_by(PromptDeployment.updated_at.desc())
        async with self.database.orm_session() as session:
            return [record(row) for row in await session.scalars(query)]

    async def create_dataset(
        self,
        *,
        name: str,
        prompt_id: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        async with self.database.orm_session(write=True) as session:
            if prompt_id and await session.get(PromptDefinition, prompt_id) is None:
                raise KeyError(f"Prompt 不存在: {prompt_id}")
            row = PromptDataset(
                id=_id("dataset"),
                prompt_id=prompt_id,
                name=name,
                description=description,
            )
            session.add(row)
            await session.flush()
            return record(row)

    async def add_case(
        self,
        *,
        dataset_id: str,
        name: str,
        input_data: Any,
        expected_output: Any = None,
        grading_rules: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        source_trace_id: str | None = None,
        source_issue_id: str | None = None,
    ) -> dict[str, Any]:
        async with self.database.orm_session(write=True) as session:
            if await session.get(PromptDataset, dataset_id) is None:
                raise KeyError(f"评测数据集不存在: {dataset_id}")
            row = DatasetCase(
                id=_id("case"),
                dataset_id=dataset_id,
                name=name,
                input_json=input_data,
                expected_output_json={} if expected_output is None else expected_output,
                grading_rules_json=grading_rules or {},
                tags_json=tags or [],
                source_trace_id=source_trace_id,
                source_issue_id=source_issue_id,
            )
            session.add(row)
            await session.flush()
            return row.public()

    async def get_case(self, case_id: str) -> dict[str, Any]:
        async with self.database.orm_session() as session:
            row = await session.get(DatasetCase, case_id)
            if row is None:
                raise KeyError(f"评测用例不存在: {case_id}")
            return row.public()

    async def list_cases(self, dataset_id: str) -> list[dict[str, Any]]:
        async with self.database.orm_session() as session:
            rows = await session.scalars(
                select(DatasetCase)
                .where(DatasetCase.dataset_id == dataset_id)
                .order_by(DatasetCase.created_at)
            )
            return [row.public() for row in rows]

    async def get_deployment(self, prompt_id: str, environment: str) -> dict[str, Any]:
        async with self.database.orm_session() as session:
            row = await session.scalar(
                select(PromptDeployment).where(
                    PromptDeployment.prompt_id == prompt_id,
                    PromptDeployment.environment == environment,
                )
            )
            if row is None:
                raise KeyError(f"Prompt 部署不存在: {prompt_id}/{environment}")
            return record(row)

    async def resolve_version(
        self, prompt_key: str, *, environment: str = "production", bucket: int = 100
    ) -> dict[str, Any]:
        active_version = case(
            (
                and_(
                    PromptDeployment.canary_version_id.is_not(None),
                    PromptDeployment.canary_percentage >= bucket,
                ),
                PromptDeployment.canary_version_id,
            ),
            else_=PromptDeployment.current_version_id,
        )
        query = (
            select(PromptDefinition.id, PromptDeployment.id, PromptVersion)
            .select_from(PromptDefinition)
            .outerjoin(
                PromptDeployment,
                and_(
                    PromptDeployment.prompt_id == PromptDefinition.id,
                    PromptDeployment.environment == environment,
                ),
            )
            .outerjoin(PromptVersion, PromptVersion.id == active_version)
            .where(PromptDefinition.prompt_key == prompt_key)
        )
        async with self.database.orm_session() as session:
            result = (await session.execute(query)).one_or_none()
            if result is None:
                raise KeyError(f"Prompt 不存在: {prompt_key}")
            prompt_id, deployment_id, version = result
            if deployment_id is None:
                raise KeyError(f"Prompt 部署不存在: {prompt_id}/{environment}")
            if version is None:
                raise RuntimeError(f"Prompt 尚未部署: {prompt_key}/{environment}")
            return version.public()

    async def start_canary(
        self,
        prompt_id: str,
        version_id: str,
        *,
        environment: str,
        percentage: int,
    ) -> dict[str, Any]:
        if not 1 <= percentage <= 100:
            raise ValueError("灰度比例必须在 1 到 100 之间")
        return await self._change_deployment(
            prompt_id,
            version_id,
            environment=environment,
            action="CANARY",
            percentage=percentage,
            reason=f"canary={percentage}%",
        )

    async def deploy_if_missing(
        self,
        prompt_id: str,
        version_id: str,
        *,
        environment: str,
        reason: str,
    ) -> dict[str, Any]:
        return await self._change_deployment(
            prompt_id,
            version_id,
            environment=environment,
            action="SEED",
            reason=reason,
        )

    async def promote(
        self,
        prompt_id: str,
        version_id: str,
        *,
        environment: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        return await self._change_deployment(
            prompt_id,
            version_id,
            environment=environment,
            action="PROMOTE",
            reason=reason,
        )

    async def rollback(
        self,
        prompt_id: str,
        version_id: str,
        *,
        environment: str,
        reason: str,
    ) -> dict[str, Any]:
        return await self._change_deployment(
            prompt_id,
            version_id,
            environment=environment,
            action="ROLLBACK",
            reason=reason,
        )

    @staticmethod
    async def _lock_prompt(session: AsyncSession, prompt_id: str) -> None:
        if await session.get(PromptDefinition, prompt_id, with_for_update=True) is None:
            raise KeyError(f"Prompt 不存在: {prompt_id}")

    async def _change_deployment(
        self,
        prompt_id: str,
        version_id: str,
        *,
        environment: str,
        action: Literal["SEED", "CANARY", "PROMOTE", "ROLLBACK"],
        reason: str | None,
        percentage: int = 0,
    ) -> dict[str, Any]:
        async with self.database.orm_session(write=True) as session:
            await self._lock_prompt(session, prompt_id)
            version = await session.get(PromptVersion, version_id)
            if version is None:
                raise KeyError(f"Prompt 版本不存在: {version_id}")
            if version.prompt_id != prompt_id:
                raise ValueError("版本不属于指定 Prompt")
            deployment = await session.scalar(
                select(PromptDeployment).where(
                    PromptDeployment.prompt_id == prompt_id,
                    PromptDeployment.environment == environment,
                )
            )
            if deployment is not None and action == "SEED":
                return record(deployment)
            if deployment is None:
                if action == "ROLLBACK":
                    raise KeyError(f"Prompt 部署不存在: {prompt_id}/{environment}")
                deployment = PromptDeployment(
                    id=_id("deployment"),
                    prompt_id=prompt_id,
                    environment=environment,
                )
                session.add(deployment)

            previous = deployment.current_version_id
            if action == "CANARY":
                version.stage = VersionStage.CANARY.value
                deployment.canary_version_id = version_id
                deployment.canary_percentage = percentage
            else:
                if previous and previous != version_id:
                    await session.execute(
                        update(PromptVersion)
                        .where(PromptVersion.id == previous)
                        .values(stage=VersionStage.RETIRED.value)
                    )
                version.stage = VersionStage.CURRENT.value
                deployment.current_version_id = version_id
                deployment.canary_version_id = None
                deployment.canary_percentage = 0
            deployment.updated_at = now()
            session.add(
                PromotionHistory(
                    id=_id("promotion"),
                    prompt_id=prompt_id,
                    environment=environment,
                    action="PROMOTE" if action == "SEED" else action,
                    from_version_id=previous,
                    to_version_id=version_id,
                    reason=reason,
                )
            )
            await session.flush()
            return record(deployment)
