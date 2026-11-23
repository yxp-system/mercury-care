import os

import pytest
from fastapi.testclient import TestClient
from mercury.api import create_app
from mercury.auth import login
from mercury.config import Settings
from mercury.models import Base
from mercury.seed import seed
from mercury.workflow import checkpointer
from sqlalchemy import text


@pytest.fixture
def app(tmp_path):
    database_url = os.getenv("TEST_DATABASE_URL", "sqlite:///" + str(tmp_path / "test.db"))
    assert "test" in database_url.lower(), "Tests refuse a database URL without an explicit test namespace"
    settings = Settings(
        _env_file=None,
        env="test",
        database_url=database_url,
        data_dir=tmp_path,
        jwt_secret="test-only-signing-secret-with-at-least-32-characters",
        demo_password="TestPassword-123!",
        token_budget=100000,
        webhook_secret="test-only-callback-secret",
    )
    application = create_app(settings)
    db = application.state.db
    if db.engine.dialect.name == "postgresql":
        with db.engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            Base.metadata.drop_all(conn)
            Base.metadata.create_all(conn)
        with checkpointer(db) as saver:
            saver.setup()
    else:
        Base.metadata.create_all(db.engine)
    seed(db)
    yield application
    db.engine.dispose()


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def db(app):
    return app.state.db


@pytest.fixture
def headers(db):
    def authenticated(username="demo"):
        other = username == "outsider"
        result = login(
            db, username, "TestPassword-123!", "other" if other else "northstar", "de" if other else "uk"
        )
        return {"Authorization": "Bearer " + result["access_token"]}

    return authenticated
