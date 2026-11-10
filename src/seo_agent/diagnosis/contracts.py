from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from src.seo_agent.opportunities.models import OpportunityCandidate
from src.seo_agent.opportunities.normalizer import OpportunityCandidateNormalizer


class DiagnosisRequest(BaseModel):
    """诊断引擎的稳定输入。

    这里只传递已持久化的证据引用和必要上下文，不把 Provider 原始响应
    直接泄漏到业务机会层。
    """

    site_id: str = Field(min_length=1)
    scan_run_id: str = Field(min_length=1)
    evidence_snapshot_ids: list[str] = Field(min_length=1)
    question: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class DiagnosisEngine(Protocol):
    async def diagnose(self, request: DiagnosisRequest) -> list[OpportunityCandidate]: ...


class DiagnosisCoordinator:
    """真实诊断引擎与确定性业务层之间的边界。

    生产环境必须显式注入 DiagnosisEngine。这里没有默认引擎，也不会在
    模型或工具未配置时生成演示机会。
    """

    def __init__(
        self,
        *,
        engine: DiagnosisEngine,
        normalizer: OpportunityCandidateNormalizer | None = None,
    ) -> None:
        self.engine = engine
        self.normalizer = normalizer or OpportunityCandidateNormalizer()

    async def diagnose(self, request: DiagnosisRequest) -> list[OpportunityCandidate]:
        candidates = await self.engine.diagnose(request)
        return [self.normalizer.normalize(candidate) for candidate in candidates]
