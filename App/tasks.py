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
    s = mpm.mpf("0")
    ...
    return str(s)

def _split_ranges(total_terms: int, shards: int) -> List[Tuple[int, int]]:
    shards = max(1, shards)
    base = total_terms // shards
    rem = total_terms % shards
    ranges: List[Tuple[int, int]] = []
    start = 0
    for i in range(shards):
        end = start + base + (i < rem)
        if start < end:
            ranges.append((start, end))
        start = end
    return ranges

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

