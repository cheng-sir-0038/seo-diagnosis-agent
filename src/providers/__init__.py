"""
Provider 层 — 各数据源的适配器实现。

每个 Provider 将外部 API 的异构响应翻译为统一数据模型（src.models）。
上层通过 Facade 调用，不直接接触 Provider 实例。

扩展新数据源: 继承 BaseProvider，实现所需方法，在 Facade 中注册即可。
"""

from src.providers.base import BaseProvider, ProviderMeta

__all__ = ["BaseProvider", "ProviderMeta"]
