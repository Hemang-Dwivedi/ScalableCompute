from celery import Celery
from .config import BROKER_URL, RESULT_BACKEND

celery_app = Celery(
    "distcomp",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=["App.tasks"],
)

# Sensible defaults for production-ish use
celery_app.conf.update(
    task_track_started=True,
    task_acks_late=True,          # better at-least-once semantics
    worker_prefetch_multiplier=1, # fair scheduling
    result_expires=3600,          # 1 hour
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
)
