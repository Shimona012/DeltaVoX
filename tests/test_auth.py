"""Authentication, session and access-control (IDOR) tests."""
from fastapi.testclient import TestClient

from conftest import H
from backend import config
from backend.main import app

REF = "dataset/speech_001_persuasive/level_0_ideal.wav"
L2 = "dataset/speech_001_persuasive/level_2_minor.wav"
PW = "correct horse battery"


def signup(c, email, pw=PW, name="Tester", terms=True):
    return c.post("/api/auth/signup", headers=H,
                  json={"email": email, "password": pw, "display_name": name, "accept_terms": terms})


def verify(c, email, outbox):
    item = next(m for m in reversed(outbox) if m["email"] == email.lower() and m["purpose"] == "verify_email")
    return c.post("/api/auth/verification/confirm", headers=H, json={"token": item["token"]})


def verified_signup(c, email, outbox, **kwargs):
    result = signup(c, email, **kwargs)
    assert result.status_code == 201
    assert verify(c, email, outbox).status_code == 200
    assert c.post("/api/auth/login", headers=H, json={"email": email, "password": kwargs.get("pw", PW)}).status_code == 200
    return result


def test_signup_requires_email_verification_before_session(client, email_outbox):
    r = signup(client, "cookie@example.com")
    assert r.status_code == 201
    assert r.json()["email_verification_required"] is True
    assert "set-cookie" not in r.headers
    assert client.get("/api/auth/me").json()["user"] is None
    assert verify(client, "cookie@example.com", email_outbox).status_code == 200
    login = client.post("/api/auth/login", headers=H, json={"email": "cookie@example.com", "password": PW})
    assert login.status_code == 200
    sc = login.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=strict" in sc and "path=/" in sc
    assert "password" not in r.text and "hash" not in r.text
    assert client.get("/api/auth/me").json()["user"]["email"] == "cookie@example.com"


def test_verification_token_is_single_use(client, email_outbox):
    r = signup(client, "verify@example.com")
    assert r.status_code == 201
    token = email_outbox[-1]["token"]
    assert client.post("/api/auth/verification/confirm", headers=H, json={"token": token}).status_code == 200
    assert client.post("/api/auth/verification/confirm", headers=H, json={"token": token}).status_code == 400


