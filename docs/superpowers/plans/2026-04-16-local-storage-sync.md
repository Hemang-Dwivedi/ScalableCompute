# Local Storage with Store-and-Forward Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add store-and-forward networking to ScalableCompute — each Celery worker saves Pi results to a local JSON file, and auto-syncs to the FastAPI server when the file reaches a configurable threshold, broadcasting live to the dashboard via WebSocket.

**Architecture:** Workers write to `results_<worker_id>.json` after each `pi_reduce` task. When `len(pending) >= SYNC_THRESHOLD`, the worker POSTs to `/sync` and clears the file. FastAPI broadcasts each sync payload over `/ws` to connected dashboard clients.

**Tech Stack:** Python stdlib (`json`, `pathlib`, `urllib.request`), FastAPI WebSockets, pytest + httpx (test only)

---

## Prerequisites — Install test dependencies

These are only needed to run tests, not to run the app:

```bash
pip install pytest httpx
```

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `App/config.py` | Add `SYNC_THRESHOLD`, `MAIN_SERVER_URL`, `LOCAL_STORE_DIR` |
| **Create** | `App/local_store.py` | JSON file helpers: load / append / clear |
| Modify | `App/tasks.py` | Import local_store; call after `pi_reduce` completes |
| Modify | `App/api.py` | Add `ConnectionManager`, `POST /sync`, `GET /ws` |
| Modify | `App/templates/index.html` | Add Live Sync Feed panel with WebSocket JS |
| **Create** | `tests/__init__.py` | Makes tests/ a package |
| **Create** | `tests/test_local_store.py` | Unit tests for local_store.py |
| **Create** | `tests/test_sync_api.py` | Integration tests for /sync and /ws |

All commands assume the working directory is `C:\Users\heman\OneDrive\Documents\GitHub\ScalableCompute`.

---

## Task 1: Add sync config

**Files:**
- Modify: `App/config.py`

- [ ] **Step 1: Append three settings to `App/config.py`**

Open `App/config.py`. It currently ends at line 11 (`API_AUTH_TOKEN = ...`). Add after that line:

```python
# Store-and-forward settings
SYNC_THRESHOLD = int(os.getenv("SYNC_THRESHOLD", "5"))
MAIN_SERVER_URL = os.getenv("MAIN_SERVER_URL", "http://localhost:8000")
LOCAL_STORE_DIR = os.getenv("LOCAL_STORE_DIR", ".")
```

- [ ] **Step 2: Verify import works**

```bash
python -c "from App.config import SYNC_THRESHOLD, MAIN_SERVER_URL, LOCAL_STORE_DIR; print(SYNC_THRESHOLD, MAIN_SERVER_URL, LOCAL_STORE_DIR)"
```
Expected output: `5 http://localhost:8000 .`

- [ ] **Step 3: Commit**

```bash
git add App/config.py
git commit -m "config: add SYNC_THRESHOLD, MAIN_SERVER_URL, LOCAL_STORE_DIR"
```

---

## Task 2: Local storage helpers (TDD)

**Files:**
- Create: `App/local_store.py`
- Create: `tests/__init__.py`
- Create: `tests/test_local_store.py`

- [ ] **Step 1: Create `tests/__init__.py`** (empty file)

- [ ] **Step 2: Write failing tests — create `tests/test_local_store.py`**

