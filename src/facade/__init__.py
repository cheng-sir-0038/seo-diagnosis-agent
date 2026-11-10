"""
Facade 层 — 统一查询入口 + Provider 路由。

所有外部 API 调用必须通过此层，不直接访问 Provider。
"""

from src.facade.data_facade import DataFacade, facade

__all__ = ["DataFacade", "facade"]
