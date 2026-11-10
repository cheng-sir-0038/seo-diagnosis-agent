from __future__ import annotations

import pytest

from src.agent.providers.deepseek import DeepSeekProvider


def test_deepseek_parser_assembles_streamed_tool_calls() -> None:
    provider = DeepSeekProvider(api_key="test", model="deepseek-v4-flash")
    chunks = [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {"name": "calculator", "arguments": '{"expression":'},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"1+2"}'}}]},
                    "finish_reason": "tool_calls",
                }
            ]
        },
    ]
    emitted = []
    for payload in chunks:
        emitted.extend(provider.parse_payload(payload))
    assert emitted[-1].tool_calls[0].name == "calculator"
    assert emitted[-1].tool_calls[0].arguments == {"expression": "1+2"}


def test_deepseek_parser_emits_text_delta() -> None:
    provider = DeepSeekProvider(api_key="test", model="deepseek-v4-flash")
    emitted = provider.parse_payload(
        {"choices": [{"delta": {"content": "你好"}, "finish_reason": None}]}
    )
    assert emitted[0].text == "你好"


def test_deepseek_rejects_invalid_tool_json() -> None:
    provider = DeepSeekProvider(api_key="test", model="deepseek-v4-flash")
    provider.parse_payload(
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {"name": "calculator", "arguments": "{"},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        }
    )
    with pytest.raises(ValueError, match="工具参数不是有效 JSON"):
        provider.parse_payload({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]})


def test_deepseek_payload_uses_configured_model_and_tools() -> None:
    provider = DeepSeekProvider(api_key="test", model="deepseek-v4-flash")
    payload = provider.build_payload(
        [{"role": "user", "content": "hi"}],
        [{"type": "function", "function": {"name": "x", "parameters": {"type": "object"}}}],
    )
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["stream"] is True
    assert payload["tool_choice"] == "auto"


def test_deepseek_payload_can_disable_tool_calls_for_budget_summary() -> None:
    provider = DeepSeekProvider(api_key="test", model="deepseek-v4-flash")
    payload = provider.build_payload(
        [{"role": "user", "content": "summarize"}],
        [{"type": "function", "function": {"name": "x", "parameters": {"type": "object"}}}],
        tool_choice="none",
    )
    assert payload["tools"]
    assert payload["tool_choice"] == "none"
