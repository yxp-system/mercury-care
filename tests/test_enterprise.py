import hashlib
import socket
import struct
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from mercury.attachments import scan_attachment
from mercury.errors import DomainError
from mercury.models import BrowserSession, Case, Job, Notification, OrderLine, Policy, Run
from mercury.notifications import dispatch_notification
from mercury.runtime import schedule_escalations
from mercury.sessions import begin_login, finish_login, hashed, rate_limit, resolve_session, revoke_session
from pydantic import SecretStr
from sqlalchemy import func, select, update
from test_business import CTX, MAIN, authorize, drain, execute, make_plan


def oidc(db, monkeypatch):
    settings = db.settings
    for name, value in {
        "auth_mode": "oidc",
        "oidc_issuer": "https://id.example.test",
        "oidc_client_id": "mercury-web",
        "oidc_authorization_url": "https://id.example.test/authorize",
        "oidc_token_url": "https://id.example.test/token",
        "oidc_redirect_uri": "https://care.example.test/api/auth/oidc/callback",
        "session_encryption_keys": [SecretStr(Fernet.generate_key().decode())],
    }.items():
        monkeypatch.setattr(settings, name, value)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key()))
    issued = {}
    calls = []

    def provider(request):
        form = parse_qs(request.content.decode())
        calls.append(form)
        if form["grant_type"] == ["authorization_code"]:
            challenge = jwt.utils.base64url_encode(
                hashlib.sha256(form["code_verifier"][0].encode()).digest()
            ).decode()
            assert challenge == issued["challenge"]
            claims = {
                "sub": "agent",
                "iss": settings.oidc_issuer,
                "aud": settings.oidc_client_id,
                "iat": int(time.time()),
                "exp": int(time.time()) + 600,
                "nonce": issued["nonce"],
            }
            token = jwt.encode(claims, key, algorithm="RS256")
            return httpx.Response(
                200,
                json={
                    "id_token": token,
                    "access_token": "provider-access",
                    "refresh_token": "private-refresh",
                    "token_type": "Bearer",
                    "expires_in": 300,
                },
            )
        assert form["refresh_token"] == ["private-refresh"]
        return httpx.Response(
            200,
            json={
                "access_token": "rotated-access",
                "refresh_token": "rotated-refresh",
                "token_type": "Bearer",
                "expires_in": 300,
            },
        )

    original = httpx.Client
    monkeypatch.setattr(
        "mercury.sessions.httpx.Client",
        lambda **kwargs: original(transport=httpx.MockTransport(provider), **kwargs),
    )
    start = begin_login(db, "northstar", "uk")
    query = parse_qs(urlparse(start["authorization_url"]).query)
    issued.update(nonce=query["nonce"][0], challenge=query["code_challenge"][0])
    return start, query["state"][0], jwks, calls


def test_oidc_pkce_binding_replay_encryption_refresh_and_logout(db, monkeypatch):
    start, state, jwks, calls = oidc(db, monkeypatch)
    with pytest.raises(DomainError, match="登录请求"):
        finish_login(db, jwks, state, "wrong-browser-binding", "code")
    assert not calls
    result = finish_login(db, jwks, state, start["binding"], "code")
    handle = result["access_token"]
    assert handle.startswith("mc_") and "provider" not in handle
    with pytest.raises(DomainError):
        finish_login(db, jwks, state, start["binding"], "code")
    with db.session(write=True) as s:
        saved = s.get(BrowserSession, hashed(handle))
        assert "private-refresh" not in saved.refresh_ciphertext and saved.id != handle
        saved.provider_expires_at = 0
    assert resolve_session(db, jwks, handle).user_id == "agent"
    assert len(calls) == 2
    revoke_session(db, handle)
    with pytest.raises(DomainError):
        resolve_session(db, jwks, handle)


def test_oidc_nonce_and_membership_are_not_client_authority(db, monkeypatch):
    start, state, jwks, calls = oidc(db, monkeypatch)
    from mercury.models import LoginTransaction

    with db.session(write=True) as s:
        s.get(LoginTransaction, hashed(state)).nonce = "different-nonce"
    with pytest.raises(DomainError):
        finish_login(db, jwks, state, start["binding"], "code")
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(BrowserSession)) == 0


def test_shared_login_rate_limit(db):
    from mercury.db import Database

    second = Database(db.settings)
    for _ in range(3):
        rate_limit(db, "account-test", 3, seconds=600)
    with pytest.raises(DomainError) as error:
        rate_limit(second, "account-test", 3, seconds=600)
    assert error.value.status == 429
    second.engine.dispose()


