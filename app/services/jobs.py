from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from app.schemas import JobState


class JobManager:
    def __init__(self):
        self._jobs: dict[str, JobState] = {}
        self._lock = asyncio.Lock()

    async def create(self) -> JobState:
        job = JobState(id=uuid.uuid4().hex[:12])
        async with self._lock:
            self._jobs[job.id] = job
        return job

    async def get(self, job_id: str) -> JobState | None:
        async with self._lock:
            return self._jobs.get(job_id)

    async def update(self, job_id: str, **values) -> None:
        async with self._lock:
            job = self._jobs[job_id]
            for key, value in values.items():
                setattr(job, key, value)

    async def fail(self, job_id: str, error: Exception) -> None:
        await self.update(
            job_id,
            status="error",
            stage="error",
            message=str(error),
            error=str(error),
            finished_at=datetime.utcnow(),
        )

    async def complete(self, job_id: str, results) -> None:
        await self.update(
            job_id,
            status="done",
            stage="done",
            progress=1.0,
            message=f"Готово: {len(results)} предложений",
            results=results,
            finished_at=datetime.utcnow(),
        )
