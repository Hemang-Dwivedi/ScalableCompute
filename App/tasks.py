# app/tasks.py
import time
from typing import List, Tuple
from celery import states
# at top of tasks.py
from mpmath import mp as mpm  # <-- use a unique alias to avoid collisions

import redis

from .celery_app import celery_app
from .config import RESULT_BACKEND, NAMESPACE

# Single Redis client (uses your RESULT_BACKEND URL)
R = redis.from_url(RESULT_BACKEND)

# ---------- PI COMPUTATION (Chudnovsky) ----------

def _terms_needed(digits: int) -> int:
    mpm.dps = 50
    return max(1, int(mpm.ceil(digits / mpm.mpf("14.181647462"))))

def _chudnovsky_term(k: int) -> mpm.mpf:
    sixk = 6 * k
    threek = 3 * k
    num = mpm.factorial(sixk) * (13591409 + 545140134 * k) * ((-1) ** k)
    den = mpm.factorial(threek) * (mpm.factorial(k) ** 3) * (mpm.mpf(640320) ** (3 * k))
    return num / den

@celery_app.task(bind=True)
def pi_chunk(self, start_k: int, end_k: int, digits: int, stream_id: str, idx: int):
    mpm.dps = int(digits) + 30
    total = int(end_k) - int(start_k)
    if total <= 0:
        # This should never happen with the safe splitter; make it obvious if it does.
        raise ValueError(f"empty range shard idx={idx} [{start_k},{end_k})")

    s = mpm.mpf("0")
    for done, k in enumerate(range(start_k, end_k), 1):
        s += _chudnovsky_term(k)
        if (done % max(100, total // 100) == 0) or done == total:
            self.update_state(state="PROGRESS",
                meta={"range": [start_k, end_k], "done": done, "total": total, "progress": done / total})

    # write partial for streaming and return for the chord
    R.hset(f"{NAMESPACE}:pi:{stream_id}:partials", str(idx), str(s))
    R.sadd(f"{NAMESPACE}:pi:{stream_id}:done", str(idx))
    return str(s)


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


@celery_app.task(bind=True)
@celery_app.task
def pi_reduce(partials: list[str], digits: int, stream_id: str):
    mpm.dps = int(digits) + 10
    S = mpm.fsum([mpm.mpf(p) for p in partials])
    pi_val = (mpm.mpf(426880) * mpm.sqrt(mpm.mpf(10005))) / S

    mpm.dps = int(digits) + 2
    pi_str = mpm.nstr(pi_val, n=digits + 2)
    R.set(f"{NAMESPACE}:pi:{stream_id}:final", pi_str)
    return {"digits": digits, "pi": pi_str}

