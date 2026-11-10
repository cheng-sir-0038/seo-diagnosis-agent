from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.routes import promptops as route
from src.observability.storage import ObservabilityDatabase
from src.promptops.evaluation import EvaluationService
from src.promptops.experiments import ExperimentService
from src.promptops.registry import PromptRegistry
from src.server import app


@pytest.mark.asyncio
async def test_promptops_api_manages_prompt_versions_and_dataset(
    tmp_path: Path, monkeypatch
) -> None:
    database = ObservabilityDatabase(tmp_path / "api.db")
    await database.startup()
    registry = PromptRegistry(database)
    await registry.startup()
    evaluator = EvaluationService(database)
    monkeypatch.setattr(route, "prompt_registry", registry)
    monkeypatch.setattr(route, "prompt_evaluation_service", evaluator)
    monkeypatch.setattr(
        route,
        "prompt_experiment_service",
        ExperimentService(database, registry, evaluator),
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            prompt_response = await client.post(
                "/api/v1/promptops/prompts",
                json={"prompt_key": "test.prompt", "name": "Test", "purpose": "Test"},
            )
            prompt = prompt_response.json()
            version_response = await client.post(
                f"/api/v1/promptops/prompts/{prompt['id']}/versions",
                json={
                    "system_template": "system",
                    "user_template": "{{input}}",
                    "output_schema": {"required": ["answer"]},
                },
            )
            dataset_response = await client.post(
                "/api/v1/promptops/datasets",
                json={"name": "Regression", "prompt_id": prompt["id"]},
            )
            dataset = dataset_response.json()
            case_response = await client.post(
                f"/api/v1/promptops/datasets/{dataset['id']}/cases",
                json={"name": "Case", "input_data": {"x": 1}},
            )
            versions = await client.get(f"/api/v1/promptops/prompts/{prompt['id']}/versions")
        assert prompt_response.status_code == 201
        assert version_response.status_code == 201
        assert case_response.status_code == 201
        assert versions.json()[0]["version_label"] == "v1"
    finally:
        await database.shutdown()
