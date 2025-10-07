import time
from typing import Dict, List
from celery import states
from celery import chord
from .celery_app import celery_app

@celery_app.task(bind=True)
def heavy_compute(self, n: int = 1_000_000, steps: int = 10):
    """
    Example CPU task with progress reporting.
    Update this with your real compute.
    """
    try:
        acc = 0
        per_step = max(1, n // steps)
        start = time.perf_counter()
        for i in range(steps):
            # Simulate work
            inner = 0
            for _ in range(per_step):
                inner += 1
            acc += inner

            self.update_state(
                state="PROGRESS",
                meta={
                    "step": i + 1,
                    "steps": steps,
                    "progress": (i + 1) / steps,
                    "latency_sec": time.perf_counter() - start,
                },
            )
            time.sleep(0.05)  # simulate chunk latency

        return {"result": acc, "latency_sec": time.perf_counter() - start}
    except Exception as e:
        self.update_state(state=states.FAILURE, meta={"exc": str(e)})
        raise

# --- PI COMPUTATION (Chudnovsky) --------------------------------------------
from mpmath import mp

def _terms_needed(digits: int) -> int:
    # ~14.181647462 digits per term for Chudnovsky
    return max(1, int(mp.ceil(digits / mp.mpf("14.181647462"))))

def _chudnovsky_term(k: int) -> mp.mpf:
    # a_k = (-1)^k * (6k)! * (13591409 + 545140134 k) / ((3k)! * (k!)^3 * 640320^(3k))
    # Using mp factorials is fine; gmpy2 (if installed) will make it much faster.
    sixk = 6 * k
    threek = 3 * k
    num = mp.factorial(sixk) * (13591409 + 545140134 * k) * ((-1) ** k)
    den = mp.factorial(threek) * (mp.factorial(k) ** 3) * (mp.mpf(640320) ** (3 * k))
    return num / den

@celery_app.task(bind=True)
def pi_chunk(self, start_k: int, end_k: int, digits: int):
    """
    Compute partial sum of the Chudnovsky series for k in [start_k, end_k).
    Returns a stringified mp.mpf to avoid float rounding on serialization.
    """
    # Guard digits for internal ops
    mp.mp.dps = int(digits) + 30
    s = mp.mpf("0")
    total = max(0, end_k - start_k)
    for idx, k in enumerate(range(start_k, end_k), 1):
        s += _chudnovsky_term(k)
        # progress updates every ~1% (at least every 100 iters)
        if total > 0 and (idx % max(100, total // 100) == 0 or idx == total):
            self.update_state(
                state="PROGRESS",
                meta={
                    "range": [start_k, end_k],
                    "done": idx,
                    "total": total,
                    "progress": idx / total
                }
            )
    return str(s)

@celery_app.task
def pi_reduce(partials: list[str], digits: int):
    """
    Reduce partial sums to π using:
    π = (426880 * sqrt(10005)) / sum_k a_k
    """
    mp.mp.dps = int(digits) + 10
    # Sum partials with high precision
    S = mp.fsum([mp.mpf(p) for p in partials])
    pi_val = (mp.mpf(426880) * mp.sqrt(mp.mpf(10005))) / S
    # Format: exact number of digits as string (no commas, etc.)
    # mp.nstr rounds; to truncate, use string slicing on fixed-point.
    # We’ll output a normalized string with requested digits after decimal.
    mp.mp.dps = int(digits) + 2
    pi_str = mp.nstr(pi_val, n=digits + 2)  # includes "3." + digits
    return {
        "digits": digits,
        "pi": pi_str,
    }

def _split_ranges(total_terms: int, shards: int) -> list[tuple[int, int]]:
    shards = max(1, shards)
    base = total_terms // shards
    rem = total_terms % shards
    r = []
    start = 0
    for i in range(shards):
        end = start + base + (1 if i < rem else 0)
        if start != end:
            r.append((start, end))
        start = end
    return r


@celery_app.task
def reduce_sum(results: List[Dict]):
    """
    Reducer for sharded jobs: sum up 'result' fields and keep max latency.
    """
    total = 0
    max_latency = 0.0
    for r in results:
        if isinstance(r, dict):
            total += int(r.get("result", 0))
            max_latency = max(max_latency, float(r.get("latency_sec", 0.0)))
    return {"result": total, "latency_sec": max_latency}

def shard_and_route(n: int, shards: int, queues: List[str], steps: int = 10):
    """
    Create a chord (fan-out/fan-in) that splits N into 'shards' and routes each
    shard to the given queues round-robin.
    Returns an AsyncResult for the chord body (the reduce_sum result).
    """
    shards = max(1, shards)
    per = n // shards
    remainder = n - per * shards

    sigs = []
    for i in range(shards):
        size = per + (1 if i < remainder else 0)
        queue = queues[i % len(queues)]
        sigs.append(heavy_compute.s(n=size, steps=steps).set(queue=queue))

    return chord(sigs)(reduce_sum.s())
