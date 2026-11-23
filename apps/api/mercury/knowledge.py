"""Versioned import jobs; reviewed publication activates a complete document atomically."""

import re

from sqlalchemy import func, select

from mercury.auth import get_case, load_identity
from mercury.db import scoped
from mercury.errors import require
from mercury.models import KnowledgeDocument, Membership, Order, Policy, uid
from mercury.services import audit, digest, embed, enqueue, owned, record_dict, scope_values


def can_read(policy, ctx):
    return (not policy.allowed_roles and not policy.allowed_users) or bool(
        set(policy.allowed_roles).intersection(ctx.roles) or ctx.user_id in policy.allowed_users
    )


def import_document(db, ctx, payload):
    ctx.require_role("knowledge")
    require(payload.effective_until > payload.effective_from, "invalid_period", "有效期无效", 422)
    with db.session(ctx, write=True) as s:
        s.scalar(
            select(Membership)
            .where(*scoped(Membership, ctx))
            .order_by(Membership.id)
            .limit(1)
            .with_for_update()
        )
        version = (
            s.scalar(
                select(func.max(KnowledgeDocument.version)).where(
                    *scoped(KnowledgeDocument, ctx), KnowledgeDocument.family == payload.family
                )
            )
            or 0
        ) + 1
        document = KnowledgeDocument(
            **scope_values(ctx),
            id=uid(),
            family=payload.family,
            version=version,
            title=payload.title,
            content=payload.content,
            content_hash=digest(payload.content),
            author_id=ctx.user_id,
            metadata_json=payload.model_dump(exclude={"content", "family", "title"}),
        )
        s.add(document)
        s.flush()
        enqueue(s, ctx, "knowledge", document.id, "knowledge:" + document.id)
        audit(s, ctx, "knowledge.imported", document.id, {"content_hash": document.content_hash})
        return record_dict(document) | {"content": None}


def chunks(content, title):
    section, lines = title, []
    sections = []
    for line in content.splitlines():
        if re.match(r"^#{1,6}\s+", line):
            if lines:
                sections.append((section, "\n".join(lines)))
            section, lines = re.sub(r"^#+\s+", "", line)[:200], []
        else:
            lines.append(line)
    if lines:
        sections.append((section, "\n".join(lines)))
    result = []
    for section, body in sections:
        for offset in range(0, len(body), 1400):
            value = body[offset : offset + 1600].strip()
            if value:
                result.append((section, value))
    require(0 < len(result) <= 160, "import_size", "文档为空或分块数量超限", 422)
    return result


def process_import(db, ctx, document_id):
    with db.session(ctx) as s:
        document = owned(s, KnowledgeDocument, ctx, document_id)
        if document.status in {"draft", "published", "withdrawn"}:
            return
        actor = load_identity(db, document.author_id, ctx.tenant_id, ctx.store_id)
        actor.require_role("knowledge")
        metadata = document.metadata_json
        pieces = chunks(document.content, document.title)
    vectors = (
        [embed(db.settings, body) for _, body in pieces]
        if db.settings.embedding_model
        else [None] * len(pieces)
    )
    with db.session(ctx, write=True) as s:
        document = owned(s, KnowledgeDocument, ctx, document_id, True)
        if document.status != "queued":
            return
        for index, ((section, body), vector) in enumerate(zip(pieces, vectors, strict=True)):
            s.add(
                Policy(
                    **scope_values(ctx),
                    family=digest([document.family, index])[:64],
                    version=document.version,
                    title=document.title,
                    body=body,
                    section=section,
                    document_id=document.id,
                    kind=metadata["kind"],
                    region=metadata["region"],
                    effective_from=metadata["effective_from"],
                    effective_until=metadata["effective_until"],
                    source="uploaded",
                    author_id=document.author_id,
                    content_hash=digest(body),
                    embedding=vector,
                    embedding_model=db.settings.embedding_model or None,
                    allowed_roles=metadata["allowed_roles"],
                    allowed_users=metadata["allowed_users"],
                )
            )
        document.status = "draft"
        audit(
            s,
            ctx,
            "knowledge.indexed",
            document.id,
            {"chunks": len(pieces), "model": db.settings.embedding_model or None},
        )


def publish_document(db, ctx, document_id, payload, withdraw=False):
    ctx.require_role("knowledge_reviewer")
    with db.session(ctx, write=True) as s:
        document = owned(s, KnowledgeDocument, ctx, document_id, True)
        s.scalar(
            select(Membership)
            .where(*scoped(Membership, ctx))
            .order_by(Membership.id)
            .limit(1)
            .with_for_update()
        )
        require(document.author_id != ctx.user_id, "separation_of_duties", "需另一位审核人审核", 403)
        require(document.status in {"draft", "published"}, "index_not_ready", "文档索引尚未完成")
        if not withdraw:
            for case_id in payload.validation_cases:
                case = get_case(s, ctx, case_id)
                order = owned(s, Order, ctx, case.order_id)
                meta = document.metadata_json
                require(
                    case.kind == meta["kind"]
                    and order.region == meta["region"]
                    and meta["effective_from"] <= order.created_at < meta["effective_until"],
                    "validation_mismatch",
                    "验证案例不满足规则范围",
                )
            older = list(
                s.scalars(
                    select(KnowledgeDocument)
                    .where(
                        *scoped(KnowledgeDocument, ctx),
                        KnowledgeDocument.family == document.family,
                        KnowledgeDocument.status == "published",
                        KnowledgeDocument.id != document.id,
                    )
                    .with_for_update()
                )
            )
            for previous in older:
                previous.status = "withdrawn"
                for policy in s.scalars(
                    select(Policy).where(*scoped(Policy, ctx), Policy.document_id == previous.id)
                ):
                    policy.status = "withdrawn"
        document.status, document.reviewer_id = ("withdrawn" if withdraw else "published"), ctx.user_id
        for policy in s.scalars(
            select(Policy).where(*scoped(Policy, ctx), Policy.document_id == document.id)
        ):
            policy.status, policy.reviewer_id, policy.validation = (
                document.status,
                ctx.user_id,
                payload.model_dump(),
            )
        audit(s, ctx, "knowledge." + document.status, document.id)
        return record_dict(document) | {"content": None}
