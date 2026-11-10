from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from src.agent.config import (
    AgentConfig,
    AgentPromptRenderer,
    Skill,
    SkillRegistry,
)


def seo_skill(
    name: str = "seo-diagnosis",
    *,
    tools: list[str] | None = None,
    metadata: dict | None = None,
    domain: str = "seo",
) -> Skill:
    return Skill(
        name=name,
        description="用于 SEO 诊断和页面问题分析",
        domain=domain,
        prompt_fragment="所有结论必须引用真实 SEO 证据。",
        tools=tools or ["seo_page_metadata"],
        metadata=metadata or {"match_terms": ["诊断", "TDK"]},
    )


def base_config(**overrides) -> AgentConfig:
    payload = {
        "id": "seo-specialist",
        "name": "SEO 优化专员",
        "description": "只读 SEO 诊断 Agent",
        "instructions": (
            "你是 SEO 优化专员。\n{goals}\n{targets}\n{constraints}\n{skills}\n{context}"
        ),
        "tools": ["seo_page_metadata", "seo_page_performance"],
        "skills": ["seo-diagnosis"],
        "goals": ["定位页面问题", "给出证据结论"],
        "targets": {"evidence_coverage": 1.0, "max_tool_calls": 6},
        "constraints": ["不得使用伪数据", "不得自动发布"],
        "domain": "seo",
    }
    payload.update(overrides)
    return AgentConfig(**payload)


def test_agent_config_defaults_and_fail_closed_validation() -> None:
    config = AgentConfig(
        id="seo-agent",
        name="SEO Agent",
        description="SEO diagnosis",
        instructions="Inspect the site. {context}",
        domain="seo",
    )
    assert config.model == "deepseek/deepseek-v4-flash"
    assert config.tools == []
    assert config.skills == []
    assert config.auto_resolve_skills is True

    with pytest.raises(ValidationError):
        AgentConfig(
            id="seo-agent",
            name="SEO Agent",
            description="SEO diagnosis",
            instructions="Inspect",
            domain="seo",
            constraint=["typo must be rejected"],
        )
    with pytest.raises(ValidationError):
        base_config(model="deepseek-v4-flash")
    with pytest.raises(ValidationError):
        base_config(tools=["seo_page_metadata", "seo_page_metadata"])
    with pytest.raises(ValidationError):
        base_config(targets={"bad": math.nan})
    with pytest.raises(ValidationError):
        base_config(targets={"bad": math.inf})


def test_skill_registry_is_deterministic_and_rejects_duplicates() -> None:
    registry = SkillRegistry(
        [
            seo_skill(),
            seo_skill(
                "page-performance",
                tools=["seo_page_performance"],
                metadata={"match_terms": ["速度", "PageSpeed"]},
            ),
            seo_skill(
                "seo-baseline",
                tools=[],
                metadata={"always_load": True},
            ),
            seo_skill("finance-risk", tools=[], domain="finance"),
        ]
    )

    assert [item.name for item in registry.resolve("seo", "检查首页 TDK 和速度")] == [
        "seo-diagnosis",
        "page-performance",
        "seo-baseline",
    ]
    assert [item.name for item in registry.resolve("support", "检查首页 TDK")] == []
    assert registry.get("seo-diagnosis").name == "seo-diagnosis"
    with pytest.raises(KeyError):
        registry.get("missing")
    with pytest.raises(ValueError):
        registry.register(seo_skill())


def test_renderer_merges_explicit_and_auto_skills_without_tool_escalation() -> None:
    registry = SkillRegistry(
        [
            seo_skill(),
            seo_skill(
                "page-performance",
                tools=["seo_page_performance"],
                metadata={"match_terms": ["速度"]},
            ),
            seo_skill("seo-baseline", tools=[], metadata={"always_load": True}),
        ]
    )
    renderer = AgentPromptRenderer(registry)
    config = base_config()

    rendered = renderer.render(
        config,
        context={"site_id": "site_1", "domain": "example.com"},
        task="检查首页 TDK 和速度",
    )

    assert rendered.resolved_skill_names == [
        "seo-diagnosis",
        "page-performance",
        "seo-baseline",
    ]
    assert "# Goals" in rendered.system_prompt
    assert "1. 定位页面问题" in rendered.system_prompt
    assert "evidence_coverage: 1" in rendered.system_prompt
    assert "# Hard Constraints" in rendered.system_prompt
    assert '<skill name="seo-diagnosis" domain="seo">' in rendered.system_prompt
    assert '<runtime_context trust="untrusted">' in rendered.system_prompt
    assert '"domain": "example.com"' in rendered.system_prompt
    assert len(rendered.prompt_hash) == 64
    assert (
        renderer.render(
            config,
            context={"domain": "example.com", "site_id": "site_1"},
            task="检查首页 TDK 和速度",
        ).prompt_hash
        == rendered.prompt_hash
    )

    unauthorized = base_config(
        tools=["seo_page_metadata"],
        skills=["page-performance"],
        auto_resolve_skills=False,
    )
    with pytest.raises(ValueError, match="未授权工具"):
        renderer.render(unauthorized, context={}, task="检查速度")


def test_agent_config_direct_render_is_pure_and_rejects_unknown_template_variables() -> None:
    skill = seo_skill()
    config = base_config(auto_resolve_skills=False)
    prompt = config.render_system_prompt(
        {"site_id": "site_1"},
        resolved_skills=[skill],
    )
    assert "seo-diagnosis" in prompt
    assert "site_1" in prompt

    bad = base_config(instructions="Hello {unknown}")
    with pytest.raises(ValueError, match="未知模板变量"):
        bad.render_system_prompt({})


def test_renderer_appends_nonempty_sections_when_placeholders_are_omitted() -> None:
    registry = SkillRegistry([seo_skill()])
    config = base_config(instructions="你是 SEO Agent。")
    rendered = AgentPromptRenderer(registry).render(
        config,
        context={"site_id": "site_1"},
        task="诊断页面",
    )
    assert rendered.system_prompt.startswith("你是 SEO Agent。")
    assert rendered.system_prompt.count("# Goals") == 1
    assert rendered.system_prompt.count("# Loaded Skills") == 1
    assert rendered.system_prompt.count("# Runtime Context") == 1