```python
# tests/test_local_store.py
import pytest
from App.local_store import load_pending, append_result, clear_results, _store_path


def test_load_pending_returns_empty_list_when_no_file(tmp_path):
    assert load_pending("worker_1", str(tmp_path)) == []


def test_append_result_creates_file_and_returns_list(tmp_path):
    result = {"digits": 100, "pi_head": "3.14159", "computed_at": "2026-04-16T00:00:00", "synced": False}
    pending = append_result("worker_1", result, str(tmp_path))
    assert len(pending) == 1
    assert pending[0]["digits"] == 100


def test_append_result_accumulates_multiple_calls(tmp_path):
    for i in range(3):
        append_result("worker_1", {"digits": i}, str(tmp_path))
    assert len(load_pending("worker_1", str(tmp_path))) == 3


def test_clear_results_resets_file_to_empty_list(tmp_path):
    append_result("worker_1", {"digits": 100}, str(tmp_path))
    clear_results("worker_1", str(tmp_path))
    assert load_pending("worker_1", str(tmp_path)) == []


def test_worker_id_with_at_sign_is_sanitized_in_filename(tmp_path):
    append_result("celery@hostname", {"digits": 50}, str(tmp_path))
    path = _store_path("celery@hostname", str(tmp_path))
    assert path.exists()
    assert "@" not in path.name


def test_corrupted_json_file_returns_empty_list(tmp_path):
    path = _store_path("worker_1", str(tmp_path))
    path.write_text("not json", encoding="utf-8")
    assert load_pending("worker_1", str(tmp_path)) == []
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
python -m pytest tests/test_local_store.py -v
```
Expected: `ModuleNotFoundError: No module named 'App.local_store'`

- [ ] **Step 4: Create `App/local_store.py`**

```python
# App/local_store.py
import json
from pathlib import Path


def _store_path(worker_id: str, storage_dir: str = ".") -> Path:
    safe_id = (
        worker_id
        .replace("@", "_")
        .replace("/", "_")
        .replace("\\", "_")
    )
    return Path(storage_dir) / f"results_{safe_id}.json"


def load_pending(worker_id: str, storage_dir: str = ".") -> list:
    path = _store_path(worker_id, storage_dir)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def append_result(worker_id: str, result: dict, storage_dir: str = ".") -> list:
    pending = load_pending(worker_id, storage_dir)
    pending.append(result)
    _store_path(worker_id, storage_dir).write_text(
        json.dumps(pending, indent=2), encoding="utf-8"
    )
    return pending


def clear_results(worker_id: str, storage_dir: str = ".") -> None:
    _store_path(worker_id, storage_dir).write_text("[]", encoding="utf-8")
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
python -m pytest tests/test_local_store.py -v
```
Expected: `6 passed`

- [ ] **Step 6: Commit**

```bash
git add App/local_store.py tests/__init__.py tests/test_local_store.py
git commit -m "feat: add local JSON storage helpers with tests"
```

---

## Task 3: Integrate local store into pi_reduce

**Files:**
- Modify: `App/tasks.py`

The `pi_reduce` task is at the bottom of `App/tasks.py`. Its final lines are:

```python
    R.set(f"{NAMESPACE}:pi:{stream_id}:final", pi_str)

    log_event(R, NAMESPACE, stream_id, "task_end", {
        "phase": "reduce", "queue": queue_name, "host": host,
        "exec_ms": now_ms() - t0,
        "bytes": len(pi_str.encode("utf-8")), "crc32": crc32_str(pi_str)
    })

    return {"digits": digits, "pi": pi_str}
```

- [ ] **Step 1: Update the import line in `App/tasks.py`**

Find:
```python
from .config import RESULT_BACKEND, NAMESPACE
```
Replace with:
```python
from .config import RESULT_BACKEND, NAMESPACE, SYNC_THRESHOLD, MAIN_SERVER_URL, LOCAL_STORE_DIR
from .local_store import append_result, load_pending, clear_results
```

- [ ] **Step 2: Add `_sync_to_main` helper function**

Add this function immediately before the `@celery_app.task` decorator for `pi_reduce`:

```python
def _sync_to_main(worker_id: str) -> bool:
    """POST pending local results to the main server.
    Returns True if sync succeeded. On failure, keeps local data for retry."""
    import urllib.request
    pending = load_pending(worker_id, LOCAL_STORE_DIR)
    if not pending:
        return True
    payload = json.dumps({
        "worker_id": worker_id,
        "results": [{**r, "synced": True} for r in pending],
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{MAIN_SERVER_URL}/sync",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5)
        clear_results(worker_id, LOCAL_STORE_DIR)
        return True
    except Exception as exc:
        print(f"[local_store] sync failed, keeping {len(pending)} results locally: {exc}")
        return False
```

