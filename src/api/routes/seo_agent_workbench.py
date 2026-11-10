from fastapi import APIRouter

from src.seo_agent.runtime import workbench_service

router = APIRouter(prefix="/seo-agent/workbench", tags=["SEO Agent Workbench"])


@router.get("/summary")
async def summary(site_id: str) -> dict:
    return await workbench_service.summary(site_id)
