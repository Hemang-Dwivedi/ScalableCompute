# app/api.py
import json
import uuid
from typing import List, Optional

import redis
from celery import chord
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from celery.result import AsyncResult
from mpmath import mp
from fastapi import Header
from .celery_app import celery_app
from .config import API_AUTH_TOKEN, RESULT_BACKEND, NAMESPACE
from .tasks import _terms_needed, _split_ranges, pi_chunk, pi_reduce

app = FastAPI(title="Distributed Compute API")
templates = Jinja2Templates(directory="app/templates")
R = redis.from_url(RESULT_BACKEND)


def auth(api_auth_token: Optional[str] = Header(None, alias="API_AUTH_TOKEN")):
    if API_AUTH_TOKEN and api_auth_token != API_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/workers")
def workers(_: bool = Depends(auth)):
    i = celery_app.control.inspect(timeout=2.0)
    res = {
        "active_queues": i.active_queues() or {},
        "stats": i.stats() or {},
        "ping": i.ping() or {}
    }
    queues = []
    for wname, qinfo in (res["active_queues"] or {}).items():
        for q in qinfo:
            queues.append({"worker": wname, "queue": q.get("name")})
    return {"workers": queues, "raw": res}

class PiJobIn(BaseModel):
    digits: int = Field(100000, ge=10, le=1_200_000)
    target_queues: List[str] = Field(default_factory=list)  # e.g., ["cpu-a","laptop-b"]
    shards: int = Field(0, ge=0)
    terms: Optional[int] = Field(default=None, ge=1)

@app.post("/submit_pi")
def submit_pi(job: PiJobIn, _: bool = Depends(auth)):
    if not job.target_queues:
        raise HTTPException(400, "Provide at least one target queue (device).")

    terms = job.terms or _terms_needed(job.digits)
    shards = job.shards or max(1, len(job.target_queues))
    ranges = _split_ranges(terms, shards)
    if not ranges:
        raise HTTPException(400, "Computed empty ranges; check inputs.")

    # Stream id for Redis aggregation (stable across tasks)
    stream_id = uuid.uuid4().hex

    # Build signatures with stream_id + shard index
    sigs = []
    for i, (start_k, end_k) in enumerate(ranges):
        q = job.target_queues[i % len(job.target_queues)]
        sigs.append(pi_chunk.s(start_k=start_k, end_k=end_k, digits=job.digits, stream_id=stream_id, idx=i).set(queue=q))

    ch = chord(sigs)(pi_reduce.s(digits=job.digits, stream_id=stream_id))

    # Persist mapping so UI can stream by parent task id
    R.set(f"{NAMESPACE}:pi:map:{ch.id}", stream_id)
    meta_key = f"{NAMESPACE}:pi:{stream_id}:meta"
    meta = {
        "digits": str(job.digits),
        "terms": str(terms),
        "total_shards": str(len(ranges)),
        "ranges_json": json.dumps(ranges),
        "queues_json": json.dumps(job.target_queues),
        "parent_id": str(ch.id),
    }
    pipe = R.pipeline()
    for k, v in meta.items():
        pipe.hset(meta_key, k, v)
    pipe.execute()

    return {
        "task_id": ch.id,
        "digits": job.digits,
        "terms": terms,
        "shards": len(ranges),
        "stream_id": stream_id,
        "mapping": [{"range": r, "queue": job.target_queues[i % len(job.target_queues)]}
                    for i, r in enumerate(ranges)]
    }

@app.get("/status/{task_id}")
def status(task_id: str):
    r = AsyncResult(task_id, app=celery_app)
    meta = r.info if isinstance(r.info, dict) else {}
    return {"state": r.state, "meta": meta}

@app.get("/status_tree/{task_id}")
def status_tree(task_id: str):
    r = AsyncResult(task_id, app=celery_app)
    out = {
        "parent": {"id": r.id, "state": r.state, "meta": r.info if isinstance(r.info, dict) else {}},
        "children": []
    }
    try:
        if r.children:
            for c in r.children:
                out["children"].append({
                    "id": c.id,
                    "state": c.state,
                    "meta": c.info if isinstance(c.info, dict) else {}
                })
    except Exception:
        pass
    return out

