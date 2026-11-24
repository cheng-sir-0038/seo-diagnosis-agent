from fastapi import HTTPException, Request


async def read_limited_body(request: Request, max_bytes: int, size_error: str) -> bytes:
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > max_bytes:
            raise HTTPException(413, size_error)
        content.extend(chunk)
    return bytes(content)
