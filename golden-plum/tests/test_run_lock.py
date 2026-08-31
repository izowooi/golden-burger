from multiprocessing import Process, Queue
from pathlib import Path

from polybot.utils.run_lock import exclusive_job_run_lock


def _try_lock(path: str, queue: Queue) -> None:
    with exclusive_job_run_lock(Path(path)) as acquired:
        queue.put(acquired)


def test_second_process_skips_while_runtime_lock_is_held(tmp_path) -> None:
    lock_path = tmp_path / ".cycle-run.lock"
    queue: Queue = Queue()
    with exclusive_job_run_lock(lock_path) as first:
        assert first is True
        process = Process(target=_try_lock, args=(str(lock_path), queue))
        process.start()
        process.join(timeout=5)
        assert process.exitcode == 0
        assert queue.get(timeout=1) is False

    with exclusive_job_run_lock(lock_path) as after_release:
        assert after_release is True
