import os
import sys
import tempfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

# Isolated, offline, deterministic test environment (set BEFORE importing the app).
_TMP = tempfile.mkdtemp(prefix="sce_test_")
os.environ["SCE_DB_PATH"] = os.path.join(_TMP, "test.sqlite3")
os.environ.pop("GEMINI_API_KEY", None)
os.environ["SCE_ENV"] = "development"

from fastapi.testclient import TestClient  # noqa: E402
from backend.main import app  # noqa: E402
import backend.main as main  # noqa: E402
from backend import auth, config  # noqa: E402

H = {"X-SCE-Request": "1"}
DATASET = os.path.join(ROOT, "dataset")


@pytest.fixture(autouse=True)
def _reset_limits(email_outbox):
    auth.reset_rate_limits()
    yield
    auth.reset_rate_limits()


@pytest.fixture
def email_outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(config, "SMTP_FROM", "noreply@example.test")
    monkeypatch.setattr(config, "SMTP_USERNAME", "test-user")
    monkeypatch.setattr(config, "SMTP_PASSWORD", "test-password")
    monkeypatch.setattr(main, "_send_auth_email", lambda email, purpose, token: sent.append({
        "email": email, "purpose": purpose, "token": token
    }))
    return sent


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def make_user(client):
    counter = {"n": 0}

    def _make(c=None):
        c = c or client
        counter["n"] += 1
        email = f"user{counter['n']}_{os.getpid()}_{id(c)}@example.com"
        r = c.post("/api/auth/signup", headers=H, json={
            "email": email, "password": "correct horse battery", "display_name": f"Tester {counter['n']}",
            "accept_terms": True})
        assert r.status_code == 201, r.text
        return email
    return _make
