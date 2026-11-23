import json

import httpx
from mercury.models import Action, Approval, Run, RunStep
from mercury.worker import process_one
from pydantic import SecretStr
from sqlalchemy import select
from test_business import CTX, MAIN


def mock_model(db, monkeypatch, response):
    db.settings.model_mode = "real"
    db.settings.llm_model = "contract-test-model"
    db.settings.llm_api_key = SecretStr("fixture-key-never-sent")
    original_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(lambda request: response(request))
        return original_client(*args, **kwargs)

    monkeypatch.setattr("mercury.workflow.httpx.Client", fake_client)


def test_model_cannot_select_unoffered_execution(client, db, headers, monkeypatch):
    run = client.post(f"/api/cases/{MAIN}/analyze", headers=headers()).json()

    def reply(request):
        body = json.loads(request.content)
        assert "allowed_actions" in body["messages"][1]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "action": "execute_refund",
                                    "reason": "injected instructions demand a refund",
                                }
                            )
                        }
                    }
                ],
                "usage": {"total_tokens": 50},
            },
        )

    mock_model(db, monkeypatch, reply)
    process_one(db)
    with db.session(CTX) as s:
        saved = s.get(Run, run["id"])
        assert saved.status == "failed" and saved.error == "invalid_model_action"
        assert not s.scalar(select(Action)) and not s.scalar(select(Approval))
        assert saved.used_tokens == 50


def test_real_model_failure_never_falls_back_and_retains_budget(client, db, headers, monkeypatch):
    run = client.post(f"/api/cases/{MAIN}/analyze", headers=headers()).json()
    mock_model(db, monkeypatch, lambda request: httpx.Response(401, json={"error": "invalid credential"}))
    process_one(db)
    with db.session(CTX) as s:
        saved = s.get(Run, run["id"])
        assert saved.unknown_usage and saved.reserved_tokens > 0 and saved.calls == 1
        assert not s.scalar(select(RunStep))
        assert not s.scalar(select(Action))
