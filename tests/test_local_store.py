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


def test_append_result_creates_parent_directory_if_needed(tmp_path):
    nested_dir = str(tmp_path / "subdir" / "nested")
    result = append_result("worker_1", {"digits": 10}, nested_dir)
    assert len(result) == 1
    from pathlib import Path
    assert Path(nested_dir).exists()
