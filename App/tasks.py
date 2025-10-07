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
