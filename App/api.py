# app/api.py
import json
from typing import List, Optional
import os, signal as pysignal
import redis
from uuid import uuid4
from fastapi import FastAPI, HTTPException, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from celery.result import AsyncResult
from fastapi import Header
from .celery_app import celery_app
from .config import API_AUTH_TOKEN, RESULT_BACKEND, NAMESPACE
from .tasks import _terms_needed, _split_ranges, pi_chunk, pi_reduce
import sys
import json
from pydantic import BaseModel, Field, conint
from fastapi import Query
import time
from celery import chord
# make sure you import your tasks the same way you already do elsewhere:
from App.tasks import pi_chunk, pi_reduce   # or: from app.tasks import ...

def now_ms() -> int:
    return int(time.time() * 1000)

class SubmitPiBody(BaseModel):
    digits: conint(ge=10, le=1_200_000) = Field(..., description="Number of digits to compute")
    target_queues: list[str] = Field(..., min_items=1, description="Queues/devices to use")
    shards: int = Field(0, description="0 = auto")
    terms: int | None = Field(None, description="Override terms (advanced)")
try:
    # 0 = no limit. Or set to something high like 1_000_000 if you prefer.
    sys.set_int_max_str_digits(0)
except AttributeError:
    # Python < 3.11 doesn't have this
    pass
app = FastAPI(title="Distributed Compute API")
templates = Jinja2Templates(directory="app/templates")
R = redis.from_url(RESULT_BACKEND)


# --- Store-and-forward WebSocket manager ---
class _ConnectionManager:
    def __init__(self):
        self._active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._active:
            self._active.remove(ws)

    async def broadcast(self, message: dict) -> None:
        import json as _json
        text = _json.dumps(message)
        dead = []
        for ws in self._active:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

_ws_manager = _ConnectionManager()
# --- end WebSocket manager ---


import json
from fastapi import Query

@app.get("/metrics/{task_or_stream_id}")
def metrics(task_or_stream_id: str, limit: int = Query(500, ge=1, le=5000)):
    # Resolve parent task id -> stream id (if needed)
    sid = R.get(f"{NAMESPACE}:pi:map:{task_or_stream_id}")
    stream_id = sid.decode() if sid else task_or_stream_id

    key = f"{NAMESPACE}:log:{stream_id}"

    # Read last N events from LIST (newest at end); returns bytes
    raw = R.lrange(key, -limit, -1)

    events = []
    total_bytes = 0
    by_shard = {}

    for item in raw:
        try:
            ev = json.loads(item.decode() if isinstance(item, (bytes, bytearray)) else item)
        except Exception:
            continue

        # Normalize numeric fields
        for f in ("ts", "idx", "bytes", "crc32", "exec_ms", "dispatch_ms", "done", "total"):
            if f in ev:
                try:
                    ev[f] = int(ev[f])
                except Exception:
                    pass

        events.append(ev)

        b = ev.get("bytes")
        if isinstance(b, int):
            total_bytes += b
        else:
            try:
                total_bytes += int(b)
            except Exception:
                pass

        if ev.get("phase") == "chunk" and "idx" in ev:
            idx = ev.get("idx")
            by_shard.setdefault(idx, {}).update(ev)

    # Aggregates
    exec_vals = [v.get("exec_ms") for v in by_shard.values() if isinstance(v.get("exec_ms"), int)]
    disp_vals = [v.get("dispatch_ms") for v in by_shard.values() if isinstance(v.get("dispatch_ms"), int)]
    agg = {
        "total_events": len(events),
        "total_bytes": total_bytes,
        "avg_exec_ms_chunk": round(sum(exec_vals) / len(exec_vals)) if exec_vals else 0,
        "avg_dispatch_ms": round(sum(disp_vals) / len(disp_vals)) if disp_vals else 0,
    }

    return {"stream_id": stream_id, "events": events, "agg": agg}



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
def submit_pi(body: SubmitPiBody):
    # read validated values from the request
    digits = int(body.digits)
    target_queues = [q.strip() for q in body.target_queues if q.strip()]
    if not target_queues:
        raise HTTPException(status_code=400, detail="target_queues must not be empty")

    # figure out terms
    terms = int(body.terms) if (body.terms and body.terms > 0) else _terms_needed(digits)

    # decide shards; never more shards than terms; at least 1
    shards = int(body.shards or 0)
    if shards <= 0:
        shards = min(terms, max(1, len(target_queues)))
    shards = max(1, min(shards, terms))

    # split work into non-empty ranges
    ranges = _split_ranges(terms, shards)  # -> list[(start_k, end_k)]

    # stream id for live metrics/results
    stream_id = uuid4().hex

    # store meta (compat: write as individual fields)
    t_submit = now_ms()
    meta_key = f"{NAMESPACE}:pi:{stream_id}:meta"
    pipe = R.pipeline()
    pipe.hset(meta_key, "t_submit_ms", t_submit)
    pipe.hset(meta_key, "digits", digits)
    pipe.hset(meta_key, "terms", terms)
    pipe.hset(meta_key, "shards", shards)
    pipe.hset(meta_key, "queues_json", json.dumps(target_queues))
    pipe.hset(meta_key, "ranges_json", json.dumps(ranges))
    pipe.execute()

    # build chunk signatures and pass queue name as an arg the task expects
    sigs = []
    assignments = []
    for i, (start_k, end_k) in enumerate(ranges):
        q = target_queues[i % len(target_queues)]
        # no queue_name arg here now
        sigs.append(pi_chunk.s(start_k, end_k, digits, stream_id, i).set(queue=q))
        assignments.append({"idx": i, "start": start_k, "end": end_k, "queue": q})

    reducer_queue = target_queues[0]
    # reducer also without extra arg
    callback = pi_reduce.s(digits, stream_id).set(queue=reducer_queue)
    # dispatch chord -> returns AsyncResult for the reducer (parent)
    result = chord(sigs)(callback)
    parent_id = result.id
    meta_key = f"{NAMESPACE}:pi:{stream_id}:meta"
    pipe = R.pipeline()
    pipe.set(f"{NAMESPACE}:pi:map:{parent_id}", stream_id)  # mapping used by /metrics
    pipe.hset(meta_key, "parent_id", parent_id)
    pipe.execute()

    return {
        "task_id": parent_id,
        "stream_id": stream_id,
        "digits": digits,
        "shards": shards,
        "assignments": assignments,
    }



