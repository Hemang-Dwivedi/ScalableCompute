# app/tasks.py
import time
from typing import List, Tuple
from celery import states
# at top of tasks.py
from mpmath import mp as mpm  # <-- use a unique alias to avoid collisions
import redis
import json
from .celery_app import celery_app
from .config import RESULT_BACKEND, NAMESPACE
import time, os, zlib, socket
from typing import Dict, Any

def now_ms() -> int:
    return int(time.time() * 1000)

def _queue_name_from_request(task_self):
    di = getattr(task_self.request, "delivery_info", None) or {}
    # common fields: routing_key is usually the queue name we used in .set(queue=...)
    return di.get("routing_key") or di.get("queue") or di.get("exchange") or "unknown"

def crc32_str(s: str) -> int:
    return zlib.crc32(s.encode("utf-8")) & 0xffffffff


def log_event(R, ns: str, stream_id: str, kind: str, fields: Dict[str, Any]):
    # Coerce simple types, stamp kind/ts
    payload = {k: (int(v) if isinstance(v, bool) else v) for k, v in fields.items()}
    payload["kind"] = kind
    payload["ts"] = now_ms()

    key = f"{ns}:log:{stream_id}"
    line = json.dumps(payload, separators=(",", ":"))  # compact JSON line

    pipe = R.pipeline()
    pipe.rpush(key, line)          # append newest event
    pipe.ltrim(key, -5000, -1)     # keep only last 5000 events (ring buffer)
    pipe.execute()


# Single Redis client (uses your RESULT_BACKEND URL)
R = redis.from_url(RESULT_BACKEND)

# ---------- PI COMPUTATION (Chudnovsky) ----------
import sys
try:
    # 0 = no limit. Or set to something high like 1_000_000 if you prefer.
    sys.set_int_max_str_digits(0)
except AttributeError:
    # Python < 3.11 doesn't have this
    pass
def _terms_needed(digits: int) -> int:
    mpm.dps = 50
    return max(1, int(mpm.ceil(digits / mpm.mpf("14.181647462"))))

def _chudnovsky_term(k: int) -> mpm.mpf:
    sixk = 6 * k
    threek = 3 * k
    num = mpm.factorial(sixk) * (13591409 + 545140134 * k) * ((-1) ** k)
    den = mpm.factorial(threek) * (mpm.factorial(k) ** 3) * (mpm.mpf(640320) ** (3 * k))
    return num / den

@celery_app.task(bind=True, name="App.tasks.pi_chunk")
def pi_chunk(self, start_k: int, end_k: int, digits: int, stream_id: str, idx: int):
    mpm.dps = digits + 30
    total = end_k - start_k
    assert total > 0, f"empty range idx={idx}"
    host = socket.gethostname()
    queue_name = _queue_name_from_request(self)

    # START event
    log_event(R, NAMESPACE, stream_id, "task_start", {
        "phase": "chunk", "idx": idx, "queue": queue_name, "host": host,
        "start_k": start_k, "end_k": end_k
    })

    # compute partial sum
    s = mpm.mpf(0)
    for k in range(start_k, end_k):
        s += _chudnovsky_term(k)

    # write partial
    partial_str = mpm.nstr(s, n=digits + 10)
    pipe = R.pipeline()
    pipe.hset(f"{NAMESPACE}:pi:{stream_id}:partials", str(idx), partial_str)
    pipe.sadd(f"{NAMESPACE}:pi:{stream_id}:done", idx)
    pipe.execute()

    # END event
    log_event(R, NAMESPACE, stream_id, "task_end", {
        "phase": "chunk", "idx": idx, "queue": queue_name, "host": host,
        "bytes": len(partial_str.encode("utf-8")), "crc32": crc32_str(partial_str)
    })

    return partial_str

def _split_ranges(total_terms: int, shards: int) -> list[tuple[int, int]]:
    total_terms = max(1, int(total_terms))
    shards = max(1, int(shards))
    shards = min(shards, total_terms)  # never more shards than terms

    base = total_terms // shards
    rem = total_terms % shards

    out: list[tuple[int, int]] = []
    start = 0
    for i in range(shards):
        take = base + (1 if i < rem else 0)  # >= 1
        end = start + take
        out.append((start, end))             # always end > start
        start = end
    return out


@celery_app.task(bind=True, name="App.tasks.pi_reduce")
def pi_reduce(self, partials: list[str], digits: int, stream_id: str):
    host = socket.gethostname()
    queue_name = _queue_name_from_request(self)
    t0 = now_ms()

    log_event(R, NAMESPACE, stream_id, "task_start", {
        "phase": "reduce", "queue": queue_name, "host": host
    })

    mpm.dps = digits + 10
    S = mpm.fsum([mpm.mpf(p) for p in partials])
    pi_val = (mpm.mpf(426880) * mpm.sqrt(mpm.mpf(10005))) / S
    mpm.dps = digits + 2
    pi_str = mpm.nstr(pi_val, n=digits + 2)
    R.set(f"{NAMESPACE}:pi:{stream_id}:final", pi_str)

    log_event(R, NAMESPACE, stream_id, "task_end", {
        "phase": "reduce", "queue": queue_name, "host": host,
        "exec_ms": now_ms() - t0,
        "bytes": len(pi_str.encode("utf-8")), "crc32": crc32_str(pi_str)
    })

    return {"digits": digits, "pi": pi_str}


