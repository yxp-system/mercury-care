from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Kind(StrEnum):
    replacement = "replacement"
    refund = "refund"
    return_goods = "return"
    delivery = "delivery"
    cancellation = "cancellation"
    address = "address"
    missing = "missing"
    wrong = "wrong"


class LoginInput(StrictModel):
    username: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=1, max_length=200)
    tenant_id: str = "northstar"
    store_id: str = "uk"


class CaseInput(StrictModel):
    order_id: str = Field(max_length=80)
    line_id: str = Field(max_length=80)
    kind: Kind
    subject: str = Field(min_length=3, max_length=200)
    message: str = Field(min_length=1, max_length=10000)
    source_id: str = Field(min_length=8, max_length=120)


class VersionInput(StrictModel):
    version: int = Field(ge=1)


class MessageInput(VersionInput):
    body: str = Field(min_length=1, max_length=10000)
    source_id: str = Field(min_length=8, max_length=120)


class PlanInput(VersionInput):
    kind: Kind
    quantity: int = Field(default=1, ge=1, le=100)
    amount_minor: int = Field(default=0, ge=0, le=100000000)
    address: str = Field(default="", max_length=500)
    rationale: str = Field(min_length=3, max_length=3000)
    citations: list[str] = Field(min_length=1, max_length=10)


class ChoiceInput(StrictModel):
    plan_id: str
    accepted: bool


class ApprovalInput(StrictModel):
    plan_id: str
    decision: str = Field(pattern="^(approved|rejected|revoked)$")
    reason: str = Field(min_length=2, max_length=1000)


class ExecuteInput(StrictModel):
    plan_id: str
    business_action_id: str = Field(min_length=8, max_length=80)


class EventInput(StrictModel):
    source_id: str = Field(min_length=8, max_length=120)
    sequence: int = Field(ge=1)
    kind: str = Field(
        pattern="^(shipped|delivered|succeeded|failed|received|inspected|investigated|cancelled)$"
    )
    evidence: str = Field(min_length=3, max_length=2000)


class ClosureInput(VersionInput):
    reason: str = Field(min_length=3, max_length=2000)


class PolicyInput(StrictModel):
    family: str = Field(min_length=3, max_length=80)
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=20, max_length=20000)
    kind: Kind
    region: str = Field(pattern="^[A-Z]{2}$", default="GB")
    effective_from: float
    effective_until: float


class PublishInput(StrictModel):
    validation_cases: list[str] = Field(min_length=1, max_length=30)
    note: str = Field(min_length=5, max_length=2000)


class NotificationInput(StrictModel):
    body: str = Field(min_length=1, max_length=4000)


class ClaimInput(StrictModel):
    counterparty: str = Field(min_length=2, max_length=160)
    amount_minor: int = Field(gt=0)
    evidence: str = Field(min_length=5, max_length=3000)


class ClaimUpdate(VersionInput):
    status: str = Field(pattern="^(submitted|accepted|settled|rejected)$")
    settlement_ref: str = Field(default="", max_length=160)


class ImprovementInput(StrictModel):
    title: str = Field(min_length=3, max_length=200)
    hypothesis: str = Field(min_length=5, max_length=3000)
    case_ids: list[str] = Field(min_length=1, max_length=100)
    due_at: float


class ImprovementUpdate(VersionInput):
    measure: str = Field(min_length=5, max_length=3000)
    sample_size: int = Field(ge=1)
    observation: str = Field(min_length=10, max_length=3000)
    close: bool = False


class RoleOutput(StrictModel):
    summary: str = Field(min_length=1, max_length=3000)
    references: list[str] = Field(default_factory=list, max_length=30)
    missing: list[str] = Field(default_factory=list, max_length=20)


class SupervisorOutput(StrictModel):
    action: str
    reason: str = Field(max_length=500)
