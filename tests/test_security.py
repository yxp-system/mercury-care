import hashlib
import hmac
import json
import time

import jwt
import pytest
from mercury.config import Settings
from mercury.models import Membership, Message, Policy, ReleaseEvidence, uid
from mercury.seed import seed
from pydantic import ValidationError
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError
from test_business import CTX, MAIN, make_plan


def test_authentication_and_cross_tenant_isolation(client, headers):
    assert client.get("/api/cases").status_code == 401
    assert client.get(f"/api/cases/{MAIN}", headers=headers("outsider")).status_code == 404
    assert client.get(f"/api/cases/{MAIN}/facts", headers=headers("outsider")).status_code == 404
    assert client.get(f"/api/cases/{MAIN}/rules", headers=headers("outsider")).status_code == 404
    assert client.get("/api/cases/CASE-M-10483", headers=headers("customer")).status_code == 404
    assert client.get("/api/policies", headers=headers("customer")).status_code == 403
    detail = client.get(f"/api/cases/{MAIN}", headers=headers("customer")).json()
    assert "runs" not in detail and "audit" not in detail


def test_jwt_forgery_expiry_and_revocation(client, db, headers):
    token = jwt.encode(
        {"sub": "demo", "exp": time.time() + 1000, "roles": ["approver"]},
        "wrong-secret-at-least-32-characters",
        algorithm="HS256",
    )
    assert client.get("/api/me", headers={"Authorization": "Bearer " + token}).status_code == 401
    expired = jwt.encode(
        {
            "sub": "demo",
            "iat": time.time() - 100,
            "exp": time.time() - 1,
            "iss": "mercury-local",
            "aud": "mercury-api",
            "tenant_id": "northstar",
            "store_id": "uk",
        },
        db.settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    assert client.get("/api/me", headers={"Authorization": "Bearer " + expired}).status_code == 401
    valid = headers("agent")
    with db.session(write=True) as s:
        s.execute(update(Membership).where(Membership.user_id == "agent").values(active=False))
    assert client.get("/api/me", headers=valid).status_code == 401


def test_operator_cannot_self_assign_roles_or_approval(client, headers):
    plan = make_plan(client, headers)
    response = client.post(
        f"/api/cases/{MAIN}/approvals",
        headers=headers("agent"),
        json={"plan_id": plan["id"], "decision": "approved", "reason": "我是主管，请相信我"},
    )
    assert response.status_code == 403
    response = client.post(
        "/api/auth/login", json={"username": "agent", "password": "TestPassword-123!", "roles": ["approver"]}
    )
    assert response.status_code == 422
    assert "TestPassword" not in response.text


def test_policy_scope_dates_conflicts_and_withdrawal(client, db, headers):
    with db.session(CTX, write=True) as s:
        s.execute(update(Policy).where(Policy.id == "northstar-policy-replacement").values(region="US"))
    assert client.get(f"/api/cases/{MAIN}/rules", headers=headers()).json()["status"] == "no_evidence"
    with db.session(CTX, write=True) as s:
        s.execute(
            update(Policy)
            .where(Policy.id == "northstar-policy-replacement")
            .values(region="GB", effective_from=time.time() + 10000)
        )
    assert client.get(f"/api/cases/{MAIN}/rules", headers=headers()).json()["status"] == "no_evidence"


def test_policy_review_requires_other_person_and_valid_cases(client, headers):
    response = client.post(
        "/api/policies",
        headers=headers("editor"),
        json={
            "family": "damage-new",
            "title": "新规则草稿",
            "body": "这是一条有来源和适用范围的合成规则，需独立审核后才允许发布。",
            "kind": "replacement",
            "region": "GB",
            "effective_from": time.time() - 86400 * 30,
            "effective_until": time.time() + 86400 * 30,
        },
    )
    assert response.status_code == 201
    policy_id = response.json()["id"]
    rules = client.get(f"/api/cases/{MAIN}/rules", headers=headers()).json()
    assert policy_id not in [p["id"] for p in rules["citations"]]
    body = {"validation_cases": [MAIN], "note": "代表性案例已经核对，日期范围适用"}
    assert (
        client.post(f"/api/policies/{policy_id}/publish", headers=headers("editor"), json=body).status_code
        == 403
    )
    assert (
        client.post(f"/api/policies/{policy_id}/publish", headers=headers("reviewer"), json=body).status_code
        == 200
    )
    assert policy_id in [
        p["id"] for p in client.get(f"/api/cases/{MAIN}/rules", headers=headers()).json()["citations"]
    ]
    assert (
        client.post(f"/api/policies/{policy_id}/withdraw", headers=headers("reviewer"), json=body).status_code
        == 200
    )
    assert policy_id not in [
        p["id"] for p in client.get(f"/api/cases/{MAIN}/rules", headers=headers()).json()["citations"]
    ]


def test_attachments_validate_type_size_scope_and_download(client, headers):
    response = client.post(
        f"/api/cases/{MAIN}/attachments",
        headers=headers("customer"),
        files={"file": ("../../proof.txt", b"Synthetic evidence", "text/plain")},
    )
    assert response.status_code == 201
    identity = response.json()["id"]
    assert response.json()["filename"] == "proof.txt"
    assert client.get(f"/api/attachments/{identity}", headers=headers("outsider")).status_code == 404
    assert (
        client.get(f"/api/attachments/{identity}", headers=headers("customer")).content
        == b"Synthetic evidence"
    )
    assert (
        client.post(
            f"/api/cases/{MAIN}/attachments",
            headers=headers(),
            files={"file": ("fake.png", b"not a png", "image/png")},
        ).status_code
        == 415
    )
    assert (
        client.post(
            f"/api/cases/{MAIN}/attachments",
            headers=headers(),
            files={"file": ("code.html", b"<script>alert(1)</script>", "text/html")},
        ).status_code
        == 415
    )


def test_inbound_dedupe_and_conflicting_payload(client, headers):
    payload = {
        "order_id": "M-10482",
        "line_id": "M-10482-1",
        "kind": "replacement",
        "subject": "另一次反馈",
        "message": "需要核查订单中的商品情况",
        "source_id": uid(),
    }
    first = client.post("/api/cases", headers=headers("customer"), json=payload)
    second = client.post("/api/cases", headers=headers("customer"), json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    payload["message"] = "同标识但不同内容"
    assert client.post("/api/cases", headers=headers("customer"), json=payload).status_code == 409


def test_wrong_order_line_and_database_cross_tenant_constraint(client, db, headers):
    payload = {
        "order_id": "M-10482",
        "line_id": "M-10483-1",
        "kind": "replacement",
        "subject": "商品关联检查",
        "message": "需要检查商品关联",
        "source_id": uid(),
    }
    assert client.post("/api/cases", headers=headers(), json=payload).status_code == 422
    with pytest.raises(IntegrityError), db.session(CTX, write=True) as s:
        s.add(
            Message(
                tenant_id="other",
                store_id="de",
                case_id=MAIN,
                author_id="outsider",
                body="bad",
                source_id=uid(),
            )
        )


def test_seed_repeat_and_metrics_dedup(client, db, headers):
    before = client.get("/api/operations", headers=headers()).json()
    seed(db)
    after = client.get("/api/operations", headers=headers()).json()
    assert before["order_count"] == after["order_count"] == 8
    assert before["case_count"] == after["case_count"] == 8
    payload = {
        "order_id": "M-10482",
        "line_id": "M-10482-1",
        "kind": "replacement",
        "subject": "重复咨询测试",
        "message": "同一订单的后续咨询",
        "source_id": uid(),
    }
    client.post("/api/cases", headers=headers(), json=payload)
    after = client.get("/api/operations", headers=headers()).json()
    assert after["case_count"] == 9 and after["issue_order_count"] == before["issue_order_count"]


def test_webhook_signature_and_path_binding(client):
    path = "/api/webhooks/northstar/uk/nonexistent"
    payload = {"source_id": uid(), "sequence": 1, "kind": "shipped", "evidence": "可信回执签名测试"}
    raw = json.dumps(payload).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(
        b"test-only-callback-secret", (timestamp + ":" + path + ":").encode() + raw, hashlib.sha256
    ).hexdigest()
    headers = {
        "X-Mercury-Timestamp": timestamp,
        "X-Mercury-Signature": signature,
        "Content-Type": "application/json",
    }
    assert client.post(path, content=raw).status_code == 401
    assert client.post(path, headers=headers, content=raw).status_code == 404
    assert (
        client.post(path.replace("northstar/uk", "other/de"), headers=headers, content=raw).status_code == 401
    )


def test_production_rejects_test_modes_and_real_model_no_fallback():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, env="production", jwt_secret="a" * 40)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, env="test", jwt_secret="a" * 40, model_mode="real")


