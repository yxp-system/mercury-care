from concurrent.futures import ThreadPoolExecutor

import pytest
from mercury.auth import AuthContext
from mercury.models import (
    Action,
    Approval,
    Case,
    Inventory,
    Membership,
    Order,
    OrderLine,
    ProviderRequest,
    Run,
    uid,
)
from mercury.schemas import ExecuteInput
from mercury.services import dispatch_action, prepare_action
from mercury.worker import process_one
from sqlalchemy import select, update

MAIN = "CASE-M-10482"
CTX = AuthContext("demo", "northstar", "uk", frozenset({"agent", "approver", "executor"}))


def drain(db):
    for _ in range(30):
        if not process_one(db):
            return
    raise AssertionError("Worker did not drain")


def make_plan(client, headers, case_id=MAIN, kind=None, amount=0, address=""):
    auth = headers()
    case = client.get(f"/api/cases/{case_id}", headers=auth).json()
    rules = client.get(f"/api/cases/{case_id}/rules", headers=auth).json()
    response = client.post(
        f"/api/cases/{case_id}/plans",
        headers=auth,
        json={
            "version": case["version"],
            "kind": kind or case["kind"],
            "quantity": 1,
            "amount_minor": amount,
            "address": address,
            "rationale": "基于本地合成业务记录与政策",
            "citations": [x["id"] for x in rules["citations"]],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def authorize(client, headers, db, plan, case_id=MAIN):
    with db.session(CTX, write=True) as s:
        case = s.get(Case, case_id)
        s.execute(
            update(Membership).where(Membership.user_id == "customer").values(customer_id=case.customer_id)
        )
    response = client.post(
        f"/api/cases/{case_id}/choice",
        headers=headers("customer"),
        json={"plan_id": plan["id"], "accepted": True},
    )
    assert response.status_code == 200, response.text
    response = client.post(
        f"/api/cases/{case_id}/approvals",
        headers=headers("manager"),
        json={"plan_id": plan["id"], "decision": "approved", "reason": "核对依据后批准"},
    )
    assert response.status_code == 200, response.text


def execute(client, headers, plan, case_id=MAIN, action_id=None):
    return client.post(
        f"/api/cases/{case_id}/actions",
        headers=headers("executor"),
        json={"plan_id": plan["id"], "business_action_id": action_id or uid()},
    )


def event(client, headers, action_id, kind, sequence=1, source_id=None):
    return client.post(
        f"/api/demo/actions/{action_id}/events",
        headers=headers("executor"),
        json={
            "source_id": source_id or uid(),
            "sequence": sequence,
            "kind": kind,
            "evidence": "受控合成系统的可核对回执",
        },
    )


def test_full_graph_replacement_fulfillment_and_closure(client, db, headers):
    response = client.post(f"/api/cases/{MAIN}/analyze", headers=headers("agent"))
    assert response.status_code == 202
    run_id = response.json()["id"]
    drain(db)
    detail = client.get(f"/api/cases/{MAIN}", headers=headers()).json()
    assert detail["runs"][-1]["status"] == "waiting", detail["runs"]
    assert {x["role"] for x in detail["runs"][-1]["steps"]} == {
        "supervisor",
        "facts",
        "rules",
        "proposal",
        "reply",
    }
    plan = detail["plans"][-1]
    authorize(client, headers, db, plan)
    drain(db)
    with db.session(CTX) as s:
        assert s.get(Run, run_id).status == "completed"
    action = execute(client, headers, plan).json()
    drain(db)
    inventory = client.get("/api/inventory", headers=headers()).json()
    stock = next(row for row in inventory if row["sku"] == "CUP-WHITE")
    assert (stock["on_hand"], stock["reserved"], stock["available"]) == (3, 1, 2)
    assert event(client, headers, action["id"], "shipped").status_code == 200
    assert event(client, headers, action["id"], "delivered", 2).status_code == 200
    stock = next(
        row for row in client.get("/api/inventory", headers=headers()).json() if row["sku"] == "CUP-WHITE"
    )
    assert (stock["on_hand"], stock["reserved"], stock["available"], stock["shipped"]) == (2, 0, 2, 1)
    detail = client.get(f"/api/cases/{MAIN}", headers=headers()).json()
    # Receipt is not customer resolution.
    response = client.post(
        f"/api/cases/{MAIN}/close",
        headers=headers(),
        json={"version": detail["version"], "reason": "已收到签收"},
    )
    assert response.status_code == 409
    response = client.post(
        f"/api/cases/{MAIN}/resolve",
        headers=headers("customer"),
        json={"version": detail["version"], "reason": "补发杯子完好，问题已解决"},
    )
    assert response.status_code == 200, response.text
    response = client.post(
        f"/api/cases/{MAIN}/close",
        headers=headers(),
        json={"version": response.json()["version"], "reason": "客户已确认解决"},
    )
    assert response.json()["status"] == "closed"
    response = client.post(
        f"/api/cases/{MAIN}/reopen",
        headers=headers(),
        json={"version": response.json()["version"], "reason": "客户提供新的问题材料"},
    )
    assert response.json()["status"] == "open"


def test_approval_and_customer_choice_are_separate(client, headers):
    plan = make_plan(client, headers)
    assert execute(client, headers, plan).status_code == 409
    response = client.post(
        f"/api/cases/{MAIN}/approvals",
        headers=headers("manager"),
        json={"plan_id": plan["id"], "decision": "approved", "reason": "声称客户已确认"},
    )
    assert response.status_code == 409
    assert (
        client.post(
            f"/api/cases/{MAIN}/choice",
            headers=headers("agent"),
            json={"plan_id": plan["id"], "accepted": True},
        ).status_code
        == 403
    )


def test_repeat_and_new_uuid_reuse_original_action(client, db, headers):
    plan = make_plan(client, headers)
    authorize(client, headers, db, plan)
    first = execute(client, headers, plan).json()
    for _ in range(30):
        response = execute(client, headers, plan)
        assert response.status_code == 202
        assert response.json()["id"] == first["id"]
    with db.session(CTX) as s:
        assert len(s.scalars(select(Action)).all()) == 1
        assert (
            s.scalar(
                select(Inventory).where(Inventory.tenant_id == "northstar", Inventory.sku == "CUP-WHITE")
            ).reserved
            == 1
        )


def test_unknown_receipt_reconciles_without_second_request(client, db, headers):
    plan = make_plan(client, headers)
    authorize(client, headers, db, plan)
    action = execute(client, headers, plan).json()
    result = dispatch_action(db, CTX, action["id"], lose_response=True)
    assert result["status"] == "unknown"
    with db.session(CTX) as s:
        assert s.get(Action, action["id"]).status == "unknown"
    response = client.post(f"/api/actions/{action['id']}/reconcile", headers=headers("executor"))
    assert response.json()["status"] == "accepted"
    drain(db)
    with db.session(CTX) as s:
        assert len(s.scalars(select(ProviderRequest)).all()) == 1


def test_old_plan_and_revoked_approver_cannot_execute(client, db, headers):
    first = make_plan(client, headers)
    authorize(client, headers, db, first)
    second = make_plan(client, headers)
    assert execute(client, headers, first).status_code == 409
    authorize(client, headers, db, second)
    with db.session(write=True) as s:
        s.execute(update(Membership).where(Membership.user_id == "manager").values(active=False))
    assert execute(client, headers, second).status_code == 401


def test_insufficient_inventory_is_rechecked(client, db, headers):
    plan = make_plan(client, headers)
    authorize(client, headers, db, plan)
    with db.session(CTX, write=True) as s:
        s.execute(
            update(Inventory)
            .where(Inventory.tenant_id == "northstar", Inventory.sku == "CUP-WHITE")
            .values(on_hand=0)
        )
    assert execute(client, headers, plan).json()["error"]["code"] == "insufficient_stock"


def test_concurrent_execution_has_one_reservation(client, db, headers):
    plan = make_plan(client, headers)
    authorize(client, headers, db, plan)

    def call(_):
        return prepare_action(db, CTX, MAIN, ExecuteInput(plan_id=plan["id"], business_action_id=uid()))["id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(call, range(8)))
    assert len(set(results)) == 1
    with db.session(CTX) as s:
        assert (
            s.scalar(
                select(Inventory).where(Inventory.tenant_id == "northstar", Inventory.sku == "CUP-WHITE")
            ).reserved
            == 1
        )


@pytest.mark.parametrize(
    "case_id,kind,events",
    [
        ("CASE-M-10483", "refund", ["succeeded"]),
        ("CASE-M-10484", "return", ["received", "inspected"]),
        ("CASE-M-10485", "delivery", ["investigated"]),
        ("CASE-M-10486", "missing", ["shipped", "delivered"]),
        ("CASE-M-10487", "wrong", ["shipped", "delivered"]),
        ("CASE-M-10488", "cancellation", ["succeeded"]),
        ("CASE-M-10489", "address", ["succeeded"]),
    ],
)
def test_branch_lifecycle(client, db, headers, case_id, kind, events):
    plan = make_plan(
        client,
        headers,
        case_id,
        amount=1200 if kind == "refund" else 0,
        address="New synthetic address, London" if kind == "address" else "",
    )
    authorize(client, headers, db, plan, case_id)
    response = execute(client, headers, plan, case_id)
    assert response.status_code == 202, response.text
    action = response.json()
    drain(db)
    for sequence, transition in enumerate(events, 1):
        response = event(client, headers, action["id"], transition, sequence)
        assert response.status_code == 200, response.text
    with db.session(CTX) as s:
        order = s.get(Order, case_id.removeprefix("CASE-"))
        if kind == "refund":
            assert (order.refunded_minor, order.refund_reserved_minor) == (1200, 0)
        if kind == "cancellation":
            assert order.status == "cancelled"
        if kind == "address":
            assert order.address == "New synthetic address, London"


def test_duplicate_and_out_of_order_events(client, db, headers):
    plan = make_plan(client, headers)
    authorize(client, headers, db, plan)
    action = execute(client, headers, plan).json()
    drain(db)
    assert event(client, headers, action["id"], "delivered", 2).status_code == 409
    source_id = uid()
    first = event(client, headers, action["id"], "shipped", 1, source_id)
    assert first.status_code == 200
    for _ in range(30):
        assert event(client, headers, action["id"], "shipped", 1, source_id).json() == first.json()
    assert event(client, headers, action["id"], "delivered", 2, source_id).status_code == 409


def test_refund_unknown_reserves_money_and_failure_releases(client, db, headers):
    case_id = "CASE-M-10483"
    plan = make_plan(client, headers, case_id, amount=3200)
    authorize(client, headers, db, plan, case_id)
    action = execute(client, headers, plan, case_id).json()
    dispatch_action(db, CTX, action["id"], lose_response=True)
    with db.session(CTX) as s:
        assert s.get(Order, "M-10483").refund_reserved_minor == 3200
        assert s.get(OrderLine, "M-10483-1").remedy_minor == 3200
    assert event(client, headers, action["id"], "failed").status_code == 409
    client.post(f"/api/actions/{action['id']}/reconcile", headers=headers("executor"))
    assert event(client, headers, action["id"], "failed").status_code == 200
    with db.session(CTX) as s:
        assert s.get(Order, "M-10483").refund_reserved_minor == 0
        assert s.get(OrderLine, "M-10483-1").remedy_minor == 0


def test_logistics_short_path_and_waiting_no_extra_calls(client, db, headers):
    run = client.post("/api/cases/CASE-M-10485/analyze", headers=headers()).json()
    drain(db)
    detail = client.get("/api/cases/CASE-M-10485", headers=headers()).json()
    assert {s["role"] for s in detail["runs"][-1]["steps"]} == {"supervisor", "facts", "reply"}
    assert detail["runs"][-1]["status"] == "completed"
    with db.session(CTX) as s:
        calls = s.get(Run, run["id"]).calls
    from mercury.workflow import run_workflow

    for _ in range(30):
        run_workflow(db, CTX, run["id"])
    with db.session(CTX) as s:
        assert s.get(Run, run["id"]).calls == calls


def test_inventory_release_does_not_reduce_on_hand(client, db, headers):
    plan = make_plan(client, headers)
    authorize(client, headers, db, plan)
    action = execute(client, headers, plan).json()
    drain(db)
    assert event(client, headers, action["id"], "cancelled").status_code == 200
    with db.session(CTX) as s:
        stock = s.scalar(
            select(Inventory).where(Inventory.tenant_id == "northstar", Inventory.sku == "CUP-WHITE")
        )
        assert (stock.on_hand, stock.reserved) == (3, 0)


def test_expired_approval_and_same_id_different_plan(client, db, headers):
    plan = make_plan(client, headers)
    authorize(client, headers, db, plan)
    with db.session(CTX, write=True) as s:
        s.execute(update(Approval).where(Approval.plan_id == plan["id"]).values(expires_at=0))
    assert execute(client, headers, plan).status_code == 403
    with db.session(CTX, write=True) as s:
        s.execute(update(Approval).where(Approval.plan_id == plan["id"]).values(expires_at=9999999999))
    action = execute(client, headers, plan).json()
    other = make_plan(client, headers, "CASE-M-10483", amount=100)
    authorize(client, headers, db, other, "CASE-M-10483")
    response = execute(client, headers, other, "CASE-M-10483", action["id"])
    assert response.status_code == 409
