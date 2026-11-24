from sqlalchemy import select

from mercury.auth import AuthContext, passwords
from mercury.errors import require
from mercury.models import Case, Inventory, Membership, Message, Order, OrderLine, Policy, User, now
from mercury.services import digest, scope_values

ACCOUNTS = {
    "demo": ("演示工作台", ["agent", "approver", "executor", "operations", "knowledge"]),
    "agent": ("林晓 · 客服", ["agent"]),
    "manager": ("周悦 · 审批", ["approver"]),
    "executor": ("陈越 · 执行", ["executor"]),
    "customer": ("小林 · 客户", ["customer"]),
    "ops": ("苏宁 · 运营", ["operations"]),
    "editor": ("知识编辑", ["knowledge"]),
    "reviewer": ("知识审核", ["knowledge_reviewer"]),
    "release-reviewer": ("上线验收审核", ["release_reviewer"]),
    "outsider": ("另一租户", ["agent"]),
}


def seed(db):
    require(db.settings.env in {"demo", "test"}, "seed_forbidden", "种子数据仅允许本地演示和测试", 403)
    password = db.settings.demo_password.get_secret_value()
    require(len(password) >= 10, "password_required", "请先配置演示密码，至少10字符")
    for username, (name, roles) in ACCOUNTS.items():
        with db.session(write=True) as s:
            if not s.get(User, username):
                s.add(
                    User(
                        id=username,
                        username=username,
                        display_name=name,
                        password_hash=passwords.hash(password),
                    )
                )
                s.flush()
                s.add(
                    Membership(
                        user_id=username,
                        tenant_id="other" if username == "outsider" else "northstar",
                        store_id="de" if username == "outsider" else "uk",
                        roles=roles,
                        customer_id="customer-lin" if username == "customer" else None,
                    )
                )
    catalogue = [
        ("replacement", "白瓷马克杯运输破损", "小林", "CUP-WHITE", "白瓷马克杯 · 350ml", 2400),
        ("refund", "申请退还未使用的商品款项", "Emma Wilson", "MUG-SAND", "砂岩咖啡杯", 3200),
        ("return", "商品尺寸不合适，咨询退货", "Oliver Smith", "BOWL-STONE", "陶瓷餐碗套装", 4800),
        ("delivery", "包裹物流停滞，尚未收到", "Amelia Brown", "VASE-GLASS", "透明玻璃花瓶", 5600),
        ("missing", "收到包裹后发现少一件", "Noah Taylor", "CUP-WHITE", "白瓷马克杯 · 350ml", 4800),
        ("wrong", "收到错误颜色，需要换货", "Isla Thomas", "PLATE-GREEN", "森林绿餐盘", 3800),
        ("cancellation", "出库前申请取消订单", "Leo Davis", "MUG-SAND", "砂岩咖啡杯", 3200),
        ("address", "订单地址填写有误", "Mia Evans", "VASE-GLASS", "透明玻璃花瓶", 5600),
    ]
    for tenant, store in [("northstar", "uk"), ("other", "de")]:
        ctx = AuthContext("seed", tenant, store, frozenset())
        with db.session(ctx, write=True) as s:
            for i, (kind, subject, name, sku, title, amount) in enumerate(catalogue):
                prefix = "" if tenant == "northstar" else "OTHER-"
                order_id = f"{prefix}M-{10482 + i}"
                case_id = "CASE-" + order_id
                if s.get(Order, order_id):
                    continue
                order = Order(
                    **scope_values(ctx),
                    id=order_id,
                    customer_id="customer-lin" if i == 0 else f"{prefix}customer-{i}",
                    customer_name=name,
                    paid_minor=amount,
                    address="Synthetic address · London, GB",
                    status="processing" if kind in {"address", "cancellation"} else "delivered",
                    created_at=now() - 86400 * (i + 1),
                    source_updated_at=now(),
                )
                s.add(order)
                s.flush()
                line = OrderLine(
                    **scope_values(ctx),
                    id=order_id + "-1",
                    order_id=order_id,
                    sku=sku,
                    title=title,
                    quantity=2 if kind == "missing" else 1,
                    total_minor=amount,
                )
                s.add(line)
                s.flush()
                if not s.scalar(
                    select(Inventory).where(
                        Inventory.tenant_id == tenant, Inventory.store_id == store, Inventory.sku == sku
                    )
                ):
                    s.add(Inventory(**scope_values(ctx), sku=sku, on_hand=3 if sku == "CUP-WHITE" else 12))
                case = Case(
                    **scope_values(ctx),
                    id=case_id,
                    order_id=order_id,
                    line_id=line.id,
                    customer_id=order.customer_id,
                    kind=kind,
                    subject=subject,
                    priority="high" if i in {0, 3} else "normal",
                    created_at=now() - i * 3600,
                )
                s.add(case)
                s.flush()
                s.add(
                    Message(
                        **scope_values(ctx),
                        case_id=case_id,
                        author_id=order.customer_id,
                        source_id="seed-" + case_id,
                        body="收到的白杯破损了，希望补发一只，谢谢。" if i == 0 else subject,
                    )
                )
            for kind, *_ in catalogue:
                policy_id = tenant + "-policy-" + kind
                if not s.get(Policy, policy_id):
                    body = (
                        f"【本地合成政策，非平台通用规则】{kind}：核验订单归属、商品、证据与既有处理权益；"
                        "处理方案须由客户确认及授权岗位审核，执行前复查资源和版本。"
                        "建单或受理不表示已履约，存在未知结果时核查原请求，客户确认解决后方可结案。"
                    )
                    s.add(
                        Policy(
                            **scope_values(ctx),
                            id=policy_id,
                            family="demo-" + kind,
                            version=1,
                            title={
                                "replacement": "破损补发与库存预留",
                                "refund": "退款额度与资金核对",
                                "return": "退货授权与质检",
                                "delivery": "物流异常调查",
                                "missing": "少件核验",
                                "wrong": "错件与换货",
                                "cancellation": "出库前取消",
                                "address": "改址确认",
                            }[kind],
                            body=body,
                            kind=kind,
                            status="published",
                            effective_from=now() - 366 * 86400,
                            effective_until=now() + 366 * 86400,
                            source="synthetic",
                            author_id="seed",
                            reviewer_id="seed-fixture",
                            validation={"source": "explicit_demo_fixture"},
                            content_hash=digest(body),
                        )
                    )