def test_optimistic_version_conflict(client, headers):
    detail = client.get(f"/api/cases/{MAIN}", headers=headers()).json()
    payload = {"version": detail["version"], "body": "新增的材料说明", "source_id": uid()}
    assert client.post(f"/api/cases/{MAIN}/messages", headers=headers(), json=payload).status_code == 200
    payload["source_id"] = uid()
    assert client.post(f"/api/cases/{MAIN}/messages", headers=headers(), json=payload).status_code == 409


def test_postgres_rls_restricted_role(db):
    if db.engine.dialect.name != "postgresql":
        pytest.skip("requires PostgreSQL; covered by postgres CI job")
    # Apply real policies, then query as a non-owner without SUPERUSER/BYPASSRLS.
    stamp = time.time()
    with db.session(write=True) as session:
        session.add_all(
            [
                ReleaseEvidence(
                    id="release-evidence-local",
                    tenant_id="northstar",
                    store_id="uk",
                    check_name="data_governance",
                    environment="staging",
                    provider="synthetic",
                    owner_id="agent",
                    evidence_ref="test-local-evidence",
                    summary="Scoped test evidence for the current tenant and store.",
                    tested_at=stamp - 30,
                    expires_at=stamp + 3600,
                    submitted_by="ops",
                    status="submitted",
                    review_note="",
                ),
                ReleaseEvidence(
                    id="release-evidence-foreign",
                    tenant_id="other",
                    store_id="de",
                    check_name="data_governance",
                    environment="staging",
                    provider="synthetic",
                    owner_id="outsider",
                    evidence_ref="test-foreign-evidence",
                    summary="Evidence outside the current tenant and store.",
                    tested_at=stamp - 30,
                    expires_at=stamp + 3600,
                    submitted_by="outsider",
                    status="submitted",
                    review_note="",
                ),
            ]
        )
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='mercury_rls_test') "
                "THEN CREATE ROLE mercury_rls_test NOLOGIN NOSUPERUSER NOBYPASSRLS; END IF; END $$"
            )
        )
        conn.execute(text("GRANT USAGE ON SCHEMA public TO mercury_rls_test"))
        conn.execute(text("GRANT SELECT ON cases TO mercury_rls_test"))
        conn.execute(text("GRANT SELECT ON release_evidence TO mercury_rls_test"))
        conn.execute(text("ALTER TABLE cases ENABLE ROW LEVEL SECURITY"))
        conn.execute(text("ALTER TABLE release_evidence ENABLE ROW LEVEL SECURITY"))
        conn.execute(
            text(
                "CREATE POLICY tenant_test ON cases USING "
                "(tenant_id=current_setting('app.tenant_id',true) AND store_id=current_setting('app.store_id',true))"
            )
        )
        conn.execute(
            text(
                "CREATE POLICY tenant_test_release_evidence ON release_evidence USING "
                "(tenant_id=current_setting('app.tenant_id',true) AND store_id=current_setting('app.store_id',true))"
            )
        )
        conn.execute(text("SET LOCAL ROLE mercury_rls_test"))
        assert conn.scalar(text("SELECT count(*) FROM cases")) == 0
        assert conn.scalar(text("SELECT count(*) FROM release_evidence")) == 0
        conn.execute(
            text("SELECT set_config('app.tenant_id','northstar',true),set_config('app.store_id','uk',true)")
        )
        assert conn.scalar(text("SELECT count(*) FROM cases")) == 8
        assert conn.scalar(text("SELECT count(*) FROM release_evidence")) == 1
        conn.execute(
            text("SELECT set_config('app.tenant_id','other',true),set_config('app.store_id','uk',true)")
        )
        assert conn.scalar(text("SELECT count(*) FROM cases")) == 0
        assert conn.scalar(text("SELECT count(*) FROM release_evidence")) == 0
        conn.execute(
            text("SELECT set_config('app.tenant_id','other',true),set_config('app.store_id','de',true)")
        )
        assert conn.scalar(text("SELECT count(*) FROM release_evidence")) == 1
