from __future__ import annotations

import asyncio
from typing import Annotated
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import StringConstraints
from sqlalchemy.exc import SQLAlchemyError

from src.agent.workspace.documents import (
    LIBRARY_SCOPE,
    MAX_FILE_BYTES,
    WorkspaceDocumentStore,
    conversation_scope,
    parse_document,
    workspace_owner,
)
from src.api.routes.workspace.request_body import read_limited_body

router = APIRouter(prefix="/workspace/documents", tags=["Workspace Documents"])
_ACCESS_HEADER = "X-Workspace-Key"
_ACCESS_PATTERN = r"^[A-Za-z0-9_-]{32,128}$"
WorkspaceKey = Annotated[str, Header(alias=_ACCESS_HEADER, pattern=_ACCESS_PATTERN)]
OptionalWorkspaceKey = Annotated[str | None, Header(alias=_ACCESS_HEADER, pattern=_ACCESS_PATTERN)]
DocumentId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
ConversationId = Annotated[str | None, Query(min_length=1, max_length=256)]
_NO_CACHE = {"Cache-Control": "private, no-store"}


def document_store() -> WorkspaceDocumentStore:
    from src.api.routes.workspace import conversations

    database = conversations.seo_chat_harness_store.database
    if not database.started:
        raise HTTPException(503, "资料存储暂不可用。")
    return WorkspaceDocumentStore(database)


DocumentStore = Annotated[WorkspaceDocumentStore, Depends(document_store)]


@router.get("")
async def list_library(
    key: WorkspaceKey, store: DocumentStore,
    q: Annotated[str, Query(max_length=160)] = "",
    include_references: bool = True,
) -> JSONResponse:
    try:
        result = await store.library(
            workspace_owner(key), q.strip(), include_references=include_references,
        )
    except SQLAlchemyError:
        raise HTTPException(503, "暂时无法读取知识库。") from None
    return JSONResponse(result, headers=_NO_CACHE)


@router.post("", status_code=201)
async def upload_document(
    request: Request, key: WorkspaceKey, store: DocumentStore,
    filename: Annotated[str, Header(alias="X-Workspace-File", min_length=1, max_length=2400)],
    conversation_id: ConversationId = None,
) -> JSONResponse:
    raw = await read_limited_body(request, MAX_FILE_BYTES, "单个文件最多 1 MB。")
    try:
        decoded = unquote(filename, errors="strict")
        name, sections = await asyncio.to_thread(parse_document, decoded, raw)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    scope = conversation_scope(conversation_id) if conversation_id else LIBRARY_SCOPE
    try:
        result = await store.add(workspace_owner(key), scope, name, raw, sections)
    except SQLAlchemyError:
        raise HTTPException(503, "暂时无法保存资料。") from None
    return JSONResponse(result, status_code=201, headers=_NO_CACHE)


@router.get("/{document_id}/download")
async def download_document(
    document_id: DocumentId, key: WorkspaceKey, store: DocumentStore,
) -> Response:
    try:
        name, raw = await store.download(workspace_owner(key), document_id)
    except KeyError:
        raise HTTPException(404, "资料不存在或无法访问。") from None
    except SQLAlchemyError:
        raise HTTPException(503, "暂时无法下载资料。") from None
    return Response(raw, media_type="application/octet-stream", headers={
        **_NO_CACHE, "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name, safe='')}",
        "X-Content-Type-Options": "nosniff",
    })


@router.delete("/{document_id}", status_code=204)
async def remove_library_document(
    document_id: DocumentId, key: WorkspaceKey, store: DocumentStore,
) -> Response:
    try:
        await store.remove(workspace_owner(key), document_id)
    except KeyError:
        raise HTTPException(404, "知识库资料不存在或无法访问。") from None
    except SQLAlchemyError:
        raise HTTPException(503, "暂时无法删除资料。") from None
    return Response(status_code=204, headers=_NO_CACHE)
