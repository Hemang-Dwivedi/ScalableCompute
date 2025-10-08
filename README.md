# Distributed Compute Dashboard (FastAPI + Celery + Redis)

A small, production-minded scaffold for **scalable computing** across multiple machines:

- 🌐 **FastAPI** dashboard (pick which workers/devices to use)
- 🧵 **Celery** task fan-out / chord reduce
- 🧮 Distributed **π digits** computation (Chudnovsky), up to ~1M digits
- 📡 **Live streaming** of π as chunks finish (and final fallback)
- ✅ Task cards turn **green** on success, **red** on failure/cancel
- 🔒 Optional API auth token, Redis password, LAN-only firewall rules
- 🪟 Windows-friendly (workers default to `--pool=solo`)

> Works on a single PC or across laptops on the same network. No Docker required.

## 0) Repo Structure
```
.
├─ app/ (or App/)         # Python package (ensure __init__.py exists)
│  ├─ api.py              # FastAPI endpoints + dashboard
│  ├─ tasks.py            # Celery tasks (pi_chunk, pi_reduce) + helpers
│  ├─ celery_app.py       # Celery setup
│  ├─ config.py           # Config & env variables
│  ├─ worker.py           # Worker launcher (adds --pool default on Windows)
│  └─ templates/
│     └─ index.html       # Dashboard UI: worker selection, π job, progress
└─ README.md
```

## 1) Requirements
- Python **3.11 or 3.12** recommended (3.13 on Windows must use `--pool=solo`)
- Redis 6+
- Same Python deps on API & each worker

## 2) Install

### Create venv & install packages
```bash
# Windows (CMD/PowerShell)
python -m venv .venv
.\.venv\Scriptsctivate
pip install fastapi==0.115.0 uvicorn[standard]==0.30.6 celery[redis]==5.4.0 redis==5.0.7 pydantic==2.9.2 jinja2==3.1.4 mpmath==1.3.0

# Optional speedup for big jobs
pip install gmpy2==2.1.5

# Optional monitoring
# pip install flower==2.0.1
```

### Redis

**Windows (one of):**
- Scoop:
  ```powershell
  Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
  irm get.scoop.sh | iex
  scoop install redis
  redis-server
  ```
- Manual download (Microsoft port):
  - https://github.com/microsoftarchive/redis/releases  
  - Unzip to `C:\Redis`, run `redis-server.exe`

**macOS / Linux:**
```bash
# macOS (Homebrew)
brew install redis
brew services start redis

# Ubuntu/Debian
sudo apt update && sudo apt install -y redis-server
sudo systemctl enable --now redis-server
```

#### LAN + Password (recommended)
```
bind 0.0.0.0
protected-mode no
port 6379
tcp-keepalive 300
requirepass YourStrongPasswordHere
```

## 3) Configuration
Set these variables on API & all workers:
```cmd
:: If Redis is local
set BROKER_URL=redis://:YourStrongPasswordHere@127.0.0.1:6379/0
set RESULT_BACKEND=redis://:YourStrongPasswordHere@127.0.0.1:6379/1

:: If Redis is remote
:: set BROKER_URL=redis://:YourStrongPasswordHere@192.168.0.110:6379/0
:: set RESULT_BACKEND=redis://:YourStrongPasswordHere@192.168.0.110:6379/1

:: Optional API token
set API_AUTH_TOKEN=secret123

:: Optional key prefix (default = distcomp)
set APP_NAMESPACE=distcomp
```

## 4) Run

### API
```bash
# If package folder is app/
python -m uvicorn app.api:app --host 0.0.0.0 --port 8000

# If package folder is App/
python -m uvicorn App.api:app --host 0.0.0.0 --port 8000
```
Open: `http://localhost:8000`

### Workers (Windows → pool=solo)
```bash
# Helper (recommended)
python -m App.worker --queue cpu-a --pool solo --concurrency 1

# Another machine / queue
python -m App.worker --queue laptop-b --pool solo --concurrency 1
```

_Parallelism on Windows:_ run multiple worker processes, each with `--pool=solo`.

## 5) Multi‑machine
1. Clone repo, create venv, install deps on each laptop.
2. Point env vars to host’s Redis (IP + password).
3. Start a worker with a unique `--queue` (e.g., `laptop-b`).
4. In dashboard, click **Refresh** and select queues to use.

## 6) Using the Dashboard
- **Workers panel:** select the queues/devices to use.
- **Compute π:** set digits, choose shards (0 = auto, clamped to terms).
- **Tasks list:** parent + subtask bars; live π pane updates; cards turn green on SUCCESS, red on FAILURE/REVOKED.

## 7) API Endpoints
When `API_AUTH_TOKEN` is set, add header: `API_AUTH_TOKEN: <token>`
- `GET /` – dashboard
- `GET /workers` – workers/queues
- `POST /submit_pi` – start a π job
- `GET /status/{task_id}`, `/status_tree/{task_id}`, `/result/{task_id}`
- `GET /pi_stream/{task_or_stream_id}?max_chars=4000` – live or final π

## 8) How π is computed & streamed
- **Chudnovsky** series, digits/term ≈ 14.181647462; `terms = ceil(digits/14.181647462)`
- Ranges split into shards (never more shards than terms; no empty ranges).
- Shard writes:
  - `<NS>:pi:<stream>:partials` (HASH idx→partial),
  - `...:done` (SET idx),
  - `...:final` (STR),
  - `<NS>:pi:map:<parent>` (STR stream id)
- Reducer runs on a selected queue and writes `final`.

## 9) Performance Notes
- Install `gmpy2` for large digit counts.
- Choose shard sizes so each has ≥ 200–500 terms.
- For ≤ 5k digits: one shard, one worker.
- LAN latency matters more than math for small digit counts.

## 10) Security
- Redis password + LAN binding only.
- Limit firewall to your private network.
- Use API token for remote dashboard.

## 11) Troubleshooting
- **uvicorn not found:** use `python -m uvicorn` or activate venv.
- **ModuleNotFoundError: app:** run from repo root; ensure package name matches; add `__init__.py`.
- **Worker connect resets:** Redis bound to `127.0.0.1` only — use `bind 0.0.0.0`, password, firewall rules.
- **Cancel sends SIGKILL on Windows:** server normalizes to SIGTERM; UI doesn’t send a signal.
- **Chord stuck:** reducer routed to default `celery` queue — we route it to the first selected queue.
- **ZeroDivisionError in reducer:** empty shards — splitter now forbids, shard task asserts early if mis-split.
- **Redis HSET wrong args:** we write meta via a pipeline (one field per command).
- **Python 3.11+ big-int limit:** we call `sys.set_int_max_str_digits(0)` for mpmath parsing.
- **Windows + Python 3.13 billiard error:** use `--pool=solo` or prefer 3.11/3.12.

## 12) Quick Commands
```cmd
:: API (Windows)
.\.venv\Scriptsctivate
set BROKER_URL=redis://:YourStrongPasswordHere@192.168.0.110:6379/0
set RESULT_BACKEND=redis://:YourStrongPasswordHere@192.168.0.110:6379/1
python -m uvicorn App.api:app --host 0.0.0.0 --port 8000
```

```cmd
:: Worker (Windows)
.\.venv\Scriptsctivate
set BROKER_URL=redis://:YourStrongPasswordHere@192.168.0.110:6379/0
set RESULT_BACKEND=redis://:YourStrongPasswordHere@192.168.0.110:6379/1
python -m App.worker --queue laptop-b --pool solo --concurrency 1
```
