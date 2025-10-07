from fastapi import FastAPI, HTTPException, Depends, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from typing import List, Optional
from celery.result import AsyncResult
from .celery_app import celery_app
from .tasks import heavy_compute, shard_and_route
from .config import API_AUTH_TOKEN

app = FastAPI(title="Distributed Compute API")
templates = Jinja2Templates(directory="app/templates")

def auth(token: Optional[str] = None):
    if API_AUTH_TOKEN:
        if token != API_AUTH_TOKEN:
            raise HTTPException(status_code=401, detail="Unauthorized")
    return True

class JobIn(BaseModel):
    n: int = Field(1_000_000, ge=1)
    steps: int = Field(10, ge=1, le=10_000)
    queue: Optional[str] = None              # send entire job to a specific queue
    target_queues: Optional[List[str]] = None # shard across these queues
    shards: int = Field(default=0, ge=0)      # if >0 and target_queues set => sharded

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/workers")
def workers(_: bool = Depends(auth)):
    """
    Show active workers and the queues they listen on.
    """
    i = celery_app.control.inspect(timeout=2.0)
    res = {
        "active_queues": i.active_queues() or {},
        "stats": i.stats() or {},
        "ping": i.ping() or {}
    }
    # Flatten to provide a friendlier 'queues' list
    queues = []
    for wname, qinfo in (res["active_queues"] or {}).items():
        for q in qinfo:
            queues.append({"worker": wname, "queue": q.get("name")})
    return {"workers": queues, "raw": res}

@app.post("/submit")
def submit(job: JobIn, _: bool = Depends(auth)):
    """
    Submit a job either to a single queue or sharded across multiple queues.
    """
    if job.target_queues and job.shards > 0:
        if len(job.target_queues) == 0:
            raise HTTPException(400, "target_queues must be non-empty")
        ar = shard_and_route(n=job.n, shards=job.shards, queues=job.target_queues, steps=job.steps)
        return {"task_id": ar.id, "mode": "sharded", "shards": job.shards, "target_queues": job.target_queues}
    else:
        # Single routed task (or default Celery routing if queue=None)
        opts = {}
        if job.queue:
            opts["queue"] = job.queue
        ar = heavy_compute.apply_async(kwargs={"n": job.n, "steps": job.steps}, **opts)
        return {"task_id": ar.id, "mode": "single", "queue": job.queue}

@app.get("/status/{task_id}")
def status(task_id: string):
    r = AsyncResult(task_id, app=celery_app)
    meta = r.info if isinstance(r.info, dict) else {}
    return {"state": r.state, "meta": meta}

@app.get("/result/{task_id}")
def result(task_id: str):
    r = AsyncResult(task_id, app=celery_app)
    if not r.ready():
        return {"ready": False, "state": r.state}
    try:
        return {"ready": True, "result": r.get(timeout=1)}
    except Exception as e:
        raise HTTPException(500, f"Task error: {e}")
