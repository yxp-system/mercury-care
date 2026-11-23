"""Prepare and verify an isolated synthetic recovery fixture, never a production database."""

import argparse
import hashlib
import json
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from mercury.api import create_app
from mercury.auth import load_identity, login
from mercury.config import Settings
from mercury.errors import DomainError
from mercury.models import Action, BrowserSession, Inventory, Job, LoginTransaction, ProviderRequest, now
from mercury.seed import seed
from mercury.services import dispatch_action
from mercury.sessions import hashed
from sqlalchemy import func, select
from sqlalchemy.engine import make_url


def inventory(db):
    with db.session() as session:
        return [
            [row.tenant_id, row.store_id, row.sku, row.on_hand, row.reserved]
            for row in session.scalars(select(Inventory).order_by(Inventory.id))
        ]


def prepare(settings, application):
    if not settings.writes_enabled:
        raise SystemExit("Fixture preparation requires writes in the isolated test database")
    db = application.state.db
    seed(db)
    with db.session() as session:
        if session.scalar(select(func.count()).select_from(Action)):
            raise SystemExit("Recovery fixture requires a fresh synthetic seed without actions")
    credentials = {
        user: {
            "Authorization": "Bearer "
            + login(db, user, settings.demo_password.get_secret_value(), "northstar", "uk")["access_token"]
        }
        for user in ("agent", "customer", "manager", "executor")
    }
    with TestClient(application) as client:

        def call(method, path, user="agent", **kwargs):
            response = client.request(method, "/api" + path, headers=credentials[user], **kwargs)
            response.raise_for_status()
            return response.json()

        case_id = "CASE-M-10482"
        case = call("GET", f"/cases/{case_id}")
        rules = call("GET", f"/cases/{case_id}/rules")
        plan = call(
            "POST",
            f"/cases/{case_id}/plans",
            json={
                "version": case["version"],
                "kind": "replacement",
                "quantity": 1,
                "amount_minor": 0,
                "rationale": "隔离恢复演练：验证原请求与库存占用",
                "citations": [row["id"] for row in rules["citations"]],
            },
        )
        call("POST", f"/cases/{case_id}/choice", "customer", json={"plan_id": plan["id"], "accepted": True})
        call(
            "POST",
            f"/cases/{case_id}/approvals",
            "manager",
            json={
                "plan_id": plan["id"],
                "decision": "approved",
                "reason": "隔离恢复演练批准合成动作",
            },
        )
        action = call(
            "POST",
            f"/cases/{case_id}/actions",
            "executor",
            json={
                "plan_id": plan["id"],
                "business_action_id": "ci-recovery-action",
            },
        )
        ctx = load_identity(db, "executor", "northstar", "uk")
        dispatch_action(db, ctx, action["id"], lose_response=True)
        attachment = call(
            "POST",
            f"/cases/{case_id}/attachments",
            files={
                "file": (
                    "recovery-evidence.txt",
                    b"Synthetic recovery drill evidence. No customer data.",
                    "text/plain",
                ),
            },
        )
    test_cipher = Fernet(Fernet.generate_key()).encrypt(b"synthetic-drill-only-refresh").decode()
    session_id, transaction_id = hashed("ci-recovery-session"), hashed("ci-recovery-login")
    with db.session(write=True) as session:
        session.add(
            BrowserSession(
                id=session_id,
                user_id="agent",
                tenant_id="northstar",
                store_id="uk",
                refresh_ciphertext=test_cipher,
                provider_expires_at=now() + 300,
                expires_at=now() + 3600,
            )
        )
        session.add(
            LoginTransaction(
                id=transaction_id,
                binding_hash=hashed("ci-recovery-binding"),
                verifier=test_cipher,
                nonce="ci-recovery-nonce",
                tenant_id="northstar",
                store_id="uk",
                expires_at=now() + 300,
            )
        )
    expected = {
        "action_id": action["id"],
        "request_id": action["request_id"],
        "plan_id": plan["id"],
        "attachment_id": attachment["id"],
        "attachment_sha256": attachment["sha256"],
        "session_id": session_id,
        "transaction_id": transaction_id,
        "inventory": inventory(db),
    }
    (settings.data_dir / "recovery-expected.json").write_text(
        json.dumps(expected, indent=2), encoding="utf-8"
    )
    print(
        "Prepared synthetic unknown action, occupied stock, attachment, active session and login transaction."
    )


def verify(settings, application, expected_path):
    if settings.writes_enabled:
        raise SystemExit("Recovered database must remain frozen")
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    db = application.state.db
    with db.session() as session:
        action = session.get(Action, expected["action_id"])
        assert action and action.status == "unknown"
        assert (action.request_id, action.plan_id) == (expected["request_id"], expected["plan_id"])
        assert session.scalar(select(func.count()).select_from(ProviderRequest)) == 1
        assert session.scalar(select(func.count()).select_from(Job).where(Job.status == "pending")) >= 1
        restored = session.get(BrowserSession, expected["session_id"])
        assert restored and restored.revoked and restored.refresh_ciphertext == ""
        assert session.get(LoginTransaction, expected["transaction_id"]) is None
    assert inventory(db) == expected["inventory"], "Inventory occupancy changed during recovery"
    content = (settings.data_dir / "uploads" / expected["attachment_id"]).read_bytes()
    assert hashlib.sha256(content).hexdigest() == expected["attachment_sha256"]
    try:
        dispatch_action(db, load_identity(db, "executor", "northstar", "uk"), expected["action_id"])
    except DomainError as error:
        assert error.code == "writes_frozen"
    else:
        raise AssertionError("Frozen restored instance dispatched an action")
    receipt = {
        "drill": "passed",
        "original_request_preserved": True,
        "inventory_unchanged": True,
        "attachment_verified": True,
        "session_revoked": True,
        "login_transaction_cleared": True,
        "dispatch_frozen": True,
        "external_recovery_window": "unverified",
    }
    (settings.data_dir / "recovery-drill.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "verify"])
    parser.add_argument("--expected", type=Path)
    args = parser.parse_args()
    settings = Settings(_env_file=None)
    database_name = make_url(settings.database_url).database or ""
    if settings.env != "test" or "test" not in database_name.lower() or settings.business_mode != "synthetic":
        raise SystemExit("Recovery drill only permits an explicit synthetic test database")
    application = create_app(settings)
    if args.mode == "prepare":
        prepare(settings, application)
    else:
        if args.expected is None:
            parser.error("verify requires --expected")
        verify(settings, application, args.expected)
    application.state.db.engine.dispose()


if __name__ == "__main__":
    main()
