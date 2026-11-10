"""隔离调用 python-seo-analyzer 的本地子进程客户端。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from src.core.config import settings
from src.providers.page_metadata.client import FetchedHtmlPage


@dataclass(frozen=True, slots=True)
class PythonSeoAnalyzerConfig:
    enabled: bool = field(default_factory=lambda: settings.python_seo_analyzer_enabled)
    python_executable: str = field(
        default_factory=lambda: settings.python_seo_analyzer_python
    )
    runner_path: Path = field(
        default_factory=lambda: Path(__file__).with_name("pyseo_runner.py")
    )
    timeout: float = field(default_factory=lambda: settings.python_seo_analyzer_timeout)
    max_output_bytes: int = field(
        default_factory=lambda: settings.python_seo_analyzer_max_output_bytes
    )


class PythonSeoAnalyzerClient:
    """把已安全获取的 HTML 交给隔离 Python 环境分析。"""

    def __init__(self, config: PythonSeoAnalyzerConfig | None = None) -> None:
        self.config = config or PythonSeoAnalyzerConfig()

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    async def analyze(self, page: FetchedHtmlPage) -> dict[str, Any]:
        if not self.enabled:
            return {
                "analyzer_name": "python-seo-analyzer",
                "analyzer_status": "disabled",
            }

        executable = _resolve_executable(self.config.python_executable)
        runner_path = self.config.runner_path.expanduser().resolve()
        if not runner_path.is_file():
            raise FileNotFoundError("python-seo-analyzer runner 不存在")

        parsed = urlsplit(page.final_url)
        base_domain = f"{parsed.scheme}://{parsed.netloc}"
        payload = json.dumps(
            {
                "url": page.final_url,
                "base_domain": base_domain,
                "html": page.html,
            },
            ensure_ascii=False,
        ).encode("utf-8")

        process = await asyncio.create_subprocess_exec(
            executable,
            str(runner_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_subprocess_environment(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(payload),
                timeout=self.config.timeout,
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise TimeoutError("python-seo-analyzer 分析超时") from exc

        if len(stdout) > self.config.max_output_bytes:
            raise ValueError("python-seo-analyzer 输出超过大小限制")
        if process.returncode != 0:
            error_type = stderr.decode("utf-8", errors="replace").strip().split(":", 1)[0]
            raise RuntimeError(
                f"python-seo-analyzer runner 失败: {error_type or 'unknown'}"
            )

        try:
            result = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("python-seo-analyzer 返回了无效 JSON") from exc
        if not isinstance(result, dict):
            raise ValueError("python-seo-analyzer 返回结果必须是对象")
        return result


def _resolve_executable(value: str) -> str:
    executable = str(value or "").strip()
    if not executable:
        raise FileNotFoundError("未配置 python-seo-analyzer 隔离 Python")
    expanded = os.path.expanduser(executable)
    if os.path.sep in expanded:
        if not Path(expanded).is_file():
            raise FileNotFoundError("python-seo-analyzer 隔离 Python 不存在")
        return expanded
    resolved = shutil.which(expanded)
    if not resolved:
        raise FileNotFoundError("python-seo-analyzer 隔离 Python 不存在")
    return resolved


def _subprocess_environment() -> dict[str, str]:
    environment = dict(os.environ)
    # 该 runner 永远不启用第三方包中的 LLM 分析；同时移除 Anthropic Key，
    # 避免未来代码变化时意外产生外部模型请求。
    environment.pop("ANTHROPIC_API_KEY", None)
    return environment
