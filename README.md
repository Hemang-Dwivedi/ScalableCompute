# Distributed Compute (Celery + FastAPI) — No Docker

This repo runs a FastAPI API (coordinator) and Celery workers on **different machines**.
You can **select workers** by naming queues and routing tasks accordingly. Also includes a tiny dashboard.

## 1) Prereqs

- Python 3.10+
- Redis (or RabbitMQ) reachable by all machines

### Environment (on every machine)
