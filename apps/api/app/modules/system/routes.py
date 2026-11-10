from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.core.metrics import http_metrics
from app.core.security import require_operator
from app.integrations.queue import get_task_queue
from app.modules.system.health import readiness

router = APIRouter()


@router.get("/health/live", tags=["health"])
def live() -> dict[str, Any]:
    return {"data": {"status": "ok", "service": "knowledge-os-api", "version": "0.1.0"}}


@router.get("/health/ready", tags=["health"])
async def ready() -> JSONResponse:
    result = await readiness()
    return JSONResponse({"data": result}, status_code=200 if result["status"] == "ready" else 503)


operator = APIRouter(
    prefix="/api/v1/system", tags=["system"], dependencies=[Depends(require_operator)]
)


@operator.get("/metrics")
def metrics() -> dict[str, Any]:
    """Operator-only bounded aggregates; no tenant data or external exporter."""
    return {"data": http_metrics.snapshot()}


@operator.post("/smoke-jobs", status_code=202)
def submit_smoke_job(queue: Annotated[Any, Depends(get_task_queue)]) -> dict[str, Any]:
    """Only an infrastructure ping. No user payloads or tenant data accepted."""
    task_id = f"smoke-{uuid4()}"
    try:
        queue.send_task("knowledge_os.smoke", task_id=task_id)
    except Exception as exc:
        raise HTTPException(503, detail={"code": "QUEUE_UNAVAILABLE"}) from exc
    return {"data": {"id": task_id, "status": "submitted"}}


@operator.get("/smoke-jobs/{job_id}")
def smoke_job(job_id: str, queue: Annotated[Any, Depends(get_task_queue)]) -> dict[str, Any]:
    try:
        UUID(job_id.removeprefix("smoke-"))
        if not job_id.startswith("smoke-"):
            raise ValueError
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "INVALID_SMOKE_JOB_ID"}) from exc
    try:
        result = queue.AsyncResult(job_id)
        status = result.state
        # Never disclose arbitrary task results or exception details.
        return {"data": {"id": job_id, "status": status}}
    except Exception as exc:
        raise HTTPException(503, detail={"code": "QUEUE_UNAVAILABLE"}) from exc


router.include_router(operator)
