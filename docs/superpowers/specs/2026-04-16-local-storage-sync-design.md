# Design: Local Device Storage with Central Sync (Store-and-Forward)

**Date:** 2026-04-16  
**Project:** ScalableCompute  
**Subject:** Data Storage Networking  
**Status:** Approved

---

## Overview

Adapt the existing FastAPI + Celery + Redis distributed Pi-digit calculator to demonstrate **store-and-forward networking**: each Celery worker stores computed results locally in a JSON file, and once a configurable threshold of results accumulates, automatically forwards them to the main FastAPI server, which broadcasts them live to the dashboard.

---

## Architecture

```
[Worker 1]  compute Pi → results_worker1.json  ─┐
[Worker 2]  compute Pi → results_worker2.json  ─┤─ HTTP POST /sync ──► [FastAPI] ──► WebSocket ──► Dashboard
[Worker N]  compute Pi → results_workerN.json  ─┘   (when N results)
```

Workers operate independently. Redis and Celery continue to function as before for task dispatch — this feature is a pure addition on top of the existing stack.

---

## Components & Changes

| File | Change |
|---|---|
| `App/config.py` | Add `SYNC_THRESHOLD = 5` and `MAIN_SERVER_URL = "http://localhost:8000"` |
| `App/tasks.py` | After each Pi computation, append result to `results_<worker_id>.json`; when `len(pending) >= SYNC_THRESHOLD`, POST to `/sync` and clear the local file |
| `App/api.py` | Add `POST /sync` endpoint; add `GET /ws` WebSocket endpoint that broadcasts incoming sync payloads to connected clients |
| `App/templates/index.html` | Add a live results panel that opens a WebSocket connection and renders incoming synced results |

No new files are required. All changes slot into the existing structure.

---

## Data Formats

### Local JSON file — `results_<worker_id>.json`

Written to the worker's working directory. Reset to `[]` after each successful sync.

```json
[
  {
    "worker_id": "worker_1",
    "digits": 100,
    "result": "3.14159...",
    "computed_at": "2026-04-16T10:00:00",
    "synced": false
  }
]
```

### Sync POST payload — `POST /sync`

```json
{
  "worker_id": "worker_1",
  "results": [
    {
      "worker_id": "worker_1",
      "digits": 100,
      "result": "3.14159...",
      "computed_at": "2026-04-16T10:00:00",
      "synced": true
    }
  ]
}
```

### WebSocket broadcast message — `GET /ws`

Sent to all connected dashboard clients after each successful sync.

```json
{
  "worker_id": "worker_1",
  "count": 5,
  "results": [ ... ]
}
```

---

## Data Flow

1. Task dispatched via existing Celery mechanism
2. Worker computes Pi digits
3. Worker appends result to `results_<worker_id>.json` (local file)
4. Worker checks: `if len(pending_results) >= SYNC_THRESHOLD`
5. If threshold met: POST results to `MAIN_SERVER_URL/sync`, then clear local file
6. If POST fails (server unreachable): local file retains data; retry on next threshold hit
7. FastAPI `/sync` receives payload → broadcasts via WebSocket to dashboard
8. Dashboard WebSocket client renders incoming results live

---

## Error Handling

- **Sync failure (server unreachable):** Local file is NOT cleared. Data accumulates and is retried when the next threshold is reached. This naturally demonstrates store-and-forward resilience.
- **File write failure:** Task logs the error but does not crash — result is reported back to Celery as usual.
- **WebSocket client disconnect:** FastAPI silently drops that connection from its active set; other clients are unaffected.

---

## Configuration

Both values live in `App/config.py` for easy adjustment during demos:

| Setting | Default | Purpose |
|---|---|---|
| `SYNC_THRESHOLD` | `5` | Number of local results before auto-sync |
| `MAIN_SERVER_URL` | `http://localhost:8000` | FastAPI server address workers POST to |

---

## What This Demonstrates (DSN Concepts)

- **Store-and-forward:** Data is held locally on the device until a forwarding condition is met
- **Edge storage:** Each compute node maintains its own independent local store
- **On-demand network use:** The network is only used when the threshold is reached, not for every result
- **Fault tolerance:** If the central server is down, no data is lost — it stays on the device
