from __future__ import annotations

import pytest

from src.agent.tools.calculator import CalculatorTool
from src.agent.tools.current_time import CurrentTimeTool


@pytest.mark.asyncio
async def test_current_time_uses_iana_timezone() -> None:
    result = await CurrentTimeTool().execute({"timezone": "Europe/London"})
    assert result.success is True
    assert result.data["timezone"] == "Europe/London"
    assert result.data["iso_time"]


@pytest.mark.asyncio
async def test_calculator_evaluates_safe_expression() -> None:
    result = await CalculatorTool().execute({"expression": "1234 * 56"})
    assert result.success is True
    assert result.data["result"] == 69104


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expression", ["__import__('os').system('id')", "(1).__class__", "2 ** 1000000"]
)
async def test_calculator_rejects_unsafe_expression(expression: str) -> None:
    result = await CalculatorTool().execute({"expression": expression})
    assert result.success is False