- [ ] **Step 3: Add store-and-forward block inside `pi_reduce`**

Find the final `return` in `pi_reduce`:
```python
    return {"digits": digits, "pi": pi_str}
```
Replace with:
```python
    # --- store-and-forward ---
    entry = {
        "worker_id": host,
        "digits": digits,
        "pi_head": pi_str[:100],
        "computed_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "synced": False,
    }
    pending = append_result(host, entry, LOCAL_STORE_DIR)
    if len(pending) >= SYNC_THRESHOLD:
        _sync_to_main(host)
    # --- end store-and-forward ---

    return {"digits": digits, "pi": pi_str}
```

- [ ] **Step 4: Verify the module imports without errors**

```bash
python -c "from App.tasks import pi_reduce, _sync_to_main; print('OK')"
```
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add App/tasks.py
git commit -m "feat: store pi results locally and sync to main when threshold reached"
```

---

## Task 4: Add /sync and /ws endpoints to api.py (TDD)

**Files:**
- Modify: `App/api.py`
- Create: `tests/test_sync_api.py`

- [ ] **Step 1: Write failing tests — create `tests/test_sync_api.py`**

```python
# tests/test_sync_api.py
import pytest
from fastapi.testclient import TestClient
from App.api import app

client = TestClient(app)


def test_sync_endpoint_accepts_results_and_returns_ok():
    payload = {
        "worker_id": "worker_1",
        "results": [
            {
                "worker_id": "worker_1",
                "digits": 100,
                "pi_head": "3.14159265",
                "computed_at": "2026-04-16T00:00:00",
                "synced": True,
            }
        ],
    }
    r = client.post("/sync", json=payload)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["received"] == 1


def test_sync_endpoint_accepts_empty_results_list():
    r = client.post("/sync", json={"worker_id": "worker_1", "results": []})
    assert r.status_code == 200
    assert r.json()["received"] == 0


def test_sync_endpoint_rejects_missing_worker_id():
    r = client.post("/sync", json={"results": []})
    assert r.status_code == 422


def test_websocket_connection_is_accepted():
    with client.websocket_connect("/ws") as ws:
        assert ws is not None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_sync_api.py -v
```
Expected: `FAILED` with `404 Not Found` on `/sync` and `/ws`

- [ ] **Step 3: Add WebSocket import to `App/api.py`**

Find the existing line:
```python
from fastapi import FastAPI, HTTPException, Depends, Request
```
Replace with:
```python
from fastapi import FastAPI, HTTPException, Depends, Request, WebSocket, WebSocketDisconnect
```

- [ ] **Step 4: Add ConnectionManager to `App/api.py`**

Find this line in `api.py`:
```python
R = redis.from_url(RESULT_BACKEND)
```
Add the following block immediately after it:

```python

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
```

- [ ] **Step 5: Add /sync and /ws endpoints at the end of `App/api.py`**

Append these endpoints at the very end of the file:

```python

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
```

- [ ] **Step 6: Run tests to confirm they pass**

```bash
python -m pytest tests/test_sync_api.py -v
```
Expected: `4 passed`

- [ ] **Step 7: Run all tests to confirm nothing is broken**

```bash
python -m pytest tests/ -v
```
Expected: `10 passed` (6 from test_local_store + 4 from test_sync_api)

- [ ] **Step 8: Commit**

```bash
git add App/api.py tests/test_sync_api.py
git commit -m "feat: add /sync POST and /ws WebSocket endpoints for store-and-forward"
```

---

## Task 5: Update dashboard with Live Sync Feed

**Files:**
- Modify: `App/templates/index.html`

- [ ] **Step 1: Add the sync feed panel to the HTML**

Find this block in `App/templates/index.html`:
```html
  <div style="margin-top:24px">
    <h3>Tasks</h3>
    <div id="tasks"></div>
  </div>
