from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import init_db
from app.schemas import HealthResponse, JobState, SearchRequest
from app.services.jobs import JobManager
from app.services.pipeline import DealPipeline

settings = get_settings()
jobs = JobManager()
pipeline = DealPipeline(settings, jobs)
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Smart Parser", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        ai_configured=bool(settings.openrouter_api_key),
        proxy_configured=bool(settings.avito_proxy_urls),
        web_search_provider=settings.web_search_provider,
    )


@app.post("/api/search", response_model=JobState)
async def create_search(request: SearchRequest) -> JobState:
    job = await jobs.create()
    asyncio.create_task(pipeline.run(job.id, request))
    return job


@app.get("/api/jobs/{job_id}", response_model=JobState)
async def get_job(job_id: str) -> JobState:
    job = await jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
