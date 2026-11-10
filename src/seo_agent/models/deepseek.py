from __future__ import annotations

from langchain_deepseek import ChatDeepSeek


class LangChainDeepSeekFactory:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    def create(self, *, temperature: float = 0.0) -> ChatDeepSeek:
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY 未配置")
        return ChatDeepSeek(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            temperature=temperature,
            timeout=self.timeout,
            max_retries=self.max_retries,
            streaming=True,
        )