def test_stream_refresh_checks_current_membership_and_customer_scope(app, db, headers):
    from mercury.models import Membership
    from mercury.streaming import revision
    from starlette.requests import Request

    authorization = headers("customer")["Authorization"]
    request = Request({"type": "http", "app": app, "headers": [(b"authorization", authorization.encode())]})
    before = revision(request)
    with db.session(CTX, write=True) as s:
        s.execute(update(Case).where(Case.id != MAIN).values(version=Case.version + 1))
    assert revision(request) == before
    with db.session(write=True) as s:
        s.execute(update(Membership).where(Membership.user_id == "customer").values(active=False))
    with pytest.raises(DomainError):
        revision(request)


def test_fenced_worker_rejects_writes_after_lease_replacement(db):
    from mercury.models import uid
    from mercury.runtime import renew_lease
    from mercury.worker import claim_job

    with db.session(CTX, write=True) as s:
        s.add(
            Job(
                tenant_id=CTX.tenant_id,
                store_id=CTX.store_id,
                kind="run",
                target_id="no-effect",
                dedupe_key=uid(),
            )
        )
    job = claim_job(db, "worker-one")
    with pytest.raises(DomainError):
        with renew_lease(db, job, "worker-one"):
            # Simulate a successor's atomic claim without inheriting the old ContextVar guard.
            with db.engine.begin() as connection:
                connection.execute(
                    update(Job)
                    .where(Job.id == job["id"])
                    .values(fence=job["fence"] + 1, lease_owner="worker-two")
                )
            with db.session(CTX, write=True) as s:
                s.get(Case, MAIN).subject = "must never persist"
    with db.session(CTX) as s:
        assert s.get(Case, MAIN).subject != "must never persist"


def test_cancelled_run_does_not_resume_or_consume_model_budget(client, db, headers):
    run = client.post(f"/api/cases/{MAIN}/analyze", headers=headers("agent")).json()
    drain(db)
    with db.session(CTX) as s:
        count = s.get(Run, run["id"]).calls
    response = client.post(
        f"/api/runs/{run['id']}/cancel", headers=headers("agent"), json={"reason": "人工接管处理"}
    )
    assert response.status_code == 200
    from mercury.services import enqueue

    with db.session(CTX, write=True) as s:
        enqueue(s, CTX, "run", run["id"], "cancelled-replay")
    drain(db)
    with db.session(CTX) as s:
        saved = s.get(Run, run["id"])
        assert saved.status == "cancelled" and saved.calls == count
    assert (
        client.post(
            f"/api/runs/{run['id']}/cancel", headers=headers("customer"), json={"reason": "越权操作测试"}
        ).status_code
        == 403
    )


def test_sla_handoff_is_durable_deduplicated_and_scoped(client, db, headers):
    with db.session(CTX, write=True) as s:
        s.get(Case, MAIN).updated_at = time.time() - 1000000
    schedule_escalations(db)
    schedule_escalations(db)
    records = client.get("/api/escalations", headers=headers("ops")).json()
    matching = [row for row in records if row["case_id"] == MAIN]
    assert len(matching) == 1 and matching[0]["reason"] == "case_overdue"
    assert client.get("/api/escalations", headers=headers("customer")).status_code == 403
    response = client.post(
        f"/api/escalations/{matching[0]['id']}/acknowledge",
        headers=headers("ops"),
        json={"reason": "已交人工负责人核查"},
    )
    assert response.json()["owner_id"] == "ops"


def test_notification_lost_receipt_queries_original_without_resending(client, db, headers):
    detail = client.get(f"/api/cases/{MAIN}", headers=headers()).json()
    notice = client.post(
        f"/api/cases/{MAIN}/notifications",
        headers=headers("agent"),
        json={"version": detail["version"], "body": "正在核查您的订单情况"},
    ).json()
    with pytest.raises(DomainError):
        dispatch_notification(db, CTX, notice["id"], lose_receipt=True)
    drain(db)
    with db.session(CTX) as s:
        saved = s.get(Notification, notice["id"])
        assert saved.status == "delivered" and saved.attempts == 1
        assert saved.request_id == notice["request_id"]


def test_notification_changed_while_queued_requires_review(client, db, headers):
    detail = client.get(f"/api/cases/{MAIN}", headers=headers()).json()
    notice = client.post(
        f"/api/cases/{MAIN}/notifications",
        headers=headers("agent"),
        json={"version": detail["version"], "body": "正在核查订单资料"},
    ).json()
    make_plan(client, headers)
    drain(db)
    with db.session(CTX) as s:
        saved = s.get(Notification, notice["id"])
        assert saved.status == "needs_review" and saved.attempts == 0


