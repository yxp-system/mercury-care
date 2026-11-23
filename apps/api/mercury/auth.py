import time
from dataclasses import dataclass

import jwt
from fastapi import Header, Request
from jwt import PyJWKClient
from pwdlib import PasswordHash
from sqlalchemy import select

from mercury.db import scoped
from mercury.errors import DomainError, require
from mercury.models import Case, Membership, User

passwords = PasswordHash.recommended()


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    tenant_id: str
    store_id: str
    roles: frozenset[str]
    customer_id: str | None = None

    def require_role(self, *allowed):
        require(bool(self.roles.intersection(allowed)), "forbidden", "当前身份没有此操作权限", 403)


def load_identity(db, user_id, tenant_id, store_id):
    with db.session() as session:
        user = session.get(User, user_id)
        membership = session.scalar(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.tenant_id == tenant_id,
                Membership.store_id == store_id,
                Membership.active.is_(True),
            )
        )
        require(user and user.active and membership, "unauthorized", "身份或店铺授权已失效", 401)
        return AuthContext(user.id, tenant_id, store_id, frozenset(membership.roles), membership.customer_id)


def login(db, username, password, tenant_id, store_id):
    settings = db.settings
    require(
        settings.auth_mode == "local" and settings.env in {"demo", "test"},
        "local_login_disabled",
        "此环境仅接受企业身份令牌",
        403,
    )
    with db.session() as session:
        user = session.scalar(select(User).where(User.username == username))
        valid = user and user.active and passwords.verify(password, user.password_hash)
        require(valid, "invalid_credentials", "账号或密码不正确", 401)
        ctx = load_identity(db, user.id, tenant_id, store_id)
        issued = int(time.time())
        token = jwt.encode(
            {
                "sub": ctx.user_id,
                "tenant_id": ctx.tenant_id,
                "store_id": ctx.store_id,
                "iat": issued,
                "exp": issued + settings.access_token_minutes * 60,
                "iss": "mercury-local",
                "aud": "mercury-api",
            },
            settings.jwt_secret.get_secret_value(),
            algorithm="HS256",
        )
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": settings.access_token_minutes * 60,
        }


def authenticate(
    request: Request,
    authorization: str = Header(default=""),
    x_tenant_id: str = Header(default=""),
    x_store_id: str = Header(default=""),
):
    settings = request.app.state.settings
    require(authorization.startswith("Bearer "), "unauthorized", "请先登录", 401)
    token = authorization.removeprefix("Bearer ")
    try:
        if settings.auth_mode == "local":
            claims = jwt.decode(
                token,
                settings.jwt_secret.get_secret_value(),
                algorithms=["HS256"],
                issuer="mercury-local",
                audience="mercury-api",
                options={"require": ["exp", "iat", "sub", "tenant_id", "store_id"]},
            )
            tenant_id, store_id = claims["tenant_id"], claims["store_id"]
        else:
            client: PyJWKClient = request.app.state.jwks
            key = client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                issuer=settings.oidc_issuer,
                audience=settings.oidc_audience,
                options={"require": ["exp", "iat", "sub"]},
            )
            tenant_id, store_id = x_tenant_id, x_store_id
        # Role claims are never trusted; membership and revocation are checked every request.
        return load_identity(request.app.state.db, claims["sub"], tenant_id, store_id)
    except (jwt.PyJWTError, ValueError, KeyError):
        raise DomainError("unauthorized", "令牌无效或已过期", 401) from None


def get_case(session, ctx, case_id, lock=False):
    query = select(Case).where(Case.id == case_id, *scoped(Case, ctx))
    if ctx.customer_id:
        query = query.where(Case.customer_id == ctx.customer_id)
    if lock:
        query = query.with_for_update()
    case = session.scalar(query)
    require(case is not None, "not_found", "记录不存在或无权访问", 404)
    return case