def test_verification_token_expires(client, email_outbox, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_VERIFY_TTL_SEC", 0)
    assert signup(client, "expired@example.com").status_code == 201
    token = email_outbox[-1]["token"]
    assert client.post("/api/auth/verification/confirm", headers=H, json={"token": token}).status_code == 400


def test_unverified_account_cannot_sign_in(client):
    assert signup(client, "pending@example.com").status_code == 201
    r = client.post("/api/auth/login", headers=H, json={"email": "pending@example.com", "password": PW})
    assert r.status_code == 403
    assert client.get("/api/auth/me").json()["user"] is None


def test_password_reset_requires_single_use_email_token(client, email_outbox):
    verified_signup(client, "reset@example.com", email_outbox)
    assert client.get("/api/auth/me").json()["user"] is not None
    assert client.post("/api/auth/reset-password", headers=H,
                       json={"email": "reset@example.com", "new_password": "new secure passphrase"}).status_code == 422
    request = client.post("/api/auth/password-reset/request", headers=H,
                          json={"email": "reset@example.com"})
    assert request.status_code == 200
    assert "If an account exists" in request.json()["detail"]
    token = email_outbox[-1]["token"]
    changed = client.post("/api/auth/reset-password", headers=H,
                          json={"token": token, "new_password": "new secure passphrase"})
    assert changed.status_code == 200
    assert client.get("/api/auth/me").json()["user"] is None
    assert client.post("/api/auth/reset-password", headers=H,
                       json={"token": token, "new_password": "another secure passphrase"}).status_code == 400
    assert client.post("/api/auth/login", headers=H,
                       json={"email": "reset@example.com", "password": "new secure passphrase"}).status_code == 200


def test_signup_validation(client):
    assert signup(client, "not-an-email").status_code == 400
    assert signup(client, "weak@example.com", pw="short").status_code == 400
    assert signup(client, "weak2@example.com", pw="password123").status_code == 400
    assert signup(client, "weak3@example.com", pw="aaaaaaaaaaaa").status_code == 400
    assert signup(client, "xss@example.com", name="<script>alert(1)</script>").status_code == 400
    assert signup(client, "terms@example.com", terms=False).status_code == 400
    r = client.post("/api/auth/signup", json={"email": "c@example.com", "password": PW, "display_name": "x",
                                              "accept_terms": True})
    assert r.status_code == 403


def test_duplicate_signup_is_generic(client):
    assert signup(client, "dup@example.com").status_code == 201
    with TestClient(app) as other:
        r = signup(other, "DUP@example.com")
        assert r.status_code == 400 and "already exists" not in r.text.lower()


def test_login_errors_are_generic(client, email_outbox):
    verified_signup(client, "login@example.com", email_outbox)
    with TestClient(app) as c:
        wrong = c.post("/api/auth/login", headers=H, json={"email": "login@example.com", "password": "wrong password!"})
        unknown = c.post("/api/auth/login", headers=H, json={"email": "nobody@example.com", "password": "wrong password!"})
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()
        ok = c.post("/api/auth/login", headers=H, json={"email": "LOGIN@example.com", "password": PW})
        assert ok.status_code == 200


def test_account_lockout(client, email_outbox):
    verified_signup(client, "lock@example.com", email_outbox)
    with TestClient(app) as c:
        for _ in range(config.MAX_FAILED_LOGINS):
            assert c.post("/api/auth/login", headers=H, json={"email": "lock@example.com", "password": "bad bad bad"}).status_code == 401
        r = c.post("/api/auth/login", headers=H, json={"email": "lock@example.com", "password": PW})
        assert r.status_code == 401  # locked even with the right password


def test_login_rate_limited(client, monkeypatch):
    monkeypatch.setattr(config, "AUTH_RATE_PER_15MIN", 3)
    codes = [client.post("/api/auth/login", headers=H, json={"email": "x@example.com", "password": "nope nope"}).status_code
             for _ in range(5)]
    assert codes[-1] == 429


def test_logout_revokes_server_side_session(client, email_outbox):
    verified_signup(client, "logout@example.com", email_outbox)
    token = client.cookies.get(config.SESSION_COOKIE)
    assert client.post("/api/auth/logout", headers=H).status_code == 200
    assert client.get("/api/auth/me").json()["user"] is None
    with TestClient(app) as replay:  # stolen cookie no longer works after logout
        replay.cookies.set(config.SESSION_COOKIE, token)
        assert replay.get("/api/auth/me").json()["user"] is None


def test_session_rotates_on_login(client, email_outbox):
    verified_signup(client, "rotate@example.com", email_outbox)
    first = client.cookies.get(config.SESSION_COOKIE)
    client.post("/api/auth/login", headers=H, json={"email": "rotate@example.com", "password": PW})
    second = client.cookies.get(config.SESSION_COOKIE)
    assert first != second
    with TestClient(app) as old:
        old.cookies.set(config.SESSION_COOKIE, first)
        assert old.get("/api/auth/me").json()["user"] is None


def test_forged_token_rejected(client):
    client.cookies.set(config.SESSION_COOKIE, "pro_admin")
    assert client.get("/api/auth/me").json()["user"] is None
    assert client.get("/api/history").status_code == 401


def test_history_requires_login(client):
    assert client.get("/api/history").status_code == 401


def test_history_is_owner_scoped_no_idor(client, email_outbox):
    with TestClient(app) as a, TestClient(app) as b:
        verified_signup(a, "alice@example.com", email_outbox)
        verified_signup(b, "bob@example.com", email_outbox)
        r = a.post("/api/analyze", headers=H, data={"reference_preset": REF, "evaluated_preset": L2})
        assert r.status_code == 200 and r.json()["saved"] is True
        item = r.json()["analysis_id"]

        assert len(a.get("/api/history").json()["items"]) == 1
        assert b.get("/api/history").json()["items"] == []
        assert b.delete(f"/api/history/{item}", headers=H).status_code == 404     # cannot delete Alice's
        assert len(a.get("/api/history").json()["items"]) == 1
        assert a.delete("/api/history/not-a-uuid", headers=H).status_code == 404
        assert a.delete(f"/api/history/{item}", headers=H).status_code == 200
        assert a.get("/api/history").json()["items"] == []


def test_delete_account_removes_everything(client, email_outbox):
    with TestClient(app) as a:
        verified_signup(a, "gone@example.com", email_outbox)
        a.post("/api/analyze", headers=H, data={"reference_preset": REF, "evaluated_preset": L2})
        assert a.delete("/api/account").status_code == 403  # needs anti-forgery header
        assert a.delete("/api/account", headers=H).status_code == 200
        assert a.get("/api/auth/me").json()["user"] is None
        r = a.post("/api/auth/login", headers=H, json={"email": "gone@example.com", "password": PW})
        assert r.status_code == 401
