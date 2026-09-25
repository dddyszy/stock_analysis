"""后台任务：同名任务同一时间只跑一个，进度写入 job_log 供设置页展示。"""

import asyncio
import logging
import time
import traceback
from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy import select

from app.db.models import JobLog
from app.db.session import session_scope

logger = logging.getLogger(__name__)

_running: dict[str, asyncio.Task] = {}
_labels: dict[str, str] = {}


def register_labels(labels: dict[str, str]) -> None:
    _labels.update(labels)


def job_label(name: str) -> str:
    return _labels.get(name, name)


class JobAlreadyRunning(Exception):
    pass


class JobContext:
    def __init__(self, job_id: int, name: str) -> None:
        self.job_id = job_id
        self.name = name
        self._last_flush = 0.0
        self.done = 0
        self.total = 0
        self.message = ""

    def update(self, done: int | None = None, total: int | None = None, message: str | None = None, force: bool = False) -> None:
        if done is not None:
            self.done = done
        if total is not None:
            self.total = total
        if message is not None:
            self.message = message
        now = time.monotonic()
        if not force and now - self._last_flush < 1.0:
            return
        self._last_flush = now
        with session_scope() as db:
            job = db.get(JobLog, self.job_id)
            if job:
                job.progress, job.total, job.message = self.done, self.total, self.message[:5000]

    def step(self, message: str | None = None) -> None:
        self.update(done=self.done + 1, message=message)


JobFn = Callable[[JobContext], Awaitable[dict | None]]


def is_running(name: str) -> bool:
    task = _running.get(name)
    return task is not None and not task.done()


async def run_job(name: str, fn: JobFn) -> dict | None:
    current = asyncio.current_task()
    if is_running(name) and _running[name] is not current:
        raise JobAlreadyRunning(name)
    registered_here = name not in _running
    if registered_here and current is not None:
        _running[name] = current
    try:
        return await _execute(name, fn)
    finally:
        if registered_here:
            _running.pop(name, None)


async def _execute(name: str, fn: JobFn) -> dict | None:
    with session_scope() as db:
        job = JobLog(job_name=name, status="running")
        db.add(job)
        db.flush()
        job_id = job.id
    ctx = JobContext(job_id, name)
    status, detail, message = "success", None, None
    try:
        detail = await fn(ctx)
    except asyncio.CancelledError:
        status, message = "cancelled", f"已取消（进度 {ctx.done}/{ctx.total}）"
        raise
    except Exception as exc:
        status, message = "failed", f"{type(exc).__name__}: {exc}"
        logger.error("任务 %s 失败\n%s", name, traceback.format_exc())
        detail = {"traceback": traceback.format_exc()[-4000:]}
        raise
    finally:
        with session_scope() as db:
            job = db.get(JobLog, job_id)
            if job:
                job.status = status
                job.progress, job.total = ctx.done, ctx.total
                job.message = (message or ctx.message or "")[:5000]
                job.detail = detail if isinstance(detail, dict) else None
                job.finished_at = datetime.now()
        if status == "failed":
            from app.services.notify import notify

            notify("job_failed", f"任务失败：{job_label(name)}", (message or "")[:500], "error", key=name)
    return detail


def start_job(name: str, fn: JobFn) -> None:
    if is_running(name):
        raise JobAlreadyRunning(name)

    async def _wrapper() -> None:
        try:
            await run_job(name, fn)
        except Exception:
            pass
        finally:
            _running.pop(name, None)

    _running[name] = asyncio.create_task(_wrapper())


def running_jobs() -> list[str]:
    return [k for k, t in _running.items() if not t.done()]


def cancel_job(name: str) -> bool:
    task = _running.get(name)
    if task is None or task.done():
        return False
    task.cancel()
    return True


def mark_interrupted() -> int:
    """进程重启后，上次没跑完的任务记录仍是 running，统一标记为 interrupted。"""
    with session_scope() as db:
        rows = db.execute(select(JobLog).where(JobLog.status == "running")).scalars().all()
        names = []
        for j in rows:
            j.status = "interrupted"
            j.finished_at = datetime.now()
            j.message = f"服务重启时中断（进度 {j.progress}/{j.total}），可重新运行，会从断点继续"
            names.append(j.job_name)
    if names:
        from app.services.notify import notify

        for name in dict.fromkeys(names):
            notify("job_interrupted", f"任务被中断：{job_label(name)}", "服务重启时任务还没跑完，可以在设置页重新运行，会从断点继续", "warning", key=name)
    return len(names)
