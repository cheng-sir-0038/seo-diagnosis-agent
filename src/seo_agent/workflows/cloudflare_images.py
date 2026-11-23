from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from base64 import b64decode
from pathlib import Path
from typing import TypedDict
from uuid import UUID, uuid4

import httpx

from src.core.config import Settings, settings
from src.core.exceptions import ErrorCode, InternalAdapterError

MODEL = "@cf/black-forest-labs/flux-2-klein-4b"
MAX_RESPONSE_BYTES = 10_000_000
MEDIA_TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
HTTP_ERRORS = {
    401: ErrorCode.AUTH_FAILED, 403: ErrorCode.AUTH_FAILED,
    408: ErrorCode.UPSTREAM_TIMEOUT, 504: ErrorCode.UPSTREAM_TIMEOUT,
    429: ErrorCode.RATE_LIMITED,
}


class ShareImageAsset(TypedDict):
    image_url: str
    content_type: str
    size_bytes: int


def _image_suffix(image: bytes) -> str:
    if image.startswith(b"\xff\xd8\xff") and image.endswith(b"\xff\xd9"):
        return ".jpg"
    if image.startswith(b"\x89PNG\r\n\x1a\n") and image.endswith(b"IEND\xaeB`\x82"):
        return ".png"
    if image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        return ".webp"
    raise ValueError("未识别到支持的图片格式")


class CloudflareShareImages:
    def __init__(self, config: Settings) -> None:
        self.config = config

    @property
    def configured(self) -> bool:
        return bool(self.config.cloudflare_account_id and self.config.cloudflare_api_token)

    @property
    def directory(self) -> Path:
        return self.config.share_image_directory

    async def generate(self, prompt: str, *, width: int, height: int) -> ShareImageAsset:
        account = self.config.cloudflare_account_id
        token = self.config.cloudflare_api_token.get_secret_value()
        if not re.fullmatch(r"[a-fA-F0-9]{32}", account) or not token:
            raise InternalAdapterError("Cloudflare 生图配置无效", error_code=ErrorCode.AUTH_FAILED)

        url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{MODEL}"
        fields = {
            "prompt": (None, prompt), "width": (None, str(width)), "height": (None, str(height)),
        }
        body = bytearray()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10)) as client:
                async with client.stream(
                    "POST", url, headers={"Authorization": f"Bearer {token}"}, files=fields,
                ) as response:
                    if not response.is_success:
                        code = HTTP_ERRORS.get(response.status_code, ErrorCode.UPSTREAM_ERROR)
                        raise InternalAdapterError(
                            f"Cloudflare 生图返回 HTTP {response.status_code}",
                            error_code=code,
                        )
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise ValueError("Cloudflare 响应过大")
            payload = json.loads(body)
            encoded = payload["result"]["image"]
            if payload.get("success") is not True or not isinstance(encoded, str):
                raise ValueError("未收到生成图像")
            image = b64decode(encoded, validate=True)
            suffix = _image_suffix(image)
        except httpx.TimeoutException:
            raise InternalAdapterError(
                "Cloudflare 生图超时", error_code=ErrorCode.UPSTREAM_TIMEOUT,
            ) from None
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            raise InternalAdapterError(
                "Cloudflare 生图没有返回可用图片", error_code=ErrorCode.UPSTREAM_ERROR,
            ) from None

        image_id = uuid4()
        try:
            await asyncio.to_thread(self._save, image_id, suffix, image)
        except OSError:
            raise InternalAdapterError("生成的分享图保存失败") from None
        return {
            "image_url": f"/api/v1/seo-agent/chat/images/{image_id}",
            "content_type": MEDIA_TYPES[suffix],
            "size_bytes": len(image),
        }

    def _save(self, image_id: UUID, suffix: str, image: bytes) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, name = tempfile.mkstemp(dir=self.directory)
        pending = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(image)
            pending.replace(self.directory / f"{image_id.hex}{suffix}")
        finally:
            pending.unlink(missing_ok=True)

    def get_file(self, image_id: UUID) -> tuple[Path, str] | None:
        for suffix, media_type in MEDIA_TYPES.items():
            path = self.directory / f"{image_id.hex}{suffix}"
            if path.is_file():
                return path, media_type
        return None


cloudflare_share_images = CloudflareShareImages(settings)
