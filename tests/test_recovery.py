import os
import subprocess
import sys
import time

from mercury.models import Action, Job, Notification, Policy, Run, uid
from mercury.services import dispatch_action, enqueue
from mercury.worker import process_one
from sqlalchemy import select, update
from test_business import CTX, MAIN, authorize, drain, event, execute, make_plan


def test_checkpoint_survives_independent_worker_process(client, db, headers):
    env = os.environ.copy()
    env.update(
        {
            "MERCURY_ENV": "test",
            "MERCURY_DATABASE_URL": db.settings.database_url,
            "MERCURY_DATA_DIR": str(db.settings.data_dir),
            "MERCURY_JWT_SECRET": db.settings.jwt_secret.get_secret_value(),
            "MERCURY_DEMO_PASSWORD": "TestPassword-123!",
            "MERCURY_TOKEN_BUDGET": "100000",
        }
    )
    run = client.post(f"/api/cases/{MAIN}/analyze", headers=headers("agent")).json()
    first = subprocess.run(
        [sys.executable, "-m", "mercury.worker", "--once"],
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert first.returncode == 0, first.stderr
    detail = client.get(f"/api/cases/{MAIN}", headers=headers()).json()
    assert detail["runs"][-1]["status"] == "waiting"
    before_calls = detail["runs"][-1]["calls"]
    # New processes repeatedly read the same durable interrupt without consuming budget.
    for _ in range(2):
        with db.session(CTX, write=True) as s:
            enqueue(s, CTX, "run", run["id"], "wake:" + uid())
        result = subprocess.run(
            [sys.executable, "-m", "mercury.worker", "--once"],
            env=env,
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert result.returncode == 0
    with db.session(CTX) as s:
        assert s.get(Run, run["id"]).calls == before_calls
    authorize(client, headers, db, detail["plans"][-1])
    second = subprocess.run(
        [sys.executable, "-m", "mercury.worker", "--once"],
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert second.returncode == 0, second.stderr
    with db.session(CTX) as s:
        assert s.get(Run, run["id"]).status == "completed"


def test_policy_withdrawn_after_approval_blocks_dispatch(client, db, headers):
    plan = make_plan(client, headers)
    authorize(client, headers, db, plan)
    with db.session(CTX, write=True) as s:
        s.execute(update(Policy).where(Policy.id == plan["citations"][0]).values(status="withdrawn"))
    response = execute(client, headers, plan)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "policy_changed"


def test_notification_dedup_and_claim_independent_of_customer_case(client, db, headers):
    detail = client.get(f"/api/cases/{MAIN}", headers=headers("agent")).json()
    notice = {"version": detail["version"], "body": "目前正在核实订单，尚未安排补发。"}
    first = client.post(f"/api/cases/{MAIN}/notifications", headers=headers("agent"), json=notice)
    second = client.post(f"/api/cases/{MAIN}/notifications", headers=headers("agent"), json=notice)
    assert first.json()["id"] == second.json()["id"]
    drain(db)
    with db.session(CTX) as s:
        saved = s.get(Notification, first.json()["id"])
        assert saved.status == "delivered" and saved.receipt["source"] == "synthetic"
    claim = client.post(
        f"/api/cases/{MAIN}/claims",
        headers=headers("ops"),
        json={"counterparty": "合成承运商", "amount_minor": 1200, "evidence": "破损材料与运输记录关联"},
    ).json()
    for status in ("submitted", "accepted"):
        claim = client.patch(
            f"/api/claims/{claim['id']}",
            headers=headers("ops"),
            json={"version": claim["version"], "status": status},
        ).json()
    response = client.patch(
        f"/api/claims/{claim['id']}",
        headers=headers("ops"),
        json={"version": claim["version"], "status": "settled"},
    )
    assert response.status_code == 409
    assert client.get(f"/api/cases/{MAIN}", headers=headers()).json()["status"] == "open"


def test_notification_requires_current_reviewed_case_version(client, db, headers):
    detail = client.get(f"/api/cases/{MAIN}", headers=headers("agent")).json()
    notice = {"version": detail["version"], "body": detail["notification_suggestion"]}
    make_plan(client, headers)
    stale = client.post(f"/api/cases/{MAIN}/notifications", headers=headers("agent"), json=notice)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"
    with db.session(CTX) as s:
        assert s.scalar(select(Notification).where(Notification.case_id == MAIN)) is None
        assert s.scalar(select(Job).where(Job.kind == "notification")) is None
    current = client.get(f"/api/cases/{MAIN}", headers=headers("agent")).json()
    assert current["notification_suggestion"] != detail["notification_suggestion"]
    reviewed = {"version": current["version"], "body": current["notification_suggestion"]}
    accepted = client.post(f"/api/cases/{MAIN}/notifications", headers=headers("agent"), json=reviewed)
    assert accepted.status_code == 202
    assert accepted.json()["body"] == reviewed["body"]


def test_improvement_requires_review_sample_and_authorized_evidence(client, headers):
    payload = {
        "title": "核查包装保护措施",
        "hypothesis": "包装缓冲可能不足，需要依据样本复查",
        "case_ids": [MAIN],
        "due_at": time.time() + 86400,
    }
    assert client.post("/api/improvements", headers=headers("outsider"), json=payload).status_code == 403
    item = client.post("/api/improvements", headers=headers("ops"), json=payload).json()
    review = {
        "version": item["version"],
        "measure": "调整包装并记录观察样本",
        "sample_size": 2,
        "observation": "当前仅收集两个样本，不能确认改善结论。",
        "close": True,
    }
    assert (
        client.patch(f"/api/improvements/{item['id']}", headers=headers("ops"), json=review).status_code
        == 409
    )
    review["close"] = False
    assert (
        client.patch(f"/api/improvements/{item['id']}", headers=headers("ops"), json=review).json()["status"]
        == "reviewing"
    )


def test_unknown_not_found_never_resubmits(client, db, headers):
    plan = make_plan(client, headers)
    authorize(client, headers, db, plan)
    action = execute(client, headers, plan).json()
    with db.session(CTX, write=True) as s:
        s.execute(update(Action).where(Action.id == action["id"]).values(status="unknown"))
    result = dispatch_action(db, CTX, action["id"])
    assert result["status"] == "unknown"
    from mercury.models import ProviderRequest

    with db.session(CTX) as s:
        assert not s.scalar(select(ProviderRequest))


def test_budget_is_durable_and_no_silent_reset(client, db, headers):
    run = client.post(f"/api/cases/{MAIN}/analyze", headers=headers()).json()
    with db.session(CTX, write=True) as s:
        s.execute(update(Run).where(Run.id == run["id"]).values(calls=db.settings.max_model_calls))
    assert process_one(db)
    with db.session(CTX) as s:
        saved = s.get(Run, run["id"])
        assert saved.status == "failed" and saved.error == "budget_exhausted"
        assert saved.calls == db.settings.max_model_calls
        assert s.scalar(select(Job).where(Job.target_id == run["id"])).status == "dead"


def test_separate_approver_executor_and_original_receipt_after_expiry(client, db, headers):
    plan = make_plan(client, headers)
    client.post(
        f"/api/cases/{MAIN}/choice",
        headers=headers("customer"),
        json={"plan_id": plan["id"], "accepted": True},
    )
    client.post(
        f"/api/cases/{MAIN}/approvals",
        headers=headers(),
        json={"plan_id": plan["id"], "decision": "approved", "reason": "合成审批已核对"},
    )
    response = client.post(
        f"/api/cases/{MAIN}/actions",
        headers=headers(),
        json={"plan_id": plan["id"], "business_action_id": uid()},
    )
    assert response.status_code == 403
    action = execute(client, headers, plan).json()
    drain(db)
    from mercury.models import Plan

    with db.session(CTX, write=True) as s:
        s.execute(update(Plan).where(Plan.id == plan["id"]).values(expires_at=0))
    assert execute(client, headers, plan).json()["id"] == action["id"]


def test_new_plan_cannot_repeat_consumed_entitlement(client, db, headers):
    plan = make_plan(client, headers)
    authorize(client, headers, db, plan)
    action = execute(client, headers, plan).json()
    drain(db)
    event(client, headers, action["id"], "shipped")
    event(client, headers, action["id"], "delivered", 2)
    next_plan = make_plan(client, headers)
    authorize(client, headers, db, next_plan)
    response = execute(client, headers, next_plan)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_remedy"