def test_clamav_protocol_quarantine_and_integrity(client, db, headers, monkeypatch):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    monkeypatch.setattr(db.settings, "scanner_mode", "clamav")
    monkeypatch.setattr(db.settings, "clamav_host", "127.0.0.1")
    monkeypatch.setattr(db.settings, "clamav_port", server.getsockname()[1])
    received = []

    def service():
        with server:
            connection, _ = server.accept()
            with connection:

                def exact(length):
                    data = b""
                    while len(data) < length:
                        data += connection.recv(length - len(data))
                    return data

                assert exact(10) == b"zINSTREAM\0"
                while size := struct.unpack("!I", exact(4))[0]:
                    received.append(exact(size))
                connection.sendall(b"stream: OK\0")

    thread = threading.Thread(target=service, daemon=True)
    thread.start()
    item = client.post(
        f"/api/cases/{MAIN}/attachments",
        headers=headers(),
        files={"file": ("evidence.txt", b"safe evidence", "text/plain")},
    ).json()
    assert client.get(f"/api/attachments/{item['id']}", headers=headers()).status_code == 423
    scan_attachment(db, CTX, item["id"])
    thread.join(timeout=5)
    assert b"".join(received) == b"safe evidence"
    assert client.get(f"/api/attachments/{item['id']}", headers=headers()).content == b"safe evidence"
    (db.settings.data_dir / "uploads" / item["id"]).write_bytes(b"tampered")
    assert client.get(f"/api/attachments/{item['id']}", headers=headers()).status_code == 423


def test_document_import_acl_publication_and_atomic_revision(client, db, headers):
    payload = {
        "family": "enterprise-policy",
        "title": "企业破损政策",
        "content": "# 条款一\n" + "确认订单及实际破损后形成方案。" * 200,
        "kind": "replacement",
        "region": "GB",
        "effective_from": 0,
        "effective_until": 4102444800,
        "allowed_users": ["agent"],
        "allowed_roles": [],
    }
    doc = client.post("/api/knowledge/documents", headers=headers("editor"), json=payload).json()
    publish = {"validation_cases": [MAIN], "note": "已核对适用范围和引用原文"}
    assert (
        client.post(
            f"/api/knowledge/documents/{doc['id']}/publish", headers=headers("reviewer"), json=publish
        ).status_code
        == 409
    )
    drain(db)
    assert (
        client.post(
            f"/api/knowledge/documents/{doc['id']}/publish", headers=headers("reviewer"), json=publish
        ).status_code
        == 200
    )
    rules = client.get(f"/api/cases/{MAIN}/rules", headers=headers("agent")).json()
    assert any(rule["document_id"] == doc["id"] for rule in rules["citations"])
    hidden = client.get(f"/api/cases/{MAIN}/rules", headers=headers("manager")).json()
    assert not any(rule["document_id"] == doc["id"] for rule in hidden["citations"])
    revision = client.post(
        "/api/knowledge/documents",
        headers=headers("editor"),
        json=payload | {"content": "# 新条款\n" + "必须核查当前版本与适用区域。" * 20},
    ).json()
    drain(db)
    assert (
        client.post(
            f"/api/knowledge/documents/{revision['id']}/publish", headers=headers("reviewer"), json=publish
        ).status_code
        == 200
    )
    with db.session(CTX) as s:
        assert all(
            row.status == "withdrawn"
            for row in s.scalars(select(Policy).where(Policy.document_id == doc["id"]))
        )
    assert client.get("/api/knowledge/documents", headers=headers("outsider")).status_code == 403


def test_partial_shipments_preserve_stock_and_require_all_deliveries(client, db, headers):
    with db.session(CTX, write=True) as s:
        case = s.get(Case, MAIN)
        s.get(OrderLine, case.line_id).quantity = 2
    case = client.get(f"/api/cases/{MAIN}", headers=headers()).json()
    rules = client.get(f"/api/cases/{MAIN}/rules", headers=headers()).json()
    plan = client.post(
        f"/api/cases/{MAIN}/plans",
        headers=headers(),
        json={
            "version": case["version"],
            "kind": "replacement",
            "quantity": 2,
            "rationale": "按订单授权分两个包裹补发",
            "citations": [r["id"] for r in rules["citations"]],
        },
    ).json()
    authorize(client, headers, db, plan)
    action = execute(client, headers, plan).json()
    drain(db)

    def delivery(kind, package, seq):
        return client.post(
            f"/api/demo/actions/{action['id']}/events",
            headers=headers("executor"),
            json={
                "source_id": f"package-event-{seq}",
                "sequence": seq,
                "kind": kind,
                "package_id": package,
                "quantity": 1,
                "evidence": "独立包裹的受控合成事件",
            },
        )

    assert delivery("shipped", "pkg-one", 1).json()["status"] == "partially_shipped"
    assert delivery("delivered", "pkg-one", 2).json()["status"] == "partially_shipped"
    assert delivery("shipped", "pkg-two", 3).json()["status"] == "shipped"
    assert delivery("delivered", "pkg-two", 4).json()["status"] == "delivered"
    assert delivery("delivered", "pkg-two", 4).status_code == 200
    stock = next(
        row for row in client.get("/api/inventory", headers=headers()).json() if row["sku"] == "CUP-WHITE"
    )
    assert (stock["on_hand"], stock["reserved"], stock["shipped"]) == (1, 0, 2)
    assert len(client.get(f"/api/cases/{MAIN}", headers=headers()).json()["shipments"]) == 2
