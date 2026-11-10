"""
Ahrefs Provider — Ahrefs API v3 数据适配器。

实现 BaseProvider 定义的所有业务能力，将 Ahrefs API 响应
翻译为统一数据模型（``src.models``）。

模块结构:
  - client.py: HTTP 客户端（速率控制、重试、缓存）
  - config.py: 配置 + 端点路由表
  - management.py: T1 — 项目管理 + 订阅信息
  - search.py: T2 — Site Explorer（搜索表现、关键词、Top Pages）
  - audit.py: T3 — Site Audit（健康分、问题、Page Explorer）
  - keywords.py: T4a — Keywords Explorer + SERP Overview
  - rank.py: T4b — Rank Tracker
  - backlinks.py: T5a — 外链（Backlinks、Refdomains、Broken）
  - competitors.py: T5b — 竞争分析（Competitors、Batch Analysis）
"""

from src.providers.ahrefs.provider import AhrefsProvider

__all__ = ["AhrefsProvider"]
