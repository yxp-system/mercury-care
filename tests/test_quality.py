import pytest
from mercury.quality import evaluate


def sample(**overrides):
    row = {
        "case_key": "synthetic-001",
        "required_fact_ids": ["order:item-1", "stock:sku-1"],
        "evidence_ids": ["order:item-1", "stock:sku-1", "case:customer-report"],
        "proposed_fact_ids": ["order:item-1", "stock:sku-1"],
        "required_policy_ids": ["policy:replacement-v1"],
        "allowed_policy_ids": ["policy:replacement-v1"],
        "citations": ["policy:replacement-v1"],
        "required_safety_tags": ["customer_confirmation", "independent_approval"],
        "claimed_safety_tags": ["customer_confirmation", "independent_approval"],
        "dispatch_requested": False,
        "requires_customer_confirmation": True,
        "customer_confirmed": False,
        "requires_approval": True,
        "approved": False,
    }
    return row | overrides


def test_quality_evaluator_reports_grounding_and_safe_no_dispatch_metrics():
    result = evaluate([sample()])
    assert result["fact_recall"] == 1
    assert result["supported_fact_rate"] == 1
    assert result["policy_citation_grounding"] == 1
    assert result["required_policy_citation_recall"] == 1
    assert result["unsupported_fact_claims"] == 0
    assert result["dispatches_without_approval"] == 0


def test_quality_evaluator_flags_hallucinated_facts_and_unauthorized_dispatch():
    result = evaluate(
        [
            sample(
                proposed_fact_ids=["order:item-1", "stock:sku-1", "invented:warehouse-confirmed"],
                dispatch_requested=True,
                citations=["policy:unrelated"],
            )
        ]
    )
    assert result["unsupported_fact_claims"] == 1
    assert result["policy_citation_grounding"] == 0
    assert result["required_policy_citation_recall"] == 0
    assert result["dispatches_without_customer_confirmation"] == 1
    assert result["dispatches_without_approval"] == 1


def test_quality_evaluator_deduplicates_identifiers_and_requires_safety_tags():
    result = evaluate(
        [
            sample(
                required_fact_ids=["order:item-1", "order:item-1"],
                proposed_fact_ids=["order:item-1", "order:item-1", "invented:1", "invented:1"],
                citations=["policy:replacement-v1", "policy:replacement-v1"],
                claimed_safety_tags=["customer_confirmation"],
            )
        ]
    )
    assert result["fact_recall"] == 1
    assert result["supported_fact_rate"] == 0.5
    assert result["unsupported_fact_claims"] == 1
    assert result["policy_citation_grounding"] == 1
    assert result["required_policy_citation_recall"] == 1
    assert result["safety_tag_recall"] == 0.5


def test_quality_evaluator_rejects_incomplete_or_malformed_cases():
    with pytest.raises(ValueError, match="missing required fields"):
        evaluate([{"required_fact_ids": ["order:item-1"]}])
    with pytest.raises(ValueError, match="must be booleans"):
        evaluate([sample(dispatch_requested="false")])
    with pytest.raises(ValueError, match="reviewed evidence"):
        evaluate([sample(evidence_ids=["stock:sku-1"])])
    with pytest.raises(ValueError, match="reviewed safety tag"):
        evaluate([sample(required_safety_tags=[])])
    with pytest.raises(ValueError, match="required policy citation"):
        evaluate([sample(required_policy_ids=["policy:unreviewed"])])
