from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from src.observability.storage import ObservabilityDatabase
from src.promptops.models import VersionStage
from src.promptops.schema import PROMPTOPS_SCHEMA_SQL, PROMPTOPS_SCHEMA_VERSION


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class PromptRegistry:
    def __init__(self, database: ObservabilityDatabase) -> None:
        self.database = database

    async def startup(self) -> None:
        await self.database.executescript(PROMPTOPS_SCHEMA_SQL)
        await self.database.execute(
            "INSERT OR IGNORE INTO prompt_schema_migrations(version, applied_at) "
            "VALUES (?, datetime('now'))",
            (PROMPTOPS_SCHEMA_VERSION,),
        )

    async def create_prompt(self, *, prompt_key: str, name: str, purpose: str) -> dict[str, Any]:
        existing = await self.database.fetch_one(
            "SELECT * FROM prompt_definitions WHERE prompt_key = ?", (prompt_key,)
        )
        if existing is not None:
            return existing
        prompt_id = _id("prompt")
        now = _now()
        await self.database.execute(
            "INSERT INTO prompt_definitions(id, prompt_key, name, purpose, status, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?)",
            (prompt_id, prompt_key, name, purpose, now, now),
        )
        row = await self.database.fetch_one(
            "SELECT * FROM prompt_definitions WHERE id = ?", (prompt_id,)
        )
        assert row is not None
        return row

    async def list_prompts(self) -> list[dict[str, Any]]:
        return await self.database.fetch_all("SELECT * FROM prompt_definitions ORDER BY prompt_key")

    async def get_prompt(self, prompt_id: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            "SELECT * FROM prompt_definitions WHERE id = ?", (prompt_id,)
        )
        if row is None:
            raise KeyError(f"Prompt 不存在: {prompt_id}")
        return row

    async def get_prompt_by_key(self, prompt_key: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            "SELECT * FROM prompt_definitions WHERE prompt_key = ?", (prompt_key,)
        )
        if row is None:
            raise KeyError(f"Prompt 不存在: {prompt_key}")
        return row

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
        await self.get_prompt(prompt_id)
        payload = {
            "system_template": system_template,
            "user_template": user_template,
            "output_schema": output_schema,
            "config": config or {},
        }
        content_hash = hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
        existing = await self.database.fetch_one(
            "SELECT * FROM prompt_versions WHERE prompt_id = ? AND content_hash = ?",
            (prompt_id, content_hash),
        )
        if existing is not None:
            return self._hydrate_version(existing)
        current = await self.database.fetch_value(
            "SELECT COALESCE(MAX(version_no), 0) FROM prompt_versions WHERE prompt_id = ?",
            (prompt_id,),
        )
        version_no = int(current or 0) + 1
        version_id = _id("promptv")
        await self.database.execute(
            "INSERT INTO prompt_versions(id, prompt_id, version_no, version_label, stage, "
            "system_template, user_template, output_schema_json, config_json, content_hash, "
            "parent_version_id, change_summary, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                version_id,
                prompt_id,
                version_no,
                f"v{version_no}",
                stage.value,
                system_template,
                user_template,
                _json(output_schema),
                _json(config or {}),
                content_hash,
                parent_version_id,
                change_summary,
                created_by,
                _now(),
            ),
        )
        return await self.get_version(version_id)

    async def get_version(self, version_id: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            "SELECT * FROM prompt_versions WHERE id = ?", (version_id,)
        )
        if row is None:
            raise KeyError(f"Prompt 版本不存在: {version_id}")
        return self._hydrate_version(row)

    async def list_versions(self, prompt_id: str) -> list[dict[str, Any]]:
        rows = await self.database.fetch_all(
            "SELECT * FROM prompt_versions WHERE prompt_id = ? ORDER BY version_no DESC",
            (prompt_id,),
        )
        return [self._hydrate_version(row) for row in rows]

    async def list_datasets(self, prompt_id: str | None = None) -> list[dict[str, Any]]:
        if prompt_id:
            return await self.database.fetch_all(
                "SELECT * FROM prompt_datasets WHERE prompt_id = ? ORDER BY created_at DESC",
                (prompt_id,),
            )
        return await self.database.fetch_all(
            "SELECT * FROM prompt_datasets ORDER BY created_at DESC"
        )

    async def list_deployments(self, prompt_id: str | None = None) -> list[dict[str, Any]]:
        if prompt_id:
            return await self.database.fetch_all(
                "SELECT * FROM prompt_deployments WHERE prompt_id = ? ORDER BY environment",
                (prompt_id,),
            )
        return await self.database.fetch_all(
            "SELECT * FROM prompt_deployments ORDER BY updated_at DESC"
        )

    async def create_dataset(
        self,
        *,
        name: str,
        prompt_id: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        if prompt_id:
            await self.get_prompt(prompt_id)
        dataset_id = _id("dataset")
        now = _now()
        await self.database.execute(
            "INSERT INTO prompt_datasets(id, prompt_id, name, description, status, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?)",
            (dataset_id, prompt_id, name, description, now, now),
        )
        row = await self.database.fetch_one(
            "SELECT * FROM prompt_datasets WHERE id = ?", (dataset_id,)
        )
        assert row is not None
        return row

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
        dataset = await self.database.fetch_one(
            "SELECT * FROM prompt_datasets WHERE id = ?", (dataset_id,)
        )
        if dataset is None:
            raise KeyError(f"评测数据集不存在: {dataset_id}")
        case_id = _id("case")
        await self.database.execute(
            "INSERT INTO prompt_dataset_cases(id, dataset_id, name, input_json, "
            "expected_output_json, grading_rules_json, tags_json, source_trace_id, "
            "source_issue_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                case_id,
                dataset_id,
                name,
                _json(input_data),
                _json(expected_output or {}),
                _json(grading_rules or {}),
                _json(tags or []),
                source_trace_id,
                source_issue_id,
                _now(),
            ),
        )
        return await self.get_case(case_id)

    async def get_case(self, case_id: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            "SELECT * FROM prompt_dataset_cases WHERE id = ?", (case_id,)
        )
        if row is None:
            raise KeyError(f"评测用例不存在: {case_id}")
        return self._hydrate_case(row)

    async def list_cases(self, dataset_id: str) -> list[dict[str, Any]]:
        rows = await self.database.fetch_all(
            "SELECT * FROM prompt_dataset_cases WHERE dataset_id = ? ORDER BY created_at",
            (dataset_id,),
        )
        return [self._hydrate_case(row) for row in rows]

    async def get_deployment(self, prompt_id: str, environment: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            "SELECT * FROM prompt_deployments WHERE prompt_id = ? AND environment = ?",
            (prompt_id, environment),
        )
        if row is None:
            raise KeyError(f"Prompt 部署不存在: {prompt_id}/{environment}")
        return row

    async def resolve_version(
        self, prompt_key: str, *, environment: str = "production", bucket: int = 100
    ) -> dict[str, Any]:
        prompt = await self.get_prompt_by_key(prompt_key)
        deployment = await self.get_deployment(prompt["id"], environment)
        version_id = deployment["current_version_id"]
        if deployment["canary_version_id"] and bucket <= deployment["canary_percentage"]:
            version_id = deployment["canary_version_id"]
        if not version_id:
            raise RuntimeError(f"Prompt 尚未部署: {prompt_key}/{environment}")
        return await self.get_version(str(version_id))

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
        version = await self.get_version(version_id)
        if version["prompt_id"] != prompt_id:
            raise ValueError("版本不属于指定 Prompt")
        deployment = await self._ensure_deployment(prompt_id, environment)
        await self.database.execute(
            "UPDATE prompt_deployments SET canary_version_id = ?, canary_percentage = ?, "
            "updated_at = ? WHERE id = ?",
            (version_id, percentage, _now(), deployment["id"]),
        )
        await self.database.execute(
            "UPDATE prompt_versions SET stage = 'CANARY' WHERE id = ?", (version_id,)
        )
        await self._history(
            prompt_id,
            environment,
            "CANARY",
            deployment.get("current_version_id"),
            version_id,
            f"canary={percentage}%",
        )
        return await self.get_deployment(prompt_id, environment)

    async def promote(
        self,
        prompt_id: str,
        version_id: str,
        *,
        environment: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        version = await self.get_version(version_id)
        if version["prompt_id"] != prompt_id:
            raise ValueError("版本不属于指定 Prompt")
        deployment = await self._ensure_deployment(prompt_id, environment)
        previous = deployment.get("current_version_id")
        if previous and previous != version_id:
            await self.database.execute(
                "UPDATE prompt_versions SET stage = 'RETIRED' WHERE id = ?", (previous,)
            )
        await self.database.execute(
            "UPDATE prompt_versions SET stage = 'CURRENT' WHERE id = ?", (version_id,)
        )
        await self.database.execute(
            "UPDATE prompt_deployments SET current_version_id = ?, canary_version_id = NULL, "
            "canary_percentage = 0, updated_at = ? WHERE id = ?",
            (version_id, _now(), deployment["id"]),
        )
        await self._history(prompt_id, environment, "PROMOTE", previous, version_id, reason)
        return await self.get_deployment(prompt_id, environment)

    async def rollback(
        self,
        prompt_id: str,
        version_id: str,
        *,
        environment: str,
        reason: str,
    ) -> dict[str, Any]:
        version = await self.get_version(version_id)
        if version["prompt_id"] != prompt_id:
            raise ValueError("版本不属于指定 Prompt")
        deployment = await self.get_deployment(prompt_id, environment)
        previous = deployment.get("current_version_id")
        if previous and previous != version_id:
            await self.database.execute(
                "UPDATE prompt_versions SET stage = 'RETIRED' WHERE id = ?", (previous,)
            )
        await self.database.execute(
            "UPDATE prompt_versions SET stage = 'CURRENT' WHERE id = ?", (version_id,)
        )
        await self.database.execute(
            "UPDATE prompt_deployments SET current_version_id = ?, canary_version_id = NULL, "
            "canary_percentage = 0, updated_at = ? WHERE id = ?",
            (version_id, _now(), deployment["id"]),
        )
        await self._history(prompt_id, environment, "ROLLBACK", previous, version_id, reason)
        return await self.get_deployment(prompt_id, environment)

    async def _ensure_deployment(self, prompt_id: str, environment: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            "SELECT * FROM prompt_deployments WHERE prompt_id = ? AND environment = ?",
            (prompt_id, environment),
        )
        if row is not None:
            return row
        deployment_id = _id("deployment")
        await self.database.execute(
            "INSERT INTO prompt_deployments(id, prompt_id, environment, canary_percentage, "
            "updated_at) VALUES (?, ?, ?, 0, ?)",
            (deployment_id, prompt_id, environment, _now()),
        )
        result = await self.database.fetch_one(
            "SELECT * FROM prompt_deployments WHERE id = ?", (deployment_id,)
        )
        assert result is not None
        return result

    async def _history(
        self,
        prompt_id: str,
        environment: str,
        action: str,
        from_version_id: str | None,
        to_version_id: str | None,
        reason: str | None,
    ) -> None:
        await self.database.execute(
            "INSERT INTO prompt_promotion_history(id, prompt_id, environment, action, "
            "from_version_id, to_version_id, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _id("promotion"),
                prompt_id,
                environment,
                action,
                from_version_id,
                to_version_id,
                reason,
                _now(),
            ),
        )

    @staticmethod
    def _hydrate_version(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["output_schema_data"] = json.loads(row["output_schema_json"])
        row["config_data"] = json.loads(row["config_json"])
        return row

    @staticmethod
    def _hydrate_case(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["input_json_data"] = json.loads(row["input_json"])
        row["expected_output_data"] = json.loads(row["expected_output_json"])
        row["grading_rules_data"] = json.loads(row["grading_rules_json"])
        row["tags_data"] = json.loads(row["tags_json"])
        return row
