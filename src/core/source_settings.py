"""Local data-source overrides used by Settings at process startup."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Literal


@dataclass(frozen=True)
class SourceField:
    name: str
    label: str
    kind: Literal["secret", "text", "switch"] = "secret"
    required: bool = True


@dataclass(frozen=True)
class SourceDefinition:
    id: str
    name: str
    group: str
    description: str
    fields: tuple[SourceField, ...] = ()


SOURCES = (
    SourceDefinition("deepseek", "DeepSeek", "模型", "主 Agent 调度和资料问答", (
        SourceField("deepseek_api_key", "API Key"),
    )),
    SourceDefinition("page_html", "网页 HTML", "SEO / GEO", "读取公开网页的标题、正文和页面标签"),
    SourceDefinition("serper", "Serper", "SEO / GEO", "Google 搜索结果、联想词和媒体检索", (
        SourceField("serper_api_key", "API Key"),
    )),
    SourceDefinition("dataforseo", "DataForSEO", "SEO / GEO", "关键词指标、自然排名和竞品资料", (
        SourceField("dataforseo_login", "API 用户名", "text"),
        SourceField("dataforseo_api_key", "API 密码"),
    )),
    SourceDefinition(
        "pagespeed", "PageSpeed Insights", "SEO / GEO", "单页面 Lighthouse 实验室数据",
        (SourceField("pagespeed_api_key", "API Key"),),
    ),
    SourceDefinition("semrush", "Semrush", "SEO / GEO", "站点流量价值和自然搜索竞品", (
        SourceField("semrush_api_key", "API Key"),
    )),
    SourceDefinition("ahrefs", "Ahrefs", "SEO / GEO", "现有兼容适配器"),
    SourceDefinition("open_meteo", "Open-Meteo", "商旅行程", "读取目的地逐日天气预报"),
    SourceDefinition("flyai", "飞猪 FlyAI", "商旅行程", "查询交通与酒店候选，需安装 FlyAI CLI", (
        SourceField("flyai_api_key", "API Key"),
    )),
    SourceDefinition("baidu_maps", "百度地图", "商旅行程", "查询地点位置和驾车接驳", (
        SourceField("baidu_maps_ak", "Web 服务 AK"),
    )),
    SourceDefinition("taobao", "淘宝联盟", "购物采购", "查商品、参考价和推广券资料", (
        SourceField("taobao_tbk_app_key", "App Key"),
        SourceField("taobao_tbk_app_secret", "App Secret"),
        SourceField("taobao_tbk_adzone_id", "推广位 ID", "text", required=False),
    )),
    SourceDefinition("local_catalog", "本地商品库", "购物采购", "项目内置的合成商品资料", (
        SourceField("taobao_tbk_local_catalog", "使用本地商品库", "switch"),
    )),
    SourceDefinition("pubmed", "PubMed", "医疗健康", "通过 NCBI 查询公开医学题录"),
)
_FIELDS = {field.name: field for source in SOURCES for field in source.fields}
_LOCK = Lock()


def source_config_path() -> Path:
    configured = os.environ.get("WORKSPACE_SOURCE_CONFIG")
    return Path(configured) if configured else (
        Path(__file__).resolve().parents[2] / "data" / "workspace-sources.json"
    )


def read_source_settings() -> dict[str, str | bool]:
    try:
        loaded = json.loads(source_config_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (UnicodeError, ValueError, RecursionError):
        raise ValueError("数据源配置文件无法读取。") from None
    if not isinstance(loaded, dict):
        raise ValueError("数据源配置文件格式无效。")
    selected = {key: value for key, value in loaded.items() if key in _FIELDS}
    if any(type(value) is not (bool if _FIELDS[key].kind == "switch" else str)
           for key, value in selected.items()):
        raise ValueError("数据源配置文件格式无效。")
    return selected


def save_source_settings(changes: dict[str, str | bool]) -> None:
    path = source_config_path()
    with _LOCK:
        merged = {**read_source_settings(), **changes}
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        staged: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", dir=path.parent, encoding="utf-8", delete=False,
            ) as file:
                staged = Path(file.name)
                json.dump(merged, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(staged, path)
        finally:
            if staged:
                staged.unlink(missing_ok=True)


def environment_manages(field_name: str) -> bool:
    return any(key.casefold() == field_name for key in os.environ)
