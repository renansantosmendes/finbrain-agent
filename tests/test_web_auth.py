"""Unit tests for web_auth.py and the authenticated mode of /chat and
/invoices/analyze.

Tokens are signed here with a throwaway RSA key, in the same format the
auth service issues; `web_auth._jwks_client` is patched so the JWKS is
never downloaded. As in test_api.py, the TestClient is used without the
`with` block, so the app's lifespan (real network calls) never runs.
"""
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import web_auth
from app import AgentRuntime, app, get_runtime
from tests.test_api import FakeAgent

SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWKS_URL = "https://auth.example.com/.well-known/jwks.json"


def make_token(sub=None, key=SIGNING_KEY, expires_in=900, **overrides):
    now = int(time.time())
    claims = {
        "sub": sub or str(uuid.uuid4()),
        "email": "ana@example.com",
        "name": "Ana Lima",
        "typ": "access",
        "iss": "auth-service",
        "aud": "synapse-apps",
        "iat": now,
        "exp": now + expires_in,
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


class FakeJwksClient:
    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=SIGNING_KEY.public_key())


class UnreachableJwksClient:
    def get_signing_key_from_jwt(self, token):
        raise jwt.PyJWKClientConnectionError("connection refused")


@pytest.fixture
def fake_runtime():
    runtime = AgentRuntime()
    runtime.agent = FakeAgent()
    return runtime


@pytest.fixture
def auth_client(fake_runtime, monkeypatch):
    monkeypatch.setenv("AUTH_JWKS_URL", JWKS_URL)
    monkeypatch.setattr(web_auth, "_jwks_client", lambda url: FakeJwksClient())
    app.dependency_overrides[get_runtime] = lambda: fake_runtime
    with patch("persistence.log_message", new=AsyncMock()):
        yield TestClient(app)
    app.dependency_overrides.clear()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def thread_ids(runtime):
    return [config["configurable"]["thread_id"] for _, config in runtime.agent.received_calls]


def test_open_mode_returns_no_user(monkeypatch):
    monkeypatch.delenv("AUTH_JWKS_URL", raising=False)
    assert web_auth.auth_jwks_url() is None
    assert web_auth.resolve_session(None, "abc") == ("abc", "abc")


def test_cors_origins_are_parsed(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://a.app, ,https://b.app")
    assert web_auth.cors_allowed_origins() == ["https://a.app", "https://b.app"]


def test_chat_requires_token_when_auth_is_enabled(auth_client):
    response = auth_client.post("/chat", json={"message": "oi"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        make_token(key=OTHER_KEY),
        make_token(expires_in=-60),
        make_token(aud="other-app"),
        make_token(iss="someone-else"),
        make_token(typ="refresh"),
    ],
)
def test_chat_rejects_untrusted_tokens(auth_client, token):
    response = auth_client.post("/chat", json={"message": "oi"}, headers=bearer(token))
    assert response.status_code == 401


def test_chat_namespaces_thread_by_user(auth_client, fake_runtime):
    user_id = str(uuid.uuid4())
    response = auth_client.post("/chat", json={"message": "oi"}, headers=bearer(make_token(sub=user_id)))
    assert response.status_code == 200
    client_session = response.json()["session_id"]
    uuid.UUID(client_session)
    assert thread_ids(fake_runtime) == [f"web:{user_id}:{client_session}"]


def test_same_session_id_from_another_user_opens_a_different_thread(auth_client, fake_runtime):
    session_id = str(uuid.uuid4())
    alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
    for user_id in (alice, bob):
        response = auth_client.post(
            "/chat",
            json={"message": "oi", "session_id": session_id},
            headers=bearer(make_token(sub=user_id)),
        )
        assert response.json()["session_id"] == session_id
    assert thread_ids(fake_runtime) == [f"web:{alice}:{session_id}", f"web:{bob}:{session_id}"]


def test_chat_rejects_non_uuid_session_id_when_authenticated(auth_client):
    response = auth_client.post(
        "/chat",
        json={"message": "oi", "session_id": "web:someone-else:123"},
        headers=bearer(make_token()),
    )
    assert response.status_code == 422


def test_unreachable_jwks_returns_503(auth_client, monkeypatch):
    monkeypatch.setattr(web_auth, "_jwks_client", lambda url: UnreachableJwksClient())
    response = auth_client.post("/chat", json={"message": "oi"}, headers=bearer(make_token()))
    assert response.status_code == 503


def test_invoices_require_token_and_namespace_thread(auth_client, fake_runtime):
    files = {"files": ("fatura.pdf", b"%PDF-1.4 not really a pdf", "application/pdf")}
    assert auth_client.post("/invoices/analyze", files=files).status_code == 401

    user_id = str(uuid.uuid4())
    with patch("invoices.extract_text", return_value=SimpleNamespace(filename="fatura.pdf", text="x")):
        response = auth_client.post(
            "/invoices/analyze",
            files=files,
            headers=bearer(make_token(sub=user_id)),
        )
    assert response.status_code == 200
    assert thread_ids(fake_runtime)[-1] == f"web:{user_id}:{response.json()['session_id']}"
