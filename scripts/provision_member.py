"""Explicit administrative OIDC subject provisioning, performed with directory-owner credentials."""

import argparse

from mercury.config import Settings
from mercury.db import Database
from mercury.models import Membership, User
from sqlalchemy import select

parser = argparse.ArgumentParser()
parser.add_argument("subject", help="Exact verified subject from the configured OIDC issuer")
parser.add_argument("--display-name", required=True)
parser.add_argument("--tenant", required=True)
parser.add_argument("--store", required=True)
parser.add_argument("--roles", required=True, help="Comma-separated explicitly assigned roles")
parser.add_argument("--customer-id")
parser.add_argument("--revoke", action="store_true")
args = parser.parse_args()
roles = set(args.roles.split(","))
if not roles.issubset(
    {"agent", "approver", "executor", "operations", "knowledge", "knowledge_reviewer", "customer"}
):
    raise SystemExit("Unknown role")
if ("customer" in roles) != bool(args.customer_id) or ("customer" in roles and len(roles) != 1):
    raise SystemExit("Customer identities require a customer ID and cannot hold staff roles")
db = Database(Settings())
if db.settings.auth_mode != "oidc":
    raise SystemExit("This command provisions only the configured enterprise issuer")
with db.session(write=True) as session:
    user = session.get(User, args.subject)
    if not user:
        user = User(id=args.subject, username=args.subject, display_name=args.display_name)
        session.add(user)
    membership = session.scalar(
        select(Membership)
        .where(
            Membership.user_id == args.subject,
            Membership.tenant_id == args.tenant,
            Membership.store_id == args.store,
        )
        .with_for_update()
    )
    if membership is None:
        membership = Membership(user_id=args.subject, tenant_id=args.tenant, store_id=args.store)
        session.add(membership)
    membership.roles, membership.customer_id, membership.active = (
        sorted(roles),
        args.customer_id,
        not args.revoke,
    )
print("Member assignment saved; authorization changes are checked on the next request.")
