"""In-process scheduler for single-service deployments (JA_EMBEDDED_SCHEDULER=true), replacing Celery worker + beat.

Runs the same task functions as app.tasks on a daemon thread: tick every 5 minutes (it still honors the user's
polling cadence, the global pause and the kill-switch file), retention once a day. Deploy with ONE replica only.
"""
import logging
import threading
import time

log = logging.getLogger("jobapplier.scheduler")
TICK_SECONDS = 300
RETENTION_SECONDS = 24 * 3600
_stop = threading.Event()
_thread: threading.Thread | None = None


def _loop() -> None:
    from . import tasks

    last_retention = 0.0
    _stop.wait(30)  # let the app finish starting
    while not _stop.is_set():
        try:
            result = tasks.tick()
            log.info("scheduler tick: %s", {k: v for k, v in (result or {}).items() if k != "matched"})
        except Exception:  # noqa: BLE001 - a failed tick must not kill the scheduler
            log.exception("scheduler tick failed")
        if time.monotonic() - last_retention > RETENTION_SECONDS:
            try:
                log.info("retention: %s", tasks.retention())
                last_retention = time.monotonic()
            except Exception:  # noqa: BLE001
                log.exception("retention failed")
        _stop.wait(TICK_SECONDS)


def start() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="jobapplier-scheduler", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
