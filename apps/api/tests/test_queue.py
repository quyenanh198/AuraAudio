import threading
import time

from aura_api.queue import enqueue_transcription_job


def test_enqueue_runs_target_function_in_background_thread(monkeypatch):
    calls = []
    done = threading.Event()

    def fake_run(job_id: str) -> None:
        calls.append((job_id, threading.current_thread() is not threading.main_thread()))
        done.set()

    import aura_api.queue as queue_module

    monkeypatch.setattr(queue_module, "run_transcription_job", fake_run)

    enqueue_transcription_job("job-123")
    assert done.wait(timeout=2.0), "job did not run within timeout"
    assert calls == [("job-123", True)]


def test_enqueue_serializes_two_jobs_without_dropping_either(monkeypatch):
    order = []
    lock = threading.Lock()

    def fake_run(job_id: str) -> None:
        time.sleep(0.05)
        with lock:
            order.append(job_id)

    import aura_api.queue as queue_module

    monkeypatch.setattr(queue_module, "run_transcription_job", fake_run)

    enqueue_transcription_job("a")
    enqueue_transcription_job("b")
    time.sleep(0.3)
    assert sorted(order) == ["a", "b"]
