"""OIDC authorization-code + PKCE; browser cookies contain only revocable random handles."""

import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import httpx
import jwt
from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy import delete, select

from mercury.errors import DomainError, require
from mercury.models import BrowserSession, LoginTransaction, RateBucket, now


def hashed(value):
    return hashlib.sha256(value.encode()).hexdigest()


def cipher(settings):
    return MultiFernet([Fernet(key.get_secret_value().encode()) for key in settings.session_encryption_keys])


def rate_limit(db, key, limit, seconds=60):
    """Atomic shared window, also shared by separate API processes; no raw account/IP persisted."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    stamp = now()
    identity = hashed(f"{key}:{int(stamp // seconds)}")
    insert = pg_insert if db.engine.dialect.name == "postgresql" else sqlite_insert
    with db.session(write=True) as s:
        statement = insert(RateBucket).values(id=identity, hits=1, expires_at=stamp + seconds * 2)
        count = s.scalar(
            statement.on_conflict_do_update(
                index_elements=[RateBucket.id], set_={"hits": RateBucket.hits + 1}
            ).returning(RateBucket.hits)
        )
        s.execute(delete(RateBucket).where(RateBucket.expires_at < stamp))
    require(count <= limit, "rate_limited", "请求过于频繁，请稍后再试", 429)


def begin_login(db, tenant_id, store_id):
    settings = db.settings
    require(
        settings.auth_mode == "oidc" and settings.oidc_client_id, "sso_unconfigured", "企业登录尚未配置", 503
    )
    state, binding, verifier, nonce = [secrets.token_urlsafe(32) for _ in range(4)]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    with db.session(write=True) as s:
        s.execute(delete(LoginTransaction).where(LoginTransaction.expires_at < now()))
        s.add(
            LoginTransaction(
                id=hashed(state),
                binding_hash=hashed(binding),
                verifier=cipher(settings).encrypt(verifier.encode()).decode(),
                nonce=nonce,
                tenant_id=tenant_id,
                store_id=store_id,
                expires_at=now() + 300,
            )
        )
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.oidc_client_id,
            "redirect_uri": settings.oidc_redirect_uri,
            "scope": "openid profile offline_access",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return {"authorization_url": settings.oidc_authorization_url + "?" + query, "binding": binding}


def token_request(settings, data):
    data = {**data, "client_id": settings.oidc_client_id}
    auth = None
    if settings.oidc_client_secret.get_secret_value():
        auth = (settings.oidc_client_id, settings.oidc_client_secret.get_secret_value())
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            response = client.post(
                settings.oidc_token_url, data=data, auth=auth, headers={"Accept": "application/json"}
            )
            response.raise_for_status()
            result = response.json()
            require(
                isinstance(result.get("expires_in"), int) and 0 < result["expires_in"] <= 86400,
                "invalid_provider_response",
                "身份服务返回的有效期无效",
                502,
            )
            require(
                result.get("token_type", "").lower() == "bearer" and result.get("access_token"),
                "invalid_provider_response",
                "身份服务返回的令牌无效",
                502,
            )
            return result
    except (httpx.HTTPError, ValueError, TypeError):
        raise DomainError("identity_unavailable", "身份服务暂不可用，请重新登录", 502) from None


def verify_id_token(settings, jwks, result, nonce=None, subject=None):
    try:
        token = result["id_token"]
        key = jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            issuer=settings.oidc_issuer,
            audience=settings.oidc_client_id,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
        if nonce is not None:
            require(
                secrets.compare_digest(str(claims.get("nonce", "")), nonce),
                "invalid_identity",
                "身份校验失败",
                401,
            )
        if subject is not None:
            require(claims["sub"] == subject, "invalid_identity", "身份校验失败", 401)
        if isinstance(claims["aud"], list) and len(claims["aud"]) > 1:
            require(claims.get("azp") == settings.oidc_client_id, "invalid_identity", "身份校验失败", 401)
        if "at_hash" in claims:
            digest = hashlib.sha256(result["access_token"].encode()).digest()[:16]
            expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            require(
                secrets.compare_digest(str(claims["at_hash"]), expected),
                "invalid_identity",
                "身份校验失败",
                401,
            )
        return claims
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        raise DomainError("invalid_identity", "身份令牌校验失败", 401) from None


def finish_login(db, jwks, state, binding, code):
    from mercury.auth import load_identity

    with db.session(write=True) as s:
        transaction = s.scalar(
            select(LoginTransaction).where(LoginTransaction.id == hashed(state)).with_for_update()
        )
        require(
            transaction
            and not transaction.consumed
            and transaction.expires_at > now()
            and secrets.compare_digest(transaction.binding_hash, hashed(binding)),
            "invalid_login_state",
            "登录请求已失效，请重新发起",
            401,
        )
        transaction.consumed = True
    result = token_request(
        db.settings,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": db.settings.oidc_redirect_uri,
            "code_verifier": cipher(db.settings).decrypt(transaction.verifier.encode()).decode(),
        },
    )
    claims = verify_id_token(db.settings, jwks, result, nonce=transaction.nonce)
    ctx = load_identity(db, claims["sub"], transaction.tenant_id, transaction.store_id)
    handle = "mc_" + secrets.token_urlsafe(48)
    with db.session(write=True) as s:
        s.add(
            BrowserSession(
                id=hashed(handle),
                user_id=ctx.user_id,
                tenant_id=ctx.tenant_id,
                store_id=ctx.store_id,
                expires_at=now() + db.settings.session_hours * 3600,
                provider_expires_at=now() + result["expires_in"],
                refresh_ciphertext=cipher(db.settings)
                .encrypt(result.get("refresh_token", "").encode())
                .decode(),
            )
        )
    return {"access_token": handle, "expires_in": db.settings.session_hours * 3600}


def resolve_session(db, jwks, handle):
    from mercury.auth import load_identity

    failure = None
    with db.session(write=True) as s:
        session = s.scalar(
            select(BrowserSession).where(BrowserSession.id == hashed(handle)).with_for_update()
        )
        require(
            session
            and not session.revoked
            and session.expires_at > now()
            and session.last_seen_at > now() - db.settings.session_idle_minutes * 60,
            "unauthorized",
            "会话已失效，请重新登录",
            401,
        )
        if session.provider_expires_at <= now() + 15:
            try:
                refresh = cipher(db.settings).decrypt(session.refresh_ciphertext.encode()).decode()
                require(bool(refresh), "unauthorized", "会话需要重新登录", 401)
                result = token_request(db.settings, {"grant_type": "refresh_token", "refresh_token": refresh})
                if result.get("id_token"):
                    verify_id_token(db.settings, jwks, result, subject=session.user_id)
                session.provider_expires_at = now() + result["expires_in"]
                session.refresh_ciphertext = (
                    cipher(db.settings).encrypt(result.get("refresh_token", refresh).encode()).decode()
                )
            except Exception:
                # A refresh with an unknown result must not reuse the old rotating refresh token.
                session.revoked = True
                session.refresh_ciphertext = ""
                failure = DomainError("unauthorized", "身份会话需要重新登录", 401)
        session.last_seen_at = time.time()
    if failure:
        raise failure
    return load_identity(db, session.user_id, session.tenant_id, session.store_id)


def revoke_session(db, handle):
    with db.session(write=True) as s:
        session = s.get(BrowserSession, hashed(handle))
        if session:
            session.revoked = True
            session.refresh_ciphertext = ""
