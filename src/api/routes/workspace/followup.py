from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from src.api.routes.workspace.request_body import read_limited_body
from src.followup_agent.materials import MAX_FILE_BYTES, RecordEdit, SubjectCode, read_material
from src.followup_agent.store import (
    DEMO_SUBJECT,
    CaseConflictError,
    FollowupCaseStore,
    demo_supplement,
)

router = APIRouter(prefix="/runs/{run_id}/followup")


def _store() -> FollowupCaseStore:
    from src.api.routes.workspace import conversations

    database = conversations.seo_chat_harness_store.database
    if not database.started:
        raise HTTPException(503, "复诊资料存储未启动。")
    return FollowupCaseStore(database)


CaseKey = Annotated[str, Header(alias="X-Followup-Key", min_length=1, max_length=128)]
CaseStore = Annotated[FollowupCaseStore, Depends(_store)]


async def _case_result(action: Awaitable[dict]) -> dict:
    try:
        return await action
    except KeyError:
        raise HTTPException(404, "复诊任务或记录不存在。") from None
    except CaseConflictError as exc:
        raise HTTPException(409, str(exc)) from None
    except SQLAlchemyError:
        raise HTTPException(503, "无法读写复诊资料，请稍后重试。") from None


def _response(result: dict) -> JSONResponse:
    return JSONResponse(result, headers={"Cache-Control": "private, no-store"})


@router.get("")
async def get_case(run_id: str, key: CaseKey, store: CaseStore) -> JSONResponse:
    return _response(await _case_result(store.view(run_id, key)))


@router.post("/materials")
async def add_material(
    run_id: str, request: Request, key: CaseKey, store: CaseStore,
    subject_id: Annotated[SubjectCode, Header(alias="X-Followup-Subject")],
    filename: Annotated[str, Header(alias="X-Followup-File", min_length=1, max_length=1024)],
) -> JSONResponse:
    await _case_result(store.view(run_id, key))
    content = await read_limited_body(request, MAX_FILE_BYTES, "单份资料大小上限为 192 KB。")
    if not content:
        raise HTTPException(422, "请提供有内容的资料文件。")
    material = await asyncio.to_thread(read_material, unquote(filename), content, subject_id)
    return _response(await _case_result(store.add(run_id, key, subject_id, material)))


@router.post("/demo-materials")
async def load_demo(run_id: str, key: CaseKey, store: CaseStore) -> JSONResponse:
    """Load the synthetic visit materials into this empty follow-up run."""
    return _response(await _case_result(store.load_demo(run_id, key)))


@router.post("/demo-supplement", deprecated=True)
async def supplement_demo(run_id: str, key: CaseKey, store: CaseStore) -> JSONResponse:
    """Keep the original demo supplement available for older clients."""
    case = await _case_result(store.view(run_id, key))
    if not case["demo"]:
        raise HTTPException(409, "补充样例用于演示任务。")
    return _response(await _case_result(store.add(run_id, key, DEMO_SUBJECT, demo_supplement())))


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)
    fields: RecordEdit


@router.patch("/records/{record_id}", deprecated=True)
async def edit_record(
    run_id: str, record_id: str, body: Correction, key: CaseKey, store: CaseStore,
) -> JSONResponse:
    """Support legacy clients and preserve the imported original of an edited row."""
    return _response(await _case_result(
        store.edit(run_id, key, record_id, body.revision, body.fields),
    ))