@app.get("/result/{task_id}")
def result(task_id: str):
    r = AsyncResult(task_id, app=celery_app)
    if not r.ready():
        return {"ready": False, "state": r.state}
    try:
        return {"ready": True, "result": r.get(timeout=2)}
    except Exception as e:
        raise HTTPException(500, f"Task error: {e}")

# --------- NEW: live π streaming endpoint ----------
@app.get("/pi_stream/{task_or_stream_id}")
def pi_stream(task_or_stream_id: str, max_chars: int = 4000):
    """
    Return current best π approximation using whatever partials are done.
    Accepts parent Celery task id OR the internal stream_id.
    """
    # Resolve to stream_id if a parent id was provided
    stream_id = R.get(f"{NAMESPACE}:pi:map:{task_or_stream_id}")
    if stream_id:
        stream_id = stream_id.decode()
    else:
        stream_id = task_or_stream_id  # assume caller passed stream_id

    meta = R.hgetall(f"{NAMESPACE}:pi:{stream_id}:meta")
    if not meta:
        return {"available": False, "message": "No stream meta yet."}

    def _get(name: str, default=None):
        v = meta.get(name.encode())
        return v.decode() if v else default

    digits = int(_get("digits", "1000"))
    total_shards = int(_get("total_shards", "1"))

    partials = R.hgetall(f"{NAMESPACE}:pi:{stream_id}:partials")
    done = R.scard(f"{NAMESPACE}:pi:{stream_id}:done")

    if not partials:
        return {"available": False, "done": int(done), "total": total_shards}

    # Sum what we have and compute π approx
    mp.mp.dps = digits + 10
    S = mp.fsum([mp.mpf(v.decode()) for v in partials.values()])
    pi_val = (mp.mpf(426880) * mp.sqrt(mp.mpf(10005))) / S

    mp.mp.dps = min(digits + 2, max_chars)  # cap output for UI
    pi_str = mp.nstr(pi_val, n=min(digits + 2, max_chars))

    return {
        "available": True,
        "done": int(done),
        "total": total_shards,
        "digits": digits,
        "pi_head": pi_str  # "3." + live digits (approx; final when done==total)
    }

# --------- NEW: cancel / delete tasks ----------
@app.delete("/task/{task_id}")
def cancel_task(task_id: str, terminate: bool = True, signal: str = "SIGKILL"):
    """
    Revoke a single task id (best for leaf tasks). 'terminate' tries to kill running task.
    """
    try:
        celery_app.control.revoke(task_id, terminate=terminate, signal=signal)
        return {"ok": True, "revoked": [task_id]}
    except Exception as e:
        raise HTTPException(500, f"Revoke failed: {e}")

@app.delete("/task_tree/{task_id}")
def cancel_task_tree(task_id: str, terminate: bool = True, signal: str = "SIGKILL", cleanup: bool = True):
    """
    Revoke parent + children and optionally cleanup Redis stream keys.
    """
    r = AsyncResult(task_id, app=celery_app)
    revoked = [task_id]
    try:
        celery_app.control.revoke(task_id, terminate=terminate, signal=signal)
        if r.children:
            for c in r.children:
                celery_app.control.revoke(c.id, terminate=terminate, signal=signal)
                revoked.append(c.id)
    except Exception:
        pass

    if cleanup:
        sid = R.get(f"{NAMESPACE}:pi:map:{task_id}")
        if sid:
            sid = sid.decode()
            R.delete(
                f"{NAMESPACE}:pi:map:{task_id}",
                f"{NAMESPACE}:pi:{sid}:final",
                f"{NAMESPACE}:pi:{sid}:partials",
                f"{NAMESPACE}:pi:{sid}:done",
                f"{NAMESPACE}:pi:{sid}:meta",
            )

    return {"ok": True, "revoked": revoked, "cleaned": cleanup}
