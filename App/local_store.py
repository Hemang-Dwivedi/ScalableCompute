import json
from pathlib import Path


def _store_path(worker_id: str, storage_dir: str = ".") -> Path:
    safe_id = worker_id
    for char in '@/\\:?*"<>|':
        safe_id = safe_id.replace(char, "_")
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
    path = _store_path(worker_id, storage_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pending, indent=2), encoding="utf-8")
    return pending


def clear_results(worker_id: str, storage_dir: str = ".") -> None:
    _store_path(worker_id, storage_dir).write_text("[]", encoding="utf-8")
