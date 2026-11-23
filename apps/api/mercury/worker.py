import argparse
import hashlib
import logging
import os
import signal
import time
from contextlib import contextmanager

from sqlalchemy import or_, select, text, update

from mercury.auth import AuthContext
from mercury.config import Settings
from mercury.db import Database
from mercury.errors import DomainError, require
from mercury.models import Job, Notification, Run, now, uid
from mercury.services import dispatch_action, owned
from mercury.workflow import run_workflow

log = logging.getLogger("mercury.worker")


@contextmanager
def target_lock(db, target_id):
    """A per-target OS/advisory lock spans graph + checkpoint writes; a lease alone is insufficient."""
    key = int.from_bytes(hashlib.sha256(target_id.encode()).digest()[:8], "big", signed=True)
    if db.engine.dialect.name == "postgresql":
        with db.engine.connect() as conn:
            acquired = conn.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
            conn.commit()
            require(acquired, "target_busy", "已有工作进程持有此任务", 503)
            try:
                yield
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                conn.commit()
    else:
        folder = db.settings.data_dir / "locks"
        folder.mkdir(exist_ok=True, parents=True)
        with (folder / (hashlib.sha256(target_id.encode()).hexdigest() + ".lock")).open("a+b") as handle:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                handle.write(b"0")
                handle.flush()
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    raise DomainError("target_busy", "任务已被占用", 503) from None
            else:
                import fcntl

                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise DomainError("target_busy", "任务已被占用", 503) from None
            try:
                yield
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def claim_job(db, owner):
    with db.session(write=True) as s:
        query = (
            select(Job)
            .where(
                Job.due_at <= now(),
                or_(Job.status == "pending", (Job.status == "running") & (Job.lease_until < now())),
            )
            .order_by(Job.due_at)
        )
        if db.engine.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        job = s.scalar(query.limit(1))
        if not job:
            return None
        job.status, job.lease_owner, job.lease_until = "running", owner, now() + 120
        job.fence += 1
        job.attempts += 1
        s.flush()
        return {col.name: getattr(job, col.name) for col in Job.__table__.columns}


def process_one(db, owner=None):
    owner = owner or uid()
    job = claim_job(db, owner)
    if not job:
        return False
    ctx = AuthContext("worker", job["tenant_id"], job["store_id"], frozenset({"executor"}))
    error = ""
    status = "done"
    try:
        with target_lock(db, job["target_id"]):
            # A stale lease may acquire the target lock later; verify fencing before any effect.
            with db.session() as s:
                current = s.get(Job, job["id"])
                require(
                    current.fence == job["fence"] and current.lease_owner == owner,
                    "stale_worker",
                    "工作进程租约已被替换",
                )
            if job["kind"] == "run":
                run_workflow(db, ctx, job["target_id"])
            elif job["kind"] == "action":
                dispatch_action(db, ctx, job["target_id"])
            elif job["kind"] == "notification":
                require(
                    db.settings.writes_enabled and db.settings.business_mode == "synthetic",
                    "notification_unavailable",
                    "通知接口未接入或已冻结",
                    503,
                )
                with db.session(ctx, write=True) as s:
                    notice = owned(s, Notification, ctx, job["target_id"], True)
                    if notice.status == "pending":
                        notice.status = "delivered"
                        notice.receipt = {
                            "source": "synthetic",
                            "request_id": notice.request_id,
                            "description": "本地通知记录；未向外部客户发送",
                        }
            else:
                raise DomainError("unknown_job", "不支持的任务类型")
    except Exception as exc:
        error = exc.code if isinstance(exc, DomainError) else type(exc).__name__
        status = (
            "pending"
            if isinstance(exc, DomainError) and exc.status >= 500 and job["attempts"] < 3
            else "dead"
        )
        log.warning("job_failed kind=%s code=%s attempt=%s", job["kind"], error, job["attempts"])
        if job["kind"] == "run" and status == "dead":
            with db.session(ctx, write=True) as s:
                s.execute(update(Run).where(Run.id == job["target_id"]).values(status="failed", error=error))
    with db.session(write=True) as s:
        s.execute(
            update(Job)
            .where(Job.id == job["id"], Job.fence == job["fence"], Job.lease_owner == owner)
            .values(status=status, lease_until=0, error=error, due_at=now() + min(60, 2 ** job["attempts"]))
        )
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    db = Database(Settings())
    owner = uid()
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while not stopping:
        worked = process_one(db, owner)
        if args.once:
            break
        if not worked:
            time.sleep(1)


if __name__ == "__main__":
    main()
