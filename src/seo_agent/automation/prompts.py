from __future__ import annotations

from typing import Any

from src.promptops.models import CandidatePrompt, VersionStage
from src.promptops.registry import PromptRegistry
from src.seo_agent.automation.models import DeliveryReviewOutput
from src.seo_agent.tasks.models import TaskPrdContent

SEO_PROMPT_DEFINITIONS: dict[str, dict[str, Any]] = {
    "agent.prompt.optimize": {
        "name": "提示词候选优化",
        "purpose": "根据当前版本和失败样本生成不可直接上线的 Challenger",
        "system_template": (
            "你是 PromptOps 候选版本生成器。根据当前提示词和失败样本生成一个 Challenger。"
            "不得宣称已经上线，不得修改生产部署，不得删除输出 Schema 的必填语义。"
            "必须说明变更摘要，并优先解决有证据支持的失败模式。"
        ),
        "user_template": "{{input}}",
        "output_schema": CandidatePrompt.model_json_schema(),
    },
    "seo.prd.generate": {
        "name": "SEO 任务 PRD 生成",
        "purpose": "根据已批准机会生成结构化人工执行 PRD",
        "system_template": (
            "你是 SEO 任务 PRD 生成器。只能使用输入中的机会和任务事实，"
            "不得编造搜索量、排名、报价、客户或已经验证的 SEO 结论。"
            "输出必须满足 TaskPrdContent 结构，并给人工内容执行者提供可验收要求。"
        ),
        "user_template": "{{input}}",
        "output_schema": TaskPrdContent.model_json_schema(),
    },
    "seo.delivery.review": {
        "name": "SEO 交付验收",
        "purpose": "对照指定 PRD 版本检查人工交付完整性",
        "system_template": (
            "你是交付验收器。只对照给定 PRD 检查交付完整性，不诊断排名原因，"
            "不推测发布效果，不执行修复。缺失内容必须列入 missing_items 或 failed_items。"
        ),
        "user_template": "{{input}}",
        "output_schema": DeliveryReviewOutput.model_json_schema(),
    },
}


class SeoPromptSeeder:
    def __init__(self, registry: PromptRegistry, *, environment: str = "production") -> None:
        self.registry = registry
        self.environment = environment

    async def seed(self) -> dict[str, dict[str, Any]]:
        await self.registry.startup()
        resolved: dict[str, dict[str, Any]] = {}
        for prompt_key, definition in SEO_PROMPT_DEFINITIONS.items():
            prompt = await self.registry.create_prompt(
                prompt_key=prompt_key,
                name=definition["name"],
                purpose=definition["purpose"],
            )
            initial = await self.registry.create_version(
                prompt_id=prompt["id"],
                system_template=definition["system_template"],
                user_template=definition["user_template"],
                output_schema=definition["output_schema"],
                stage=VersionStage.DRAFT,
                change_summary="内置 V1 提示词迁移",
                created_by="seo-prompt-seeder",
            )
            deployments = await self.registry.list_deployments(prompt["id"])
            if not any(item["environment"] == self.environment for item in deployments):
                await self.registry.promote(
                    prompt["id"],
                    initial["id"],
                    environment=self.environment,
                    reason="初始化生产提示词",
                )
            resolved[prompt_key] = await self.registry.resolve_version(
                prompt_key,
                environment=self.environment,
            )
        return resolved


def builtin_prompt(prompt_key: str) -> dict[str, Any]:
    definition = SEO_PROMPT_DEFINITIONS[prompt_key]
    return {
        "id": None,
        "prompt_key": prompt_key,
        "version_label": "builtin-v1",
        "system_template": definition["system_template"],
        "user_template": definition["user_template"],
        "output_schema_data": definition["output_schema"],
        "config_data": {},
    }
