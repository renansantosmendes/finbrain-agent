"""Authentication of browser clients (finbrain_front) against auth_service.

The web front end signs users in at the shared auth service, which issues
RS256 access tokens. This module verifies those tokens with the service's
public keys (JWKS) and scopes conversation threads per user, so one person
can never read or continue another person's session.

Enabled only when AUTH_JWKS_URL is set. Without it, /chat and
/invoices/analyze keep working unauthenticated exactly as before, so
existing clients and local development are unaffected.

Environment variables:
- AUTH_JWKS_URL       -- e.g. https://<auth-service>/.well-known/jwks.json
- AUTH_JWT_ISSUER     -- must match the auth service (default: auth-service)
- AUTH_JWT_AUDIENCE   -- must match the auth service (default: synapse-apps)
- CORS_ALLOWED_ORIGINS -- comma-separated origins of the web front end
"""
import os
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import jwt
from fastapi import Header, HTTPException

from logging_config import logger

JWKS_CACHE_SECONDS = 3600
REQUIRED_CLAIMS = ["sub", "exp", "iat", "iss", "aud"]


@dataclass(frozen=True)
class WebUser:
    """The person behind a verified access token."""

    user_id: str
    email: Optional[str]


def auth_jwks_url() -> Optional[str]:
    return os.environ.get("AUTH_JWKS_URL") or None


def cors_allowed_origins() -> list[str]:
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@lru_cache(maxsize=4)
def _jwks_client(jwks_url: str) -> jwt.PyJWKClient:
    # Cached per URL so a warm serverless instance downloads the keys once
    # per hour instead of on every request.
    return jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=JWKS_CACHE_SECONDS, timeout=10)


def verify_access_token(token: str, jwks_url: str) -> WebUser:
    """Verify an auth service access token and return who it belongs to.

    Raises HTTPException 401 for any invalid, expired or foreign token, and
    503 when the auth service's public keys can't be downloaded (that is
    not the token's fault, so it must not look like a sign-in problem).
    """
    try:
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=os.environ.get("AUTH_JWT_ISSUER", "auth-service"),
            audience=os.environ.get("AUTH_JWT_AUDIENCE", "synapse-apps"),
            options={"require": REQUIRED_CLAIMS},
        )
    except jwt.PyJWKClientConnectionError:
        logger.exception("web_auth: could not fetch the auth service JWKS")
        raise HTTPException(status_code=503, detail="Authentication service unavailable")
    except jwt.PyJWTError as error:
        logger.info("web_auth: rejected token (%s)", type(error).__name__)
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if claims.get("typ") != "access":
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return WebUser(user_id=str(claims["sub"]), email=claims.get("email"))


async def optional_web_user(authorization: Optional[str] = Header(default=None)) -> Optional[WebUser]:
    """FastAPI dependency: the authenticated web user, or None in open mode.

    With AUTH_JWKS_URL configured, a valid `Authorization: Bearer <token>`
    is mandatory (401 otherwise). Without it, authentication is off and
    this always returns None.
    """
    jwks_url = auth_jwks_url()
    if jwks_url is None:
        return None
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return verify_access_token(authorization[len("Bearer "):].strip(), jwks_url)


def resolve_session(user: Optional[WebUser], client_session_id: Optional[str]) -> tuple[str, str]:
    """Map the session id the client sees to the thread id used internally.

    Returns (client_session_id, thread_id). In open mode both are the same,
    as before. For authenticated users the thread id is namespaced by the
    user id, so a session id guessed or copied from someone else can only
    ever open a thread of the caller's own -- and the client id must be a
    UUID so it can't be crafted to escape that namespace.
    """
    if user is None:
        session_id = client_session_id or str(uuid.uuid4())
        return session_id, session_id

    if client_session_id is None:
        client_session_id = str(uuid.uuid4())
    else:
        try:
            client_session_id = str(uuid.UUID(client_session_id))
        except ValueError:
            raise HTTPException(status_code=422, detail="session_id must be a UUID")
    return client_session_id, f"web:{user.user_id}:{client_session_id}"