@app.get("/status/{task_id}")
def status(task_id: str):
    r = AsyncResult(task_id, app=celery_app)
    meta = r.info if isinstance(r.info, dict) else {}
    return {"state": r.state, "meta": meta}
class PiJobIn(BaseModel):
    digits: int = Field(100000, ge=10, le=1_200_000)
    target_queues: List[str] = Field(default_factory=list)
    shards: int = Field(0, ge=0)
    terms: Optional[int] = Field(default=None, ge=1)

@app.get("/metrics_debug/{task_or_stream_id}")
def metrics_debug(task_or_stream_id: str):
    sid = R.get(f"{NAMESPACE}:pi:map:{task_or_stream_id}")
    stream_id = sid.decode() if sid else task_or_stream_id
    key = f"{NAMESPACE}:log:{stream_id}"
    return {
        "stream_id": stream_id,
        "exists": bool(R.exists(key)),
        "llen": int(R.llen(key) or 0)
    }


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
    # resolve stream_id
    stream_id = R.get(f"{NAMESPACE}:pi:map:{task_or_stream_id}")
    stream_id = stream_id.decode() if stream_id else task_or_stream_id

    meta = R.hgetall(f"{NAMESPACE}:pi:{stream_id}:meta")
    total_shards = int(meta.get(b"total_shards", b"1").decode()) if meta else 1
    digits = int(meta.get(b"digits", b"1000").decode()) if meta else 1000

    # ✅ If final is present, return it immediately (even if partials missing)
    final = R.get(f"{NAMESPACE}:pi:{stream_id}:final")
    if final:
        pi_str = final.decode()
        return {
            "available": True,
            "done": total_shards,
            "total": total_shards,
            "digits": digits,
            "pi_head": pi_str[:max_chars],
        }

    # Otherwise try to assemble from partials
    partials = R.hgetall(f"{NAMESPACE}:pi:{stream_id}:partials")
    done = R.scard(f"{NAMESPACE}:pi:{stream_id}:done")
    if not partials:
        return {"available": False, "done": int(done), "total": total_shards}

    from mpmath import mp as mpm
    mpm.dps = digits + 10
    S = mpm.fsum([mpm.mpf(v.decode()) for v in partials.values()])
    pi_val = (mpm.mpf(426880) * mpm.sqrt(mpm.mpf(10005))) / S
    mpm.dps = min(digits + 2, max_chars)
    pi_str = mpm.nstr(pi_val, n=min(digits + 2, max_chars))

    return {
        "available": True,
        "done": int(done),
        "total": total_shards,
        "digits": digits,
        "pi_head": pi_str,
    }

def _normalize_signal(user_signal: str | None) -> str:
    if os.name == "nt":          # Windows: no SIGKILL
        return "SIGTERM"
    if not user_signal:
        return "SIGKILL"
    name = user_signal.upper()
    return name if hasattr(pysignal, name) else "SIGKILL"

@app.delete("/task/{task_id}")
def cancel_task(task_id: str, terminate: bool = True, signal: str | None = None):
    signame = _normalize_signal(signal)
    celery_app.control.revoke(task_id, terminate=terminate, signal=signame)
    return {"ok": True, "revoked": [task_id], "signal": signame}

@app.delete("/task_tree/{task_id}")
def cancel_task_tree(task_id: str, terminate: bool = True, signal: str | None = None, cleanup: bool = True):
    signame = _normalize_signal(signal)
    r = AsyncResult(task_id, app=celery_app)
    revoked = [task_id]
    celery_app.control.revoke(task_id, terminate=terminate, signal=signame)
    if r.children:
        for c in r.children:
            celery_app.control.revoke(c.id, terminate=terminate, signal=signame)
            revoked.append(c.id)
    # (cleanup keys … unchanged)
    return {"ok": True, "revoked": revoked, "signal": signame, "cleaned": cleanup}


# --- Store-and-forward endpoints ---
class SyncPayload(BaseModel):
    worker_id: str
    results: list


@app.post("/sync")
async def sync_results(payload: SyncPayload):
    message = {
        "worker_id": payload.worker_id,
        "count": len(payload.results),
        "results": payload.results,
    }
    await _ws_manager.broadcast(message)
    return {"ok": True, "received": len(payload.results)}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await _ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        _ws_manager.disconnect(websocket)
# --- end store-and-forward endpoints ---