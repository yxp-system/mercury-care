import asyncio
import hashlib
import json
import time

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select

from mercury.auth import authenticate
from mercury.db import scoped
from mercury.errors import DomainError
from mercury.models import Case, Job


def revision(request):
    ctx = authenticate(
        request,
        authorization=request.headers.get("authorization", ""),
        x_tenant_id=request.headers.get("x-tenant-id", ""),
        x_store_id=request.headers.get("x-store-id", ""),
    )
    db = request.app.state.db
    with db.session(ctx) as s:
        query = select(Case.id, Case.version, Case.updated_at).where(*scoped(Case, ctx)).order_by(Case.id)
        if ctx.customer_id:
            query = query.where(Case.customer_id == ctx.customer_id)
        state = [list(row) for row in s.execute(query)]
        if not ctx.customer_id:
            state += [
                list(row)
                for row in s.execute(
                    select(Job.id, Job.status, Job.fence).where(*scoped(Job, ctx)).order_by(Job.id)
                )
            ]
        return hashlib.sha256(json.dumps(state).encode()).hexdigest()


async def events(request):
    previous = ""
    until = time.monotonic() + 20
    while time.monotonic() < until and not await request.is_disconnected():
        try:
            current = await run_in_threadpool(revision, request)
        except DomainError:
            yield "event: session_expired\ndata: {}\n\n"
            return
        if current != previous:
            yield "event: refresh\ndata: {}\n\n"
            previous = current
        else:
            yield ": heartbeat\n\n"
        await asyncio.sleep(3)