```
Add the following block immediately after it (before the opening `<script>` tag):
```html
  <div style="margin-top:24px">
    <h3>Live Sync Feed <span class="muted" id="wsStatus">(connecting&hellip;)</span></h3>
    <div class="muted" style="margin-bottom:8px">
      Results forwarded by workers when their local buffer reaches the sync threshold (default: 5).
    </div>
    <div id="syncFeed"></div>
  </div>
```

- [ ] **Step 2: Add WebSocket JS to the script block**

Find the last line of the `<script>` block:
```javascript
loadWorkers();
```
Add the following immediately before it:
```javascript
// --- Store-and-forward sync feed ---
(function startSyncFeed() {
  var feed = document.getElementById("syncFeed");
  var status = document.getElementById("wsStatus");
  var proto = location.protocol === "https:" ? "wss:" : "ws:";
  var ws = new WebSocket(proto + "//" + location.host + "/ws");

  ws.onopen = function() { status.textContent = "(connected)"; };
  ws.onclose = function() { status.textContent = "(disconnected \u2014 refresh to reconnect)"; };
  ws.onerror = function() { status.textContent = "(error)"; };

  ws.onmessage = function(event) {
    var data = JSON.parse(event.data);
    var card = document.createElement("div");
    card.className = "card";
    card.style.marginBottom = "10px";

    var header = document.createElement("div");
    var workerSpan = document.createElement("strong");
    workerSpan.textContent = data.worker_id;
    var timeSpan = document.createElement("span");
    timeSpan.className = "muted";
    timeSpan.style.marginLeft = "8px";
    timeSpan.textContent = new Date().toLocaleTimeString();
    var countSpan = document.createElement("span");
    countSpan.className = "muted";
    countSpan.style.marginLeft = "8px";
    countSpan.textContent = (data.count || 0) + " result(s) forwarded";
    header.appendChild(workerSpan);
    header.appendChild(timeSpan);
    header.appendChild(countSpan);
    card.appendChild(header);

    var pillsDiv = document.createElement("div");
    pillsDiv.style.marginTop = "8px";
    (data.results || []).forEach(function(r) {
      var pill = document.createElement("div");
      pill.className = "pill";
      var digitsSpan = document.createElement("span");
      digitsSpan.textContent = r.digits + " digits";
      var piCode = document.createElement("code");
      piCode.style.fontSize = "11px";
      piCode.textContent = String(r.pi_head || "").slice(0, 30) + "\u2026";
      pill.appendChild(digitsSpan);
      pill.appendChild(piCode);
      pillsDiv.appendChild(pill);
    });
    card.appendChild(pillsDiv);
    feed.prepend(card);
  };
})();
// --- end sync feed ---
```

- [ ] **Step 3: Verify the dashboard visually**

Start the app:
```bash
python -m uvicorn App.api:app --host 0.0.0.0 --port 8000
```
Open `http://localhost:8000` and confirm:
1. "Live Sync Feed (connecting...)" section appears below Tasks
2. Status changes to "(connected)" after page load
3. Run a Pi task 5 times with the same worker queue — sync cards should appear in the feed showing worker ID, timestamp, result count, and Pi digits preview

- [ ] **Step 4: Commit**

```bash
git add App/templates/index.html
git commit -m "feat: add live sync feed panel to dashboard"
```

---

## Done

All four components are implemented and tested:

| Component | What it does |
|---|---|
| `App/local_store.py` | Reads/writes per-worker JSON files |
| `App/tasks.py` | Saves each Pi result locally; POSTs when threshold reached |
| `App/api.py` | Receives synced results; broadcasts to WebSocket clients |
| `App/templates/index.html` | Shows incoming synced results live |

To demo store-and-forward in class:
1. Start Redis, start the FastAPI server, start one or more Celery workers
2. Open the dashboard — the sync feed shows "(connected)"
3. Submit 5+ Pi jobs to the same worker
4. After the 5th job completes, the worker auto-syncs and a card appears in the feed
5. To simulate network failure: stop the FastAPI server, run 5 jobs — results accumulate in the local JSON file. Restart the server and run one more job — all pending results sync at once
